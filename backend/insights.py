"""Tiered AI insights pipeline for chess game histories."""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import chess
import chess.pgn
import httpx

from db import (
    create_insight_job,
    get_active_insight_job,
    get_connection,
    get_games_for_insights,
    get_insight_game_features,
    get_player_insights,
    update_insight_job,
    upsert_insight_game_feature,
    upsert_player_insights,
)
from full_analysis import run_full_analysis


FEATURE_VERSION = os.environ.get("INSIGHTS_FEATURE_VERSION", "1")
NARRATIVE_VERSION = os.environ.get("INSIGHTS_NARRATIVE_VERSION", "1")
MAX_GAMES_WINDOW = max(50, int(os.environ.get("INSIGHTS_MAX_GAMES", "500")))
DEEP_ANALYSIS_BUDGET = max(0, int(os.environ.get("INSIGHTS_DEEP_BUDGET", "8")))
DEEP_ANALYSIS_DEPTH = max(6, int(os.environ.get("INSIGHTS_DEEP_DEPTH", "14")))
DEEP_ANALYSIS_MULTIPV = 1
DEEP_ANALYSIS_TIME_MS = max(250, int(os.environ.get("INSIGHTS_DEEP_TIME_MS", "600")))
MAX_CONCURRENT_INSIGHTS = max(1, int(os.environ.get("MAX_CONCURRENT_INSIGHTS", "1")))
LOW_TIME_RATIO = float(os.environ.get("INSIGHTS_LOW_TIME_RATIO", "0.1"))
LOW_TIME_FLOOR_SECONDS = max(10, int(os.environ.get("INSIGHTS_LOW_TIME_FLOOR_SECONDS", "30")))
MIN_BASELINE_GAMES = max(5, int(os.environ.get("INSIGHTS_MIN_GAMES", "12")))

NARRATIVE_PROVIDER = os.environ.get("INSIGHTS_NARRATIVE_PROVIDER", "none").lower()
NARRATIVE_API_URL = os.environ.get("INSIGHTS_NARRATIVE_API_URL", "").strip()
NARRATIVE_API_KEY = os.environ.get("INSIGHTS_NARRATIVE_API_KEY", "").strip()
NARRATIVE_MODEL = os.environ.get("INSIGHTS_NARRATIVE_MODEL", "").strip()
NARRATIVE_TIMEOUT_S = max(5, int(os.environ.get("INSIGHTS_NARRATIVE_TIMEOUT_S", "30")))

_CLOCK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")
_INSIGHTS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_INSIGHTS)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _result_to_score(result: str | None) -> float:
    if result == "win":
        return 1.0
    if result == "draw":
        return 0.5
    return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _clock_to_seconds(raw_value: str | None) -> int | None:
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return int(hours * 3600 + minutes * 60 + seconds)
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return int(minutes * 60 + seconds)
    except ValueError:
        return None
    return None


def _extract_clock_seconds(comment: str | None) -> int | None:
    if not comment:
        return None
    match = _CLOCK_RE.search(comment)
    if not match:
        return None
    return _clock_to_seconds(match.group(1))


def _phase_for_ply(ply: int, opening_end_ply: int, endgame_start_ply: int | None) -> str:
    if ply <= opening_end_ply:
        return "opening"
    if endgame_start_ply is not None and ply >= endgame_start_ply:
        return "endgame"
    return "middlegame"


def extract_light_game_features(game_row: dict[str, Any]) -> dict[str, Any]:
    """Compute fast, deterministic per-game features from PGN + metadata."""
    pgn = game_row.get("pgn") or ""
    result = game_row.get("result") or "loss"
    color = (game_row.get("color") or "white").lower()
    user_is_white = color == "white"
    computed_at = _utc_now_iso()

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
        base["move_artifacts"] = []
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
        base["move_artifacts"] = []
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
        clock_seconds = _extract_clock_seconds(node.comment)
        if is_user_move:
            user_move_count += 1
            if clock_seconds is not None:
                clock_samples.append(clock_seconds)

        non_king_pieces = sum(
            1 for piece in board.piece_map().values() if piece.piece_type != chess.KING
        )
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
        event["phase"] = _phase_for_ply(event["ply"], opening_end_ply, endgame_start_ply)

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
    base["move_artifacts"] = [event for event in move_events if event["is_user_move"]]

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
        key=lambda item: _safe_parse_datetime(item.get("played_at") or "") or datetime.min.replace(tzinfo=timezone.utc),
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
    move_artifacts: list[dict[str, Any]] = []
    cp_losses: list[float] = []

    clock_lookup = {}
    for item in light_feature.get("move_artifacts", []):
        if item.get("clock_seconds") is not None:
            clock_lookup[int(item.get("ply", 0))] = int(item["clock_seconds"])
    low_time_threshold = light_feature.get("time_pressure", {}).get("low_time_threshold_s")
    low_time_cp_losses: list[float] = []

    for move in moves:
        raw_ply = int(move.get("ply", 0))
        ply = raw_ply + 1
        is_white_move = raw_ply % 2 == 0
        is_user_move = (is_white_move and user_is_white) or ((not is_white_move) and (not user_is_white))
        if not is_user_move:
            continue

        phase = _phase_for_ply(ply, opening_end_ply, endgame_start_ply)
        cp_loss = move.get("cp_loss")
        if cp_loss is None:
            continue

        cp_loss_f = float(cp_loss)
        cp_losses.append(cp_loss_f)
        phase_stats[phase]["moves"] += 1
        phase_stats[phase]["cp_loss_sum"] += cp_loss_f

        classification = move.get("classification") or "unknown"
        if classification in {"mistake", "blunder"}:
            phase_stats[phase]["mistakes"] += 1
        if classification == "blunder":
            phase_stats[phase]["blunders"] += 1

        eval_before = (move.get("eval_before") or {}).get("cp")
        user_eval_before: float | None = None
        if isinstance(eval_before, (int, float)):
            user_eval_before = float(eval_before) * sign

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

        clock_seconds = clock_lookup.get(ply)
        if (
            low_time_threshold is not None
            and clock_seconds is not None
            and clock_seconds <= int(low_time_threshold)
        ):
            low_time_cp_losses.append(cp_loss_f)

        if cp_loss_f >= 80:
            move_artifacts.append(
                {
                    "ply": ply,
                    "phase": phase,
                    "classification": classification,
                    "cp_loss": round(cp_loss_f, 2),
                    "themes": themes,
                }
            )

    for phase_name, stats in phase_stats.items():
        moves_count = stats["moves"]
        stats["avg_cp_loss"] = round((stats["cp_loss_sum"] / moves_count), 2) if moves_count > 0 else None
        del stats["cp_loss_sum"]
        stats["mistake_rate"] = round((stats["mistakes"] / moves_count), 4) if moves_count > 0 else None

    return {
        "version": FEATURE_VERSION,
        "analysis_tier": "deep",
        "computed_at": _utc_now_iso(),
        "engine_meta": deep_analysis.get("meta") or {},
        "quality": {
            "user_moves_analyzed": len(cp_losses),
            "avg_cp_loss": round(_mean(cp_losses), 2) if cp_losses else None,
            "blunder_rate": round(
                sum(1 for artifact in move_artifacts if artifact.get("classification") == "blunder")
                / len(cp_losses),
                4,
            )
            if cp_losses
            else None,
            "avg_cp_loss_low_time": round(_mean(low_time_cp_losses), 2) if low_time_cp_losses else None,
        },
        "phase_stats": phase_stats,
        "theme_counts": theme_counts,
        "move_artifacts": move_artifacts[:80],
    }


def _add_fact(
    fact_map: dict[str, dict[str, Any]],
    fact_id: str,
    label: str,
    value: Any,
    unit: str | None = None,
) -> str:
    idx = 1
    candidate = fact_id
    while candidate in fact_map:
        idx += 1
        candidate = f"{fact_id}_{idx}"
    fact_map[candidate] = {"label": label, "value": value, "unit": unit}
    return candidate


def _build_aggregate_features(
    games: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Aggregate per-game features into user-level insights artifacts."""
    light_features = [row["light"] for row in feature_rows if row.get("light")]
    deep_features = [row["deep"] for row in feature_rows if row.get("deep")]

    total_games = len(light_features)
    wins = 0
    draws = 0
    losses = 0

    by_time_class: dict[str, dict[str, float]] = {}
    by_color: dict[str, dict[str, float]] = {}
    openings: dict[str, dict[str, float]] = {}
    early_capture_rates: list[float] = []
    early_check_rates: list[float] = []
    game_lengths: list[float] = []
    clock_games = 0
    low_time_games = 0
    low_time_scores: list[float] = []
    overall_scores: list[float] = []

    for feature in light_features:
        meta = feature.get("metadata", {})
        result = meta.get("result") or "loss"
        score = _result_to_score(result)
        overall_scores.append(score)
        if result == "win":
            wins += 1
        elif result == "draw":
            draws += 1
        else:
            losses += 1

        tc = (meta.get("time_class") or "unknown").lower()
        tc_item = by_time_class.setdefault(tc, {"games": 0, "score_sum": 0.0})
        tc_item["games"] += 1
        tc_item["score_sum"] += score

        color = (meta.get("color") or "unknown").lower()
        color_item = by_color.setdefault(color, {"games": 0, "score_sum": 0.0})
        color_item["games"] += 1
        color_item["score_sum"] += score

        opening = (meta.get("opening_name") or "Unknown").strip() or "Unknown"
        opening_item = openings.setdefault(opening, {"games": 0, "score_sum": 0.0})
        opening_item["games"] += 1
        opening_item["score_sum"] += score

        style_signals = feature.get("style_signals", {})
        early_capture_rates.append(float(style_signals.get("early_capture_rate") or 0.0))
        early_check_rates.append(float(style_signals.get("early_check_rate") or 0.0))
        game_lengths.append(float(style_signals.get("avg_game_length") or 0.0))

        time_pressure = feature.get("time_pressure", {})
        if time_pressure.get("has_clock_data"):
            clock_games += 1
            low_time_rate = float(time_pressure.get("low_time_rate") or 0.0)
            if low_time_rate > 0:
                low_time_games += 1
                low_time_scores.append(score)

    overall_score_pct = round((_mean(overall_scores) * 100), 1) if overall_scores else 0.0
    low_time_score_pct = round((_mean(low_time_scores) * 100), 1) if low_time_scores else None

    phase_accum = {
        "opening": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "middlegame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "endgame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
    }
    theme_counts: dict[str, int] = {}
    total_user_moves_deep = 0
    total_blunders_deep = 0

    for deep in deep_features:
        quality = deep.get("quality", {})
        total_user_moves_deep += int(quality.get("user_moves_analyzed") or 0)

        for phase_name, stats in (deep.get("phase_stats") or {}).items():
            if phase_name not in phase_accum:
                continue
            moves_count = int(stats.get("moves") or 0)
            avg_cp_loss = float(stats.get("avg_cp_loss") or 0.0)
            phase_accum[phase_name]["moves"] += moves_count
            phase_accum[phase_name]["cp_loss_sum"] += avg_cp_loss * moves_count
            phase_accum[phase_name]["mistakes"] += int(stats.get("mistakes") or 0)
            phase_accum[phase_name]["blunders"] += int(stats.get("blunders") or 0)
            total_blunders_deep += int(stats.get("blunders") or 0)

        for theme, count in (deep.get("theme_counts") or {}).items():
            theme_counts[theme] = theme_counts.get(theme, 0) + int(count)

    phase_performance: dict[str, dict[str, Any]] = {}
    for phase_name, stats in phase_accum.items():
        moves_count = stats["moves"]
        avg_cp_loss = (stats["cp_loss_sum"] / moves_count) if moves_count > 0 else None
        phase_performance[phase_name] = {
            "moves": moves_count,
            "avg_cp_loss": round(avg_cp_loss, 2) if avg_cp_loss is not None else None,
            "mistakes": stats["mistakes"],
            "blunders": stats["blunders"],
            "mistake_rate": round((stats["mistakes"] / moves_count), 4) if moves_count > 0 else None,
        }

    opening_items = []
    for name, stats in openings.items():
        games_count = int(stats["games"])
        score_pct = round((stats["score_sum"] / games_count) * 100, 1) if games_count > 0 else 0.0
        opening_items.append({"opening": name, "games": games_count, "score_pct": score_pct})
    opening_items.sort(key=lambda item: (item["score_pct"], item["games"]), reverse=True)
    best_openings = opening_items[:3]
    worst_openings = sorted(opening_items, key=lambda item: (item["score_pct"], -item["games"]))[:3]

    draw_rate = (draws / total_games) if total_games > 0 else 0.0
    avg_early_capture = _mean(early_capture_rates)
    avg_early_check = _mean(early_check_rates)
    avg_game_len = _mean(game_lengths)
    blunder_rate = (total_blunders_deep / total_user_moves_deep) if total_user_moves_deep > 0 else 0.0

    tactical_score = _clamp01(avg_early_check * 1.8 + (theme_counts.get("tactical_oversight", 0) / max(1, len(deep_features))) * 0.2)
    positional_score = _clamp01((avg_game_len / 110.0) + draw_rate * 0.5 - avg_early_capture * 0.3)
    aggressive_score = _clamp01(avg_early_capture * 1.5 + avg_early_check * 1.2)
    solid_score = _clamp01((1.0 - blunder_rate) * 0.7 + draw_rate * 0.3)

    primary = "tactical" if tactical_score >= positional_score else "positional"
    secondary = "aggressive" if aggressive_score >= solid_score else "solid"
    style_label = f"{secondary.capitalize()} {primary.capitalize()}"

    theme_items = sorted(
        [{"theme": theme, "count": count} for theme, count in theme_counts.items()],
        key=lambda item: item["count"],
        reverse=True,
    )

    coverage = {
        "games_total": total_games,
        "games_light": len(light_features),
        "games_deep": len(deep_features),
        "deep_coverage": round((len(deep_features) / total_games), 4) if total_games > 0 else 0.0,
        "games_with_clock": clock_games,
        "clock_coverage": round((clock_games / total_games), 4) if total_games > 0 else 0.0,
        "games_with_time_pressure": low_time_games,
        "has_enough_games": total_games >= MIN_BASELINE_GAMES,
    }

    confidence = _clamp01(
        coverage["deep_coverage"] * 0.45 + coverage["clock_coverage"] * 0.2 + min(total_games / 100.0, 1.0) * 0.35
    )

    fact_map: dict[str, dict[str, Any]] = {}
    overall_games_fact = _add_fact(fact_map, "overall_games", "Games analyzed", total_games, "games")
    overall_score_fact = _add_fact(fact_map, "overall_score_pct", "Overall score", overall_score_pct, "pct")
    style_fact = _add_fact(fact_map, "style_label", "Player style", style_label)
    deep_cov_fact = _add_fact(fact_map, "deep_coverage", "Deep analysis coverage", coverage["deep_coverage"], "ratio")
    clock_cov_fact = _add_fact(fact_map, "clock_coverage", "Clock data coverage", coverage["clock_coverage"], "ratio")
    confidence_fact = _add_fact(fact_map, "confidence", "Insights confidence", round(confidence, 3), "ratio")

    phase_fact_ids: dict[str, str] = {}
    for phase_name, stats in phase_performance.items():
        if stats["avg_cp_loss"] is None:
            continue
        phase_fact_ids[phase_name] = _add_fact(
            fact_map,
            f"{phase_name}_avg_cp_loss",
            f"{phase_name.capitalize()} avg cp loss",
            stats["avg_cp_loss"],
            "cp",
        )

    best_opening_fact_ids = []
    for idx, item in enumerate(best_openings, start=1):
        best_opening_fact_ids.append(
            _add_fact(
                fact_map,
                f"best_opening_{idx}",
                f"Best opening #{idx}",
                f"{item['opening']} ({item['score_pct']}% over {item['games']} games)",
            )
        )

    weak_opening_fact_ids = []
    for idx, item in enumerate(worst_openings, start=1):
        weak_opening_fact_ids.append(
            _add_fact(
                fact_map,
                f"worst_opening_{idx}",
                f"Weak opening #{idx}",
                f"{item['opening']} ({item['score_pct']}% over {item['games']} games)",
            )
        )

    time_pressure_fact_ids: list[str] = []
    if low_time_score_pct is not None:
        time_pressure_fact_ids.append(
            _add_fact(
                fact_map,
                "low_time_score_pct",
                "Score in low-time games",
                low_time_score_pct,
                "pct",
            )
        )

    top_phase = None
    weak_phase = None
    phase_with_data = [item for item in phase_performance.items() if item[1]["avg_cp_loss"] is not None]
    if phase_with_data:
        top_phase = min(phase_with_data, key=lambda item: float(item[1]["avg_cp_loss"] or 10_000))[0]
        weak_phase = max(phase_with_data, key=lambda item: float(item[1]["avg_cp_loss"] or -1))[0]

    strengths = [
        {
            "text": f"Overall score is {overall_score_pct:.1f}% across {total_games} games.",
            "fact_ids": [overall_games_fact, overall_score_fact],
        }
    ]
    if best_openings:
        strengths.append(
            {
                "text": f"Best-performing opening cluster starts with {best_openings[0]['opening']}.",
                "fact_ids": best_opening_fact_ids[:1],
            }
        )
    if top_phase and top_phase in phase_fact_ids:
        strengths.append(
            {
                "text": f"{top_phase.capitalize()} is your most stable phase by average centipawn loss.",
                "fact_ids": [phase_fact_ids[top_phase]],
            }
        )

    weaknesses = []
    if worst_openings:
        weaknesses.append(
            {
                "text": f"The toughest opening cluster starts with {worst_openings[0]['opening']}.",
                "fact_ids": weak_opening_fact_ids[:1],
            }
        )
    if weak_phase and weak_phase in phase_fact_ids:
        weaknesses.append(
            {
                "text": f"{weak_phase.capitalize()} has your highest average centipawn loss.",
                "fact_ids": [phase_fact_ids[weak_phase]],
            }
        )
    if low_time_score_pct is not None:
        delta = round(low_time_score_pct - overall_score_pct, 1)
        delta_fact = _add_fact(
            fact_map,
            "low_time_vs_overall_delta",
            "Low-time score delta vs overall",
            delta,
            "pct",
        )
        if delta < 0:
            weaknesses.append(
                {
                    "text": "Results drop under time pressure compared to your baseline.",
                    "fact_ids": [delta_fact],
                }
            )
        else:
            strengths.append(
                {
                    "text": "You maintain or improve results in low-time situations.",
                    "fact_ids": [delta_fact],
                }
            )

    coaching_focus = []
    if weak_phase and weak_phase in phase_fact_ids:
        coaching_focus.append(
            {
                "text": f"Prioritize {weak_phase} drills to reduce average centipawn loss.",
                "fact_ids": [phase_fact_ids[weak_phase]],
            }
        )
    if worst_openings:
        coaching_focus.append(
            {
                "text": f"Review plans in {worst_openings[0]['opening']} structures.",
                "fact_ids": weak_opening_fact_ids[:1],
            }
        )
    if theme_items:
        theme = theme_items[0]
        theme_fact = _add_fact(
            fact_map,
            "top_theme",
            "Most recurring mistake theme",
            f"{theme['theme']} ({theme['count']})",
        )
        coaching_focus.append(
            {
                "text": f"Address recurring pattern: {theme['theme'].replace('_', ' ')}.",
                "fact_ids": [theme_fact],
            }
        )

    features = {
        "version": FEATURE_VERSION,
        "computed_at": _utc_now_iso(),
        "style": {
            "label": style_label,
            "scores": {
                "tactical": round(tactical_score, 3),
                "positional": round(positional_score, 3),
                "aggressive": round(aggressive_score, 3),
                "solid": round(solid_score, 3),
            },
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
                    "score_pct": round((stats["score_sum"] / stats["games"]) * 100, 1)
                    if stats["games"] > 0
                    else 0.0,
                }
                for tc, stats in sorted(by_time_class.items())
            ],
            "by_color": [
                {
                    "color": color_name,
                    "games": int(stats["games"]),
                    "score_pct": round((stats["score_sum"] / stats["games"]) * 100, 1)
                    if stats["games"] > 0
                    else 0.0,
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
            "fact_ids": time_pressure_fact_ids + [clock_cov_fact],
        },
        "recurring_themes": theme_items[:5],
        "strengths": strengths[:4],
        "weaknesses": weaknesses[:4],
        "coaching_focus": coaching_focus[:4],
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
    *,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Generate grounded narrative with verifier + deterministic fallback."""
    fallback = _build_fallback_narrative(features)
    if not allow_llm:
        return fallback

    llm_output = _generate_llm_narrative(features, fact_map)
    if llm_output and verify_narrative(llm_output, fact_map):
        llm_output["meta"] = {
            "source": "llm",
            "provider": NARRATIVE_PROVIDER,
            "version": NARRATIVE_VERSION,
        }
        return llm_output
    return fallback


async def _save_snapshot(
    user_id: str,
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
            user_id=user_id,
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
    user_id: str,
    username: str,
    site: str = "all",
    allow_deep: bool = True,
    allow_llm: bool = True,
    source_user_id: str | None = None,
) -> None:
    """Run tiered insights processing for a user."""
    started_at = _utc_now_iso()
    source_owner_id = source_user_id or user_id
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
                    user_id=source_owner_id,
                    username=username,
                    site=site,
                    limit=MAX_GAMES_WINDOW,
                )
            finally:
                conn.close()

            if not games:
                empty_features = {
                    "version": FEATURE_VERSION,
                    "computed_at": _utc_now_iso(),
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
                    user_id=user_id,
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
                        finished_at=_utc_now_iso(),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return

            for game in games:
                light_feature = await asyncio.to_thread(extract_light_game_features, game)
                conn = get_connection()
                try:
                    upsert_insight_game_feature(
                        conn,
                        user_id=user_id,
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
                    user_id=user_id,
                    username=username,
                    site=site,
                    feature_version=FEATURE_VERSION,
                )
            finally:
                conn.close()

            features, coverage, fact_map = _build_aggregate_features(games, stored_features)
            narrative = build_narrative(features, fact_map, allow_llm=allow_llm)

            initial_status = "baseline_ready" if coverage.get("has_enough_games") else "not_enough_data"
            await _save_snapshot(
                user_id=user_id,
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
                        finished_at=_utc_now_iso(),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return

            deep_candidates = []
            if allow_deep:
                deep_candidates = _select_deep_candidates(games, stored_features, DEEP_ANALYSIS_BUDGET)
            if deep_candidates:
                conn = get_connection()
                try:
                    update_insight_job(conn, job_id, status="running", stage="deep")
                    conn.commit()
                finally:
                    conn.close()

                await _save_snapshot(
                    user_id=user_id,
                    username=username,
                    site=site,
                    status="enriching",
                    coverage=coverage,
                    features=features,
                    fact_map=fact_map,
                    narrative=narrative,
                    source_job_id=job_id,
                )

                light_by_key = {
                    (row["site"], row["site_game_id"]): row.get("light", {})
                    for row in stored_features
                }

                for game in deep_candidates:
                    try:
                        full_analysis = await asyncio.to_thread(
                            run_full_analysis,
                            game.get("pgn") or "",
                            DEEP_ANALYSIS_DEPTH,
                            DEEP_ANALYSIS_MULTIPV,
                            DEEP_ANALYSIS_TIME_MS,
                        )
                        light_feature = light_by_key.get((game["site"], game["site_game_id"])) or extract_light_game_features(game)
                        deep_feature = _extract_deep_game_features(game, light_feature, full_analysis)

                        conn = get_connection()
                        try:
                            upsert_insight_game_feature(
                                conn,
                                user_id=user_id,
                                username=username,
                                site=game["site"],
                                site_game_id=game["site_game_id"],
                                feature_version=FEATURE_VERSION,
                                light=light_feature,
                                deep=deep_feature,
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    except Exception:
                        # Keep pipeline resilient: one failed deep sample should not fail the entire profile.
                        continue

                conn = get_connection()
                try:
                    stored_features = get_insight_game_features(
                        conn,
                        user_id=user_id,
                        username=username,
                        site=site,
                        feature_version=FEATURE_VERSION,
                    )
                finally:
                    conn.close()

                features, coverage, fact_map = _build_aggregate_features(games, stored_features)
                narrative = build_narrative(features, fact_map, allow_llm=allow_llm)

            await _save_snapshot(
                user_id=user_id,
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
                    finished_at=_utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - last resort error path
            conn = get_connection()
            try:
                update_insight_job(
                    conn,
                    job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc),
                    finished_at=_utc_now_iso(),
                )
                conn.commit()
            finally:
                conn.close()


def schedule_insights_refresh(
    user_id: str,
    username: str,
    site: str = "all",
    reason: str = "manual_refresh",
    force: bool = False,
    allow_deep: bool = True,
    allow_llm: bool = True,
    source_user_id: str | None = None,
) -> dict[str, Any]:
    """Create an insights job if none is currently active, then schedule it."""
    canonical_username = username.strip().lower()
    source_owner_id = source_user_id or user_id
    conn = get_connection()
    try:
        active = get_active_insight_job(conn, user_id, canonical_username, site)
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
            user_id=user_id,
            username=canonical_username,
            site=site,
            status="queued",
            stage="queued",
            reason=reason,
            feature_version=FEATURE_VERSION,
            meta={
                "window_size": MAX_GAMES_WINDOW,
                "allow_deep": allow_deep,
                "allow_llm": allow_llm,
                "source_user_id": source_owner_id,
            },
        )
        conn.commit()
    finally:
        conn.close()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            run_insights_pipeline(
                job_id,
                user_id,
                canonical_username,
                site,
                allow_deep=allow_deep,
                allow_llm=allow_llm,
                source_user_id=source_owner_id,
            )
        )
    except RuntimeError:
        # No active event loop: job is recorded as queued and can be resumed by API-triggered calls.
        pass

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
    user_id: str,
    username: str,
    site: str = "all",
) -> dict[str, Any]:
    """Fetch current insights snapshot + job status for API responses."""
    canonical_username = username.strip().lower()
    conn = get_connection()
    try:
        snapshot = get_player_insights(conn, user_id, canonical_username, site)
        active_job = get_active_insight_job(conn, user_id, canonical_username, site)
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
