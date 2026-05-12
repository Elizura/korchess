"""AI insights pipeline for chess game histories."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from typing import Any

import chess
import chess.pgn
import httpx

from repository.db import (
    clear_insights_data,
    clear_quick_scan_data,
    create_insight_job,
    get_active_insight_job,
    get_featured_game_ids,
    get_games_for_insights,
    get_insight_game_features,
    get_player_insights,
    get_quick_scan_results,
    update_insight_job,
    upsert_insight_game_feature,
    upsert_player_insights,
)
from repository.db_connection import get_connection
from utils.insights_constants import (
    FEATURE_VERSION,
    LOW_TIME_FLOOR_SECONDS,
    LOW_TIME_RATIO,
    MAX_CONCURRENT_INSIGHTS,
    MAX_GAMES_WINDOW,
    MIN_BASELINE_GAMES,
    NARRATIVE_API_KEY,
    NARRATIVE_API_URL,
    NARRATIVE_MODEL,
    NARRATIVE_PROVIDER,
    NARRATIVE_TIMEOUT_S,
    NARRATIVE_VERSION,
)
from utils.insights_utils import (
    add_fact,
    clamp01,
    extract_clock_seconds,
    mean,
    phase_for_ply,
    utc_now_iso,
)
from services.insights_aggregate import (
    aggregate_light_features,
    aggregate_scan_features,
    compute_style_scores,
    compute_opening_rankings,
    build_coverage_metrics,
    build_strengths_weaknesses,
)

_INSIGHTS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_INSIGHTS)


def extract_light_game_features(game_row: dict[str, Any]) -> dict[str, Any]:
    """Compute fast, deterministic per-game features from PGN + metadata."""
    pgn = game_row.get("pgn") or ""
    result = game_row.get("result") or "loss"
    color = (game_row.get("color") or "white").lower()
    user_is_white = color == "white"
    computed_at = utc_now_iso()

    base = {
        "version": FEATURE_VERSION,
        "analysis_tier": "light",
        "computed_at": computed_at,
        "metadata": {
            "site": game_row.get("site"),
            "site_game_id": game_row.get("site_game_id"),
            "played_at": game_row.get("played_at"),
            "time_class": game_row.get("time_class"),
            "color": color,
            "result": result,
            "opening_name": game_row.get("opening_name") or "Unknown",
            "opponent": game_row.get("opponent"),
            "white_elo": game_row.get("white_elo"),
            "black_elo": game_row.get("black_elo"),
        },
    }

    if not pgn.strip():
        base["errors"] = ["missing_pgn"]
        base["phase_profile"] = {"opening_end_ply": 0, "endgame_start_ply": None, "total_plies": 0}
        base["style_signals"] = {"early_capture_rate": 0.0, "early_check_rate": 0.0, "avg_game_length": 0.0}
        base["time_pressure"] = {
            "has_clock_data": False,
            "clock_samples": 0,
            "low_time_threshold_s": None,
            "low_time_moves": 0,
            "low_time_rate": 0.0,
        }
        base["clock_by_ply"] = {}
        return base

    parsed_game = chess.pgn.read_game(io.StringIO(pgn))
    if not parsed_game:
        base["errors"] = ["invalid_pgn"]
        base["phase_profile"] = {"opening_end_ply": 0, "endgame_start_ply": None, "total_plies": 0}
        base["style_signals"] = {"early_capture_rate": 0.0, "early_check_rate": 0.0, "avg_game_length": 0.0}
        base["time_pressure"] = {
            "has_clock_data": False,
            "clock_samples": 0,
            "low_time_threshold_s": None,
            "low_time_moves": 0,
            "low_time_rate": 0.0,
        }
        base["clock_by_ply"] = {}
        return base

    board = parsed_game.board()
    node = parsed_game

    total_captures = 0
    total_checks = 0
    user_early_captures = 0
    user_early_checks = 0
    user_move_count = 0
    clock_samples: list[int] = []
    move_events: list[dict[str, Any]] = []
    endgame_start_ply: int | None = None

    for ply, move in enumerate(parsed_game.mainline_moves(), start=1):
        is_white_to_move = board.turn == chess.WHITE
        is_user_move = (is_white_to_move and user_is_white) or ((not is_white_to_move) and (not user_is_white))

        is_capture = board.is_capture(move)
        if is_capture:
            total_captures += 1
            if is_user_move and ply <= 20:
                user_early_captures += 1

        board.push(move)
        gives_check = board.is_check()
        if gives_check:
            total_checks += 1
            if is_user_move and ply <= 20:
                user_early_checks += 1

        node = node.variation(0)
        clock_seconds = extract_clock_seconds(node.comment)
        if is_user_move:
            user_move_count += 1
            if clock_seconds is not None:
                clock_samples.append(clock_seconds)

        non_king_pieces = sum(
            1 for piece in board.piece_map().values() if piece.piece_type != chess.KING
        )

        # TODO: just non king pieces or non-king non-pawn moves 
        if endgame_start_ply is None and non_king_pieces <= 8:
            endgame_start_ply = ply

        move_events.append(
            {
                "ply": ply,
                "is_user_move": is_user_move,
                "is_capture": is_capture,
                "is_check": gives_check,
                "clock_seconds": clock_seconds if is_user_move else None,
            }
        )

    total_plies = len(move_events)
    opening_end_ply = min(20, total_plies)

    for event in move_events:
        event["phase"] = phase_for_ply(event["ply"], opening_end_ply, endgame_start_ply)

    if user_move_count <= 0:
        early_capture_rate = 0.0
        early_check_rate = 0.0
    else:
        early_capture_rate = user_early_captures / user_move_count
        early_check_rate = user_early_checks / user_move_count

    rating_delta: int | None = None
    white_elo = game_row.get("white_elo")
    black_elo = game_row.get("black_elo")
    if isinstance(white_elo, int) and isinstance(black_elo, int):
        rating_delta = (white_elo - black_elo) if user_is_white else (black_elo - white_elo)

    low_time_threshold: int | None = None
    low_time_moves = 0
    if clock_samples:
        initial_clock = max(clock_samples)
        low_time_threshold = max(LOW_TIME_FLOOR_SECONDS, int(initial_clock * LOW_TIME_RATIO))
        low_time_moves = sum(1 for value in clock_samples if value <= low_time_threshold)

    base["metadata"]["rating_delta"] = rating_delta
    base["phase_profile"] = {
        "opening_end_ply": opening_end_ply,
        "endgame_start_ply": endgame_start_ply,
        "total_plies": total_plies,
    }

    # TODO: average_game_length is total_plies, which is not correct
    base["style_signals"] = {
        "early_capture_rate": round(early_capture_rate, 4),
        "early_check_rate": round(early_check_rate, 4),
        "avg_game_length": float(total_plies),
        "capture_density": round((total_captures / total_plies) if total_plies > 0 else 0.0, 4),
        "check_density": round((total_checks / total_plies) if total_plies > 0 else 0.0, 4),
    }
    base["time_pressure"] = {
        "has_clock_data": bool(clock_samples),
        "clock_samples": len(clock_samples),
        "low_time_threshold_s": low_time_threshold,
        "low_time_moves": low_time_moves,
        "low_time_rate": round((low_time_moves / len(clock_samples)) if clock_samples else 0.0, 4),
    }
    # Store only clock data per ply for deep feature extraction
    base["clock_by_ply"] = {
        int(event["ply"]): int(event["clock_seconds"])
        for event in move_events
        if event.get("clock_seconds") is not None
    }

    return base



def _build_aggregate_features(
    feature_rows: list[dict[str, Any]],
    scan_rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Aggregate per-game features into user-level insights artifacts.

    Uses light features for win/loss/style metrics and quick-scan results
    for engine-derived metrics (phase CP loss, themes, blunder rates).

    Returns:
        (features, coverage, fact_map) tuple for player insights.
    """
    light_features = [row["light"] for row in feature_rows if row.get("light")]

    light_agg = aggregate_light_features(light_features)
    total_games = light_agg["total_games"]
    wins = light_agg["wins"]
    draws = light_agg["draws"]
    losses = light_agg["losses"]
    by_time_class = light_agg["by_time_class"]
    by_color = light_agg["by_color"]
    openings = light_agg["openings"]
    clock_games = light_agg["clock_games"]
    low_time_games = light_agg["low_time_games"]

    overall_score_pct = round((mean(light_agg["overall_scores"]) * 100), 1) if light_agg["overall_scores"] else 0.0
    low_time_score_pct = round((mean(light_agg["low_time_scores"]) * 100), 1) if light_agg["low_time_scores"] else None

    scan_agg = aggregate_scan_features(scan_rows) if scan_rows else None
    phase_performance = scan_agg["phase_performance"] if scan_agg else {}
    theme_counts = scan_agg["theme_counts"] if scan_agg else {}
    total_user_moves_scan = scan_agg["total_user_moves"] if scan_agg else 0
    total_blunders_scan = scan_agg["total_blunders"] if scan_agg else 0
    games_scanned = scan_agg["games_scanned"] if scan_agg else 0

    best_openings, worst_openings = compute_opening_rankings(openings)

    draw_rate = (draws / total_games) if total_games > 0 else 0.0
    avg_early_capture = mean(light_agg["early_capture_rates"])
    avg_early_check = mean(light_agg["early_check_rates"])
    avg_game_len = mean(light_agg["game_lengths"])
    blunder_rate = (total_blunders_scan / total_user_moves_scan) if total_user_moves_scan > 0 else 0.0

    style = compute_style_scores(
        draw_rate=draw_rate,
        avg_early_capture=avg_early_capture,
        avg_early_check=avg_early_check,
        avg_game_len=avg_game_len,
        blunder_rate=blunder_rate,
        theme_counts=theme_counts,
        scanned_game_count=games_scanned,
    )

    theme_items = sorted(
        [{"theme": theme, "count": count} for theme, count in theme_counts.items()],
        key=lambda item: item["count"],
        reverse=True,
    )

    coverage = build_coverage_metrics(
        total_games=total_games,
        light_count=len(light_features),
        scan_count=games_scanned,
        clock_games=clock_games,
        low_time_games=low_time_games,
    )
    confidence = coverage["confidence"]

    fact_map: dict[str, dict[str, Any]] = {}
    overall_games_fact = add_fact(fact_map, "overall_games", "Games analyzed", total_games, "games")
    overall_score_fact = add_fact(fact_map, "overall_score_pct", "Overall score", overall_score_pct, "pct")
    style_fact = add_fact(fact_map, "style_label", "Player style", style["label"])
    scan_cov_fact = add_fact(fact_map, "scan_coverage", "Scan coverage", coverage["scan_coverage"], "ratio")
    clock_cov_fact = add_fact(fact_map, "clock_coverage", "Clock data coverage", coverage["clock_coverage"], "ratio")
    confidence_fact = add_fact(fact_map, "confidence", "Insights confidence", round(confidence, 3), "ratio")

    phase_fact_ids: dict[str, str] = {}
    for phase_name, stats in phase_performance.items():
        if stats["avg_cp_loss"] is None:
            continue
        phase_fact_ids[phase_name] = add_fact(
            fact_map,
            f"{phase_name}_avg_cp_loss",
            f"{phase_name.capitalize()} avg cp loss",
            stats["avg_cp_loss"],
            "cp",
        )

    best_opening_fact_ids = [
        add_fact(fact_map, f"best_opening_{idx}", f"Best opening #{idx}",
                 f"{item['opening']} ({item['score_pct']}% over {item['games']} games)")
        for idx, item in enumerate(best_openings, start=1)
    ]
    weak_opening_fact_ids = [
        add_fact(fact_map, f"worst_opening_{idx}", f"Weak opening #{idx}",
                 f"{item['opening']} ({item['score_pct']}% over {item['games']} games)")
        for idx, item in enumerate(worst_openings, start=1)
    ]

    time_pressure_fact_ids: list[str] = []
    if low_time_score_pct is not None:
        time_pressure_fact_ids.append(
            add_fact(fact_map, "low_time_score_pct", "Score in low-time games", low_time_score_pct, "pct")
        )

    strengths, weaknesses, coaching_focus = build_strengths_weaknesses(
        overall_score_pct=overall_score_pct,
        total_games=total_games,
        best_openings=best_openings,
        worst_openings=worst_openings,
        phase_performance=phase_performance,
        low_time_score_pct=low_time_score_pct,
        theme_items=theme_items,
        fact_map=fact_map,
        overall_games_fact=overall_games_fact,
        overall_score_fact=overall_score_fact,
        best_opening_fact_ids=best_opening_fact_ids,
        weak_opening_fact_ids=weak_opening_fact_ids,
        phase_fact_ids=phase_fact_ids,
    )

    features = {
        "version": FEATURE_VERSION,
        "computed_at": utc_now_iso(),
        "style": {
            "label": style["label"],
            "scores": style["scores"],
            "fact_ids": [style_fact],
        },
        "performance": {
            "overall": {
                "games": total_games,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "score_pct": overall_score_pct,
                "fact_ids": [overall_games_fact, overall_score_fact],
            },
            "by_time_class": [
                {
                    "time_class": tc,
                    "games": int(stats["games"]),
                    "score_pct": round((stats["score_sum"] / stats["games"]) * 100, 1) if stats["games"] > 0 else 0.0,
                }
                for tc, stats in sorted(by_time_class.items())
            ],
            "by_color": [
                {
                    "color": color_name,
                    "games": int(stats["games"]),
                    "score_pct": round((stats["score_sum"] / stats["games"]) * 100, 1) if stats["games"] > 0 else 0.0,
                }
                for color_name, stats in sorted(by_color.items())
            ],
            "phase": phase_performance,
            "best_openings": best_openings,
            "weak_openings": worst_openings,
        },
        "time_pressure": {
            "clock_coverage": coverage["clock_coverage"],
            "games_with_clock": clock_games,
            "games_with_pressure": low_time_games,
            "score_pct_under_pressure": low_time_score_pct,
            "score_pct_overall": overall_score_pct,
            "blunders_total": total_blunders_scan,
            "fact_ids": time_pressure_fact_ids + [clock_cov_fact],
        },
        "recurring_themes": theme_items[:5],
        "strengths": strengths,
        "weaknesses": weaknesses,
        "coaching_focus": coaching_focus,
        "confidence": {
            "value": round(confidence, 3),
            "fact_ids": [confidence_fact, scan_cov_fact, clock_cov_fact],
        },
    }

    if scan_agg:
        features["scan_aggregate"] = scan_agg

    return features, coverage, fact_map


def _build_fallback_narrative(features: dict[str, Any]) -> dict[str, Any]:
    style_label = features.get("style", {}).get("label", "Balanced")
    strengths = features.get("strengths", [])
    weaknesses = features.get("weaknesses", [])
    coaching_focus = features.get("coaching_focus", [])
    phase = features.get("performance", {}).get("phase", {})
    time_pressure = features.get("time_pressure", {})
    recurring = features.get("recurring_themes", [])

    phase_candidates = [
        (name, stats.get("avg_cp_loss"))
        for name, stats in phase.items()
        if stats.get("avg_cp_loss") is not None
    ]
    phase_text = "Phase-level quality data is still building."
    phase_fact_ids: list[str] = []
    if phase_candidates:
        best = min(phase_candidates, key=lambda item: float(item[1]))
        worst = max(phase_candidates, key=lambda item: float(item[1]))
        phase_text = (
            f"Best phase is {best[0]} and most costly phase is {worst[0]} "
            f"based on average centipawn loss."
        )
        phase_fact_ids = [
            f"{best[0]}_avg_cp_loss",
            f"{worst[0]}_avg_cp_loss",
        ]

    time_text = "Clock data is limited, so time-pressure guidance is preliminary."
    time_fact_ids = list(time_pressure.get("fact_ids") or [])
    if time_pressure.get("score_pct_under_pressure") is not None:
        time_text = (
            f"Score under time pressure is {time_pressure['score_pct_under_pressure']}% "
            f"vs {time_pressure.get('score_pct_overall')}% overall."
        )

    recurring_claims = []
    for item in recurring[:3]:
        recurring_claims.append(
            {
                "text": f"Recurring theme: {str(item.get('theme', '')).replace('_', ' ')}.",
                "fact_ids": ["top_theme"] if item == recurring[0] else [],
            }
        )

    return {
        "player_type": {
            "text": f"You currently profile as {style_label}.",
            "fact_ids": ["style_label"],
        },
        "strengths": strengths[:3],
        "weaknesses": weaknesses[:3],
        "phase_performance": {
            "text": phase_text,
            "fact_ids": phase_fact_ids,
        },
        "time_pressure": {
            "text": time_text,
            "fact_ids": time_fact_ids,
        },
        "recurring_mistakes": recurring_claims,
        "coaching_takeaways": coaching_focus[:3],
        "meta": {
            "source": "deterministic_fallback",
            "version": NARRATIVE_VERSION,
        },
    }


def _extract_json_from_content(content: str) -> dict[str, Any] | None:
    content = content.strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None


def _validate_claim(item: Any, fact_map: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    text = item.get("text")
    fact_ids = item.get("fact_ids")
    if not isinstance(text, str) or not text.strip():
        return False
    if not isinstance(fact_ids, list):
        return False
    for fact_id in fact_ids:
        if not isinstance(fact_id, str):
            return False
        if fact_id and fact_id not in fact_map:
            return False
    return True


def verify_narrative(narrative: dict[str, Any], fact_map: dict[str, Any]) -> bool:
    if not isinstance(narrative, dict):
        return False

    required_keys = [
        "player_type",
        "strengths",
        "weaknesses",
        "phase_performance",
        "time_pressure",
        "recurring_mistakes",
        "coaching_takeaways",
    ]
    for key in required_keys:
        if key not in narrative:
            return False

    if not _validate_claim(narrative.get("player_type"), fact_map):
        return False
    if not _validate_claim(narrative.get("phase_performance"), fact_map):
        return False
    if not _validate_claim(narrative.get("time_pressure"), fact_map):
        return False

    for key in ("strengths", "weaknesses", "recurring_mistakes", "coaching_takeaways"):
        value = narrative.get(key)
        if not isinstance(value, list):
            return False
        for item in value:
            if not _validate_claim(item, fact_map):
                return False
    return True


def _generate_llm_narrative(
    features: dict[str, Any],
    fact_map: dict[str, Any],
) -> dict[str, Any] | None:
    if NARRATIVE_PROVIDER in {"", "none"}:
        return None
    if not NARRATIVE_API_URL or not NARRATIVE_API_KEY or not NARRATIVE_MODEL:
        return None

    prompt = {
        "task": "Generate grounded coaching narrative for chess insights.",
        "rules": [
            "Use only provided facts. Do not invent numbers or events.",
            "Every claim must include one or more fact_ids that exist in the fact map.",
            "Keep wording concise and user-facing.",
            "If evidence is weak, mention uncertainty.",
        ],
        "output_schema": {
            "player_type": {"text": "string", "fact_ids": ["fact_id"]},
            "strengths": [{"text": "string", "fact_ids": ["fact_id"]}],
            "weaknesses": [{"text": "string", "fact_ids": ["fact_id"]}],
            "phase_performance": {"text": "string", "fact_ids": ["fact_id"]},
            "time_pressure": {"text": "string", "fact_ids": ["fact_id"]},
            "recurring_mistakes": [{"text": "string", "fact_ids": ["fact_id"]}],
            "coaching_takeaways": [{"text": "string", "fact_ids": ["fact_id"]}],
        },
        "facts": fact_map,
        "features": features,
    }

    payload = {
        "model": NARRATIVE_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a chess coach assistant that only writes claims backed by explicit facts.",
            },
            {
                "role": "user",
                "content": json.dumps(prompt),
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {NARRATIVE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=NARRATIVE_TIMEOUT_S) as client:
            response = client.post(NARRATIVE_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    content: str | None = None
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
        if content is None and isinstance(data.get("output_text"), str):
            content = data.get("output_text")

    if not content:
        return None
    return _extract_json_from_content(content)


def build_narrative(
    features: dict[str, Any],
    fact_map: dict[str, Any],
) -> dict[str, Any]:
    """Generate deterministic fallback narrative.
    
    LLM narratives are currently disabled. This always returns the fallback.
    """
    return _build_fallback_narrative(features)


async def _save_snapshot(
    username: str,
    site: str,
    status: str,
    coverage: dict[str, Any],
    features: dict[str, Any],
    fact_map: dict[str, Any],
    narrative: dict[str, Any],
    source_job_id: str | None,
) -> None:
    with get_connection() as conn:
        upsert_player_insights(
            conn,
            username=username,
            site=site,
            status=status,
            feature_version=FEATURE_VERSION,
            narrative_version=NARRATIVE_VERSION,
            coverage=coverage,
            features=features,
            fact_map=fact_map,
            narrative=narrative,
            source_job_id=source_job_id,
        )
        conn.commit()


async def run_insights_pipeline(
    job_id: str,
    username: str,
    site: str = "all",
    trigger_quick_scan: bool = False,
) -> None:
    """Run tiered insights processing for a username.
    
    Insights are shared per chess username - not owned by individual users.
    """
    started_at = utc_now_iso()
    async with _INSIGHTS_SEMAPHORE:
        with get_connection() as conn:
            update_insight_job(
                conn,
                job_id,
                status="running",
                stage="light",
                error="",
                started_at=started_at,
            )
            conn.commit()

        try:
            with get_connection() as conn:
                games = get_games_for_insights(
                    conn,
                    username=username,
                    site=site,
                    limit=MAX_GAMES_WINDOW,
                )
                already_featured = get_featured_game_ids(
                    conn,
                    username=username,
                    site=site,
                    feature_version=FEATURE_VERSION,
                )

            if not games:
                empty_features = {
                    "version": FEATURE_VERSION,
                    "computed_at": utc_now_iso(),
                    "style": {"label": "Insufficient Data", "scores": {}},
                    "performance": {"overall": {"games": 0, "score_pct": 0.0}},
                    "time_pressure": {},
                    "recurring_themes": [],
                    "strengths": [],
                    "weaknesses": [],
                    "coaching_focus": [],
                    "confidence": {"value": 0.0},
                }
                coverage = {
                    "games_total": 0,
                    "games_light": 0,
                    "games_scanned": 0,
                    "scan_coverage": 0.0,
                    "games_with_clock": 0,
                    "clock_coverage": 0.0,
                    "has_enough_games": False,
                }
                fact_map: dict[str, Any] = {}
                narrative = _build_fallback_narrative(empty_features)
                await _save_snapshot(
                    username=username,
                    site=site,
                    status="not_enough_data",
                    coverage=coverage,
                    features=empty_features,
                    fact_map=fact_map,
                    narrative=narrative,
                    source_job_id=job_id,
                )
                with get_connection() as conn:
                    update_insight_job(
                        conn,
                        job_id,
                        status="completed",
                        stage="complete",
                        finished_at=utc_now_iso(),
                    )
                    conn.commit()
                return

            new_games = [
                g for g in games
                if (g["site"], g["site_game_id"]) not in already_featured
            ]

            for game in new_games:
                light_feature = await asyncio.to_thread(extract_light_game_features, game)
                with get_connection() as conn:
                    upsert_insight_game_feature(
                        conn,
                        username=username,
                        site=game["site"],
                        site_game_id=game["site_game_id"],
                        feature_version=FEATURE_VERSION,
                        light=light_feature,
                        deep=None,
                    )
                    conn.commit()

            with get_connection() as conn:
                stored_features = get_insight_game_features(
                    conn,
                    username=username,
                    site=site,
                    feature_version=FEATURE_VERSION,
                )
                scan_rows = get_quick_scan_results(conn, username, site)

            features, coverage, fact_map = _build_aggregate_features(stored_features, scan_rows)
            narrative = build_narrative(features, fact_map)

            initial_status = "baseline_ready" if coverage.get("has_enough_games") else "not_enough_data"
            await _save_snapshot(
                username=username,
                site=site,
                status=initial_status,
                coverage=coverage,
                features=features,
                fact_map=fact_map,
                narrative=narrative,
                source_job_id=job_id,
            )

            if not coverage.get("has_enough_games"):
                with get_connection() as conn:
                    update_insight_job(
                        conn,
                        job_id,
                        status="completed",
                        stage="complete",
                        finished_at=utc_now_iso(),
                    )
                    conn.commit()
                return

            await _save_snapshot(
                username=username,
                site=site,
                status="complete",
                coverage=coverage,
                features=features,
                fact_map=fact_map,
                narrative=narrative,
                source_job_id=job_id,
            )

            with get_connection() as conn:
                update_insight_job(
                    conn,
                    job_id,
                    status="completed",
                    stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()

            if trigger_quick_scan:
                from quick_scan import schedule_quick_scan
                try:
                    schedule_quick_scan(username, site=site)
                except Exception:
                    pass

        except Exception as exc:  # pragma: no cover - last resort error path
            with get_connection() as conn:
                update_insight_job(
                    conn,
                    job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc),
                    finished_at=utc_now_iso(),
                )
                conn.commit()


def schedule_insights_refresh(
    username: str,
    site: str = "all",
    reason: str = "manual_refresh",
    force: bool = False,
) -> dict[str, Any]:
    """Create an insights job if none is currently active, then schedule it.

    Insights are shared per chess username - not owned by individual users.
    When force=True, clears all existing insights, quick scan data, and stale
    jobs so that everything is rebuilt from scratch.
    """
    canonical_username = username.strip().lower()
    with get_connection() as conn:
        if force:
            clear_insights_data(conn, canonical_username, site)
            clear_quick_scan_data(conn, canonical_username, site)
            conn.commit()
        else:
            active = get_active_insight_job(conn, canonical_username, site)
            if active:
                return {
                    "scheduled": False,
                    "force_requested": force,
                    "job": active,
                }

        job_id = str(uuid.uuid4())
        create_insight_job(
            conn,
            job_id=job_id,
            username=canonical_username,
            site=site,
            status="queued",
            stage="queued",
            reason=reason,
            feature_version=FEATURE_VERSION,
            meta={
                "window_size": MAX_GAMES_WINDOW,
            },
        )
        conn.commit()

    from tasks import run_insights
    run_insights.delay(job_id, canonical_username, site, trigger_quick_scan=force)

    return {
        "scheduled": True,
        "job": {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "feature_version": FEATURE_VERSION,
        },
    }


def get_insights_state(
    username: str,
    site: str = "all",
) -> dict[str, Any]:
    """Fetch current insights snapshot + job status for API responses."""
    canonical_username = username.strip().lower()
    with get_connection() as conn:
        snapshot = get_player_insights(conn, canonical_username, site)
        active_job = get_active_insight_job(conn, canonical_username, site)

    lifecycle_status = "missing"
    if snapshot:
        lifecycle_status = snapshot.get("status") or "complete"
        if snapshot.get("feature_version") != FEATURE_VERSION:
            lifecycle_status = "stale"
    elif active_job:
        lifecycle_status = "queued"

    return {
        "username": canonical_username,
        "site": site,
        "lifecycle_status": lifecycle_status,
        "feature_version": FEATURE_VERSION,
        "narrative_version": NARRATIVE_VERSION,
        "snapshot": snapshot,
        "active_job": active_job,
    }
