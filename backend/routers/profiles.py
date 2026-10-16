"""Chess profile management for authenticated users.

Profiles store saved Lichess/Chess.com accounts with ratings.
Games are imported via streaming + Celery queue (fire-as-you-stream),
delegated to import_service which is shared with the public import router.
"""

import logging
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from services.analytics import hash_username, track_server_event
from services.chesscom import fetch_chesscom_profile, ChesscomAPIError
from repository.db import (
    delete_all_user_site_data,
    delete_chess_profile,
    get_chess_profile,
    get_chess_profiles,
    upsert_chess_profile,
)
from services.import_service import (
    UPSTREAM_ERRORS,
    import_chesscom_games,
    import_lichess_games,
    upstream_http_status,
)
from services.lichess import fetch_lichess_profile, LichessAPIError

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


@router.post("", response_model=ChessProfileWithImport)
async def create_profile(
    body: ChessProfileCreate,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a chess profile for the authenticated user.
    
    Validates the user exists on the platform and fetches their ratings.
    Games are imported separately via POST /{site}/{username}/import.
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
        fetch_profile = fetch_lichess_profile if site == "lichess" else fetch_chesscom_profile
        profile_data = await run_in_threadpool(fetch_profile, username)
    except (LichessAPIError, ChesscomAPIError) as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"User '{username}' not found on {site}."
            )
        raise HTTPException(status_code=upstream_http_status(e), detail=e.message)

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
    """Delete a chess profile and ALL associated data (games, analysis, insights)."""
    user_id = current_user["id"]

    existing = get_chess_profile(conn, user_id, username, site)
    if not existing:
        raise HTTPException(status_code=404, detail="Profile not found.")

    deleted_counts = delete_all_user_site_data(conn, username, site)
    delete_chess_profile(conn, user_id, username, site)
    conn.commit()

    await track_server_event(
        conn,
        event_name="profile.deleted",
        user_id=user_id,
        request=http_request,
        properties={
            "site": site,
            "username": hash_username(username),
            "games_deleted": deleted_counts.get("games", 0),
        },
    )
    conn.commit()

    return {
        "status": "deleted",
        "deleted": deleted_counts,
    }


@router.post("/{site}/{username}/import", response_model=ImportResponse)
async def import_profile_games(
    site: Literal["lichess", "chesscom"],
    username: str,
    http_request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Import games for an existing chess profile."""
    user_id = current_user["id"]

    existing_profile = get_chess_profile(conn, user_id, username, site)
    if not existing_profile:
        raise HTTPException(status_code=404, detail="Profile not found.")

    try:
        importer = import_lichess_games if site == "lichess" else import_chesscom_games
        import_result = await run_in_threadpool(importer, username, conn)
    except UPSTREAM_ERRORS as e:
        raise HTTPException(status_code=upstream_http_status(e), detail=e.message)

    await track_server_event(
        conn,
        event_name="profile.imported",
        user_id=user_id,
        request=http_request,
        properties={
            "site": site,
            "username": hash_username(username),
            "imported": import_result.imported,
        },
    )
    conn.commit()

    return import_result


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
        fetch_profile = fetch_lichess_profile if site == "lichess" else fetch_chesscom_profile
        profile_data = await run_in_threadpool(fetch_profile, username)
    except (LichessAPIError, ChesscomAPIError) as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"User '{username}' not found on {site}."
            )
        raise HTTPException(status_code=upstream_http_status(e), detail=e.message)

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
        importer = import_lichess_games if site == "lichess" else import_chesscom_games
        sync_result = await run_in_threadpool(importer, actual_username, conn)
    except UPSTREAM_ERRORS as e:
        # Ratings are already saved; report zero new games rather than failing the
        # whole sync. last_synced_at is untouched, so the next sync retries them.
        logger.warning("Game sync for %s/%s failed upstream: %s", site, actual_username, e.message)
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
