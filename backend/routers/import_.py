"""Import games from Lichess and Chess.com."""

import logging
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from analytics import hash_username, track_server_event
from db import (
    ensure_public_user_for_username,
    get_import_history,
    get_import_status,
    upsert_game,
    upsert_import_status,
)
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError
from chesscom import fetch_chesscom_games, ChesscomAPIError
from insights import schedule_insights_refresh

from schemas import ImportRequest, ImportResponse, ImportHistoryResponse, ImportHistoryItem
from dependencies import get_db
from auth import get_optional_user, get_registered_user

router = APIRouter(tags=["import"])
logger = logging.getLogger(__name__)


async def _maybe_return_existing_import(
    *,
    conn: psycopg.Connection,
    current_user: dict | None,
    http_request: Request,
    username: str,
    site: str,
    max_games: int,
    username_hash: str | None,
) -> ImportResponse | None:
    """Short-circuit import when games already exist for username+site."""
    public_user_id = ensure_public_user_for_username(conn, username)
    existing = get_import_status(conn, public_user_id, username, site)
    existing_games = int(existing.get("total_games") or 0)

    if existing_games <= 0:
        return None

    # Keep signed-in import history/status useful even when reusing existing public data.
    if current_user:
        imported_at = existing.get("imported_at") or datetime.now(timezone.utc).isoformat()
        upsert_import_status(
            conn,
            current_user["id"],
            username,
            site,
            int(existing.get("last_imported") or existing_games),
            int(existing.get("last_skipped") or 0),
            max_games,
            imported_at,
        )

    await track_server_event(
        conn,
        event_name="import.success",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": site,
            "max_games": max_games,
            "username_hash": username_hash,
            "imported": existing_games,
            "skipped": 0,
            "cache_hit": True,
            "is_authenticated": bool(current_user),
        },
    )
    conn.commit()

    return ImportResponse(
        username=username,
        imported=existing_games,
        skipped=0,
    )


@router.get("/history", response_model=ImportHistoryResponse)
async def get_import_history_endpoint(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get last 10 import records for the authenticated user."""
    rows = get_import_history(conn, current_user["id"], limit=10)
    return ImportHistoryResponse(
        history=[ImportHistoryItem(**r) for r in rows]
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

    username_hash = hash_username(username)
    await track_server_event(
        conn,
        event_name="import.start",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "lichess",
            "max_games": max_games,
            "username_hash": username_hash,
            "is_authenticated": bool(current_user),
        },
    )

    existing_response = await _maybe_return_existing_import(
        conn=conn,
        current_user=current_user,
        http_request=http_request,
        username=username,
        site="lichess",
        max_games=max_games,
        username_hash=username_hash,
    )
    print(existing_response is not None, "<<<<<<<<<<<<<<<")
    if existing_response is not None:
        return existing_response

    public_user_id = ensure_public_user_for_username(conn, username)

    try:
        pgn_text = fetch_lichess_pgn(username, max_games)
    except LichessAPIError as e:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "lichess",
                "max_games": max_games,
                "username_hash": username_hash,
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

    if not pgn_text.strip():
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "lichess",
                "max_games": max_games,
                "username_hash": username_hash,
                "status_code": 404,
                "reason": "No rated games found",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No rated games found for user '{username}'."
        )

    imported = 0
    games, skipped = parse_pgn_games(pgn_text, username, conn)

    if not games and skipped == 0:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "lichess",
                "max_games": max_games,
                "username_hash": username_hash,
                "status_code": 404,
                "reason": "No games parsed from PGN",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'."
        )

    for game in games:
        game["user_id"] = public_user_id
        if upsert_game(conn, game):
            imported += 1
        else:
            skipped += 1
    conn.commit()

    imported_at = datetime.now(timezone.utc).isoformat()
    upsert_import_status(
        conn, public_user_id, username, "lichess",
        imported, skipped, max_games, imported_at
    )
    if current_user:
        upsert_import_status(
            conn,
            current_user["id"],
            username,
            "lichess",
            imported,
            skipped,
            max_games,
            imported_at,
        )
    await track_server_event(
        conn,
        event_name="import.success",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "lichess",
            "max_games": max_games,
            "username_hash": username_hash,
            "imported": imported,
            "skipped": skipped,
            "is_authenticated": bool(current_user),
        },
    )
    conn.commit()

    try:
        schedule_insights_refresh(
            user_id=public_user_id,
            username=username,
            site="all",
            reason="import",
            allow_deep=False,
            allow_llm=False,
            source_user_id=public_user_id,
        )
        if current_user:
            schedule_insights_refresh(
                user_id=current_user["id"],
                username=username,
                site="all",
                reason="import",
                source_user_id=public_user_id,
            )
    except Exception as exc:
        logger.warning("Failed to schedule insights refresh after Lichess import: %s", exc)

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped
    )


@router.post("/chesscom", response_model=ImportResponse)
async def import_chesscom_games(
    request: ImportRequest,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """
    Import games from Chess.com for a user.
    Fetches games via Chess.com API, parses, and stores in database.
    """
    username = request.username.strip()
    max_games = request.max_games
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    username_hash = hash_username(username)
    await track_server_event(
        conn,
        event_name="import.start",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "chesscom",
            "max_games": max_games,
            "username_hash": username_hash,
            "is_authenticated": bool(current_user),
        },
    )

    existing_response = await _maybe_return_existing_import(
        conn=conn,
        current_user=current_user,
        http_request=http_request,
        username=username,
        site="chesscom",
        max_games=max_games,
        username_hash=username_hash,
    )
    if existing_response is not None:
        return existing_response

    public_user_id = ensure_public_user_for_username(conn, username)

    try:
        games = fetch_chesscom_games(username, max_games, conn)
    except ChesscomAPIError as e:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "chesscom",
                "max_games": max_games,
                "username_hash": username_hash,
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

    if not games:
        await track_server_event(
            conn,
            event_name="import.failed",
            user_id=current_user["id"] if current_user else None,
            request=http_request,
            properties={
                "site": "chesscom",
                "max_games": max_games,
                "username_hash": username_hash,
                "status_code": 404,
                "reason": "No games found",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' on Chess.com."
        )

    imported = 0
    skipped = 0
    for game in games:
        game["user_id"] = public_user_id
        if upsert_game(conn, game):
            imported += 1
        else:
            skipped += 1
    conn.commit()

    imported_at = datetime.now(timezone.utc).isoformat()
    upsert_import_status(
        conn, public_user_id, username, "chesscom",
        imported, skipped, max_games, imported_at
    )
    if current_user:
        upsert_import_status(
            conn,
            current_user["id"],
            username,
            "chesscom",
            imported,
            skipped,
            max_games,
            imported_at,
        )
    await track_server_event(
        conn,
        event_name="import.success",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "chesscom",
            "max_games": max_games,
            "username_hash": username_hash,
            "imported": imported,
            "skipped": skipped,
            "is_authenticated": bool(current_user),
        },
    )
    conn.commit()

    try:
        schedule_insights_refresh(
            user_id=public_user_id,
            username=username,
            site="all",
            reason="import",
            allow_deep=False,
            allow_llm=False,
            source_user_id=public_user_id,
        )
        if current_user:
            schedule_insights_refresh(
                user_id=current_user["id"],
                username=username,
                site="all",
                reason="import",
                source_user_id=public_user_id,
            )
    except Exception as exc:
        logger.warning("Failed to schedule insights refresh after Chess.com import: %s", exc)

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped
    )
