"""Openings and games-by-opening endpoints.

Game data is shared by (username, site) - not owned by individual users.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from db import (
    get_games_by_opening,
    get_import_status,
    get_openings_stats,
    get_variations_stats,
)
from schemas import (
    OpeningStats,
    ImportStatusResponse,
    OpeningGamesResponse,
    VariationStats,
)
from dependencies import get_db, validate_site

router = APIRouter(tags=["openings"])


@router.get("/openings/{site}/{username}", response_model=list[OpeningStats])
async def get_openings_report(
    site: str,
    username: str,
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    limit: int = Query(default=10, ge=1, le=100, description="Max number of openings to return (top by games)"),
    conn: psycopg.Connection = Depends(get_db),
):
    """
    Get aggregated opening statistics for a user.
    Returns top N openings by games played (default 10).
    Returns an empty list when no games match the selected filters.
    Supports filtering by color, time control, and site.
    Site can be 'lichess', 'chesscom', or 'all'.
    """
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    stats = get_openings_stats(conn, username, color, time_class, site, limit)

    return [OpeningStats(**s) for s in stats]


@router.get("/import-status/{site}/{username}", response_model=ImportStatusResponse)
async def get_import_status_endpoint(
    site: str,
    username: str,
    conn: psycopg.Connection = Depends(get_db),
):
    """Get last import status and total games count for a user on a specific site."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    status = get_import_status(conn, username, site)

    return ImportStatusResponse(**status)


@router.get("/games/{site}/{username}", response_model=OpeningGamesResponse)
async def get_games_for_opening(
    site: str,
    username: str,
    opening_key: str = Query(..., description="Opening key for the opening"),
    variation_key: str | None = Query(default=None, description="Variation key for the opening"),
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    result: str = Query(default="all", pattern="^(all|win|draw|loss)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    conn: psycopg.Connection = Depends(get_db),
):
    """Get recent games and summary for a user and opening with filters."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    if not opening_key:
        raise HTTPException(status_code=400, detail="Opening key is required.")

    result_data = get_games_by_opening(
        conn, username, opening_key, variation_key, color, time_class, result, offset, limit, site
    )

    if result_data["summary"]["total_games"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for {username} with opening {opening_key}."
        )

    return OpeningGamesResponse(**result_data)


@router.get("/openings/{site}/{username}/variations", response_model=list[VariationStats])
async def get_opening_variations(
    site: str,
    username: str,
    opening_key: str = Query(..., description="Opening key for the opening"),
    color: str = Query(default="all", pattern="^(all|white|black)$"),
    time_class: str = Query(default="all", pattern="^(all|blitz|rapid|classical)$"),
    conn: psycopg.Connection = Depends(get_db),
):
    """Get variation statistics for a user's opening key with filters."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if not opening_key:
        raise HTTPException(status_code=400, detail="Opening key is required.")

    stats = get_variations_stats(conn, username, opening_key, color, time_class, site)

    return [VariationStats(**s) for s in stats]
