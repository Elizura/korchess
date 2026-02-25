"""Deterministic single-game rule-based insights."""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from statistics import median
from typing import Any

import chess.pgn


_CLOCK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")
_ELAPSED_RE = re.compile(r"\[%emt\s+([0-9:.]+)\]")
_TACTICAL_PIECE_LABELS = {
    "p": "pawn",
    "n": "knight",
    "b": "bishop",
    "r": "rook",
    "q": "queen",
    "k": "king",
}


@dataclass(frozen=True)
class RuleConfig:
    equal_band_cp: int = int(os.environ.get("SGI_EQUAL_BAND_CP", "50"))
    clear_adv_cp: int = int(os.environ.get("SGI_CLEAR_ADV_CP", "150"))
    winning_adv_cp: int = int(os.environ.get("SGI_WINNING_ADV_CP", "300"))
    decisive_adv_cp: int = int(os.environ.get("SGI_DECISIVE_ADV_CP", "500"))
    major_swing_cp: int = int(os.environ.get("SGI_MAJOR_SWING_CP", "150"))
    critical_swing_cp: int = int(os.environ.get("SGI_CRITICAL_SWING_CP", "300"))
    mistake_cp_loss: int = int(os.environ.get("SGI_MISTAKE_CP_LOSS", "100"))
    blunder_cp_loss: int = int(os.environ.get("SGI_BLUNDER_CP_LOSS", "300"))
    min_phase_moves: int = int(os.environ.get("SGI_MIN_PHASE_MOVES", "4"))
    got_away_persisted_ratio: float = float(os.environ.get("SGI_GOT_AWAY_PERSISTED_RATIO", "0.40"))
    low_time_ratio: float = float(os.environ.get("SGI_LOW_TIME_RATIO", "0.10"))
    low_time_floor_seconds: int = int(os.environ.get("SGI_LOW_TIME_FLOOR_SECONDS", "30"))
    min_low_time_samples: int = int(os.environ.get("SGI_MIN_LOW_TIME_SAMPLES", "4"))
    min_clock_coverage: float = float(os.environ.get("SGI_MIN_CLOCK_COVERAGE", "0.60"))
    decisive_turning_ratio: float = float(os.environ.get("SGI_DECISIVE_TURNING_RATIO", "0.70"))


CFG = RuleConfig()
VERSION = "single_game_rules_v2"


CAUSE_LABELS = {
    "conversion_failure": "Conversion Failure",
    "time_pressure_collapse": "Time Pressure Collapse",
    "resilience_failure": "Resilience Failure",
    "self_errors": "Own Errors",
    "opponent_errors": "Opponent Errors",
    "resilience_success": "Resilience Strength",
    "strong_conversion": "Strong Conversion",
    "balanced_draw": "Balanced Draw",
}

CHARACTER_LABELS = {
    "advantage_lost": ("Advantage Lost", "Could not convert the winning edge"),
    "defensive_grind": ("Defensive Grind", "Recovered from sustained pressure"),
    "chaotic": ("Chaotic", "Frequent momentum swings"),
    "sharp": ("Sharp", "High-risk tactical battle"),
    "technical": ("Technical", "Low-error precision game"),
    "controlled": ("Controlled", "Steady game flow with few shocks"),
    "volatile": ("Volatile", "Momentum shifted repeatedly"),
    "stable": ("Stable", "Mostly stable evaluation profile"),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low), 0.0, 1.0)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _p90(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = max(0, min(len(sorted_values) - 1, int(round(0.9 * (len(sorted_values) - 1)))))
    return float(sorted_values[idx])


def _score_to_grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def _grade_to_rating_5(grade: str | None) -> int | None:
    if not isinstance(grade, str):
        return None
    mapping = {
        "A": 5,
        "B": 4,
        "C": 3,
        "D": 2,
        "E": 1,
    }
    return mapping.get(grade.strip().upper())


def _score_to_rating_5(score: float | None) -> int | None:
    if score is None:
        return None
    return _grade_to_rating_5(_score_to_grade(score))


def _eval_to_cp(eval_obj: dict[str, Any] | None) -> int:
    if not isinstance(eval_obj, dict):
        return 0
    cp = eval_obj.get("cp")
    if cp is not None:
        try:
            return int(cp)
        except (TypeError, ValueError):
            return 0
    mate = eval_obj.get("mate")
    if mate is None:
        return 0
    try:
        mate_int = int(mate)
        if mate_int > 0:
            return 10000
        if mate_int < 0:
            return -10000
    except (TypeError, ValueError):
        pass
    return 0


def _clock_to_seconds(raw_value: str | None) -> int | None:
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None

    if ":" not in cleaned:
        try:
            return int(float(cleaned))
        except ValueError:
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
        if len(parts) == 1:
            return int(float(parts[0]))
    except ValueError:
        return None
    return None


def _extract_tag_seconds(comment: str | None, pattern: re.Pattern[str]) -> int | None:
    if not comment:
        return None
    match = pattern.search(comment)
    if not match:
        return None
    return _clock_to_seconds(match.group(1))


def _extract_time_fallback_from_pgn(pgn: str | None) -> dict[int, dict[str, Any]]:
    if not pgn or not pgn.strip():
        return {}

    game = chess.pgn.read_game(io.StringIO(pgn))
    if not game:
        return {}

    time_by_ply: dict[int, dict[str, Any]] = {}
    node = game
    ply = 0
    while node.variations:
        node = node.variation(0)
        ply += 1
        comment = node.comment or ""
        clock_seconds = _extract_tag_seconds(comment, _CLOCK_RE)
        elapsed_seconds = _extract_tag_seconds(comment, _ELAPSED_RE)

        time_source = "missing"
        if elapsed_seconds is not None:
            time_source = "elapsed"
        elif clock_seconds is not None:
            time_source = "clock"

        time_by_ply[ply] = {
            "clock_seconds": clock_seconds,
            "time_spent_seconds": elapsed_seconds,
            "time_source": time_source,
        }

    return time_by_ply


def _bucket(eval_cp: int) -> str:
    if eval_cp >= CFG.decisive_adv_cp:
        return "decisive_better"
    if eval_cp >= CFG.winning_adv_cp:
        return "winning"
    if eval_cp >= CFG.clear_adv_cp:
        return "better"
    if abs(eval_cp) < CFG.equal_band_cp:
        return "equal"
    if eval_cp <= -CFG.decisive_adv_cp:
        return "decisive_worse"
    if eval_cp <= -CFG.winning_adv_cp:
        return "losing"
    if eval_cp <= -CFG.clear_adv_cp:
        return "worse"
    return "near_equal"


def _is_user_move(ply: int, user_color: str) -> bool:
    color = user_color.lower()
    if color == "white":
        return ply % 2 == 1
    return ply % 2 == 0


def _phase_map_from_moves(moves: list[dict[str, Any]]) -> tuple[dict[int, str], dict[str, Any]]:
    total = len(moves)
    opening_end_ply = min(20, total)

    endgame_start_ply: int | None = None
    for move in moves:
        ply = int(move.get("ply", 0)) + 1
        fen_after = str(move.get("fen_after") or "")
        board_state = fen_after.split(" ")[0] if fen_after else ""
        non_king_pieces = sum(
            1 for char in board_state if char.isalpha() and char.lower() != "k"
        )
        if non_king_pieces <= 8:
            endgame_start_ply = ply
            break

    phase_by_ply: dict[int, str] = {}
    for idx in range(total):
        ply = idx + 1
        if ply <= opening_end_ply:
            phase = "opening"
        elif endgame_start_ply is not None and ply >= endgame_start_ply:
            phase = "endgame"
        else:
            phase = "middlegame"
        phase_by_ply[ply] = phase

    return phase_by_ply, {
        "opening_end_ply": opening_end_ply,
        "endgame_start_ply": endgame_start_ply,
        "total_plies": total,
    }


def _normalize_tactical_annotation(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("tactic_detected")):
        return None

    tactic_type = str(raw.get("tactic_type") or "").upper()
    if not tactic_type:
        return None

    material = raw.get("material_outcome") if isinstance(raw.get("material_outcome"), dict) else {}
    mate = raw.get("mate_outcome") if isinstance(raw.get("mate_outcome"), dict) else {}

    return {
        "tactic_type": tactic_type,
        "tactic_types": [str(t).upper() for t in (raw.get("tactic_types") or []) if isinstance(t, str)],
        "line_source": str(raw.get("line_source") or ""),
        "missed_move_san": raw.get("missed_move_san"),
        "missed_move_uci": raw.get("missed_move_uci"),
        "hanging_piece_symbol": raw.get("hanging_piece_symbol"),
        "hanging_piece_name": raw.get("hanging_piece_name"),
        "hanging_piece_value_cp": raw.get("hanging_piece_value_cp"),
        "skewer_front_piece": raw.get("skewer_front_piece"),
        "skewer_rear_piece": raw.get("skewer_rear_piece"),
        "material_text": material.get("text"),
        "material_cp_net_for_mover": material.get("cp_net_for_mover"),
        "mate_in": mate.get("mate_in"),
        "mate_subtype": mate.get("subtype"),
    }


def _article(word: str) -> str:
    return "an" if word and word[0].lower() in {"a", "e", "i", "o", "u"} else "a"


def _display_move_text(move_san: Any, move_uci: Any) -> str | None:
    if isinstance(move_san, str) and move_san.strip():
        return move_san.strip()
    if isinstance(move_uci, str) and move_uci.strip():
        return move_uci.strip()
    return None


def _tactical_turning_reason(tactical: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(tactical, dict):
        return None

    tactic_type = str(tactical.get("tactic_type") or "").upper()
    line_source = str(tactical.get("line_source") or "")
    missed_move = _display_move_text(tactical.get("missed_move_san"), tactical.get("missed_move_uci"))

    if tactic_type == "HANGING_PIECE":
        piece_name = (
            str(tactical.get("hanging_piece_name") or "").strip().lower()
            or _TACTICAL_PIECE_LABELS.get(str(tactical.get("hanging_piece_symbol") or "").lower())
            or "piece"
        )
        return {
            "type": tactic_type,
            "line_source": line_source,
            "reason_text": f"hung {_article(piece_name)} {piece_name}",
        }

    if tactic_type == "FORCED_MATE" and line_source == "played_line":
        mate_in = tactical.get("mate_in")
        if isinstance(mate_in, int) and mate_in > 0:
            return {
                "type": tactic_type,
                "line_source": line_source,
                "reason_text": f"allowed a forced mate in {mate_in}",
            }
        return {
            "type": tactic_type,
            "line_source": line_source,
            "reason_text": "allowed a forced mating sequence",
        }

    if tactic_type == "MISSED_FORCED_MATE":
        mate_in = tactical.get("mate_in")
        suffix = f" with {missed_move}" if missed_move else ""
        if isinstance(mate_in, int) and mate_in > 0:
            return {
                "type": tactic_type,
                "line_source": line_source,
                "reason_text": f"missed a forced mate in {mate_in}{suffix}",
            }
        return {
            "type": tactic_type,
            "line_source": line_source,
            "reason_text": f"missed a forced mating line{suffix}",
        }

    if tactic_type in {"FORK", "DOUBLE_ATTACK", "SKEWER"} and line_source == "best_line":
        tactic_name = tactic_type.lower().replace("_", " ")
        suffix = f" with {missed_move}" if missed_move else ""
        if tactic_type == "SKEWER":
            front = str(tactical.get("skewer_front_piece") or "").lower()
            rear = str(tactical.get("skewer_rear_piece") or "").lower()
            if front and rear:
                return {
                    "type": tactic_type,
                    "line_source": line_source,
                    "reason_text": f"missed a skewer ({front} to {rear}){suffix}",
                }
        return {
            "type": tactic_type,
            "line_source": line_source,
            "reason_text": f"missed a {tactic_name}{suffix}",
        }

    return None


def _preprocess_rows(
    moves: list[dict[str, Any]],
    user_color: str,
    pgn_time_fallback: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sign = 1 if user_color.lower() == "white" else -1
    phase_by_ply, phase_meta = _phase_map_from_moves(moves)

    rows: list[dict[str, Any]] = []
    prev_clock = {"white": None, "black": None}

    for idx, move in enumerate(moves):
        move_index = int(move.get("ply", idx))
        ply = move_index + 1
        actor_color = "white" if ply % 2 == 1 else "black"
        actor = "user" if _is_user_move(ply, user_color) else "opponent"

        eval_before_white = _eval_to_cp(move.get("eval_before"))
        eval_after_white = _eval_to_cp(move.get("eval_after"))

        eval_before_user = eval_before_white * sign
        eval_after_user = eval_after_white * sign
        delta = eval_after_user - eval_before_user

        clock_seconds = move.get("clock_seconds")
        time_spent_seconds = move.get("time_spent_seconds")
        time_source = move.get("time_source")

        fallback = pgn_time_fallback.get(ply, {})

        if clock_seconds is None:
            clock_seconds = fallback.get("clock_seconds")
        if time_spent_seconds is None:
            time_spent_seconds = fallback.get("time_spent_seconds")
        if not time_source:
            time_source = fallback.get("time_source")

        clock_seconds = int(clock_seconds) if isinstance(clock_seconds, (int, float)) else None
        time_spent_seconds = (
            int(time_spent_seconds)
            if isinstance(time_spent_seconds, (int, float))
            else None
        )

        prev_actor_clock = prev_clock.get(actor_color)
        if time_spent_seconds is None and clock_seconds is not None and prev_actor_clock is not None:
            inferred = prev_actor_clock - clock_seconds
            if inferred >= 0:
                time_spent_seconds = int(inferred)
                time_source = "inferred"

        if clock_seconds is not None:
            prev_clock[actor_color] = clock_seconds

        if not time_source:
            if time_spent_seconds is not None:
                time_source = "elapsed"
            elif clock_seconds is not None:
                time_source = "clock"
            else:
                time_source = "missing"

        cp_loss = move.get("cp_loss")
        cp_loss = int(cp_loss) if isinstance(cp_loss, (int, float)) else None

        rows.append(
            {
                "ply": ply,
                "move_index": move_index,
                "actor": actor,
                "phase": phase_by_ply.get(ply, "middlegame"),
                "eval_before": int(eval_before_user),
                "eval_after": int(eval_after_user),
                "delta": int(delta),
                "swing_abs": abs(int(delta)),
                "cp_loss": cp_loss,
                "classification": move.get("classification"),
                "san": move.get("san"),
                "uci": move.get("uci"),
                "fen_before": move.get("fen_before"),
                "fen_after": move.get("fen_after"),
                "clock_seconds": clock_seconds,
                "time_spent_seconds": time_spent_seconds,
                "time_source": time_source,
                "tactical": _normalize_tactical_annotation(move.get("tactical")),
            }
        )

    return rows, phase_meta


def _is_mistake_or_worse(row: dict[str, Any]) -> bool:
    cp_loss = row.get("cp_loss")
    classification = str(row.get("classification") or "").lower()
    return (
        (isinstance(cp_loss, int) and cp_loss >= CFG.mistake_cp_loss)
        or classification in {"mistake", "blunder"}
    )


def _is_blunder(row: dict[str, Any]) -> bool:
    cp_loss = row.get("cp_loss")
    classification = str(row.get("classification") or "").lower()
    return (
        (isinstance(cp_loss, int) and cp_loss >= CFG.blunder_cp_loss)
        or classification == "blunder"
    )


def _anchor(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ply": int(row["ply"]),
        "move_index": int(row["move_index"]),
        "uci": row.get("uci"),
        "san": row.get("san"),
        "fen_before": row.get("fen_before"),
        "fen_after": row.get("fen_after"),
    }


def _evidence_from_rows(rows: list[dict[str, Any]], max_items: int = 3) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in rows[:max_items]:
        evidence.append(
            {
                "ply": int(row["ply"]),
                "move_index": int(row["move_index"]),
                "uci": row.get("uci"),
                "san": row.get("san"),
            }
        )
    return evidence


def _event_confidence(severity_score: float) -> float:
    return round(_clamp(0.45 + 0.55 * (severity_score / 100.0), 0.0, 1.0), 2)


def _severity_score(row: dict[str, Any], crossed_bucket: bool) -> float:
    cp_loss = float(row.get("cp_loss") or 0)
    swing = float(abs(row.get("delta") or 0))
    severity = 100.0 * (
        0.5 * _norm(swing, CFG.major_swing_cp, CFG.decisive_adv_cp * 2)
        + 0.3 * _norm(cp_loss, CFG.mistake_cp_loss, CFG.blunder_cp_loss * 2)
        + 0.2 * (1.0 if crossed_bucket else 0.6)
    )
    return round(_clamp(severity, 0.0, 100.0), 1)


def _crosses_advantage_boundary(before_cp: int, after_cp: int) -> bool:
    return (
        (before_cp >= CFG.clear_adv_cp and after_cp <= -CFG.equal_band_cp)
        or (before_cp <= -CFG.clear_adv_cp and after_cp >= CFG.equal_band_cp)
    )


def _dedupe_local_max(candidates: list[dict[str, Any]], window: int = 2) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for event in sorted(
        candidates,
        key=lambda e: (float(e.get("priority", 0.0)), int(e.get("swing_abs", 0))),
        reverse=True,
    ):
        if any(abs(event["ply"] - keep["ply"]) <= window for keep in selected):
            continue
        selected.append(event)
    return selected


def _detect_turning_points(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        bucket_before = _bucket(int(row["eval_before"]))
        bucket_after = _bucket(int(row["eval_after"]))
        crossed_bucket = bucket_before != bucket_after
        tactical_reason = _tactical_turning_reason(row.get("tactical"))
        tactical_swing_floor = max(80, int(round(CFG.major_swing_cp * 0.6)))

        is_candidate = (
            (abs(int(row["delta"])) >= CFG.major_swing_cp and crossed_bucket)
            or abs(int(row["delta"])) >= CFG.critical_swing_cp
            or _is_blunder(row)
            or (
                tactical_reason is not None
                and (
                    abs(int(row["delta"])) >= tactical_swing_floor
                    or _is_mistake_or_worse(row)
                )
            )
        )
        if not is_candidate:
            continue

        severity = _severity_score(row, crossed_bucket)
        if tactical_reason:
            severity = min(100.0, severity + 6.0)
        label = "Turning Point"
        if tactical_reason:
            if tactical_reason["type"] == "HANGING_PIECE":
                label = "Hanging Piece Turning Point"
            elif tactical_reason["type"] in {"FORCED_MATE", "MISSED_FORCED_MATE"}:
                label = "Mate Turning Point"
            elif tactical_reason["type"] == "SKEWER":
                label = "Skewer Turning Point"
            elif tactical_reason["type"] in {"FORK", "DOUBLE_ATTACK"}:
                label = "Tactical Turning Point"

        event = {
            "event_id": f"tp-{row['ply']}",
            "label_enum": "turning_point",
            "label": label,
            "ply": int(row["ply"]),
            "actor": row["actor"],
            "phase": row["phase"],
            "pre_eval_cp": int(row["eval_before"]),
            "post_eval_cp": int(row["eval_after"]),
            "swing_cp": int(row["delta"]),
            "swing_abs": int(row["swing_abs"]),
            "severity": "critical" if abs(int(row["delta"])) >= CFG.critical_swing_cp else "major",
            "severity_score": severity,
            "priority": severity,
            "is_decisive": False,
            "anchor": _anchor(row),
            "confidence": _event_confidence(severity),
            "evidence": _evidence_from_rows([row], max_items=1),
            "_crossed_adv_boundary": _crosses_advantage_boundary(
                int(row["eval_before"]), int(row["eval_after"])
            ),
        }
        if tactical_reason:
            event["tactical"] = tactical_reason
        candidates.append(event)

    deduped = _dedupe_local_max(candidates, window=2)
    ranked_by_severity = sorted(deduped, key=lambda e: e["priority"], reverse=True)

    decisive_threshold = max(
        CFG.critical_swing_cp, int(round(CFG.decisive_turning_ratio * CFG.decisive_adv_cp))
    )
    decisive_idx: int | None = None
    for idx, event in enumerate(ranked_by_severity):
        if (
            abs(int(event["swing_cp"])) >= decisive_threshold
            and (event["_crossed_adv_boundary"] or event["severity_score"] >= 85)
        ):
            decisive_idx = idx
            break

    if decisive_idx is not None:
        ranked_by_severity[decisive_idx]["is_decisive"] = True
        ranked_by_severity[decisive_idx]["label_enum"] = "decisive_turning_point"
        ranked_by_severity[decisive_idx]["label"] = "Decisive Turning Point"

    # Surface turning points in board move order for reliable narration/jump UX.
    ordered = sorted(ranked_by_severity, key=lambda e: int(e["ply"]))

    for event in ordered:
        event.pop("_crossed_adv_boundary", None)
        event.pop("swing_abs", None)

    support = min(1.0, len(ordered) / 3.0)
    confidence = round(_clamp(0.45 + 0.45 * support, 0.0, 1.0), 2)

    return {
        "label_enum": "turning_points",
        "confidence": confidence,
        "events": ordered,
    }


def _detect_missed_winning_chances(rows: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    user_rows = [row for row in rows if row["actor"] == "user"]

    for row in user_rows:
        if int(row["eval_before"]) < CFG.winning_adv_cp:
            continue

        severe = _is_mistake_or_worse(row)
        drop_condition = (
            int(row["eval_after"]) < CFG.clear_adv_cp
            or int(row["delta"]) <= -CFG.major_swing_cp
            or severe
        )
        if not drop_condition:
            continue

        lost_advantage_cp = max(0, int(row["eval_before"]) - int(row["eval_after"]))
        crossed_bucket = _bucket(int(row["eval_before"])) != _bucket(int(row["eval_after"]))
        severity = _severity_score(row, crossed_bucket)
        is_blunder = _is_blunder(row)
        events.append(
            {
                "event_id": f"missed-{row['ply']}",
                "label_enum": "missed_win",
                "label": "Winning Blunder" if is_blunder else "Winning Edge Dropped",
                "ply": int(row["ply"]),
                "phase": row["phase"],
                "lost_advantage_cp": int(lost_advantage_cp),
                "delta_cp": int(row["delta"]),
                "severity_score": severity,
                "anchor": _anchor(row),
                "confidence": _event_confidence(severity),
                "evidence": _evidence_from_rows([row], max_items=1),
            }
        )

    events = sorted(events, key=lambda e: e["severity_score"], reverse=True)[:5]
    confidence = round(_clamp(0.35 + 0.5 * min(1.0, len(events) / 3.0), 0.0, 1.0), 2)

    return {
        "label_enum": "missed_winning_chances",
        "count": len(events),
        "confidence": confidence,
        "events": events,
    }


def _detect_got_away_with_it(rows: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    for idx, row in enumerate(rows):
        if row["actor"] != "user":
            continue
        if not _is_mistake_or_worse(row):
            continue
        if int(row["eval_before"]) <= -CFG.clear_adv_cp:
            continue

        if idx + 1 >= len(rows):
            continue
        next_row = rows[idx + 1]
        if next_row["actor"] != "opponent":
            continue

        expected_damage = max(int(row.get("cp_loss") or 0), max(0, int(row["eval_before"]) - int(row["eval_after"])))
        if expected_damage <= 0:
            continue

        damage_after_reply = max(0, int(row["eval_before"]) - int(next_row["eval_after"]))
        persisted_ratio = damage_after_reply / max(expected_damage, 1)

        if persisted_ratio > CFG.got_away_persisted_ratio:
            continue

        recovered = max(0, expected_damage - damage_after_reply)
        severity = round(
            _clamp(
                100 * _norm(recovered, CFG.mistake_cp_loss, CFG.blunder_cp_loss * 2),
                0,
                100,
            ),
            1,
        )

        events.append(
            {
                "event_id": f"gwa-{row['ply']}",
                "label_enum": "got_away_with_it",
                "label": "Punish Escaped",
                "ply": int(row["ply"]),
                "phase": row["phase"],
                "cp_loss": int(row.get("cp_loss") or expected_damage),
                "persisted_ratio": round(persisted_ratio, 3),
                "severity_score": severity,
                "anchor": _anchor(row),
                "confidence": _event_confidence(severity),
                "evidence": _evidence_from_rows([row, next_row], max_items=2),
            }
        )

    events = sorted(events, key=lambda e: e["severity_score"], reverse=True)[:5]
    confidence = round(_clamp(0.3 + 0.5 * min(1.0, len(events) / 3.0), 0.0, 1.0), 2)

    return {
        "label_enum": "got_away_with_it",
        "count": len(events),
        "confidence": confidence,
        "events": events,
    }


def _score_conversion(rows: list[dict[str, Any]], result: str) -> dict[str, Any]:
    opportunities = [
        row
        for row in rows
        if row["actor"] == "user" and int(row["eval_before"]) >= CFG.clear_adv_cp
    ]

    if not opportunities:
        return {
            "label_enum": "unavailable",
            "available": False,
            "reason": "never_significantly_ahead",
            "score": None,
            "grade": "N/A",
            "rating_5": None,
            "opportunities": 0,
            "hold_rate": 0.0,
            "drop_to_equal_rate": 0.0,
            "severe_error_rate": 0.0,
            "impact": 0,
            "confidence": 0.0,
            "evidence": [],
        }

    hold_rate = sum(
        1 for row in opportunities if int(row["eval_after"]) >= (CFG.clear_adv_cp - CFG.equal_band_cp)
    ) / len(opportunities)
    drop_to_equal_rate = sum(
        1 for row in opportunities if abs(int(row["eval_after"])) < CFG.equal_band_cp
    ) / len(opportunities)
    severe_error_rate = sum(1 for row in opportunities if _is_mistake_or_worse(row)) / len(opportunities)

    score = 100 * (
        0.5 * hold_rate + 0.3 * (1 - drop_to_equal_rate) + 0.2 * (1 - severe_error_rate)
    )

    had_winning_adv = any(int(row["eval_before"]) >= CFG.winning_adv_cp for row in opportunities)
    if result == "win":
        score += 8
    elif had_winning_adv and result != "win":
        score -= 12

    score = round(_clamp(score, 0, 100), 1)
    grade = _score_to_grade(score)

    if had_winning_adv and result != "win" and score < 70:
        label_enum = "conversion_failure"
        impact = -int(round((80 - score) * 2))
    elif result == "win" and score >= 70:
        label_enum = "strong_conversion"
        impact = int(round((score - 60) * 2))
    else:
        label_enum = "conversion_mixed"
        impact = int(round((score - 60) * 0.6))

    severe_rows = sorted(
        [row for row in opportunities if _is_mistake_or_worse(row)],
        key=lambda row: abs(int(row["delta"])),
        reverse=True,
    )
    evidence_rows = severe_rows if severe_rows else sorted(
        opportunities,
        key=lambda row: abs(int(row["delta"])),
        reverse=True,
    )

    confidence = round(
        _clamp(0.4 + 0.4 * min(1.0, len(opportunities) / 10.0) + 0.2 * abs(score - 50) / 50, 0, 1),
        2,
    )

    return {
        "label_enum": label_enum,
        "available": True,
        "score": score,
        "grade": grade,
        "rating_5": _score_to_rating_5(score),
        "opportunities": len(opportunities),
        "hold_rate": round(hold_rate, 3),
        "drop_to_equal_rate": round(drop_to_equal_rate, 3),
        "severe_error_rate": round(severe_error_rate, 3),
        "impact": impact,
        "confidence": confidence,
        "evidence": _evidence_from_rows(evidence_rows, max_items=3),
    }


def _recovery_ratio(opportunities: list[dict[str, Any]], rows: list[dict[str, Any]], horizon: int = 6) -> float:
    by_ply = {int(row["ply"]): row for row in rows}
    recoveries = 0
    for row in opportunities:
        start_eval = int(row["eval_before"])
        best_eval = int(row["eval_after"])
        for step in range(1, horizon + 1):
            nxt = by_ply.get(int(row["ply"]) + step)
            if not nxt:
                break
            best_eval = max(best_eval, int(nxt["eval_after"]))
        if best_eval >= max(-CFG.equal_band_cp, start_eval + CFG.major_swing_cp):
            recoveries += 1
    return recoveries / len(opportunities) if opportunities else 0.0


def _score_resilience(rows: list[dict[str, Any]], result: str) -> dict[str, Any]:
    opportunities = [
        row
        for row in rows
        if row["actor"] == "user" and int(row["eval_before"]) <= -CFG.clear_adv_cp
    ]

    if not opportunities:
        return {
            "label_enum": "unavailable",
            "available": False,
            "reason": "never_significantly_worse",
            "score": None,
            "grade": "N/A",
            "rating_5": None,
            "defense_opportunities": 0,
            "stabilization_rate": 0.0,
            "recovery_ratio": 0.0,
            "severe_error_rate": 0.0,
            "impact": 0,
            "confidence": 0.0,
            "evidence": [],
        }

    stabilization_rate = sum(1 for row in opportunities if int(row["delta"]) >= 0) / len(opportunities)
    recovery_ratio = _recovery_ratio(opportunities, rows)
    severe_error_rate = sum(1 for row in opportunities if _is_mistake_or_worse(row)) / len(opportunities)

    score = 100 * (
        0.45 * stabilization_rate + 0.35 * recovery_ratio + 0.20 * (1 - severe_error_rate)
    )

    min_eval = min(int(row["eval_before"]) for row in opportunities)
    if min_eval <= -CFG.winning_adv_cp and result in {"draw", "win"}:
        score += 12
    elif result == "loss":
        score -= 6

    score = round(_clamp(score, 0, 100), 1)
    grade = _score_to_grade(score)

    if result in {"draw", "win"} and score >= 60 and min_eval <= -CFG.winning_adv_cp:
        label_enum = "resilience_success"
        impact = int(round((score - 55) * 2))
    elif result == "loss" and score < 50:
        label_enum = "resilience_failure"
        impact = -int(round((70 - score) * 1.5))
    else:
        label_enum = "resilience_mixed"
        impact = int(round((score - 60) * 0.5))

    evidence_rows = sorted(opportunities, key=lambda row: abs(int(row["delta"])), reverse=True)

    confidence = round(
        _clamp(0.4 + 0.4 * min(1.0, len(opportunities) / 10.0) + 0.2 * abs(score - 50) / 50, 0, 1),
        2,
    )

    return {
        "label_enum": label_enum,
        "available": True,
        "score": score,
        "grade": grade,
        "rating_5": _score_to_rating_5(score),
        "defense_opportunities": len(opportunities),
        "stabilization_rate": round(stabilization_rate, 3),
        "recovery_ratio": round(recovery_ratio, 3),
        "severe_error_rate": round(severe_error_rate, 3),
        "impact": impact,
        "confidence": confidence,
        "evidence": _evidence_from_rows(evidence_rows, max_items=3),
    }


def _detect_time_pressure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    user_rows = [row for row in rows if row["actor"] == "user"]
    if not user_rows:
        return {
            "label_enum": "time_pressure_collapse",
            "status": "unavailable",
            "status_reason": "no_user_moves",
            "low_time_threshold_s": None,
            "low_time_moves": 0,
            "normal_time_moves": 0,
            "avg_cp_low": None,
            "avg_cp_normal": None,
            "cp_drop": None,
            "blunder_rate_low": None,
            "blunder_rate_normal": None,
            "blunder_delta": None,
            "critical_low_time_swings": 0,
            "data_quality": {
                "user_moves": 0,
                "clock_moves": 0,
                "time_spent_moves": 0,
                "missing_time_moves": 0,
            },
            "impact": 0,
            "confidence": 0.0,
            "evidence": [],
        }

    clock_rows = [row for row in user_rows if isinstance(row.get("clock_seconds"), int)]
    time_spent_rows = [row for row in user_rows if isinstance(row.get("time_spent_seconds"), int)]
    data_quality = {
        "user_moves": len(user_rows),
        "clock_moves": len(clock_rows),
        "time_spent_moves": len(time_spent_rows),
        "missing_time_moves": len(user_rows) - len(clock_rows),
    }

    if not clock_rows:
        return {
            "label_enum": "time_pressure_collapse",
            "status": "unavailable",
            "status_reason": "no_clock_data",
            "low_time_threshold_s": None,
            "low_time_moves": 0,
            "normal_time_moves": 0,
            "avg_cp_low": None,
            "avg_cp_normal": None,
            "cp_drop": None,
            "blunder_rate_low": None,
            "blunder_rate_normal": None,
            "blunder_delta": None,
            "critical_low_time_swings": 0,
            "data_quality": data_quality,
            "impact": 0,
            "confidence": 0.0,
            "evidence": [],
        }

    coverage = len(clock_rows) / max(1, len(user_rows))
    if coverage < CFG.min_clock_coverage:
        return {
            "label_enum": "time_pressure_collapse",
            "status": "insufficient_data",
            "status_reason": "clock_coverage_too_low",
            "low_time_threshold_s": None,
            "low_time_moves": 0,
            "normal_time_moves": 0,
            "avg_cp_low": None,
            "avg_cp_normal": None,
            "cp_drop": None,
            "blunder_rate_low": None,
            "blunder_rate_normal": None,
            "blunder_delta": None,
            "critical_low_time_swings": 0,
            "data_quality": data_quality,
            "impact": 0,
            "confidence": round(_clamp(0.2 + 0.5 * coverage, 0, 1), 2),
            "evidence": [],
        }

    initial_clock = max(int(row["clock_seconds"]) for row in clock_rows)
    low_time_threshold_s = max(CFG.low_time_floor_seconds, int(initial_clock * CFG.low_time_ratio))

    low_rows = [row for row in clock_rows if int(row["clock_seconds"]) <= low_time_threshold_s]
    normal_rows = [row for row in clock_rows if int(row["clock_seconds"]) > low_time_threshold_s]

    if len(low_rows) < CFG.min_low_time_samples or len(normal_rows) < CFG.min_low_time_samples:
        reason = "low_time_window_too_small" if len(low_rows) < CFG.min_low_time_samples else "normal_time_window_too_small"
        return {
            "label_enum": "time_pressure_collapse",
            "status": "insufficient_data",
            "status_reason": reason,
            "low_time_threshold_s": low_time_threshold_s,
            "low_time_moves": len(low_rows),
            "normal_time_moves": len(normal_rows),
            "avg_cp_low": None,
            "avg_cp_normal": None,
            "cp_drop": None,
            "blunder_rate_low": None,
            "blunder_rate_normal": None,
            "blunder_delta": None,
            "critical_low_time_swings": 0,
            "data_quality": data_quality,
            "impact": 0,
            "confidence": round(_clamp(0.3 + 0.4 * coverage, 0, 1), 2),
            "evidence": _evidence_from_rows(low_rows, max_items=2),
        }

    def _cp_loss_for_row(row: dict[str, Any]) -> int:
        if isinstance(row.get("cp_loss"), int):
            return int(row["cp_loss"])
        return max(0, -int(row["delta"]))

    avg_cp_low = _mean([_cp_loss_for_row(row) for row in low_rows])
    avg_cp_normal = _mean([_cp_loss_for_row(row) for row in normal_rows])
    cp_drop = avg_cp_low - avg_cp_normal

    blunder_rate_low = _mean([1.0 if _is_blunder(row) else 0.0 for row in low_rows])
    blunder_rate_normal = _mean([1.0 if _is_blunder(row) else 0.0 for row in normal_rows])
    blunder_delta = blunder_rate_low - blunder_rate_normal

    critical_low_time_swings = sum(
        1 for row in low_rows if int(row["delta"]) <= -CFG.critical_swing_cp
    )

    cp_drop_threshold = max(40.0, 0.5 * max(1.0, avg_cp_normal))
    detected = (
        cp_drop >= cp_drop_threshold
        and blunder_delta >= 0.15
        and critical_low_time_swings >= 1
    )

    if detected:
        status = "detected"
        status_reason = "signal_threshold_met"
        impact = -int(round(cp_drop + (blunder_delta * 100) + (critical_low_time_swings * 30)))
    else:
        status = "not_detected"
        status_reason = "signal_threshold_not_met"
        impact = 0

    sample_factor = min(1.0, len(low_rows) / (CFG.min_low_time_samples + 2)) * min(
        1.0, len(normal_rows) / (CFG.min_low_time_samples + 2)
    )
    signal_factor = _clamp(cp_drop / max(1.0, cp_drop_threshold * 2), 0.0, 1.0)
    confidence = round(
        _clamp(0.35 + 0.30 * coverage + 0.20 * sample_factor + 0.15 * signal_factor, 0, 1),
        2,
    )

    evidence_rows = sorted(
        low_rows,
        key=lambda row: (abs(int(row["delta"])), int(row.get("cp_loss") or 0)),
        reverse=True,
    )

    return {
        "label_enum": "time_pressure_collapse",
        "status": status,
        "status_reason": status_reason,
        "low_time_threshold_s": low_time_threshold_s,
        "low_time_moves": len(low_rows),
        "normal_time_moves": len(normal_rows),
        "avg_cp_low": round(avg_cp_low, 1),
        "avg_cp_normal": round(avg_cp_normal, 1),
        "cp_drop": round(cp_drop, 1),
        "blunder_rate_low": round(blunder_rate_low, 3),
        "blunder_rate_normal": round(blunder_rate_normal, 3),
        "blunder_delta": round(blunder_delta, 3),
        "critical_low_time_swings": int(critical_low_time_swings),
        "data_quality": data_quality,
        "impact": impact,
        "confidence": confidence,
        "evidence": _evidence_from_rows(evidence_rows, max_items=3),
    }


def _grade_phases(rows: list[dict[str, Any]]) -> dict[str, Any]:
    phases = ["opening", "middlegame", "endgame"]
    result: dict[str, Any] = {"label_enum": "phase_grades"}

    for phase in phases:
        phase_rows = [row for row in rows if row["actor"] == "user" and row["phase"] == phase]

        if len(phase_rows) == 0:
            result[phase] = {
                "score": None,
                "grade": "N/A",
                "rating_5": None,
                "evaluation_state": "not_reached",
                "confidence": 0.0,
            }
            continue

        if len(phase_rows) < CFG.min_phase_moves:
            result[phase] = {
                "score": None,
                "grade": "N/A",
                "rating_5": None,
                "evaluation_state": "too_short",
                "confidence": round(_clamp(0.2 + 0.1 * len(phase_rows), 0, 1), 2),
            }
            continue

        cp_losses = [int(row.get("cp_loss") or 0) for row in phase_rows]
        avg_cp = _mean(cp_losses)
        mistake_rate = _mean([1.0 if _is_mistake_or_worse(row) else 0.0 for row in phase_rows])
        blunder_rate = _mean([1.0 if _is_blunder(row) else 0.0 for row in phase_rows])
        net_delta = _mean([float(row["delta"]) for row in phase_rows])

        score = _clamp(
            100 - 0.20 * avg_cp - 30 * mistake_rate - 60 * blunder_rate + 0.04 * net_delta,
            0,
            100,
        )

        result[phase] = {
            "score": round(score, 1),
            "grade": _score_to_grade(score),
            "rating_5": _score_to_rating_5(score),
            "evaluation_state": "scored",
            "confidence": round(_clamp(0.45 + 0.45 * min(1.0, len(phase_rows) / 10.0), 0, 1), 2),
        }

    return result


def _detect_decisive_phase(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, dict[str, float]] = {
        "opening": {"net_shift": 0.0, "volatility": 0.0, "severe_errors": 0.0, "score": 0.0},
        "middlegame": {"net_shift": 0.0, "volatility": 0.0, "severe_errors": 0.0, "score": 0.0},
        "endgame": {"net_shift": 0.0, "volatility": 0.0, "severe_errors": 0.0, "score": 0.0},
    }

    for row in rows:
        phase = row["phase"]
        phase_row = stats[phase]
        phase_row["net_shift"] += float(row["delta"])
        phase_row["volatility"] += float(abs(int(row["delta"])))
        if _is_mistake_or_worse(row):
            phase_row["severe_errors"] += 1

    for phase in stats:
        phase_stats = stats[phase]
        phase_stats["score"] = (
            abs(phase_stats["net_shift"])
            + 0.3 * phase_stats["volatility"]
            + 60.0 * phase_stats["severe_errors"]
        )

    total_score = sum(item["score"] for item in stats.values())
    best_phase = max(stats.keys(), key=lambda phase: stats[phase]["score"])
    share = stats[best_phase]["score"] / max(total_score, 1.0)

    decisive_phase = best_phase if share >= 0.40 else "mixed"

    return {
        "label_enum": "decisive_phase",
        "decisive_phase": decisive_phase,
        "phase_scores": {
            phase: {
                "net_shift": round(values["net_shift"], 1),
                "volatility": round(values["volatility"], 1),
                "severe_errors": int(values["severe_errors"]),
                "score": round(values["score"], 1),
            }
            for phase, values in stats.items()
        },
        "confidence": round(_clamp(0.45 + 0.5 * share, 0, 1), 2),
    }


def _count_lead_changes(rows: list[dict[str, Any]]) -> int:
    states: list[int] = []
    for row in rows:
        value = int(row["eval_after"])
        if value > CFG.equal_band_cp:
            states.append(1)
        elif value < -CFG.equal_band_cp:
            states.append(-1)
        else:
            states.append(0)

    changes = 0
    prev = 0
    for state in states:
        if state == 0:
            continue
        if prev != 0 and state != prev:
            changes += 1
        prev = state
    return changes


def _classify_game_character(
    rows: list[dict[str, Any]],
    result: str,
    conversion: dict[str, Any],
    resilience: dict[str, Any],
) -> dict[str, Any]:
    abs_deltas = [abs(int(row["delta"])) for row in rows]
    volatility_cp = round(float(median(abs_deltas)) if abs_deltas else 0.0, 1)
    p90_swing = round(_p90([float(v) for v in abs_deltas]), 1)
    lead_changes = _count_lead_changes(rows)

    severe_events = [row for row in rows if _is_mistake_or_worse(row)]
    severe_density = len(severe_events) / max(1, len(rows))

    user_rows = [row for row in rows if row["actor"] == "user"]
    user_cp_losses = [int(row.get("cp_loss") or 0) for row in user_rows if row.get("cp_loss") is not None]
    user_acpl = round(_mean([float(loss) for loss in user_cp_losses]), 1) if user_cp_losses else 0.0
    user_blunders = sum(1 for row in user_rows if _is_blunder(row))

    phase_means: list[float] = []
    for phase in ["opening", "middlegame", "endgame"]:
        phase_rows = [row for row in rows if row["phase"] == phase]
        if phase_rows:
            phase_means.append(_mean([float(row["delta"]) for row in phase_rows]))
    phase_stability = round(_mean([abs(value) for value in phase_means]), 1) if phase_means else 0.0

    max_eval = max((int(row["eval_before"]) for row in user_rows), default=0)
    min_eval = min((int(row["eval_before"]) for row in user_rows), default=0)

    label_enum = "stable"
    confidence = 0.68

    if (
        max_eval >= CFG.winning_adv_cp
        and result != "win"
        and conversion.get("label_enum") == "conversion_failure"
    ):
        label_enum = "advantage_lost"
        confidence = 0.82
    elif (
        min_eval <= -CFG.winning_adv_cp
        and result in {"draw", "win"}
        and resilience.get("label_enum") == "resilience_success"
    ):
        label_enum = "defensive_grind"
        confidence = 0.82
    elif volatility_cp >= CFG.major_swing_cp and lead_changes >= 2 and severe_density >= 0.12:
        label_enum = "chaotic"
        confidence = 0.80
    elif volatility_cp >= CFG.major_swing_cp and p90_swing >= CFG.critical_swing_cp:
        label_enum = "sharp"
        confidence = 0.77
    elif user_acpl <= 45 and severe_density < 0.06 and volatility_cp < (CFG.major_swing_cp / 2):
        label_enum = "technical"
        confidence = 0.78
    elif volatility_cp < (CFG.major_swing_cp * 0.75) and lead_changes <= 1 and severe_density < 0.10:
        label_enum = "controlled"
        confidence = 0.75
    elif volatility_cp >= (CFG.major_swing_cp * 0.75) or lead_changes >= 2:
        label_enum = "volatile"
        confidence = 0.72
    else:
        label_enum = "stable"
        confidence = 0.70

    label, sublabel = CHARACTER_LABELS[label_enum]

    return {
        "label_enum": label_enum,
        "label": label,
        "sublabel": sublabel,
        "confidence": round(_clamp(confidence, 0, 1), 2),
        "metrics": {
            "volatility_cp": volatility_cp,
            "p90_swing_cp": p90_swing,
            "lead_changes": int(lead_changes),
            "user_acpl": user_acpl,
            "user_blunders": int(user_blunders),
            "phase_stability": phase_stability,
            "severe_density": round(severe_density, 3),
        },
        "evidence": _evidence_from_rows(
            sorted(rows, key=lambda row: abs(int(row["delta"])), reverse=True),
            max_items=3,
        ),
    }


def _cause_order_for_result(result: str) -> list[str]:
    if result == "loss":
        return [
            "conversion_failure",
            "time_pressure_collapse",
            "resilience_failure",
            "self_errors",
            "opponent_errors",
        ]
    if result == "win":
        return [
            "resilience_success",
            "strong_conversion",
            "opponent_errors",
            "self_errors",
            "time_pressure_collapse",
        ]
    return [
        "conversion_failure",
        "resilience_success",
        "balanced_draw",
        "self_errors",
        "opponent_errors",
        "time_pressure_collapse",
    ]


def _build_result_cause(
    rows: list[dict[str, Any]],
    result: str,
    conversion: dict[str, Any],
    resilience: dict[str, Any],
    time_pressure: dict[str, Any],
    turning_points: dict[str, Any],
) -> dict[str, Any]:
    user_severe = [
        row for row in rows if row["actor"] == "user" and _is_mistake_or_worse(row) and int(row["delta"]) < 0
    ]
    opp_severe = [
        row for row in rows if row["actor"] == "opponent" and _is_mistake_or_worse(row) and int(row["delta"]) > 0
    ]

    self_errors_impact = int(round(sum(float(row["delta"]) for row in user_severe)))
    opponent_errors_impact = int(round(sum(float(row["delta"]) for row in opp_severe)))

    impacts = {
        "self_errors": self_errors_impact,
        "opponent_errors": opponent_errors_impact,
        "conversion": int(conversion.get("impact") or 0),
        "resilience": int(resilience.get("impact") or 0),
        "time_pressure": int(time_pressure.get("impact") or 0),
    }

    final_eval = int(rows[-1]["eval_after"]) if rows else 0

    valid: dict[str, bool] = {
        "conversion_failure": conversion.get("label_enum") == "conversion_failure",
        "strong_conversion": conversion.get("label_enum") == "strong_conversion",
        "resilience_success": resilience.get("label_enum") == "resilience_success",
        "resilience_failure": resilience.get("label_enum") == "resilience_failure",
        "time_pressure_collapse": time_pressure.get("status") == "detected",
        "self_errors": abs(self_errors_impact) >= CFG.major_swing_cp,
        "opponent_errors": abs(opponent_errors_impact) >= CFG.major_swing_cp,
        "balanced_draw": (
            result == "draw"
            and abs(final_eval) <= CFG.equal_band_cp
            and len(turning_points.get("events", [])) <= 1
        ),
    }

    cause_to_impact = {
        "conversion_failure": abs(int(conversion.get("impact") or 0)),
        "strong_conversion": abs(int(conversion.get("impact") or 0)),
        "resilience_success": abs(int(resilience.get("impact") or 0)),
        "resilience_failure": abs(int(resilience.get("impact") or 0)),
        "time_pressure_collapse": abs(int(time_pressure.get("impact") or 0)),
        "self_errors": abs(self_errors_impact),
        "opponent_errors": abs(opponent_errors_impact),
        "balanced_draw": 50 if valid["balanced_draw"] else 0,
    }

    cause_to_evidence = {
        "conversion_failure": conversion.get("evidence", []),
        "strong_conversion": conversion.get("evidence", []),
        "resilience_success": resilience.get("evidence", []),
        "resilience_failure": resilience.get("evidence", []),
        "time_pressure_collapse": time_pressure.get("evidence", []),
        "self_errors": _evidence_from_rows(
            sorted(user_severe, key=lambda row: abs(int(row["delta"])), reverse=True),
            max_items=3,
        ),
        "opponent_errors": _evidence_from_rows(
            sorted(opp_severe, key=lambda row: abs(int(row["delta"])), reverse=True),
            max_items=3,
        ),
        "balanced_draw": [
            {
                "ply": int(rows[-1]["ply"]),
                "move_index": int(rows[-1]["move_index"]),
                "uci": rows[-1].get("uci"),
                "san": rows[-1].get("san"),
            }
        ]
        if rows
        else [],
    }

    ordered = _cause_order_for_result(result)
    primary = None
    secondary = None

    for code in ordered:
        if valid.get(code, False):
            primary = code
            break

    if primary is None:
        if result == "loss":
            primary = "self_errors"
        elif result == "win":
            primary = "opponent_errors"
        else:
            primary = "balanced_draw"

    for code in ordered:
        if code == primary:
            continue
        if valid.get(code, False):
            secondary = code
            break

    if secondary is None:
        secondary = "self_errors" if primary != "self_errors" else "opponent_errors"

    primary_impact = float(cause_to_impact.get(primary, 0))
    impact_sum = float(sum(abs(v) for v in impacts.values())) + 1.0
    dominance = primary_impact / impact_sum
    support = min(1.0, len(cause_to_evidence.get(primary, [])) / 3.0)
    confidence = round(_clamp(0.45 + 0.35 * dominance + 0.20 * support, 0, 1), 2)

    return {
        "label_enum": "result_cause",
        "primary_reason_code": primary,
        "secondary_reason_code": secondary,
        "primary_label": CAUSE_LABELS.get(primary, primary.replace("_", " ").title()),
        "secondary_label": CAUSE_LABELS.get(secondary, secondary.replace("_", " ").title()),
        "cause_hierarchy_version": "result_cause_v2",
        "factor_impacts": {
            "self_errors": int(self_errors_impact),
            "opponent_errors": int(opponent_errors_impact),
            "conversion": int(conversion.get("impact") or 0),
            "resilience": int(resilience.get("impact") or 0),
            "time_pressure": int(time_pressure.get("impact") or 0),
        },
        "confidence": confidence,
        "evidence": cause_to_evidence.get(primary, []),
    }


def _cards(
    result_cause: dict[str, Any],
    turning_points: dict[str, Any],
    missed: dict[str, Any],
    got_away: dict[str, Any],
    conversion: dict[str, Any],
    resilience: dict[str, Any],
    time_pressure: dict[str, Any],
    game_character: dict[str, Any],
) -> dict[str, Any]:
    return {
        "result_cause": {
            "label_enum": result_cause.get("primary_reason_code"),
            "confidence": result_cause.get("confidence", 0.0),
            "evidence": result_cause.get("evidence", []),
        },
        "turning_points": {
            "label_enum": "turning_points",
            "confidence": turning_points.get("confidence", 0.0),
            "evidence": turning_points.get("events", [])[0:3],
        },
        "missed_winning_chances": {
            "label_enum": "missed_winning_chances",
            "confidence": missed.get("confidence", 0.0),
            "evidence": missed.get("events", [])[0:3],
        },
        "got_away_with_it": {
            "label_enum": "got_away_with_it",
            "confidence": got_away.get("confidence", 0.0),
            "evidence": got_away.get("events", [])[0:3],
        },
        "conversion_quality": {
            "label_enum": conversion.get("label_enum", "unavailable"),
            "confidence": conversion.get("confidence", 0.0),
            "evidence": conversion.get("evidence", []),
        },
        "resilience_quality": {
            "label_enum": resilience.get("label_enum", "unavailable"),
            "confidence": resilience.get("confidence", 0.0),
            "evidence": resilience.get("evidence", []),
        },
        "time_pressure_collapse": {
            "label_enum": time_pressure.get("status", "unavailable"),
            "confidence": time_pressure.get("confidence", 0.0),
            "evidence": time_pressure.get("evidence", []),
        },
        "game_character": {
            "label_enum": game_character.get("label_enum", "stable"),
            "confidence": game_character.get("confidence", 0.0),
            "evidence": game_character.get("evidence", []),
        },
    }


def _overall_confidence(
    result_cause: dict[str, Any],
    turning_points: dict[str, Any],
    conversion: dict[str, Any],
    resilience: dict[str, Any],
    phase_grades: dict[str, Any],
    time_pressure: dict[str, Any],
    game_character: dict[str, Any],
) -> float:
    weighted: list[tuple[float, float]] = [
        (float(result_cause.get("confidence", 0.0)), 1.8),
        (float(turning_points.get("confidence", 0.0)), 1.4),
        (float(conversion.get("confidence", 0.0)), 1.1),
        (float(resilience.get("confidence", 0.0)), 1.1),
        (float(game_character.get("confidence", 0.0)), 1.0),
    ]

    phase_conf = [
        float(phase_grades[phase].get("confidence", 0.0))
        for phase in ["opening", "middlegame", "endgame"]
        if isinstance(phase_grades.get(phase), dict)
    ]
    if phase_conf:
        weighted.append((_mean(phase_conf), 1.0))

    if time_pressure.get("status") not in {"unavailable", "insufficient_data"}:
        weighted.append((float(time_pressure.get("confidence", 0.0)), 0.8))

    score = sum(value * weight for value, weight in weighted)
    denom = sum(weight for _, weight in weighted)
    return round(_clamp(score / max(denom, 1e-9), 0, 1), 2)


def compute_single_game_insights(
    *,
    site: str,
    game_id: str,
    username: str,
    depth: int,
    multipv: int,
    full_analysis: dict[str, Any],
    game_meta: dict[str, Any],
) -> dict[str, Any]:
    moves = list(full_analysis.get("moves") or [])
    if not moves:
        return {
            "status": "ready",
            "version": VERSION,
            "analysis_ref": {
                "site": site,
                "game_id": game_id,
                "depth": depth,
                "multipv": multipv,
            },
            "cards": {},
            "result_cause": {
                "label_enum": "result_cause",
                "primary_reason_code": "balanced_draw",
                "secondary_reason_code": "self_errors",
                "primary_label": "Balanced Draw",
                "secondary_label": "Own Errors",
                "cause_hierarchy_version": "result_cause_v2",
                "factor_impacts": {
                    "self_errors": 0,
                    "opponent_errors": 0,
                    "conversion": 0,
                    "resilience": 0,
                    "time_pressure": 0,
                },
                "confidence": 0.0,
                "evidence": [],
            },
            "decisive_phase": {
                "label_enum": "decisive_phase",
                "decisive_phase": "mixed",
                "phase_scores": {},
                "confidence": 0.0,
            },
            "turning_points": {"label_enum": "turning_points", "confidence": 0.0, "events": []},
            "missed_winning_chances": {
                "label_enum": "missed_winning_chances",
                "count": 0,
                "confidence": 0.0,
                "events": [],
            },
            "got_away_with_it": {
                "label_enum": "got_away_with_it",
                "count": 0,
                "confidence": 0.0,
                "events": [],
            },
            "conversion_quality": {
                "label_enum": "unavailable",
                "available": False,
                "reason": "no_moves",
                "score": None,
                "grade": "N/A",
                "rating_5": None,
                "confidence": 0.0,
                "evidence": [],
            },
            "resilience_quality": {
                "label_enum": "unavailable",
                "available": False,
                "reason": "no_moves",
                "score": None,
                "grade": "N/A",
                "rating_5": None,
                "confidence": 0.0,
                "evidence": [],
            },
            "time_pressure_collapse": {
                "label_enum": "time_pressure_collapse",
                "status": "unavailable",
                "status_reason": "no_moves",
                "confidence": 0.0,
                "evidence": [],
            },
            "phase_grades": {
                "label_enum": "phase_grades",
                "opening": {
                    "score": None,
                    "grade": "N/A",
                    "rating_5": None,
                    "evaluation_state": "not_reached",
                    "confidence": 0.0,
                },
                "middlegame": {
                    "score": None,
                    "grade": "N/A",
                    "rating_5": None,
                    "evaluation_state": "not_reached",
                    "confidence": 0.0,
                },
                "endgame": {
                    "score": None,
                    "grade": "N/A",
                    "rating_5": None,
                    "evaluation_state": "not_reached",
                    "confidence": 0.0,
                },
            },
            "game_character": {
                "label_enum": "stable",
                "label": "Stable",
                "sublabel": "No evaluable move data",
                "confidence": 0.0,
                "metrics": {
                    "volatility_cp": 0.0,
                    "p90_swing_cp": 0.0,
                    "lead_changes": 0,
                    "user_acpl": 0.0,
                    "user_blunders": 0,
                    "phase_stability": 0.0,
                    "severe_density": 0.0,
                },
                "evidence": [],
            },
            "confidence": 0.0,
            "meta": {"phase_profile": {"opening_end_ply": 0, "endgame_start_ply": None, "total_plies": 0}},
        }

    user_color = str(game_meta.get("color") or "white").lower()
    result = str(game_meta.get("result") or "draw").lower()
    if result not in {"win", "loss", "draw"}:
        result = "draw"

    pgn_time_fallback = _extract_time_fallback_from_pgn(game_meta.get("pgn"))
    rows, phase_profile = _preprocess_rows(moves, user_color, pgn_time_fallback)

    turning_points = _detect_turning_points(rows)
    missed = _detect_missed_winning_chances(rows)
    got_away = _detect_got_away_with_it(rows)
    conversion = _score_conversion(rows, result)
    resilience = _score_resilience(rows, result)
    time_pressure = _detect_time_pressure(rows)
    phase_grades = _grade_phases(rows)
    decisive_phase = _detect_decisive_phase(rows)
    game_character = _classify_game_character(rows, result, conversion, resilience)
    result_cause = _build_result_cause(
        rows,
        result,
        conversion,
        resilience,
        time_pressure,
        turning_points,
    )

    cards = _cards(
        result_cause,
        turning_points,
        missed,
        got_away,
        conversion,
        resilience,
        time_pressure,
        game_character,
    )

    confidence = _overall_confidence(
        result_cause,
        turning_points,
        conversion,
        resilience,
        phase_grades,
        time_pressure,
        game_character,
    )

    return {
        "status": "ready",
        "version": VERSION,
        "analysis_ref": {
            "site": site,
            "game_id": game_id,
            "username": username,
            "depth": depth,
            "multipv": multipv,
        },
        "cards": cards,
        "result_cause": result_cause,
        "decisive_phase": decisive_phase,
        "turning_points": turning_points,
        "missed_winning_chances": missed,
        "got_away_with_it": got_away,
        "conversion_quality": conversion,
        "resilience_quality": resilience,
        "time_pressure_collapse": time_pressure,
        "phase_grades": phase_grades,
        "game_character": game_character,
        "confidence": confidence,
        "meta": {
            "phase_profile": phase_profile,
            "thresholds": {
                "equal_band_cp": CFG.equal_band_cp,
                "clear_adv_cp": CFG.clear_adv_cp,
                "winning_adv_cp": CFG.winning_adv_cp,
                "decisive_adv_cp": CFG.decisive_adv_cp,
                "major_swing_cp": CFG.major_swing_cp,
                "critical_swing_cp": CFG.critical_swing_cp,
                "mistake_cp_loss": CFG.mistake_cp_loss,
                "blunder_cp_loss": CFG.blunder_cp_loss,
                "low_time_ratio": CFG.low_time_ratio,
                "low_time_floor_seconds": CFG.low_time_floor_seconds,
            },
        },
    }
