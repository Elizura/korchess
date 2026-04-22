"""Shared game import logic for Lichess and Chess.com.

This module is the single source of truth for streaming, parsing, and enqueuing
games into the Celery queue. Both the public import router (anonymous/optional auth)
and the authenticated profiles router delegate to functions here.
"""

import io
import json
import logging
import os
from datetime import datetime

import chess.pgn
import psycopg
import redis as redis_lib

from chesscom import parse_chesscom_game
from db import get_import_status
from game_streamer import (
    ChesscomStreamError,
    LichessStreamError,
    stream_chesscom_games,
    stream_lichess_pgns,
)
from lichess import parse_pgn_games
from opening_match import best_opening_match, game_to_uci_plies
from schemas import ImportResponse
from tasks import process_game

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(REDIS_URL, decode_responses=True)


def import_key(username: str, site: str, field: str) -> str:
    return f"import:{username.strip().lower()}:{site}:{field}"


def datetime_to_lichess_ms(dt: datetime) -> int:
    """Convert a datetime to milliseconds since epoch (Lichess API format)."""
    return int(dt.timestamp() * 1000)


def parse_synced_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def parse_single_lichess_pgn(
    pgn_text: str,
    target_username: str,
    conn: psycopg.Connection,
) -> tuple[list[dict], int]:
    """Parse a single PGN text (may contain one or a few games)."""
    return parse_pgn_games(pgn_text, target_username, conn)


def parse_single_chesscom_json(
    game_json: dict,
    target_username: str,
    conn: psycopg.Connection,
) -> dict | None:
    """Parse a single Chess.com game JSON into a game_data dict with opening matching."""
    target_lower = target_username.strip().lower()
    game_data = parse_chesscom_game(game_json, target_lower)
    if game_data is None:
        return None

    opening = None
    try:
        pgn_text = game_data.get("pgn", "")
        if pgn_text:
            game_obj = chess.pgn.read_game(io.StringIO(pgn_text))
            if game_obj:
                uci_plies = game_to_uci_plies(game_obj, max_plies=40)
                opening = best_opening_match(conn, uci_plies)
    except Exception:
        opening = None

    if opening:
        game_data["eco"] = opening["eco"]
        game_data["opening_name"] = opening["name"]
        game_data["opening_id"] = opening["opening_id"]
        game_data["opening_ply_count"] = opening["ply_count"]
    else:
        game_data["opening_id"] = None
        game_data["opening_ply_count"] = None

    return game_data


def import_lichess_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 250,
) -> ImportResponse:
    """Stream Lichess games and fire each into the Celery queue immediately.

    Works for both anonymous and authenticated callers — auth context is not
    needed here; it belongs in the calling router for analytics/profile checks.
    Raises LichessStreamError on upstream failures (let the router handle HTTP codes).
    """
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "lichess")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = datetime_to_lichess_ms(last_synced_at)

    _redis.set(import_key(canonical, "lichess", "status"), "streaming", ex=3600)

    total_enqueued = 0
    parse_skipped = 0

    for pgn_chunk in stream_lichess_pgns(username, max_games, since=since_ms):
        for pgn_text in pgn_chunk:
            parsed_games, skipped = parse_single_lichess_pgn(pgn_text, username, conn)
            parse_skipped += skipped
            for game_data in parsed_games:
                process_game.delay(game_data, username, "lichess")
                total_enqueued += 1

    if total_enqueued == 0:
        _redis.delete(import_key(canonical, "lichess", "status"))
        return ImportResponse(username=username, imported=0, skipped=parse_skipped, is_sync=is_sync)

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }
    _redis.set(import_key(canonical, "lichess", "total"), total_enqueued, ex=3600)
    _redis.set(import_key(canonical, "lichess", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(import_key(canonical, "lichess", "status"), "processing", ex=3600)

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
        is_sync=is_sync,
    )


def import_chesscom_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 250,
) -> ImportResponse:
    """Stream Chess.com games and fire each into the Celery queue immediately.

    Works for both anonymous and authenticated callers — auth context is not
    needed here; it belongs in the calling router for analytics/profile checks.
    Raises ChesscomStreamError on upstream failures (let the router handle HTTP codes).
    """
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "chesscom")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_dt: datetime | None = last_synced_at if is_sync else None

    _redis.set(import_key(canonical, "chesscom", "status"), "streaming", ex=3600)

    total_enqueued = 0
    parse_skipped = 0

    for game_json_chunk in stream_chesscom_games(username, max_games, since=since_dt):
        for game_json in game_json_chunk:
            game_data = parse_single_chesscom_json(game_json, username, conn)
            if game_data is None:
                parse_skipped += 1
                continue
            process_game.delay(game_data, username, "chesscom")
            total_enqueued += 1

    if total_enqueued == 0:
        _redis.delete(import_key(canonical, "chesscom", "status"))
        return ImportResponse(username=username, imported=0, skipped=parse_skipped, is_sync=is_sync)

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }
    _redis.set(import_key(canonical, "chesscom", "total"), total_enqueued, ex=3600)
    _redis.set(import_key(canonical, "chesscom", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(import_key(canonical, "chesscom", "status"), "processing", ex=3600)

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
        is_sync=is_sync,
    )
