"""Aggregate feature building for player insights.

This module contains helpers for aggregating per-game features into
user-level insights (style classification, performance stats, etc).
"""

from __future__ import annotations

from typing import Any

from utils.insights_constants import (
    FEATURE_VERSION,
    MIN_BASELINE_GAMES,
)
from utils.insights_utils import (
    add_fact,
    clamp01,
    mean,
    result_to_score,
    utc_now_iso,
)


def aggregate_light_features(
    light_features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate metrics from light features across all games.
    
    Returns a dict containing:
    - Basic win/draw/loss counts
    - Performance breakdowns by time class, color, opening
    - Style signal averages (capture rate, check rate, game length)
    - Time pressure statistics
    """
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
        score = result_to_score(result)
        overall_scores.append(score)
        
        if result == "win":
            wins += 1
        elif result == "draw":
            draws += 1
        else:
            losses += 1

        # Aggregate by time class
        tc = (meta.get("time_class") or "unknown").lower()
        tc_item = by_time_class.setdefault(tc, {"games": 0, "score_sum": 0.0})
        tc_item["games"] += 1
        tc_item["score_sum"] += score

        # Aggregate by color
        color = (meta.get("color") or "unknown").lower()
        color_item = by_color.setdefault(color, {"games": 0, "score_sum": 0.0})
        color_item["games"] += 1
        color_item["score_sum"] += score

        # Aggregate by opening
        opening = (meta.get("opening_name") or "Unknown").strip() or "Unknown"
        opening_item = openings.setdefault(opening, {"games": 0, "score_sum": 0.0})
        opening_item["games"] += 1
        opening_item["score_sum"] += score

        # Style signals
        style_signals = feature.get("style_signals", {})
        early_capture_rates.append(float(style_signals.get("early_capture_rate") or 0.0))
        early_check_rates.append(float(style_signals.get("early_check_rate") or 0.0))
        game_lengths.append(float(style_signals.get("avg_game_length") or 0.0))

        # Time pressure tracking
        time_pressure = feature.get("time_pressure", {})
        if time_pressure.get("has_clock_data"):
            clock_games += 1
            low_time_rate = float(time_pressure.get("low_time_rate") or 0.0)
            if low_time_rate > 0:
                low_time_games += 1
                low_time_scores.append(score)

    return {
        "total_games": total_games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "by_time_class": by_time_class,
        "by_color": by_color,
        "openings": openings,
        "early_capture_rates": early_capture_rates,
        "early_check_rates": early_check_rates,
        "game_lengths": game_lengths,
        "clock_games": clock_games,
        "low_time_games": low_time_games,
        "low_time_scores": low_time_scores,
        "overall_scores": overall_scores,
    }


def aggregate_deep_features(
    deep_features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate metrics from deep (engine-analyzed) features.
    
    Returns phase-by-phase stats, theme counts, and blunder rates.
    """
    phase_accum = {
        "opening": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "middlegame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "endgame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
    }
    theme_counts: dict[str, int] = {}
    total_user_moves_deep = 0
    total_blunders_deep = 0
    total_low_time_blunders_deep = 0
    total_blunders_with_clock_deep = 0
    total_low_time_moves_deep = 0
    total_moves_with_clock_deep = 0

    for deep in deep_features:
        quality = deep.get("quality", {})
        total_user_moves_deep += int(quality.get("user_moves_analyzed") or 0)
        
        deep_time_pressure = quality.get("time_pressure") or {}
        total_low_time_blunders_deep += int(deep_time_pressure.get("blunders_low_time") or 0)
        total_blunders_with_clock_deep += int(deep_time_pressure.get("blunders_total") or 0)
        total_low_time_moves_deep += int(deep_time_pressure.get("user_moves_low_time") or 0)
        total_moves_with_clock_deep += int(deep_time_pressure.get("user_moves_with_clock") or 0)

        # Accumulate phase stats
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

        # Accumulate theme counts
        for theme, count in (deep.get("theme_counts") or {}).items():
            theme_counts[theme] = theme_counts.get(theme, 0) + int(count)

    # Compute final phase performance metrics
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

    return {
        "phase_performance": phase_performance,
        "theme_counts": theme_counts,
        "total_user_moves_deep": total_user_moves_deep,
        "total_blunders_deep": total_blunders_deep,
        "total_low_time_blunders_deep": total_low_time_blunders_deep,
        "total_blunders_with_clock_deep": total_blunders_with_clock_deep,
        "total_low_time_moves_deep": total_low_time_moves_deep,
        "total_moves_with_clock_deep": total_moves_with_clock_deep,
    }


def aggregate_scan_features(
    scan_rows: list[dict],
) -> dict:
    """Aggregate per-game quick-scan results into the same shape as deep aggregation.

    Each row should contain a parsed 'problems' dict with keys:
      - problems (list), move_stats (dict), summary (dict)
    """
    phase_accum = {
        "opening": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "middlegame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
        "endgame": {"moves": 0, "cp_loss_sum": 0.0, "mistakes": 0, "blunders": 0},
    }
    theme_counts: dict[str, int] = {}
    total_blunders = 0
    total_mistakes = 0
    total_inaccuracies = 0
    total_user_moves = 0
    games_scanned = 0

    for row in scan_rows:
        data = row.get("problems") or {}
        summary = data.get("summary") or row.get("summary") or {}
        move_stats = data.get("move_stats") or {}
        problems_list = data.get("problems") or []

        total_blunders += int(summary.get("blunders") or 0)
        total_mistakes += int(summary.get("mistakes") or 0)
        total_inaccuracies += int(summary.get("inaccuracies") or 0)
        total_user_moves += int(move_stats.get("total_user_moves") or 0)
        games_scanned += 1

        phase_cp = move_stats.get("phase_cp_losses") or {}
        for phase_name in ("opening", "middlegame", "endgame"):
            losses = phase_cp.get(phase_name, [])
            if not losses:
                continue
            phase_accum[phase_name]["moves"] += len(losses)
            phase_accum[phase_name]["cp_loss_sum"] += sum(losses)

        for problem in problems_list:
            classification = problem.get("classification")
            phase_name = problem.get("phase", "middlegame")
            if phase_name in phase_accum:
                if classification in ("mistake", "blunder"):
                    phase_accum[phase_name]["mistakes"] += 1
                if classification == "blunder":
                    phase_accum[phase_name]["blunders"] += 1

            tactic_type = problem.get("tactic_type")
            if tactic_type:
                theme_counts[tactic_type] = theme_counts.get(tactic_type, 0) + 1
            for tt in problem.get("tactic_types") or []:
                if tt and tt != tactic_type:
                    theme_counts[tt] = theme_counts.get(tt, 0) + 1

    phase_performance: dict[str, dict] = {}
    for phase_name, stats in phase_accum.items():
        moves_count = stats["moves"]
        avg_cp = (stats["cp_loss_sum"] / moves_count) if moves_count > 0 else None
        phase_performance[phase_name] = {
            "moves": moves_count,
            "avg_cp_loss": round(avg_cp, 2) if avg_cp is not None else None,
            "mistakes": stats["mistakes"],
            "blunders": stats["blunders"],
            "mistake_rate": round(stats["mistakes"] / moves_count, 4) if moves_count > 0 else None,
        }

    theme_items = sorted(
        [{"theme": t, "count": c} for t, c in theme_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    blunder_rate = (total_blunders / total_user_moves) if total_user_moves > 0 else 0.0

    return {
        "phase_performance": phase_performance,
        "theme_counts": theme_counts,
        "theme_items": theme_items,
        "total_blunders": total_blunders,
        "total_mistakes": total_mistakes,
        "total_inaccuracies": total_inaccuracies,
        "total_user_moves": total_user_moves,
        "games_scanned": games_scanned,
        "blunder_rate": round(blunder_rate, 4),
    }


def compute_style_scores(
    draw_rate: float,
    avg_early_capture: float,
    avg_early_check: float,
    avg_game_len: float,
    blunder_rate: float,
    theme_counts: dict[str, int],
    deep_game_count: int,
) -> dict[str, Any]:
    """Compute style classification scores and label.
    
    Returns scores for tactical/positional and aggressive/solid axes,
    plus the combined style label (e.g., "Solid Positional").
    """
    # Tactical score: early checks + tactical oversight frequency
    tactical_score = clamp01(
        avg_early_check * 1.8 
        + (theme_counts.get("tactical_oversight", 0) / max(1, deep_game_count)) * 0.2
    )
    
    # Positional score: longer games + draws - early captures
    positional_score = clamp01(
        (avg_game_len / 110.0) + draw_rate * 0.5 - avg_early_capture * 0.3
    )
    
    # Aggressive score: early captures + early checks
    aggressive_score = clamp01(
        avg_early_capture * 1.5 + avg_early_check * 1.2
    )
    
    # Solid score: low blunder rate + draws
    solid_score = clamp01(
        (1.0 - blunder_rate) * 0.7 + draw_rate * 0.3
    )

    # Determine primary and secondary style labels
    primary = "tactical" if tactical_score >= positional_score else "positional"
    secondary = "aggressive" if aggressive_score >= solid_score else "solid"
    style_label = f"{secondary.capitalize()} {primary.capitalize()}"

    return {
        "label": style_label,
        "scores": {
            "tactical": round(tactical_score, 3),
            "positional": round(positional_score, 3),
            "aggressive": round(aggressive_score, 3),
            "solid": round(solid_score, 3),
        },
    }


def compute_opening_rankings(
    openings: dict[str, dict[str, float]],
) -> tuple[list[dict], list[dict]]:
    """Rank openings by score percentage.
    
    Returns (best_openings, worst_openings) - top 3 of each.
    """
    opening_items = []
    for name, stats in openings.items():
        games_count = int(stats["games"])
        score_pct = round((stats["score_sum"] / games_count) * 100, 1) if games_count > 0 else 0.0
        opening_items.append({"opening": name, "games": games_count, "score_pct": score_pct})
    
    opening_items.sort(key=lambda item: (item["score_pct"], item["games"]), reverse=True)
    best_openings = opening_items[:3]
    worst_openings = sorted(opening_items, key=lambda item: (item["score_pct"], -item["games"]))[:3]
    
    return best_openings, worst_openings


def build_coverage_metrics(
    total_games: int,
    light_count: int,
    deep_count: int,
    clock_games: int,
    low_time_games: int,
) -> dict[str, Any]:
    """Build the coverage/confidence metrics block."""
    coverage = {
        "games_total": total_games,
        "games_light": light_count,
        "games_deep": deep_count,
        "deep_coverage": round((deep_count / total_games), 4) if total_games > 0 else 0.0,
        "games_with_clock": clock_games,
        "clock_coverage": round((clock_games / total_games), 4) if total_games > 0 else 0.0,
        "games_with_time_pressure": low_time_games,
        "has_enough_games": total_games >= MIN_BASELINE_GAMES,
    }
    
    # Confidence is weighted: deep coverage (45%) + clock coverage (20%) + sample size (35%)
    confidence = clamp01(
        coverage["deep_coverage"] * 0.45 
        + coverage["clock_coverage"] * 0.2 
        + min(total_games / 100.0, 1.0) * 0.35
    )
    coverage["confidence"] = round(confidence, 3)
    
    return coverage


def build_strengths_weaknesses(
    overall_score_pct: float,
    total_games: int,
    best_openings: list[dict],
    worst_openings: list[dict],
    phase_performance: dict[str, dict],
    low_time_score_pct: float | None,
    theme_items: list[dict],
    fact_map: dict[str, dict[str, Any]],
    overall_games_fact: str,
    overall_score_fact: str,
    best_opening_fact_ids: list[str],
    weak_opening_fact_ids: list[str],
    phase_fact_ids: dict[str, str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build strengths, weaknesses, and coaching focus lists with fact references."""
    
    # Find best and worst phases by avg cp loss
    phase_with_data = [
        item for item in phase_performance.items() 
        if item[1]["avg_cp_loss"] is not None
    ]
    top_phase = None
    weak_phase = None
    if phase_with_data:
        top_phase = min(phase_with_data, key=lambda item: float(item[1]["avg_cp_loss"] or 10_000))[0]
        weak_phase = max(phase_with_data, key=lambda item: float(item[1]["avg_cp_loss"] or -1))[0]

    # Build strengths
    strengths = [
        {
            "text": f"Overall score is {overall_score_pct:.1f}% across {total_games} games.",
            "fact_ids": [overall_games_fact, overall_score_fact],
        }
    ]
    if best_openings:
        strengths.append({
            "text": f"Best-performing opening cluster starts with {best_openings[0]['opening']}.",
            "fact_ids": best_opening_fact_ids[:1],
        })
    if top_phase and top_phase in phase_fact_ids:
        strengths.append({
            "text": f"{top_phase.capitalize()} is your most stable phase by average centipawn loss.",
            "fact_ids": [phase_fact_ids[top_phase]],
        })

    # Build weaknesses
    weaknesses = []
    if worst_openings:
        weaknesses.append({
            "text": f"The toughest opening cluster starts with {worst_openings[0]['opening']}.",
            "fact_ids": weak_opening_fact_ids[:1],
        })
    if weak_phase and weak_phase in phase_fact_ids:
        weaknesses.append({
            "text": f"{weak_phase.capitalize()} has your highest average centipawn loss.",
            "fact_ids": [phase_fact_ids[weak_phase]],
        })
    
    # Time pressure analysis
    if low_time_score_pct is not None:
        delta = round(low_time_score_pct - overall_score_pct, 1)
        delta_fact = add_fact(
            fact_map,
            "low_time_vs_overall_delta",
            "Low-time score delta vs overall",
            delta,
            "pct",
        )
        if delta < 0:
            weaknesses.append({
                "text": "Results drop under time pressure compared to your baseline.",
                "fact_ids": [delta_fact],
            })
        else:
            strengths.append({
                "text": "You maintain or improve results in low-time situations.",
                "fact_ids": [delta_fact],
            })

    # Build coaching focus
    coaching_focus = []
    if weak_phase and weak_phase in phase_fact_ids:
        coaching_focus.append({
            "text": f"Prioritize {weak_phase} drills to reduce average centipawn loss.",
            "fact_ids": [phase_fact_ids[weak_phase]],
        })
    if worst_openings:
        coaching_focus.append({
            "text": f"Review plans in {worst_openings[0]['opening']} structures.",
            "fact_ids": weak_opening_fact_ids[:1],
        })
    if theme_items:
        theme = theme_items[0]
        theme_fact = add_fact(
            fact_map,
            "top_theme",
            "Most recurring mistake theme",
            f"{theme['theme']} ({theme['count']})",
        )
        coaching_focus.append({
            "text": f"Address recurring pattern: {theme['theme'].replace('_', ' ')}.",
            "fact_ids": [theme_fact],
        })

    return strengths[:4], weaknesses[:4], coaching_focus[:4]
