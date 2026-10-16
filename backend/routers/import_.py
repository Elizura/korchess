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
from fastapi.concurrency import run_in_threadpool

from services.analytics import hash_username, track_server_event
from repository.db import get_import_history, get_import_status
from services.import_service import (
    UPSTREAM_ERRORS,
    import_chesscom_games,
    import_key,
    import_lichess_games,
    upstream_http_status,
)
from repository.redis_client import redis_client as _redis
from schemas import (
    INVALID_USERNAME_MESSAGE,
    ImportRequest,
    ImportResponse,
    ImportHistoryResponse,
    ImportHistoryItem,
    ImportProgressResponse,
    is_valid_chess_username,
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
    """Get the last 10 imports of the caller's own chess profiles."""
    rows = get_import_history(conn, current_user["id"], limit=10)
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
    if not is_valid_chess_username(username):
        raise HTTPException(status_code=400, detail=INVALID_USERNAME_MESSAGE)

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
        # Blocking HTTP + rate-limit sleeps: run in a worker thread so one
        # import doesn't stall every other request on the event loop
        importer = import_lichess_games if site == "lichess" else import_chesscom_games
        import_result = await run_in_threadpool(importer, username, conn, max_games)
    except UPSTREAM_ERRORS as e:
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
        raise HTTPException(status_code=upstream_http_status(e), detail=e.message)

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
