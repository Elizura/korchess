"""Gemini-backed narration for single-game insights."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GAME_INSIGHTS_GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_TIMEOUT_S = max(3, int(os.environ.get("GAME_INSIGHTS_GEMINI_TIMEOUT_S", "30")))
GEMINI_MIN_INTERVAL_MS = max(0, int(os.environ.get("GAME_INSIGHTS_GEMINI_MIN_INTERVAL_MS", "1200")))
NARRATION_SCHEMA_VERSION = os.environ.get("GAME_INSIGHTS_NARRATION_SCHEMA_VERSION", "6").strip() or "6"
FALLBACK_RETRY_SECONDS = max(0, int(os.environ.get("GAME_INSIGHTS_FALLBACK_RETRY_S", "0")))

EXPECTED_SECTIONS = [
    "Result summary",
    "Turning points",
    "What you did well",
    "What to improve",
    "Next game focus",
]
DECISIVE_PHASE_VALUES = {"opening", "middlegame", "endgame", "unknown"}

_GEMINI_SEMAPHORE = threading.Semaphore(1)
_GEMINI_INTERVAL_LOCK = threading.Lock()
_LAST_GEMINI_CALL_MONOTONIC = 0.0
_TIMEOUT_RE = re.compile(r"(time\s*forfeit|won\s*on\s*time|timeout|time\s*out)", re.IGNORECASE)
_FORBIDDEN_JARGON_PATTERNS = [
    re.compile(r"\bcp\b", re.IGNORECASE),
    re.compile(r"\bcentipawn(?:s)?\b", re.IGNORECASE),
    re.compile(r"\beval(?:uation)?\s*(?:shift|shifted|from|to)?\s*[+-]?\d", re.IGNORECASE),
    re.compile(r"\bgrade\s*['\"]?[a-e]['\"]?\b", re.IGNORECASE),
    re.compile(r"\b[a-e]\s*grade\b", re.IGNORECASE),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else fallback
    return fallback


def _safe_parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _coerce_bullets(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    bullets: list[str] = []
    for item in value:
        text = _safe_text(item)
        if text:
            bullets.append(text)
    return bullets


def _score_to_rating_5(score: float | int | None) -> int | None:
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value >= 85:
        return 5
    if value >= 70:
        return 4
    if value >= 55:
        return 3
    if value >= 40:
        return 2
    return 1


def _grade_to_rating_5(grade: str | None) -> int | None:
    if not isinstance(grade, str):
        return None
    normalized = grade.strip().upper()
    mapping = {
        "A": 5,
        "B": 4,
        "C": 3,
        "D": 2,
        "E": 1,
    }
    return mapping.get(normalized)


def _ply_to_move_number(ply: Any) -> int | None:
    if not isinstance(ply, int):
        return None
    if ply <= 0:
        return None
    return max(1, (ply + 1) // 2)


def _phase_reason_hint(phase: str) -> str:
    phase_key = _safe_text(phase).lower()
    if phase_key == "opening":
        return "center control and development slipped"
    if phase_key == "middlegame":
        return "piece activity and king safety were compromised"
    if phase_key == "endgame":
        return "king activity and pawn coordination were suboptimal"
    return "critical threats were not addressed in time"


def _momentum_direction(event: dict[str, Any]) -> str:
    swing = event.get("swing_cp")
    if isinstance(swing, (int, float)):
        if swing > 0:
            return "for_user"
        if swing < 0:
            return "against_user"
    return "mixed"


def _impact_level(severity_score: Any) -> str:
    if isinstance(severity_score, (int, float)):
        if float(severity_score) >= 85:
            return "decisive"
        if float(severity_score) >= 65:
            return "major"
    return "moderate"


def _strip_terminal_punctuation(text: str) -> str:
    return text.strip().rstrip(".!?").strip()


def _sanitize_reason_hint(reason_hint: str, phase: str) -> str:
    cleaned = _strip_terminal_punctuation(reason_hint)
    if not cleaned:
        return _phase_reason_hint(phase)
    for pattern in _FORBIDDEN_JARGON_PATTERNS:
        if pattern.search(cleaned):
            return _phase_reason_hint(phase)
    return cleaned


def _turning_point_actor_phrase(actor: str, direction: str) -> str:
    if actor == "user":
        if direction == "for_user":
            return "your move created a clear opportunity"
        if direction == "against_user":
            return "your move weakened your position"
        return "your move shifted the balance of the position"
    if actor == "opponent":
        if direction == "for_user":
            return "your opponent's move gave you a clear opportunity"
        if direction == "against_user":
            return "your opponent's move increased pressure on your position"
        return "your opponent's move shifted the balance of the position"
    return "the move changed the balance of the position"


def build_turning_point_bullets_from_events(events: Any) -> list[str]:
    if not isinstance(events, list):
        events = []
    ordered_events = sorted(
        [event for event in events if isinstance(event, dict)],
        key=lambda event: int(event.get("ply", 10**9))
        if isinstance(event.get("ply"), int)
        else 10**9,
    )
    bullets: list[str] = []
    for event in ordered_events:
        ply = event.get("ply")
        if not isinstance(ply, int) or ply <= 0:
            continue
        move_number = _ply_to_move_number(ply)
        if move_number is None:
            continue

        actor = _safe_text(event.get("actor"), "user").lower()
        direction = _safe_text(event.get("momentum_direction"), _momentum_direction(event))
        reason_hint = _safe_text(event.get("reason_hint"), _event_reason_hint(event))
        reason_hint = _sanitize_reason_hint(reason_hint, _safe_text(event.get("phase"), ""))
        phrase = _turning_point_actor_phrase(actor, direction)
        decisive = bool(event.get("is_decisive")) or _safe_text(
            event.get("impact_level"), _impact_level(event.get("severity_score"))
        ) == "decisive"
        suffix = " This was a decisive turning point." if decisive else ""
        bullets.append(f"At move {move_number}, {phrase} because {reason_hint}.{suffix}")

    if bullets:
        return bullets
    return [
        "No decisive turning point stood out, so the game swung through several medium mistakes rather than one collapse."
    ]


def _event_reason_hint(event: dict[str, Any]) -> str:
    tactical = event.get("tactical")
    if isinstance(tactical, dict):
        tactical_reason = _safe_text(tactical.get("reason_text"))
        if tactical_reason:
            return tactical_reason
    label = _safe_text(event.get("label")).lower()
    if "mate" in label:
        return "king safety broke down around forcing checks"
    if "hanging" in label:
        return "piece safety was overlooked"
    if "tactical" in label or "fork" in label or "skewer" in label:
        return "a tactical threat was missed"
    return _phase_reason_hint(_safe_text(event.get("phase"), ""))


def enforce_turning_points_grounding(
    narration: dict[str, Any] | None,
    raw_insights: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = _normalize_narration(narration if isinstance(narration, dict) else {})
    turning_points = (
        raw_insights.get("turning_points")
        if isinstance(raw_insights, dict) and isinstance(raw_insights.get("turning_points"), dict)
        else {}
    )
    grounded_bullets = build_turning_point_bullets_from_events(turning_points.get("events"))

    sections: list[dict[str, Any]] = []
    replaced = False
    for section in normalized.get("sections", []):
        if not isinstance(section, dict):
            continue
        if _safe_text(section.get("heading")).casefold() == "turning points":
            sections.append({"heading": "Turning points", "bullets": grounded_bullets})
            replaced = True
            continue
        sections.append(section)
    if not replaced:
        sections.append({"heading": "Turning points", "bullets": grounded_bullets})
    normalized["sections"] = sections
    return normalized


def _format_phase_rating_bullet(phase_name: str, phase_entry: dict[str, Any]) -> str:
    state = _safe_text(phase_entry.get("evaluation_state"), "not_reached")
    if state == "not_reached":
        return f"{phase_name.capitalize()} did not have enough moves for a reliable phase rating."
    if state == "too_short":
        return f"{phase_name.capitalize()} was too short for a stable rating, so treat that phase signal as low confidence."
    rating = phase_entry.get("rating_5")
    if isinstance(rating, int):
        if rating >= 4:
            tone = "this phase was a strength overall"
        elif rating <= 2:
            tone = "this phase needs the most work"
        else:
            tone = "this phase was mixed and can be improved"
        return f"{phase_name.capitalize()} was rated {rating}/5, which means {tone}."
    return f"{phase_name.capitalize()} has limited evidence for a reliable rating."


def _iter_narration_text_chunks(narration: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    for key in ("title", "one_liner", "confidence_note"):
        text = _safe_text(narration.get(key))
        if text:
            chunks.append(text)
    sections = narration.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = _safe_text(section.get("heading"))
            if heading:
                chunks.append(heading)
            bullets = section.get("bullets")
            if isinstance(bullets, list):
                for bullet in bullets:
                    if isinstance(bullet, str) and bullet.strip():
                        chunks.append(bullet.strip())
    return chunks


def narration_has_forbidden_jargon(narration: dict[str, Any] | None) -> bool:
    if not isinstance(narration, dict):
        return True
    for chunk in _iter_narration_text_chunks(narration):
        for pattern in _FORBIDDEN_JARGON_PATTERNS:
            if pattern.search(chunk):
                return True
    return False


def is_current_clean_narration_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    narration_meta = payload.get("narration_meta")
    narration = payload.get("narration")
    if not isinstance(narration_meta, dict) or not isinstance(narration, dict):
        return False
    if _safe_text(narration_meta.get("schema_version")) != NARRATION_SCHEMA_VERSION:
        return False
    if not _is_schema_compliant(narration):
        return False
    return not narration_has_forbidden_jargon(narration)


def _default_section_bullet(heading: str) -> str:
    if heading == "Result summary":
        return "The result came from interacting factors, so focus first on the primary cause and then check how the secondary factor made recovery harder."
    if heading == "Turning points":
        return "No decisive turning point crossed the current threshold, which usually means the game shifted through smaller inaccuracies rather than one tactical collapse."
    if heading == "What you did well":
        return "You still created practical chances in parts of the game, which shows your position management remained functional despite pressure."
    if heading == "What to improve":
        return "Add a short candidate-move verification step before critical decisions, because most large swings come from skipping one final tactical check."
    return "Set one concrete routine for the next game and keep it consistent so improvement is measurable rather than anecdotal."


def _normalize_sections(value: Any) -> list[dict[str, Any]]:
    section_map: dict[str, list[str]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            heading = _safe_text(item.get("heading"))
            if not heading:
                continue
            key = heading.casefold()
            section_map[key] = _coerce_bullets(item.get("bullets"))

    normalized: list[dict[str, Any]] = []
    for heading in EXPECTED_SECTIONS:
        bullets = [item.strip() for item in section_map.get(heading.casefold(), []) if item.strip()]
        if not bullets:
            bullets = [_default_section_bullet(heading)]
        normalized.append({"heading": heading, "bullets": bullets[:4]})
    return normalized


def _normalize_narration(candidate: dict[str, Any]) -> dict[str, Any]:
    labels = candidate.get("labels") if isinstance(candidate.get("labels"), dict) else {}
    decisive_phase = _safe_text(labels.get("decisive_phase"), "unknown").lower()
    if decisive_phase not in DECISIVE_PHASE_VALUES:
        decisive_phase = "unknown"

    player_style = _safe_text(labels.get("player_style"), "Unknown")
    title = _safe_text(candidate.get("title"), "Game Insights Summary")
    one_liner = _safe_text(candidate.get("one_liner"), "Review the key moments and apply one improvement focus next game.")
    confidence_note = _safe_text(
        candidate.get("confidence_note"),
        "Confidence is based on the available move and clock evidence.",
    )

    return {
        "title": title,
        "one_liner": one_liner,
        "confidence_note": confidence_note,
        "sections": _normalize_sections(candidate.get("sections")),
        "labels": {
            "decisive_phase": decisive_phase,
            "player_style": player_style,
        },
    }


def _is_schema_compliant(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    for key in ("title", "one_liner", "confidence_note", "sections", "labels"):
        if key not in candidate:
            return False
    if not isinstance(candidate.get("title"), str):
        return False
    if not isinstance(candidate.get("one_liner"), str):
        return False
    if not isinstance(candidate.get("confidence_note"), str):
        return False

    sections = candidate.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    for section in sections:
        if not isinstance(section, dict):
            return False
        if not isinstance(section.get("heading"), str):
            return False
        bullets = section.get("bullets")
        if not isinstance(bullets, list):
            return False
        if any(not isinstance(item, str) for item in bullets):
            return False

    labels = candidate.get("labels")
    if not isinstance(labels, dict):
        return False
    if not isinstance(labels.get("decisive_phase"), str):
        return False
    if not isinstance(labels.get("player_style"), str):
        return False
    return True


def _extract_json(content: str | None) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def _canonical_payload(insights_payload: dict[str, Any]) -> dict[str, Any]:
    base = dict(insights_payload)
    base.pop("narration", None)
    base.pop("narration_meta", None)
    return base


def _cache_key(game_id: str, insights_payload: dict[str, Any]) -> str:
    canonical = _canonical_payload(insights_payload)
    version = _safe_text(canonical.get("version"), "unknown")
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    seed = f"{game_id}|{version}|{blob}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _parse_retry_after_seconds(value: str | None) -> float:
    if not value:
        return 1.5
    try:
        delay = float(value.strip())
    except ValueError:
        return 1.5
    return max(0.2, min(delay, 8.0))


def _respect_rate_limit() -> None:
    global _LAST_GEMINI_CALL_MONOTONIC
    min_interval = GEMINI_MIN_INTERVAL_MS / 1000.0
    if min_interval <= 0:
        return
    with _GEMINI_INTERVAL_LOCK:
        now = time.monotonic()
        wait_s = min_interval - (now - _LAST_GEMINI_CALL_MONOTONIC)
        if wait_s > 0:
            time.sleep(wait_s)
        _LAST_GEMINI_CALL_MONOTONIC = time.monotonic()


def _extract_gemini_text(payload: dict[str, Any]) -> str | None:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0] if isinstance(candidates[0], dict) else None
    if not first:
        return None
    content = first.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list):
        return None
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    joined = "".join(chunks).strip()
    return joined or None


def _compact_events(events: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    compact: list[dict[str, Any]] = []
    for event in events[:limit]:
        if not isinstance(event, dict):
            continue
        compact.append(
            {
                "ply": event.get("ply"),
                "actor": event.get("actor"),
                "phase": event.get("phase"),
                "label": event.get("label"),
                "severity_score": event.get("severity_score"),
                "is_decisive": event.get("is_decisive"),
                "momentum_direction": _momentum_direction(event),
                "impact_level": _impact_level(event.get("severity_score")),
                "reason_hint": _event_reason_hint(event),
                "tactical": (
                    {
                        "type": event.get("tactical", {}).get("type"),
                        "reason_text": event.get("tactical", {}).get("reason_text"),
                        "line_source": event.get("tactical", {}).get("line_source"),
                    }
                    if isinstance(event.get("tactical"), dict)
                    else None
                ),
            }
        )
    return compact


def _compact_phase_ratings(phase_grades: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"label_enum": "phase_ratings"}
    for phase_name in ("opening", "middlegame", "endgame"):
        phase = phase_grades.get(phase_name) if isinstance(phase_grades, dict) else None
        if not isinstance(phase, dict):
            compact[phase_name] = {
                "rating_5": None,
                "evaluation_state": "not_reached",
                "confidence": 0.0,
            }
            continue
        rating_5 = phase.get("rating_5")
        if not isinstance(rating_5, int):
            rating_5 = _grade_to_rating_5(_safe_text(phase.get("grade")))
        compact[phase_name] = {
            "rating_5": rating_5,
            "evaluation_state": _safe_text(phase.get("evaluation_state"), "not_reached"),
            "confidence": phase.get("confidence"),
        }
    return compact


def _compact_raw_insights(raw_insights: dict[str, Any]) -> dict[str, Any]:
    result_cause = raw_insights.get("result_cause") if isinstance(raw_insights.get("result_cause"), dict) else {}
    decisive_phase = raw_insights.get("decisive_phase") if isinstance(raw_insights.get("decisive_phase"), dict) else {}
    game_character = raw_insights.get("game_character") if isinstance(raw_insights.get("game_character"), dict) else {}
    turning_points = raw_insights.get("turning_points") if isinstance(raw_insights.get("turning_points"), dict) else {}
    missed = (
        raw_insights.get("missed_winning_chances")
        if isinstance(raw_insights.get("missed_winning_chances"), dict)
        else {}
    )
    got_away = raw_insights.get("got_away_with_it") if isinstance(raw_insights.get("got_away_with_it"), dict) else {}
    phase_grades = raw_insights.get("phase_grades") if isinstance(raw_insights.get("phase_grades"), dict) else {}
    conversion_quality = (
        raw_insights.get("conversion_quality")
        if isinstance(raw_insights.get("conversion_quality"), dict)
        else {}
    )
    resilience_quality = (
        raw_insights.get("resilience_quality")
        if isinstance(raw_insights.get("resilience_quality"), dict)
        else {}
    )
    time_pressure = (
        raw_insights.get("time_pressure_collapse")
        if isinstance(raw_insights.get("time_pressure_collapse"), dict)
        else {}
    )

    return {
        "version": raw_insights.get("version"),
        "status": raw_insights.get("status"),
        "confidence": raw_insights.get("confidence"),
        "result_cause": {
            "primary_label": result_cause.get("primary_label"),
            "secondary_label": result_cause.get("secondary_label"),
            "confidence": result_cause.get("confidence"),
        },
        "decisive_phase": {
            "decisive_phase": decisive_phase.get("decisive_phase"),
            "confidence": decisive_phase.get("confidence"),
        },
        "game_character": {
            "label": game_character.get("label"),
            "sublabel": game_character.get("sublabel"),
            "confidence": game_character.get("confidence"),
        },
        "turning_points": {
            "confidence": turning_points.get("confidence"),
            "events": _compact_events(turning_points.get("events"), limit=3),
        },
        "missed_winning_chances": {
            "count": missed.get("count"),
            "events": _compact_events(missed.get("events"), limit=3),
        },
        "got_away_with_it": {
            "count": got_away.get("count"),
            "events": _compact_events(got_away.get("events"), limit=3),
        },
        "conversion_quality": {
            "available": conversion_quality.get("available"),
            "rating_5": (
                conversion_quality.get("rating_5")
                if isinstance(conversion_quality.get("rating_5"), int)
                else _score_to_rating_5(conversion_quality.get("score"))
            ),
            "confidence": conversion_quality.get("confidence"),
        },
        "resilience_quality": {
            "available": resilience_quality.get("available"),
            "rating_5": (
                resilience_quality.get("rating_5")
                if isinstance(resilience_quality.get("rating_5"), int)
                else _score_to_rating_5(resilience_quality.get("score"))
            ),
            "confidence": resilience_quality.get("confidence"),
        },
        "phase_ratings": _compact_phase_ratings(phase_grades),
        "time_pressure_collapse": {
            "status": time_pressure.get("status"),
            "status_reason": time_pressure.get("status_reason"),
            "low_time_moves": time_pressure.get("low_time_moves"),
            "normal_time_moves": time_pressure.get("normal_time_moves"),
            "critical_low_time_swings": time_pressure.get("critical_low_time_swings"),
            "data_quality": time_pressure.get("data_quality"),
            "confidence": time_pressure.get("confidence"),
        },
    }


def _build_prompt_payload(raw_insights: dict[str, Any], game_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_schema": {
            "title": "string",
            "one_liner": "string",
            "confidence_note": "string",
            "sections": [
                {"heading": "Result summary", "bullets": ["string"]},
                {"heading": "Turning points", "bullets": ["string"]},
                {"heading": "What you did well", "bullets": ["string"]},
                {"heading": "What to improve", "bullets": ["string"]},
                {"heading": "Next game focus", "bullets": ["string"]},
            ],
            "labels": {
                "decisive_phase": "opening|middlegame|endgame|unknown",
                "player_style": "string",
            },
        },
        "rules": [
            "Output JSON only. No markdown, no prose outside JSON.",
            "Use exactly the five section headings in the schema.",
            "Include only important and relevant points per section.",
            "Use as many bullets as needed to cover key points (typically 1-4); do not pad.",
            "Keep language direct, concise, and actionable. Avoid filler, hype, or generic fluff.",
            "Use concise causal reasoning where relevant (because, which led to, therefore).",
            "Turning points must use move-based wording in this format: At move X, ... because ...",
            "When a turning point includes tactical.reason_text, mention that tactic explicitly.",
            "If a phase was not reached, explicitly state not reached.",
            "Never use CP, centipawn, eval-shift notation, or letter grades A-E in prose.",
            "If phase quality is mentioned, use X/5 format only.",
            "If time-pressure data is insufficient, state that and do not speculate.",
            "Do not invent moves, stats, or phases.",
        ],
        "game_context": game_context,
        "raw_insights": _compact_raw_insights(raw_insights),
    }


def _gemini_request(system_prompt: str, user_prompt: str, game_id: str) -> tuple[str | None, str | None]:
    if not GEMINI_API_KEY:
        return None, "missing_api_key"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    try:
        with _GEMINI_SEMAPHORE:
            with httpx.Client(timeout=GEMINI_TIMEOUT_S) as client:
                for attempt in range(2):
                    _respect_rate_limit()
                    response = client.post(url, json=payload)
                    if response.status_code == 429 and attempt == 0:
                        retry_after_s = _parse_retry_after_seconds(response.headers.get("Retry-After"))
                        LOGGER.warning(
                            "Gemini rate limited for game %s; retrying in %.2fs",
                            game_id,
                            retry_after_s,
                        )
                        time.sleep(retry_after_s)
                        continue
                    response.raise_for_status()
                    body = response.json()
                    text = _extract_gemini_text(body)
                    if text:
                        return text, None
                    return None, "empty_response"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        LOGGER.warning("Gemini HTTP error for game %s (status=%s): %s", game_id, status, exc)
        return None, f"http_{status}" if status else "http_error"
    except httpx.RequestError as exc:
        LOGGER.warning("Gemini network error for game %s (%s): %s", game_id, exc.__class__.__name__, exc)
        return None, "network_error"
    except Exception as exc:
        LOGGER.warning("Gemini request failed for game %s (%s): %s", game_id, exc.__class__.__name__, exc)
        return None, "request_error"

    LOGGER.warning("Gemini request exhausted retries for game %s", game_id)
    return None, "rate_limited"


def _build_game_context(game_meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(game_meta, dict):
        return {"result": None, "timeout_loss": False, "timeout_signal": None}

    result = _safe_text(game_meta.get("result"), "")
    pgn_text = _safe_text(game_meta.get("pgn"), "")
    timeout_match = _TIMEOUT_RE.search(pgn_text)
    timeout_signal = timeout_match.group(0).strip() if timeout_match else None
    timeout_loss = result == "loss" and timeout_signal is not None

    return {
        "result": result or None,
        "timeout_loss": timeout_loss,
        "timeout_signal": timeout_signal,
    }


def _build_fallback_narration(insights_payload: dict[str, Any], game_context: dict[str, Any]) -> dict[str, Any]:
    result_cause = insights_payload.get("result_cause") if isinstance(insights_payload.get("result_cause"), dict) else {}
    decisive = insights_payload.get("decisive_phase") if isinstance(insights_payload.get("decisive_phase"), dict) else {}
    turning_points = insights_payload.get("turning_points") if isinstance(insights_payload.get("turning_points"), dict) else {}
    phase_ratings = (
        insights_payload.get("phase_ratings")
        if isinstance(insights_payload.get("phase_ratings"), dict)
        else _compact_phase_ratings(
            insights_payload.get("phase_grades")
            if isinstance(insights_payload.get("phase_grades"), dict)
            else {}
        )
    )
    time_pressure = (
        insights_payload.get("time_pressure_collapse")
        if isinstance(insights_payload.get("time_pressure_collapse"), dict)
        else {}
    )
    game_character = insights_payload.get("game_character") if isinstance(insights_payload.get("game_character"), dict) else {}

    primary_label = _safe_text(result_cause.get("primary_label"), "Mixed factors")
    secondary_label = _safe_text(result_cause.get("secondary_label"), "Unknown")
    style_label = _safe_text(game_character.get("label"), "Unknown")
    confidence = insights_payload.get("confidence")
    confidence_pct = int(round(float(confidence) * 100)) if isinstance(confidence, (int, float)) else None

    turning_events = turning_points.get("events") if isinstance(turning_points.get("events"), list) else []
    turning_bullets = build_turning_point_bullets_from_events(turning_events)

    phase_bullets: list[str] = []
    for phase_name in ("opening", "middlegame", "endgame"):
        phase = phase_ratings.get(phase_name) if isinstance(phase_ratings, dict) else None
        if not isinstance(phase, dict):
            continue
        phase_bullets.append(_format_phase_rating_bullet(phase_name, phase))
    if not phase_bullets:
        phase_bullets.append(
            "There is not enough phase-level evidence yet to assign reliable ratings in this game."
        )

    status = _safe_text(time_pressure.get("status"), "unavailable")
    clock_moves = (
        time_pressure.get("data_quality", {}).get("clock_moves")
        if isinstance(time_pressure.get("data_quality"), dict)
        else None
    )
    user_moves = (
        time_pressure.get("data_quality", {}).get("user_moves")
        if isinstance(time_pressure.get("data_quality"), dict)
        else None
    )
    if game_context.get("timeout_loss"):
        time_note = (
            "This game ended on time loss, so time management was decisive even if the collapse detector did not flag a classic low-time accuracy breakdown."
        )
    elif status == "detected":
        time_note = (
            "Low-time performance dropped, which likely amplified tactical oversight risk; simplify earlier so critical decisions happen with enough time."
        )
    elif status == "not_detected":
        time_note = (
            "No clear time-pressure collapse was detected by the model, so the main issue appears to be decision quality rather than measurable clock panic."
        )
    elif status == "insufficient_data":
        time_note = (
            "Time-pressure data is insufficient, so avoid strong claims about clock impact and focus improvement on move-quality patterns that are fully observed."
        )
    else:
        time_note = (
            "Time-pressure signals were unavailable for this game, so use the tactical and phase evidence as the primary guide for improvement."
        )
    if isinstance(clock_moves, int) and isinstance(user_moves, int):
        time_note = f"{time_note} Clock samples: {clock_moves}/{user_moves}."

    decisive_phase = _safe_text(decisive.get("decisive_phase"), "unknown").lower()
    if decisive_phase not in DECISIVE_PHASE_VALUES:
        decisive_phase = "unknown"

    title = f"{style_label} Game Summary"
    one_liner = f"{primary_label} was the main driver; focus next on cleaner decisions at key swings."
    confidence_note = (
        f"Confidence is {confidence_pct}% from deterministic game evidence."
        if confidence_pct is not None
        else "Confidence is based on deterministic game evidence."
    )

    return {
        "title": title,
        "one_liner": one_liner,
        "confidence_note": confidence_note,
        "sections": [
            {
                "heading": "Result summary",
                "bullets": [
                    f"The primary result driver was {primary_label}, and that factor repeatedly shaped critical decisions when the position demanded precision.",
                    f"The secondary contributor was {secondary_label}, which compounded the main issue and reduced your margin for recovery after mistakes.",
                    f"The decisive phase was {decisive_phase}, which is where the game direction was effectively set.",
                ],
            },
            {"heading": "Turning points", "bullets": turning_bullets},
            {
                "heading": "What you did well",
                "bullets": [
                    "You created practical chances despite momentum swings, which indicates your baseline position handling remained active under pressure.",
                    phase_bullets[0],
                    (
                        phase_bullets[1]
                        if len(phase_bullets) > 1
                        else "Even in difficult stretches, you stayed in the game long enough to keep counterplay opportunities available."
                    ),
                ],
            },
            {
                "heading": "What to improve",
                "bullets": [
                    "Before committing in critical positions, compare your intended move with one safer candidate, because this catches many avoidable tactical oversights.",
                    "After every large evaluation swing, run a quick threat scan for forcing replies, which reduces repeat blunders in unstable positions.",
                    "When the position is unclear, choose plans that reduce immediate tactical volatility, because simplification improves decision quality under practical conditions.",
                ],
            },
            {
                "heading": "Next game focus",
                "bullets": [
                    time_note,
                    "Use a fixed two-step routine in critical moments: identify opponent threats first, then select between your best two candidates with a blunder check.",
                    "Track one concrete metric next game, such as errors after eval swings, so you can verify whether the new routine is improving outcomes.",
                ],
            },
        ],
        "labels": {
            "decisive_phase": decisive_phase,
            "player_style": style_label,
        },
    }


def ensure_narration(
    insights_payload: dict[str, Any],
    game_id: str,
    game_meta: dict[str, Any] | None = None,
    retry_fallback: bool = False,
) -> dict[str, Any]:
    """Ensure structured narration exists and is cached for the current insights payload."""
    if not isinstance(insights_payload, dict):
        return insights_payload

    current_cache_key = _cache_key(game_id, insights_payload)
    existing_meta = insights_payload.get("narration_meta")
    existing_narration = insights_payload.get("narration")
    now_utc = datetime.now(timezone.utc)

    if (
        isinstance(existing_meta, dict)
        and isinstance(existing_narration, dict)
        and existing_meta.get("cache_key") == current_cache_key
        and _safe_text(existing_meta.get("schema_version")) == NARRATION_SCHEMA_VERSION
        and _is_schema_compliant(existing_narration)
        and not narration_has_forbidden_jargon(existing_narration)
    ):
        source = _safe_text(existing_meta.get("source"))
        if source == "gemini":
            return insights_payload
        if source == "fallback":
            if retry_fallback:
                pass
            elif FALLBACK_RETRY_SECONDS <= 0:
                return insights_payload
            else:
                generated_at = _safe_parse_datetime(existing_meta.get("generated_at"))
                if generated_at is not None and generated_at.tzinfo is None:
                    generated_at = generated_at.replace(tzinfo=timezone.utc)
                if generated_at is not None:
                    age = (now_utc - generated_at).total_seconds()
                    if age < FALLBACK_RETRY_SECONDS:
                        return insights_payload
                else:
                    return insights_payload
        else:
            return insights_payload

    canonical_input = _canonical_payload(insights_payload)
    game_context = _build_game_context(game_meta)
    system_prompt = (
        "You are a chess coaching assistant. "
        "Return only valid JSON matching the required schema. "
        "Be direct and concrete. "
        "Do not add markdown or explanations."
    )
    user_prompt = json.dumps(_build_prompt_payload(canonical_input, game_context), ensure_ascii=True)

    source = "fallback"
    fallback_reason = "gemini_unavailable"
    narration: dict[str, Any] | None = None

    generated, request_reason = _gemini_request(system_prompt, user_prompt, game_id)
    if request_reason:
        fallback_reason = request_reason
    if generated:
        parsed = _extract_json(generated)
        need_repair = True
        if parsed and _is_schema_compliant(parsed):
            candidate = enforce_turning_points_grounding(parsed, canonical_input)
            if not narration_has_forbidden_jargon(candidate):
                narration = candidate
                source = "gemini"
                need_repair = False
            else:
                fallback_reason = "forbidden_jargon"
        else:
            fallback_reason = "invalid_json_or_schema"

        if need_repair:
            repair_prompt = (
                "Fix the JSON to match the exact schema. "
                "Remove chess-engine jargon like CP, centipawn, eval shifts, and letter grades A-E. "
                "Return JSON only.\n\n"
                f"SCHEMA:\n{json.dumps(_build_prompt_payload({}, {})['required_schema'])}\n\n"
                f"INVALID_JSON:\n{generated}"
            )
            repaired, repair_reason = _gemini_request(system_prompt, repair_prompt, game_id)
            if repair_reason:
                fallback_reason = repair_reason
            repaired_json = _extract_json(repaired)
            if repaired_json and _is_schema_compliant(repaired_json):
                candidate = enforce_turning_points_grounding(repaired_json, canonical_input)
                if not narration_has_forbidden_jargon(candidate):
                    narration = candidate
                    source = "gemini"
                else:
                    fallback_reason = "forbidden_jargon"
            elif fallback_reason != "forbidden_jargon":
                fallback_reason = "invalid_json_or_schema"

    if narration is None:
        narration = _build_fallback_narration(canonical_input, game_context)
    narration = enforce_turning_points_grounding(narration, canonical_input)

    result = dict(canonical_input)
    result["narration"] = narration
    result["narration_meta"] = {
        "source": source,
        "cache_key": current_cache_key,
        "schema_version": NARRATION_SCHEMA_VERSION,
        "model": GEMINI_MODEL if source == "gemini" else "fallback_local",
        "generated_at": _utc_now_iso(),
        "reason": None if source == "gemini" else fallback_reason,
    }
    return result
