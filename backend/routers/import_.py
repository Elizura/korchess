"""Import games from Lichess and Chess.com.

Games and imports are shared by (username, site) - not owned by individual users.
"""

import logging
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from analytics import hash_username, track_server_event
from db import (
    bulk_upsert_games,
    get_import_history,
    get_import_status,
    upsert_import_status,
)
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError
from chesscom import fetch_chesscom_games, ChesscomAPIError
from insights import schedule_insights_refresh
from quick_scan import schedule_quick_scan

from schemas import ImportRequest, ImportResponse, ImportHistoryResponse, ImportHistoryItem
from dependencies import get_db
from auth import get_optional_user, get_registered_user

router = APIRouter(tags=["import"])
logger = logging.getLogger(__name__)


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


def _record_import_status(
    conn: psycopg.Connection,
    username: str,
    site: str,
    imported: int,
    skipped: int,
    max_games: int,
    imported_at: str,
    last_synced_at: str,
) -> None:
    """Write import status for username/site."""
    upsert_import_status(
        conn, username, site,
        imported, skipped, max_games, imported_at,
        last_synced_at=last_synced_at,
    )


def _schedule_insights(username: str, site: str) -> None:
    """Schedule insights refresh and quick scan.
    
    Insights are shared per chess username - not owned by individual users.
    """
    try:
        schedule_insights_refresh(
            username=username,
            site="all",
            reason="import",
        )
    except Exception as exc:
        logger.warning("Failed to schedule insights refresh after %s import: %s", site, exc)

    try:
        schedule_quick_scan(username, site="all")
    except Exception as exc:
        logger.warning("Failed to schedule quick scan after %s import: %s", site, exc)


@router.get("/history", response_model=ImportHistoryResponse)
async def get_import_history_endpoint(
    conn: psycopg.Connection = Depends(get_db),
):
    """Get last 10 import records."""
    rows = get_import_history(conn, limit=10)
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
    existing = get_import_status(conn, username, "lichess")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = _datetime_to_lichess_ms(last_synced_at)

    await track_server_event(
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

    try:
        pgn_text = fetch_lichess_pgn(username, max_games, since=since_ms)
    except LichessAPIError as e:
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

    now = datetime.now(timezone.utc)
    imported_at = now.isoformat()
    synced_at_value = now.isoformat()

    if not pgn_text.strip():
        if is_sync:
            _record_import_status(
                conn, username, "lichess",
                0, 0, max_games, imported_at, synced_at_value,
            )
            conn.commit()
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

    games, parse_skipped = parse_pgn_games(pgn_text, username, conn)

    if not (games or parse_skipped):
        if is_sync:
            _record_import_status(
                conn, username, "lichess",
                0, 0, max_games, imported_at, synced_at_value,
            )
            conn.commit()
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
                "reason": "No games parsed from PGN",
            },
        )
        conn.commit()
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'."
        )

    imported, db_skipped = bulk_upsert_games(conn, games)
    skipped = parse_skipped + db_skipped
    conn.commit()

    _record_import_status(
        conn, username, "lichess",
        imported, skipped, max_games, imported_at, synced_at_value,
    )

    await track_server_event(
        conn,
        event_name="import.success",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "lichess",
            "max_games": max_games,
            "username": username_hash,
            "imported": imported,
            "skipped": skipped,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )
    conn.commit()

    _schedule_insights(username, "lichess")

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped,
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

    try:
        games = fetch_chesscom_games(username, max_games, conn, since=since_dt)
    except ChesscomAPIError as e:
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

    now = datetime.now(timezone.utc)
    imported_at = now.isoformat()
    synced_at_value = now.isoformat()

    if not games:
        if is_sync:
            _record_import_status(
                conn, username, "chesscom",
                0, 0, max_games, imported_at, synced_at_value,
            )
            conn.commit()
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

    imported, skipped = bulk_upsert_games(conn, games)
    conn.commit()

    _record_import_status(
        conn, username, "chesscom",
        imported, skipped, max_games, imported_at, synced_at_value,
    )

    await track_server_event(
        conn,
        event_name="import.success",
        user_id=current_user["id"] if current_user else None,
        request=http_request,
        properties={
            "site": "chesscom",
            "max_games": max_games,
            "username": username_hash,
            "imported": imported,
            "skipped": skipped,
            "is_authenticated": bool(current_user),
            "is_sync": is_sync,
        },
    )
    conn.commit()

    _schedule_insights(username, "chesscom")

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped,
        is_sync=is_sync,
    )
