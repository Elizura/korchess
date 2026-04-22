"""Import games from Lichess and Chess.com via streaming + queue.

Games and imports are shared by (username, site) - not owned by individual users.
Games are streamed in chunks, parsed, and pushed into a Redis queue as individual
Celery tasks. Each worker processes one game at a time. A finalize task triggers
automatically when all games are done (tracked via Redis counters).
"""

import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal

import chess.pgn
import psycopg
import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request

from analytics import hash_username, track_server_event
from chesscom import parse_chesscom_game, ChesscomAPIError
from db import get_import_history, get_import_status
from game_streamer import (
    ChesscomStreamError,
    LichessStreamError,
    stream_chesscom_games,
    stream_lichess_pgns,
)
from lichess import parse_pgn_games, LichessAPIError
from opening_match import best_opening_match, game_to_uci_plies
from schemas import (
    ImportRequest,
    ImportResponse,
    ImportHistoryResponse,
    ImportHistoryItem,
    ImportProgressResponse,
)
from dependencies import get_db
from auth import get_optional_user
from tasks import process_game

router = APIRouter(tags=["import"])
logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(REDIS_URL, decode_responses=True)


def _import_key(username: str, site: str, field: str) -> str:
    return f"import:{username.strip().lower()}:{site}:{field}"


def _datetime_to_lichess_ms(dt: datetime) -> int:
    """Convert a datetime to milliseconds since epoch (Lichess API format)."""
    return int(dt.timestamp() * 1000)


def _parse_synced_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _parse_single_lichess_pgn(
    pgn_text: str,
    target_username: str,
    conn: psycopg.Connection,
) -> tuple[list[dict], int]:
    """Parse a single PGN text (may contain one or a few games) using the existing parser."""
    return parse_pgn_games(pgn_text, target_username, conn)


def _parse_single_chesscom_json(
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


@router.get("/history", response_model=ImportHistoryResponse)
async def get_import_history_endpoint(
    conn: psycopg.Connection = Depends(get_db),
):
    """Get last 10 import records."""
    rows = get_import_history(conn, limit=10)
    return ImportHistoryResponse(
        history=[ImportHistoryItem(**r) for r in rows]
    )


@router.get("/progress/{site}/{username}", response_model=ImportProgressResponse)
async def get_import_progress(
    site: Literal["lichess", "chesscom"],
    username: str,
):
    """Short-poll endpoint for import progress. Reads Redis counters."""
    canonical = username.strip().lower()

    done_raw = _redis.get(_import_key(canonical, site, "done"))
    total_raw = _redis.get(_import_key(canonical, site, "total"))
    status_raw = _redis.get(_import_key(canonical, site, "status"))

    done = int(done_raw) if done_raw is not None else 0
    total = int(total_raw) if total_raw is not None else 0

    if status_raw == "complete":
        status = "complete"
    elif total == 0:
        status = "streaming"
    elif done < total:
        status = "processing"
    else:
        status = "complete"

    return ImportProgressResponse(
        username=canonical,
        site=site,
        status=status,
        total=total,
        done=done,
    )


@router.post("/lichess", response_model=ImportResponse)
async def import_lichess_games(
    request: ImportRequest,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    username = request.username.strip()
    max_games = request.max_games
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    canonical = username.strip().lower()
    username_hash = hash_username(username)
    existing = get_import_status(conn, username, "lichess")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = _datetime_to_lichess_ms(last_synced_at)

    track_server_event(
        conn,
        event_name="import.start",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "lichess",
            "max_games": max_games,
            "username": username_hash,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )

    _redis.set(_import_key(canonical, "lichess", "status"), "streaming", ex=3600)

    try:
        total_enqueued = 0
        parse_skipped = 0

        for pgn_chunk in stream_lichess_pgns(username, max_games, since=since_ms):
            for pgn_text in pgn_chunk:
                parsed_games, skipped = _parse_single_lichess_pgn(pgn_text, username, conn)
                parse_skipped += skipped
                for game_data in parsed_games:
                    process_game.delay(game_data, username, "lichess")
                    total_enqueued += 1

    except LichessStreamError as e:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "lichess",
                "max_games": max_games,
                "username": username_hash,
                "status_code": e.status_code,
                "reason": e.message,
            },
        )
        conn.commit()
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    if total_enqueued == 0:
        _redis.delete(
            _import_key(canonical, "lichess", "status"),
        )
        if is_sync:
            return ImportResponse(username=username, imported=0, skipped=0, is_sync=True)

        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "lichess",
                "max_games": max_games,
                "username": username_hash,
                "status_code": 404,
                "reason": "No rated games found",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No rated games found for user '{username}'."
        )

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }

    _redis.set(_import_key(canonical, "lichess", "total"), total_enqueued, ex=3600)
    _redis.set(_import_key(canonical, "lichess", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(_import_key(canonical, "lichess", "status"), "processing", ex=3600)

    await track_server_event(
        conn,
        event_name="import.queued",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "lichess",
            "max_games": max_games,
            "username": username_hash,
            "enqueued": total_enqueued,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )
    conn.commit()

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
        is_sync=is_sync,
    )


@router.post("/chesscom", response_model=ImportResponse)
async def import_chesscom_games(
    request: ImportRequest,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """Import or sync games from Chess.com for a user."""
    username = request.username.strip()
    max_games = request.max_games
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    canonical = username.strip().lower()
    username_hash = hash_username(username)
    existing = get_import_status(conn, username, "chesscom")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_dt: datetime | None = last_synced_at if is_sync else None

    await track_server_event(
        conn,
        event_name="import.start",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "chesscom",
            "max_games": max_games,
            "username": username_hash,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )

    _redis.set(_import_key(canonical, "chesscom", "status"), "streaming", ex=3600)

    try:
        total_enqueued = 0
        parse_skipped = 0

        for game_json_chunk in stream_chesscom_games(username, max_games, since=since_dt):
            for game_json in game_json_chunk:
                game_data = _parse_single_chesscom_json(game_json, username, conn)
                if game_data is None:
                    parse_skipped += 1
                    continue
                process_game.delay(game_data, username, "chesscom")
                total_enqueued += 1

    except ChesscomStreamError as e:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "chesscom",
                "max_games": max_games,
                "username": username_hash,
                "status_code": e.status_code,
                "reason": e.message,
            },
        )
        conn.commit()
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    if total_enqueued == 0:
        _redis.delete(
            _import_key(canonical, "chesscom", "status"),
        )
        if is_sync:
            return ImportResponse(username=username, imported=0, skipped=0, is_sync=True)

        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "chesscom",
                "max_games": max_games,
                "username": username_hash,
                "status_code": 404,
                "reason": "No games found",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' on Chess.com."
        )

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }

    _redis.set(_import_key(canonical, "chesscom", "total"), total_enqueued, ex=3600)
    _redis.set(_import_key(canonical, "chesscom", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(_import_key(canonical, "chesscom", "status"), "processing", ex=3600)

    await track_server_event(
        conn,
        event_name="import.queued",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "chesscom",
            "max_games": max_games,
            "username": username_hash,
            "enqueued": total_enqueued,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )
    conn.commit()

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
        is_sync=is_sync,
    )
