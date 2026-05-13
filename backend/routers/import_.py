"""Import games from Lichess and Chess.com via streaming + queue.

Games and imports are shared by (username, site) - not owned by individual users.
Games are streamed in chunks, parsed, and pushed into a Redis queue as individual
Celery tasks. Each worker processes one game at a time. A finalize task triggers
automatically when all games are done (tracked via Redis counters).
"""

import logging
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from services.analytics import hash_username, track_server_event
from repository.db import get_import_history, get_import_status
from services.game_streamer import ChesscomStreamError, LichessStreamError
from services.import_service import import_chesscom_games, import_lichess_games, import_key
from repository.redis_client import redis_client as _redis
from schemas import (
    ImportRequest,
    ImportResponse,
    ImportHistoryResponse,
    ImportHistoryItem,
    ImportProgressResponse,
)
from dependencies import get_db
from auth import get_current_user

router = APIRouter(tags=["import"])
logger = logging.getLogger(__name__)


@router.get("/history", response_model=ImportHistoryResponse)
async def get_import_history_endpoint(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
):
    """Short-poll endpoint for import progress. Reads Redis counters."""
    canonical = username.strip().lower()

    done_raw = _redis.get(import_key(canonical, site, "done"))
    total_raw = _redis.get(import_key(canonical, site, "total"))
    status_raw = _redis.get(import_key(canonical, site, "status"))

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


@router.post("/{site}", response_model=ImportResponse)
async def import_games(
    site: Literal["lichess", "chesscom"],
    request: ImportRequest,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Import or sync games from Lichess or Chess.com. Requires authentication."""
    username = request.username.strip()
    max_games = request.max_games
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    username_hash = hash_username(username)
    existing = get_import_status(conn, username, site)
    existing_games = int(existing.get("total_games") or 0)
    is_sync = existing_games > 0

    await track_server_event(
        conn,
        event_name="import.start",
        user_id=current_user["id"],
        request=http_request,
        properties={
            "site": site,
            "max_games": max_games,
            "username": username_hash,
            "is_sync": is_sync,
        },
    )

    try:
        if site == "lichess":
            import_result = import_lichess_games(username, conn, max_games)
        else:
            import_result = import_chesscom_games(username, conn, max_games)
    except (LichessStreamError, ChesscomStreamError) as e:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"],
            request=http_request,
            properties={
                "site": site,
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

    if import_result.imported == 0 and not import_result.is_sync:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"],
            request=http_request,
            properties={
                "site": site,
                "max_games": max_games,
                "username": username_hash,
                "status_code": 404,
                "reason": "No games found",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' on {site}."
        )

    await track_server_event(
        conn,
        event_name="import.queued",
        user_id=current_user["id"],
        request=http_request,
        properties={
            "site": site,
            "max_games": max_games,
            "username": username_hash,
            "enqueued": import_result.imported,
            "is_sync": import_result.is_sync,
        },
    )
    conn.commit()

    return import_result
