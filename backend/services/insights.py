"""Tiered AI insights pipeline for chess game histories."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import chess
import chess.pgn
import httpx

from repository.db import (
    clear_insights_data,
    clear_quick_scan_data,
    create_insight_job,
    get_active_insight_job,
    get_connection,
    get_featured_game_ids,
    get_full_analysis,
    get_games_for_insights,
    get_insight_game_features,
    get_player_insights,
    save_full_analysis,
    update_insight_job,
    upsert_insight_game_feature,
    upsert_player_insights,
)
from services.full_analysis import run_full_analysis
from utils.insights_constants import (
    DEEP_ANALYSIS_DEPTH,
    DEEP_ANALYSIS_MULTIPV,
    DEEP_ANALYSIS_TIME_MS,
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
    safe_parse_datetime,
    utc_now_iso,
)
from services.insights_aggregate import (
    aggregate_light_features,
    aggregate_deep_features,
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


def _select_deep_candidates(
    games: list[dict[str, Any]],
    features: list[dict[str, Any]],
    budget: int,
) -> list[dict[str, Any]]:
    if budget <= 0:
        return []

    feature_by_key = {
        (row["site"], row["site_game_id"]): row for row in features
    }
    unresolved = []
    for game in games:
        key = (game.get("site"), game.get("site_game_id"))
        row = feature_by_key.get(key)
        if row and row.get("deep"):
            continue
        if not (game.get("pgn") or "").strip():
            continue
        unresolved.append(game)

    if not unresolved:
        return []

    unresolved.sort(
        key=lambda item: safe_parse_datetime(item.get("played_at") or "") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    seen_time_class: set[str] = set()
    seen_color: set[str] = set()

    for game in unresolved:
        if len(selected) >= budget:
            break
        time_class = (game.get("time_class") or "unknown").lower()
        color = (game.get("color") or "unknown").lower()
        novelty = (time_class not in seen_time_class) or (color not in seen_color)
        if novelty:
            selected.append(game)
            seen_time_class.add(time_class)
            seen_color.add(color)

    if len(selected) < budget:
        selected_keys = {(item["site"], item["site_game_id"]) for item in selected}
        for game in unresolved:
            if len(selected) >= budget:
                break
            key = (game["site"], game["site_game_id"])
            if key in selected_keys:
                continue
            selected.append(game)
            selected_keys.add(key)

    return selected


def _extract_deep_game_features(
    game_row: dict[str, Any],
    light_feature: dict[str, Any],
    deep_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Build deep per-game artifacts from full engine analysis."""
    moves = deep_analysis.get("moves") or []
    color = (game_row.get("color") or "white").lower()
    user_is_white = color == "white"
    sign = 1 if user_is_white else -1

    phase_profile = light_feature.get("phase_profile", {})
    opening_end_ply = int(phase_profile.get("opening_end_ply") or 20)
    endgame_start_ply = phase_profile.get("endgame_start_ply")
    if isinstance(endgame_start_ply, str):
        try:
            endgame_start_ply = int(endgame_start_ply)
        except ValueError:
            endgame_start_ply = None

    phase_stats = {
        "opening": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "middlegame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "endgame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
    }
    theme_counts: dict[str, int] = {}
    cp_losses: list[float] = []
    blunders_total = 0
    blunders_low_time = 0
    user_moves_with_clock = 0
    user_moves_low_time = 0

    clock_lookup = light_feature.get("clock_by_ply", {})
    low_time_threshold = light_feature.get("time_pressure", {}).get("low_time_threshold_s")
    low_time_cp_losses: list[float] = []

    for move in moves:
        raw_ply = int(move.get("ply", 0))
        ply = raw_ply + 1
        is_white_move = raw_ply % 2 == 0
        is_user_move = (is_white_move and user_is_white) or ((not is_white_move) and (not user_is_white))
        if not is_user_move:
            continue

        phase = phase_for_ply(ply, opening_end_ply, endgame_start_ply)
        classification = move.get("classification") or "unknown"
        cp_loss = move.get("cp_loss")

        eval_before = (move.get("eval_before") or {}).get("cp")
        user_eval_before: float | None = None
        if isinstance(eval_before, (int, float)):
            user_eval_before = float(eval_before) * sign

        clock_seconds = clock_lookup.get(ply)
        is_low_time_move = (
            low_time_threshold is not None
            and clock_seconds is not None
            and clock_seconds <= int(low_time_threshold)
        )
        if clock_seconds is not None:
            user_moves_with_clock += 1
        if is_low_time_move:
            user_moves_low_time += 1
        if classification == "blunder":
            blunders_total += 1
            if is_low_time_move:
                blunders_low_time += 1

        if cp_loss is None:
            continue

        cp_loss_f = float(cp_loss)
        cp_losses.append(cp_loss_f)
        phase_stats[phase]["moves"] += 1
        phase_stats[phase]["cp_loss_sum"] += cp_loss_f

        if classification in {"mistake", "blunder"}:
            phase_stats[phase]["mistakes"] += 1
        if classification == "blunder":
            phase_stats[phase]["blunders"] += 1

        themes: list[str] = []
        if cp_loss_f >= 300:
            if phase == "opening":
                themes.append("opening_blunder")
            elif phase == "middlegame":
                themes.append("tactical_oversight")
            else:
                themes.append("endgame_blunder")
        elif cp_loss_f >= 120:
            themes.append("critical_inaccuracy")

        if user_eval_before is not None and cp_loss_f >= 120:
            if user_eval_before > 120:
                themes.append("conversion_miss")
            elif user_eval_before < -120:
                themes.append("defensive_slip")

        if not themes and cp_loss_f >= 80:
            themes.append("small_technique_error")

        for theme in themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

        if is_low_time_move:
            low_time_cp_losses.append(cp_loss_f)

    for phase_name, stats in phase_stats.items():
        moves_count = stats["moves"]
        stats["avg_cp_loss"] = round((stats["cp_loss_sum"] / moves_count), 2) if moves_count > 0 else None
        del stats["cp_loss_sum"]
        stats["mistake_rate"] = round((stats["mistakes"] / moves_count), 4) if moves_count > 0 else None

    return {
        "version": FEATURE_VERSION,
        "analysis_tier": "deep",
        "computed_at": utc_now_iso(),
        "engine_meta": deep_analysis.get("meta") or {},
        "quality": {
            "user_moves_analyzed": len(cp_losses),
            "avg_cp_loss": round(mean(cp_losses), 2) if cp_losses else None,
            "blunder_rate": round((blunders_total / len(cp_losses)), 4) if cp_losses else None,
            "avg_cp_loss_low_time": round(mean(low_time_cp_losses), 2) if low_time_cp_losses else None,
            "time_pressure": {
                "user_moves_with_clock": user_moves_with_clock,
                "user_moves_low_time": user_moves_low_time,
                "blunders_total": blunders_total,
                "blunders_low_time": blunders_low_time,
                "blunder_share_low_time": round((blunders_low_time / blunders_total), 4)
                if blunders_total > 0
                else None,
                "blunder_rate_low_time": round((blunders_low_time / user_moves_low_time), 4)
                if user_moves_low_time > 0
                else None,
            },
        },
        "phase_stats": phase_stats,
        "theme_counts": theme_counts,
    }


def _load_cached_full_analysis_payload(
    owner_user_id: str,
    username: str,
    site: str,
    site_game_id: str,
) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        cached = get_full_analysis(
            conn,
            owner_user_id,
            username,
            site_game_id,
            DEEP_ANALYSIS_DEPTH,
            DEEP_ANALYSIS_MULTIPV,
            site,
        )
    finally:
        conn.close()

    if not cached:
        return None

    try:
        return {
            "moves": json.loads(cached.get("moves_json") or "[]"),
            "summary": json.loads(cached.get("summary_json") or "{}"),
            "meta": json.loads(cached.get("meta_json") or "{}"),
        }
    except (TypeError, json.JSONDecodeError):
        return None


def _save_full_analysis_cache_payload(
    owner_user_id: str,
    username: str,
    site: str,
    site_game_id: str,
    full_analysis: dict[str, Any],
) -> None:
    conn = get_connection()
    try:
        save_full_analysis(
            conn,
            owner_user_id,
            username,
            site_game_id,
            depth=DEEP_ANALYSIS_DEPTH,
            multipv=DEEP_ANALYSIS_MULTIPV,
            moves_json=json.dumps(full_analysis.get("moves") or []),
            summary_json=json.dumps(full_analysis.get("summary") or {}),
            meta_json=json.dumps(full_analysis.get("meta") or {}),
            insights_json=None,
            site=site,
        )
        conn.commit()
    finally:
        conn.close()


def _build_deep_feature_with_cache(
    game: dict[str, Any],
    light_feature: dict[str, Any],
    *,
    source_owner_id: str,
    username: str,
) -> dict[str, Any]:
    site = str(game.get("site") or "lichess")
    site_game_id = str(game.get("site_game_id") or "")
    if not site_game_id:
        raise ValueError("Missing site_game_id for deep feature build.")

    full_analysis = _load_cached_full_analysis_payload(
        source_owner_id,
        username,
        site,
        site_game_id,
    )
    if full_analysis is None:
        full_analysis = run_full_analysis(
            game.get("pgn") or "",
            DEEP_ANALYSIS_DEPTH,
            DEEP_ANALYSIS_MULTIPV,
            DEEP_ANALYSIS_TIME_MS,
            opening_ply_count=game.get("opening_ply_count"),
        )
        _save_full_analysis_cache_payload(
            source_owner_id,
            username,
            site,
            site_game_id,
            full_analysis,
        )

    return _extract_deep_game_features(game, light_feature, full_analysis)


def _build_aggregate_features(
    feature_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Aggregate per-game features into user-level insights artifacts.
    
    Returns:
        (features, coverage, fact_map) tuple for player insights.
    """
    light_features = [row["light"] for row in feature_rows if row.get("light")]
    deep_features = [row["deep"] for row in feature_rows if row.get("deep")]

    # Aggregate light feature metrics
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

    # Aggregate deep feature metrics
    deep_agg = aggregate_deep_features(deep_features)
    phase_performance = deep_agg["phase_performance"]
    theme_counts = deep_agg["theme_counts"]
    total_user_moves_deep = deep_agg["total_user_moves_deep"]
    total_blunders_deep = deep_agg["total_blunders_deep"]
    total_low_time_blunders_deep = deep_agg["total_low_time_blunders_deep"]
    total_blunders_with_clock_deep = deep_agg["total_blunders_with_clock_deep"]
    total_low_time_moves_deep = deep_agg["total_low_time_moves_deep"]
    total_moves_with_clock_deep = deep_agg["total_moves_with_clock_deep"]

    # Compute opening rankings
    best_openings, worst_openings = compute_opening_rankings(openings)

    # Compute style classification
    draw_rate = (draws / total_games) if total_games > 0 else 0.0
    avg_early_capture = mean(light_agg["early_capture_rates"])
    avg_early_check = mean(light_agg["early_check_rates"])
    avg_game_len = mean(light_agg["game_lengths"])
    blunder_rate = (total_blunders_deep / total_user_moves_deep) if total_user_moves_deep > 0 else 0.0

    style = compute_style_scores(
        draw_rate=draw_rate,
        avg_early_capture=avg_early_capture,
        avg_early_check=avg_early_check,
        avg_game_len=avg_game_len,
        blunder_rate=blunder_rate,
        theme_counts=theme_counts,
        deep_game_count=len(deep_features),
    )

    # Sort themes by count
    theme_items = sorted(
        [{"theme": theme, "count": count} for theme, count in theme_counts.items()],
        key=lambda item: item["count"],
        reverse=True,
    )

    # Build coverage metrics
    coverage = build_coverage_metrics(
        total_games=total_games,
        light_count=len(light_features),
        deep_count=len(deep_features),
        clock_games=clock_games,
        low_time_games=low_time_games,
    )
    confidence = coverage["confidence"]

    # Build fact map for grounded narrative
    fact_map: dict[str, dict[str, Any]] = {}
    overall_games_fact = add_fact(fact_map, "overall_games", "Games analyzed", total_games, "games")
    overall_score_fact = add_fact(fact_map, "overall_score_pct", "Overall score", overall_score_pct, "pct")
    style_fact = add_fact(fact_map, "style_label", "Player style", style["label"])
    deep_cov_fact = add_fact(fact_map, "deep_coverage", "Deep analysis coverage", coverage["deep_coverage"], "ratio")
    clock_cov_fact = add_fact(fact_map, "clock_coverage", "Clock data coverage", coverage["clock_coverage"], "ratio")
    confidence_fact = add_fact(fact_map, "confidence", "Insights confidence", round(confidence, 3), "ratio")

    # Phase facts
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

    # Opening facts
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

    # Time pressure facts
    time_pressure_fact_ids: list[str] = []
    if low_time_score_pct is not None:
        time_pressure_fact_ids.append(
            add_fact(fact_map, "low_time_score_pct", "Score in low-time games", low_time_score_pct, "pct")
        )
    blunders_under_pressure_pct: float | None = None
    if total_blunders_with_clock_deep > 0:
        blunders_under_pressure_pct = round(
            (total_low_time_blunders_deep / total_blunders_with_clock_deep) * 100, 1
        )
        time_pressure_fact_ids.append(
            add_fact(fact_map, "blunders_under_time_pressure_pct",
                     "Share of blunders under time pressure", blunders_under_pressure_pct, "pct")
        )

    # Build strengths, weaknesses, coaching focus
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

    # Assemble final features payload
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
            "blunders_under_pressure": total_low_time_blunders_deep,
            "blunders_total_with_clock": total_blunders_with_clock_deep,
            "blunders_under_pressure_pct": blunders_under_pressure_pct,
            "low_time_moves_deep": total_low_time_moves_deep,
            "moves_with_clock_deep": total_moves_with_clock_deep,
            "fact_ids": time_pressure_fact_ids + [clock_cov_fact],
        },
        "recurring_themes": theme_items[:5],
        "strengths": strengths,
        "weaknesses": weaknesses,
        "coaching_focus": coaching_focus,
        "confidence": {
            "value": round(confidence, 3),
            "fact_ids": [confidence_fact, deep_cov_fact, clock_cov_fact],
        },
    }

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
    conn = get_connection()
    try:
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
    finally:
        conn.close()


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
        conn = get_connection()
        try:
            update_insight_job(
                conn,
                job_id,
                status="running",
                stage="light",
                error="",
                started_at=started_at,
            )
            conn.commit()
        finally:
            conn.close()

        try:
            conn = get_connection()
            try:
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
            finally:
                conn.close()

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
                    "games_deep": 0,
                    "deep_coverage": 0.0,
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
                conn = get_connection()
                try:
                    update_insight_job(
                        conn,
                        job_id,
                        status="completed",
                        stage="complete",
                        finished_at=utc_now_iso(),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return

            new_games = [
                g for g in games
                if (g["site"], g["site_game_id"]) not in already_featured
            ]

            for game in new_games:
                light_feature = await asyncio.to_thread(extract_light_game_features, game)
                conn = get_connection()
                try:
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
                finally:
                    conn.close()

            conn = get_connection()
            try:
                stored_features = get_insight_game_features(
                    conn,
                    username=username,
                    site=site,
                    feature_version=FEATURE_VERSION,
                )
            finally:
                conn.close()

            features, coverage, fact_map = _build_aggregate_features(stored_features)
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
                conn = get_connection()
                try:
                    update_insight_job(
                        conn,
                        job_id,
                        status="completed",
                        stage="complete",
                        finished_at=utc_now_iso(),
                    )
                    conn.commit()
                finally:
                    conn.close()
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

            conn = get_connection()
            try:
                update_insight_job(
                    conn,
                    job_id,
                    status="completed",
                    stage="complete",
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()

            if trigger_quick_scan:
                from quick_scan import schedule_quick_scan
                try:
                    schedule_quick_scan(username, site=site)
                except Exception:
                    pass

        except Exception as exc:  # pragma: no cover - last resort error path
            conn = get_connection()
            try:
                update_insight_job(
                    conn,
                    job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc),
                    finished_at=utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()


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
    conn = get_connection()
    try:
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
    finally:
        conn.close()

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
    conn = get_connection()
    try:
        snapshot = get_player_insights(conn, canonical_username, site)
        active_job = get_active_insight_job(conn, canonical_username, site)
    finally:
        conn.close()

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
