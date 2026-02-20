"""Game analysis endpoints (lightweight and full)."""

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from db import (
    get_connection,
    get_analysis,
    save_analysis,
    get_game_by_id,
    get_full_analysis,
    save_full_analysis,
    save_full_analysis_insights,
    create_analysis_job,
    get_analysis_job,
    delete_analysis_job,
    count_user_full_analysis_completed_utc_day,
)
from analysis import run_lightweight_analysis
from full_analysis import run_full_analysis
from game_insights_narration import ensure_narration
from single_game_insights import compute_single_game_insights

from schemas import AnalysisResponse, FullAnalysisResponse, SingleGameInsightsResponse
from dependencies import get_db, validate_site
from auth import get_registered_user

router = APIRouter(tags=["analysis"])

MAX_CONCURRENT_ANALYSES = 2
active_analysis_count = 0
active_analysis_lock = asyncio.Lock()
DEEP_ANALYSIS_DAILY_LIMIT = max(0, int(os.environ.get("DEEP_ANALYSIS_DAILY_LIMIT", "3")))
DEEP_ANALYSIS_LIMIT_FORCE = os.environ.get("DEEP_ANALYSIS_LIMIT_FORCE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEEP_ANALYSIS_UNLIMITED_EMAILS = {
    entry.strip().lower()
    for entry in os.environ.get("DEEP_ANALYSIS_UNLIMITED_EMAILS", "").split(",")
    if entry.strip()
}


def _is_production_environment() -> bool:
    for key in ("ENVIRONMENT", "APP_ENV", "NODE_ENV"):
        if os.environ.get(key, "").strip().lower() == "production":
            return True
    return False


def _is_limit_enabled() -> bool:
    return DEEP_ANALYSIS_LIMIT_FORCE or _is_production_environment()


def _is_unlimited_email(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in DEEP_ANALYSIS_UNLIMITED_EMAILS


def _current_utc_day_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def _build_full_analysis_payload(cached: dict) -> dict:
    return {
        "moves": json.loads(cached["moves_json"]),
        "summary": json.loads(cached["summary_json"]),
        "meta": json.loads(cached["meta_json"]),
    }


def _load_cached_insights(cached: dict) -> dict | None:
    raw = cached.get("insights_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


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
        full_analysis = {
            "moves": result["moves"],
            "summary": result["summary"],
            "meta": result["meta"],
        }

        conn = get_connection()
        try:
            insights_payload: dict | None = None
            try:
                game_meta = get_game_by_id(conn, user_id, username, game_id, site)
                if game_meta:
                    raw_insights = compute_single_game_insights(
                        site=site,
                        game_id=game_id,
                        username=username,
                        depth=depth,
                        multipv=multipv,
                        full_analysis=full_analysis,
                        game_meta=game_meta,
                    )
                    try:
                        insights_payload = await asyncio.to_thread(
                            ensure_narration,
                            raw_insights,
                            game_id,
                            game_meta,
                        )
                    except Exception as narration_err:
                        print(
                            f"[Analysis] Narration generation failed for game {game_id} on {site}: {narration_err}"
                        )
                        insights_payload = raw_insights
            except Exception as insights_err:
                print(f"[Analysis] Insights generation failed for game {game_id} on {site}: {insights_err}")

            save_full_analysis(
                conn, user_id, username, game_id,
                depth=depth,
                multipv=multipv,
                moves_json=json.dumps(full_analysis["moves"]),
                summary_json=json.dumps(full_analysis["summary"]),
                meta_json=json.dumps(full_analysis["meta"]),
                insights_json=json.dumps(insights_payload) if insights_payload else None,
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


@router.get(
    "/{site}/{username}/{game_id}/single-insights",
    response_model=SingleGameInsightsResponse,
)
async def get_single_game_insights_endpoint(
    site: str,
    username: str,
    game_id: str,
    depth: int = Query(default=18, ge=1, le=30),
    multipv: int = Query(default=1, ge=1, le=5),
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get deterministic single-game rule insights derived from cached full analysis."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    cached = get_full_analysis(conn, current_user["id"], username, game_id, depth, multipv, site)
    if not cached:
        job = get_analysis_job(conn, current_user["id"], username, game_id, depth, multipv, site)
        if job:
            return SingleGameInsightsResponse(
                status="analysis_processing",
                version="single_game_rules_v2",
            )
        return SingleGameInsightsResponse(
            status="analysis_missing",
            version="single_game_rules_v2",
        )

    cached_insights = _load_cached_insights(cached)
    if cached_insights:
        return SingleGameInsightsResponse(**cached_insights)

    game = get_game_by_id(conn, current_user["id"], username, game_id, site)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    full_analysis = _build_full_analysis_payload(cached)

    insights_payload = compute_single_game_insights(
        site=site,
        game_id=game_id,
        username=username,
        depth=depth,
        multipv=multipv,
        full_analysis=full_analysis,
        game_meta=game,
    )

    try:
        save_full_analysis_insights(
            conn,
            current_user["id"],
            username,
            game_id,
            depth,
            multipv,
            site,
            json.dumps(insights_payload),
        )
        conn.commit()
    except Exception as persist_err:
        print(f"[Analysis] Failed to persist computed insights for game {game_id} on {site}: {persist_err}")

    return SingleGameInsightsResponse(**insights_payload)


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
        full_analysis = _build_full_analysis_payload(cached)
        return FullAnalysisResponse(
            status="completed",
            analysis=full_analysis,
            insights=_load_cached_insights(cached),
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
        full_analysis = _build_full_analysis_payload(cached)
        insights_payload = _load_cached_insights(cached)
        insights_updated = False
        if not insights_payload:
            try:
                game = get_game_by_id(conn, current_user["id"], username, game_id, site)
                if game:
                    insights_payload = compute_single_game_insights(
                        site=site,
                        game_id=game_id,
                        username=username,
                        depth=depth,
                        multipv=multipv,
                        full_analysis=full_analysis,
                        game_meta=game,
                    )
                    insights_updated = True
            except Exception as insights_err:
                print(
                    f"[Analysis] Unable to hydrate missing insights for cached analysis "
                    f"{game_id} on {site}: {insights_err}"
                )

        if insights_payload:
            game_meta_for_narration = get_game_by_id(conn, current_user["id"], username, game_id, site)
            try:
                narrated_payload = await asyncio.to_thread(
                    ensure_narration,
                    insights_payload,
                    game_id,
                    game_meta_for_narration,
                    True,
                )
                if narrated_payload != insights_payload:
                    insights_payload = narrated_payload
                    insights_updated = True
            except Exception as narration_err:
                print(
                    f"[Analysis] Unable to hydrate narration for cached analysis "
                    f"{game_id} on {site}: {narration_err}"
                )

        if insights_updated and insights_payload:
            try:
                save_full_analysis_insights(
                    conn,
                    current_user["id"],
                    username,
                    game_id,
                    depth,
                    multipv,
                    site,
                    json.dumps(insights_payload),
                )
                conn.commit()
            except Exception as persist_err:
                print(
                    f"[Analysis] Failed to persist hydrated insights for cached analysis "
                    f"{game_id} on {site}: {persist_err}"
                )

        return FullAnalysisResponse(
            status="completed",
            analysis=full_analysis,
            insights=insights_payload,
            created_at=cached["created_at"]
        )

    existing_job = get_analysis_job(conn, current_user["id"], username, game_id, depth, multipv, site)
    if existing_job:
        return FullAnalysisResponse(status="processing")

    if (
        _is_limit_enabled()
        and DEEP_ANALYSIS_DAILY_LIMIT > 0
        and not _is_unlimited_email(current_user.get("email"))
    ):
        day_start_utc, day_end_utc = _current_utc_day_bounds()
        completed_today = count_user_full_analysis_completed_utc_day(
            conn,
            current_user["id"],
            day_start_utc,
            day_end_utc,
        )
        if completed_today >= DEEP_ANALYSIS_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily in-depth analysis limit reached ({DEEP_ANALYSIS_DAILY_LIMIT} per day). "
                    "You can request more after 00:00 UTC."
                ),
            )

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
