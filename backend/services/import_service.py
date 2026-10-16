"""Shared game import logic for Lichess and Chess.com.

This module is the single source of truth for fetching, parsing, and enqueuing
games into the Celery queue. Both the public import router (anonymous/optional auth)
and the authenticated profiles router delegate to functions here.
"""

import io
import json
import logging
from datetime import datetime, timezone

import chess.pgn
import psycopg

from services.chesscom import ChesscomAPIError, fetch_chesscom_games, parse_chesscom_game
from repository.db import get_existing_game_ids, get_import_status, upsert_import_status
from services.lichess import LichessAPIError, parse_pgn_games, fetch_lichess_pgn
from services.opening_match import best_opening_match, game_to_uci_plies
from repository.redis_client import redis_client as _redis
from schemas import ImportResponse
from repository.tasks import process_game

logger = logging.getLogger(__name__)

# What the importers raise on upstream (Lichess/Chess.com) failures.
UPSTREAM_ERRORS = (LichessAPIError, ChesscomAPIError)


def upstream_http_status(error: LichessAPIError | ChesscomAPIError) -> int:
    """HTTP status to return for an upstream error: pass through 400/404/429, else 502."""
    if error.status_code in (400, 404, 429):
        return error.status_code
    return 502


def import_key(username: str, site: str, field: str) -> str:
    return f"import:{username.strip().lower()}:{site}:{field}"


def datetime_to_lichess_ms(dt: datetime) -> int:
    """Convert a datetime to milliseconds since epoch (Lichess API format)."""
    return int(dt.timestamp() * 1000)


def parse_synced_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_single_lichess_pgn(
    pgn_text: str,
    target_username: str,
    conn: psycopg.Connection,
) -> tuple[list[dict], int]:
    """Parse a single PGN text (may contain one or a few games).
    
    Useful for streaming imports where PGNs arrive in chunks.
    """
    return parse_pgn_games(pgn_text, target_username, conn)


def parse_single_chesscom_json(
    game_json: dict,
    target_username: str,
    conn: psycopg.Connection,
) -> dict | None:
    """Parse a single Chess.com game JSON into a game_data dict with opening matching.
    
    Useful for streaming imports where games arrive individually.
    """
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


def parse_played_at(raw: str | None) -> datetime | None:
    """Parse a stored played_at ("2024-03-05T12:34:56Z", ISO with offset, or a bare date) as UTC."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _enqueue_and_record(
    conn: psycopg.Connection,
    username: str,
    site: str,
    games: list[dict],
    parse_skipped: int,
    max_games: int,
    is_sync: bool,
    last_synced_at: datetime | None,
) -> ImportResponse:
    """Enqueue the fetched games that aren't stored yet, then record the import.

    last_synced_at advances to the newest *fetched* game's timestamp, not now():
    the next sync then starts where the fetched data ends, so games still in
    progress during this fetch (Lichess filters `since` by game start) aren't
    skipped. The boundary game is fetched again and dropped here as known.
    """
    canonical = username.strip().lower()
    known_ids = get_existing_game_ids(conn, canonical, site, [g["site_game_id"] for g in games])
    new_games = [g for g in games if g["site_game_id"] not in known_ids]

    if new_games:
        _redis.set(import_key(canonical, site, "total"), len(new_games), ex=3600)
        _redis.set(import_key(canonical, site, "status"), "processing", ex=3600)

        for game_data in new_games:
            process_game.delay(game_data, username, site)

        import_meta = {
            "max_games": max_games,
            "is_sync": is_sync,
            "parse_skipped": parse_skipped,
        }
        _redis.set(import_key(canonical, site, "meta"), json.dumps(import_meta), ex=3600)
    else:
        _redis.delete(import_key(canonical, site, "status"))

    played = [dt for dt in (parse_played_at(g.get("played_at")) for g in games) if dt]
    synced_through = max(played + ([last_synced_at] if last_synced_at else []), default=None)
    if synced_through is not None:
        upsert_import_status(
            conn,
            username,
            site,
            imported=len(new_games),
            skipped=parse_skipped,
            max_games=max_games,
            imported_at=datetime.now(timezone.utc).isoformat(),
            last_synced_at=synced_through,
        )
        # The games are already queued; keep the record even if the caller rolls back.
        conn.commit()

    return ImportResponse(
        username=username,
        imported=len(new_games),
        skipped=parse_skipped,
        is_sync=is_sync,
    )


def import_lichess_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 150,
) -> ImportResponse:
    """Fetch Lichess games and enqueue each new one into the Celery queue.

    Works for both anonymous and authenticated callers — auth context is not
    needed here; it belongs in the calling router for analytics/profile checks.
    Raises LichessAPIError on upstream failures (let the router handle HTTP codes).
    """
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "lichess")
    is_sync = int(existing.get("total_games") or 0) > 0
    last_synced_at = parse_synced_at(existing.get("last_synced_at"))

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = datetime_to_lichess_ms(last_synced_at)

    _redis.set(import_key(canonical, "lichess", "status"), "fetching", ex=3600)

    logger.info(f"Fetching {max_games} games for {username} since {since_ms}")

    pgn_text = fetch_lichess_pgn(username, max_games, since=since_ms)
    parsed_games, parse_skipped = parse_pgn_games(pgn_text, username, conn)

    return _enqueue_and_record(
        conn, username, "lichess", parsed_games, parse_skipped, max_games, is_sync, last_synced_at,
    )


def import_chesscom_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 150,
) -> ImportResponse:
    """Fetch Chess.com games and enqueue each new one into the Celery queue.

    Works for both anonymous and authenticated callers — auth context is not
    needed here; it belongs in the calling router for analytics/profile checks.
    Raises ChesscomAPIError on upstream failures (let the router handle HTTP codes).
    """
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "chesscom")
    is_sync = int(existing.get("total_games") or 0) > 0
    last_synced_at = parse_synced_at(existing.get("last_synced_at"))

    since_dt: datetime | None = last_synced_at if is_sync else None

    _redis.set(import_key(canonical, "chesscom", "status"), "fetching", ex=3600)

    logger.info(f"Fetching {max_games} games for {username} since {since_dt}")

    games = fetch_chesscom_games(username, max_games, conn=conn, since=since_dt)

    return _enqueue_and_record(
        conn, username, "chesscom", games, 0, max_games, is_sync, last_synced_at,
    )
