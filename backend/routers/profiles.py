"""Chess profile management for authenticated users.

Profiles store saved Lichess/Chess.com accounts with ratings.
Games are imported via streaming + Celery queue (fire-as-you-stream).
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
from chesscom import parse_chesscom_game, fetch_chesscom_profile, ChesscomAPIError
from db import (
    delete_all_user_site_data,
    delete_chess_profile,
    get_chess_profile,
    get_chess_profiles,
    get_import_status,
    upsert_chess_profile,
)
from game_streamer import (
    ChesscomStreamError,
    LichessStreamError,
    stream_chesscom_games,
    stream_lichess_pgns,
)
from lichess import fetch_lichess_profile, parse_pgn_games, LichessAPIError
from opening_match import best_opening_match, game_to_uci_plies
from tasks import process_game

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


def _import_lichess_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 250,
) -> ImportResponse:
    """Stream Lichess games and fire each into the queue immediately."""
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "lichess")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_ms: int | None = None
    if is_sync and last_synced_at is not None:
        since_ms = _datetime_to_lichess_ms(last_synced_at)

    _redis.set(_import_key(canonical, "lichess", "status"), "streaming", ex=3600)

    total_enqueued = 0
    parse_skipped = 0

    for pgn_chunk in stream_lichess_pgns(username, max_games, since=since_ms):
        for pgn_text in pgn_chunk:
            parsed_games, skipped = parse_pgn_games(pgn_text, username, conn)
            parse_skipped += skipped
            for game_data in parsed_games:
                process_game.delay(game_data, username, "lichess")
                total_enqueued += 1

    if total_enqueued == 0:
        _redis.delete(_import_key(canonical, "lichess", "status"))
        return ImportResponse(username=username, imported=0, skipped=parse_skipped, is_sync=is_sync)

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }
    _redis.set(_import_key(canonical, "lichess", "total"), total_enqueued, ex=3600)
    _redis.set(_import_key(canonical, "lichess", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(_import_key(canonical, "lichess", "status"), "processing", ex=3600)

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
        is_sync=is_sync,
    )


def _import_chesscom_games(
    username: str,
    conn: psycopg.Connection,
    max_games: int = 250,
) -> ImportResponse:
    """Stream Chess.com games and fire each into the queue immediately."""
    canonical = username.strip().lower()
    existing = get_import_status(conn, username, "chesscom")
    existing_games = int(existing.get("total_games") or 0)
    last_synced_at = _parse_synced_at(existing.get("last_synced_at"))
    is_sync = existing_games > 0 and last_synced_at is not None

    since_dt: datetime | None = last_synced_at if is_sync else None

    _redis.set(_import_key(canonical, "chesscom", "status"), "streaming", ex=3600)

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

    if total_enqueued == 0:
        _redis.delete(_import_key(canonical, "chesscom", "status"))
        return ImportResponse(username=username, imported=0, skipped=parse_skipped, is_sync=is_sync)

    import_meta = {
        "max_games": max_games,
        "is_sync": is_sync,
        "parse_skipped": parse_skipped,
    }
    _redis.set(_import_key(canonical, "chesscom", "total"), total_enqueued, ex=3600)
    _redis.set(_import_key(canonical, "chesscom", "meta"), json.dumps(import_meta), ex=3600)
    _redis.set(_import_key(canonical, "chesscom", "status"), "processing", ex=3600)

    return ImportResponse(
        username=username,
        imported=total_enqueued,
        skipped=parse_skipped,
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
        if site == "lichess":
            import_result = _import_lichess_games(username, conn)
        else:
            import_result = _import_chesscom_games(username, conn)
    except (LichessStreamError, ChesscomStreamError) as e:
        if e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        raise HTTPException(status_code=502, detail=e.message)

    track_server_event(
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
            sync_result = _import_lichess_games(actual_username, conn)
        else:
            sync_result = _import_chesscom_games(actual_username, conn)
    except (LichessStreamError, ChesscomStreamError):
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
