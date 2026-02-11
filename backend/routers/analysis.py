"""Game analysis endpoints (lightweight and full)."""

import asyncio
import json
import uuid

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from db import (
    get_connection,
    get_analysis,
    save_analysis,
    get_game_by_id,
    get_full_analysis,
    save_full_analysis,
    create_analysis_job,
    get_analysis_job,
    delete_analysis_job,
)
from analysis import run_lightweight_analysis
from full_analysis import run_full_analysis

from schemas import AnalysisResponse, FullAnalysisResponse
from dependencies import get_db, validate_site
from auth import get_registered_user

router = APIRouter(tags=["analysis"])

MAX_CONCURRENT_ANALYSES = 2
active_analysis_count = 0
active_analysis_lock = asyncio.Lock()


async def run_analysis_background(
    job_id: str,
    user_id: str,
    username: str,
    game_id: str,
    pgn: str,
    depth: int,
    multipv: int,
    site: str
):
    """Background task to run Stockfish analysis."""
    global active_analysis_count

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_full_analysis(pgn, depth, multipv)
        )

        conn = get_connection()
        try:
            save_full_analysis(
                conn, user_id, username, game_id,
                depth=depth,
                multipv=multipv,
                moves_json=json.dumps(result["moves"]),
                summary_json=json.dumps(result["summary"]),
                meta_json=json.dumps(result["meta"]),
                site=site
            )
            delete_analysis_job(conn, job_id)
            conn.commit()
            print(f"[Analysis] Completed for game {game_id} on {site}")
        finally:
            conn.close()

    except Exception as e:
        print(f"[Analysis] Failed for game {game_id} on {site}: {e}")
        conn = get_connection()
        try:
            delete_analysis_job(conn, job_id)
            conn.commit()
        finally:
            conn.close()

    finally:
        async with active_analysis_lock:
            active_analysis_count -= 1
            print(f"[Analysis] Active count now: {active_analysis_count}")


@router.get("/{site}/{username}/{game_id}", response_model=AnalysisResponse)
async def get_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get cached analysis for a game."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached = get_analysis(conn, current_user["id"], username, game_id, site)

    if not cached:
        return AnalysisResponse(status="missing")

    return AnalysisResponse(
        status="ready",
        analysis=json.loads(cached["result_json"]),
        created_at=cached["created_at"]
    )


@router.post("/{site}/{username}/{game_id}", response_model=AnalysisResponse)
async def run_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Run analysis on a game (or return cached)."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached = get_analysis(conn, current_user["id"], username, game_id, site)
    if cached:
        return AnalysisResponse(
            status="ready",
            analysis=json.loads(cached["result_json"]),
            created_at=cached["created_at"]
        )

    game = get_game_by_id(conn, current_user["id"], username, game_id, site)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.get("pgn"):
        raise HTTPException(status_code=400, detail="Game has no PGN")

    try:
        result = run_lightweight_analysis(game["pgn"], game["color"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    settings = {"time_ms": 150, "checkpoints": [10, 20, 30, 40]}
    save_analysis(
        conn, current_user["id"], username, game_id, site,
        engine_name="stockfish",
        engine_version="15+",
        settings_json=json.dumps(settings),
        result_json=json.dumps(result)
    )
    conn.commit()

    saved = get_analysis(conn, current_user["id"], username, game_id, site)

    return AnalysisResponse(
        status="ready",
        analysis=result,
        created_at=saved["created_at"] if saved else None
    )


@router.get("/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def get_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get full analysis status for a game."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached = get_full_analysis(conn, current_user["id"], username, game_id, depth, multipv, site)
    if cached:
        return FullAnalysisResponse(
            status="completed",
            analysis={
                "moves": json.loads(cached["moves_json"]),
                "summary": json.loads(cached["summary_json"]),
                "meta": json.loads(cached["meta_json"])
            },
            created_at=cached["created_at"]
        )

    job = get_analysis_job(conn, current_user["id"], username, game_id, depth, multipv, site)
    if job:
        return FullAnalysisResponse(status="processing")

    return FullAnalysisResponse(status="missing")


@router.post("/{site}/{username}/{game_id}/full", response_model=FullAnalysisResponse)
async def run_full_analysis_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Start full move-by-move analysis on a game (async with background task)."""
    global active_analysis_count

    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached = get_full_analysis(conn, current_user["id"], username, game_id, depth, multipv, site)
    if cached:
        return FullAnalysisResponse(
            status="completed",
            analysis={
                "moves": json.loads(cached["moves_json"]),
                "summary": json.loads(cached["summary_json"]),
                "meta": json.loads(cached["meta_json"])
            },
            created_at=cached["created_at"]
        )

    existing_job = get_analysis_job(conn, current_user["id"], username, game_id, depth, multipv, site)
    if existing_job:
        return FullAnalysisResponse(status="processing")

    async with active_analysis_lock:
        if active_analysis_count >= MAX_CONCURRENT_ANALYSES:
            raise HTTPException(
                status_code=429,
                detail="Server busy. Max 2 analyses can run at once. Try again shortly."
            )
        active_analysis_count += 1
        print(f"[Analysis] Starting new analysis. Active count: {active_analysis_count}")

    game = get_game_by_id(conn, current_user["id"], username, game_id, site)
    if not game:
        async with active_analysis_lock:
            active_analysis_count -= 1
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.get("pgn"):
        async with active_analysis_lock:
            active_analysis_count -= 1
        raise HTTPException(status_code=400, detail="Game has no PGN")

    job_id = str(uuid.uuid4())
    create_analysis_job(conn, job_id, current_user["id"], username, game_id, depth, multipv, site)
    conn.commit()

    asyncio.create_task(run_analysis_background(
        job_id, current_user["id"], username, game_id, game["pgn"], depth, multipv, site
    ))

    return FullAnalysisResponse(status="processing")
