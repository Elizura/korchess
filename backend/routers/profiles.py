"""Chess profile management for authenticated users.

Profiles store saved Lichess/Chess.com accounts with ratings.
"""

import logging
from datetime import datetime, timezone
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from analytics import hash_username, track_server_event
from db import (
    bulk_upsert_games,
    get_chess_profile,
    get_chess_profiles,
    get_import_status,
    upsert_chess_profile,
    delete_chess_profile,
    upsert_import_status,
)
from lichess import fetch_lichess_pgn, fetch_lichess_profile, parse_pgn_games, LichessAPIError
from chesscom import fetch_chesscom_games, fetch_chesscom_profile, ChesscomAPIError
from insights import schedule_insights_refresh
from quick_scan import schedule_quick_scan

from schemas import (
    ChessProfile,
    ChessProfileCreate,
    ChessProfileListResponse,
    ChessProfileSyncResponse,
    ChessProfileWithImport,
    ImportResponse,
)
from dependencies import get_db
from auth import get_current_user

router = APIRouter(tags=["profiles"])
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


def _schedule_insights(username: str, site: str) -> None:
    """Schedule insights refresh and quick scan."""
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


def _db_row_to_profile(row: dict) -> ChessProfile:
    """Convert a DB row dict to a ChessProfile model."""
    return ChessProfile(
        chess_username=row["chess_username"],
        site=row["site"],
        bullet_rating=row.get("bullet_rating"),
        blitz_rating=row.get("blitz_rating"),
        rapid_rating=row.get("rapid_rating"),
        classical_rating=row.get("classical_rating"),
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        updated_at=row["updated_at"].isoformat() if row.get("updated_at") else None,
    )


async def _import_lichess_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 500,
) -> ImportResponse:
    """Import games from Lichess. Returns import result."""
    existing = get_import_status(conn, username, "lichess")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = _datetime_to_lichess_ms(last_synced_at)

    pgn_text = fetch_lichess_pgn(username, max_games, since=since_ms)

    now = datetime.now(timezone.utc)
    imported_at = now.isoformat()
    synced_at_value = now.isoformat()

    if not pgn_text.strip():
        if is_sync:
            upsert_import_status(
                conn, username, "lichess",
                0, 0, max_games, imported_at, synced_at_value,
            )
            return ImportResponse(username=username, imported=0, skipped=0, is_sync=True)
        return ImportResponse(username=username, imported=0, skipped=0, is_sync=False)

    games, parse_skipped = parse_pgn_games(pgn_text, username, conn)

    if not (games or parse_skipped):
        if is_sync:
            upsert_import_status(
                conn, username, "lichess",
                0, 0, max_games, imported_at, synced_at_value,
            )
            return ImportResponse(username=username, imported=0, skipped=0, is_sync=True)
        return ImportResponse(username=username, imported=0, skipped=0, is_sync=False)

    imported, db_skipped = bulk_upsert_games(conn, games)
    skipped = parse_skipped + db_skipped

    upsert_import_status(
        conn, username, "lichess",
        imported, skipped, max_games, imported_at, synced_at_value,
    )

    _schedule_insights(username, "lichess")

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped,
        is_sync=is_sync,
    )


async def _import_chesscom_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 500,
) -> ImportResponse:
    """Import games from Chess.com. Returns import result."""
    existing = get_import_status(conn, username, "chesscom")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_dt: datetime | None = last_synced_at if is_sync else None

    games = fetch_chesscom_games(username, max_games, conn, since=since_dt)

    now = datetime.now(timezone.utc)
    imported_at = now.isoformat()
    synced_at_value = now.isoformat()

    if not games:
        if is_sync:
            upsert_import_status(
                conn, username, "chesscom",
                0, 0, max_games, imported_at, synced_at_value,
            )
            return ImportResponse(username=username, imported=0, skipped=0, is_sync=True)
        return ImportResponse(username=username, imported=0, skipped=0, is_sync=False)

    imported, skipped = bulk_upsert_games(conn, games)

    upsert_import_status(
        conn, username, "chesscom",
        imported, skipped, max_games, imported_at, synced_at_value,
    )

    _schedule_insights(username, "chesscom")

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped,
        is_sync=is_sync,
    )


@router.post("", response_model=ChessProfileWithImport)
async def create_profile(
    body: ChessProfileCreate,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a chess profile for the authenticated user.
    
    Validates the user exists on the platform, fetches their ratings,
    and triggers an initial game import.
    """
    username = body.username.strip()
    site = body.site
    user_id = current_user["id"]

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    existing_profile = get_chess_profile(conn, user_id, username, site)
    if existing_profile:
        raise HTTPException(
            status_code=409,
            detail=f"Profile for {username} on {site} already exists. Use sync to update."
        )

    try:
        if site == "lichess":
            profile_data = fetch_lichess_profile(username)
        else:
            profile_data = fetch_chesscom_profile(username)
    except (LichessAPIError, ChesscomAPIError) as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"User '{username}' not found on {site}."
            )
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    actual_username = profile_data.get("username", username)

    profile_row = upsert_chess_profile(
        conn,
        user_id=user_id,
        chess_username=actual_username,
        site=site,
        bullet_rating=profile_data.get("bullet_rating"),
        blitz_rating=profile_data.get("blitz_rating"),
        rapid_rating=profile_data.get("rapid_rating"),
        classical_rating=profile_data.get("classical_rating"),
    )
    conn.commit()

    try:
        if site == "lichess":
            import_result = await _import_lichess_games(actual_username, conn)
        else:
            import_result = await _import_chesscom_games(actual_username, conn)
        conn.commit()
    except (LichessAPIError, ChesscomAPIError) as e:
        import_result = ImportResponse(
            username=actual_username,
            imported=0,
            skipped=0,
            is_sync=False,
        )

    await track_server_event(
        conn,
        event_name="profile.created",
        user_id=user_id,
        request=http_request,
        properties={
            "site": site,
            "username": hash_username(actual_username),
            "imported": import_result.imported,
        },
    )
    conn.commit()

    return ChessProfileWithImport(
        profile=_db_row_to_profile(profile_row),
        import_result=import_result,
    )


@router.get("", response_model=ChessProfileListResponse)
async def list_profiles(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get all chess profiles for the authenticated user."""
    user_id = current_user["id"]
    rows = get_chess_profiles(conn, user_id)
    profiles = [_db_row_to_profile(row) for row in rows]
    return ChessProfileListResponse(profiles=profiles)


@router.delete("/{site}/{username}")
async def remove_profile(
    site: Literal["lichess", "chesscom"],
    username: str,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a chess profile."""
    user_id = current_user["id"]
    deleted = delete_chess_profile(conn, user_id, username, site)
    conn.commit()

    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found.")

    await track_server_event(
        conn,
        event_name="profile.deleted",
        user_id=user_id,
        request=http_request,
        properties={
            "site": site,
            "username": hash_username(username),
        },
    )
    conn.commit()

    return {"status": "deleted"}


@router.post("/{site}/{username}/sync", response_model=ChessProfileSyncResponse)
async def sync_profile(
    site: Literal["lichess", "chesscom"],
    username: str,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Sync a chess profile: refresh ratings and import new games."""
    user_id = current_user["id"]

    existing_profile = get_chess_profile(conn, user_id, username, site)
    if not existing_profile:
        raise HTTPException(status_code=404, detail="Profile not found.")

    try:
        if site == "lichess":
            profile_data = fetch_lichess_profile(username)
        else:
            profile_data = fetch_chesscom_profile(username)
    except (LichessAPIError, ChesscomAPIError) as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"User '{username}' not found on {site}."
            )
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    actual_username = profile_data.get("username", username)

    profile_row = upsert_chess_profile(
        conn,
        user_id=user_id,
        chess_username=actual_username,
        site=site,
        bullet_rating=profile_data.get("bullet_rating"),
        blitz_rating=profile_data.get("blitz_rating"),
        rapid_rating=profile_data.get("rapid_rating"),
        classical_rating=profile_data.get("classical_rating"),
    )
    conn.commit()

    try:
        if site == "lichess":
            sync_result = await _import_lichess_games(actual_username, conn)
        else:
            sync_result = await _import_chesscom_games(actual_username, conn)
        conn.commit()
    except (LichessAPIError, ChesscomAPIError) as e:
        sync_result = ImportResponse(
            username=actual_username,
            imported=0,
            skipped=0,
            is_sync=True,
        )

    await track_server_event(
        conn,
        event_name="profile.synced",
        user_id=user_id,
        request=http_request,
        properties={
            "site": site,
            "username": hash_username(actual_username),
            "imported": sync_result.imported,
        },
    )
    conn.commit()

    return ChessProfileSyncResponse(
        profile=_db_row_to_profile(profile_row),
        sync_result=sync_result,
    )
