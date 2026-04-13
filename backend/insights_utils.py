"""Utility functions for chess insights processing."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_CLOCK_RE = re.compile(r"\[%clk\s+([0-9:.]+)\]")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def result_to_score(result: str | None) -> float:
    if result == "win":
        return 1.0
    if result == "draw":
        return 0.5
    return 0.0


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def clock_to_seconds(raw_value: str | None) -> int | None:
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


def extract_clock_seconds(comment: str | None) -> int | None:
    if not comment:
        return None
    match = _CLOCK_RE.search(comment)
    if not match:
        return None
    return clock_to_seconds(match.group(1))


def phase_for_ply(ply: int, opening_end_ply: int, endgame_start_ply: int | None) -> str:
    if ply <= opening_end_ply:
        return "opening"
    if endgame_start_ply is not None and ply >= endgame_start_ply:
        return "endgame"
    return "middlegame"


def add_fact(
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
