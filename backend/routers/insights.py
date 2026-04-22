"""AI insights endpoints.

Insights are shared by (username, site) - not owned by individual users.
"""

import os

import psycopg
import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query

from db import (
    get_latest_scan_job,
    get_problems_by_theme,
    get_quick_scan_problem_spotter,
)
from dependencies import get_db, validate_site
from insights import get_insights_state, schedule_insights_refresh
from schemas import InsightsProfileResponse, InsightsRequest, ProblemsByThemeResponse

router = APIRouter(tags=["insights"])

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(REDIS_URL, decode_responses=True)


def _get_import_progress(username: str) -> dict | None:
    """Check Redis for an active import across both sites. Returns combined progress."""
    canonical = username.strip().lower()
    total_done = 0
    total_total = 0
    any_active = False

    for site in ("lichess", "chesscom"):
        status_raw = _redis.get(f"import:{canonical}:{site}:status")
        if status_raw in ("streaming", "processing"):
            any_active = True
            done_raw = _redis.get(f"import:{canonical}:{site}:done")
            total_raw = _redis.get(f"import:{canonical}:{site}:total")
            total_done += int(done_raw) if done_raw else 0
            total_total += int(total_raw) if total_raw else 0

    if not any_active:
        return None

    status = "streaming" if total_total == 0 else "processing"
    return {"status": status, "done": total_done, "total": total_total}


def _build_profile_response(state: dict, conn: psycopg.Connection) -> InsightsProfileResponse:
    snapshot = state.get("snapshot") or {}
    active_job = state.get("active_job")
    username = state["username"]
    site = state["site"]

    response_payload = {
        "username": username,
        "site": site,
        "lifecycle_status": state["lifecycle_status"],
        "feature_version": state["feature_version"],
        "narrative_version": state["narrative_version"],
        "updated_at": snapshot.get("updated_at"),
        "coverage": snapshot.get("coverage"),
        "features": snapshot.get("features"),
        "narrative": snapshot.get("narrative"),
        "active_job": None,
        "scan_progress": None,
        "problem_spotter": None,
    }
    if active_job:
        response_payload["active_job"] = {
            "id": active_job.get("id"),
            "status": active_job.get("status"),
            "stage": active_job.get("stage"),
            "reason": active_job.get("reason"),
            "error": active_job.get("error"),
            "created_at": active_job.get("created_at"),
            "updated_at": active_job.get("updated_at"),
        }

    scan_job = get_latest_scan_job(conn, username, site)
    if scan_job:
        response_payload["scan_progress"] = {
            "status": scan_job["status"],
            "done": scan_job.get("games_done", 0),
            "total": scan_job.get("total_games", 0),
        }

    import_progress = _get_import_progress(username)
    if import_progress and import_progress["status"] in ("streaming", "processing"):
        existing_scan = response_payload.get("scan_progress")
        if not existing_scan or existing_scan["status"] not in ("running", "queued"):
            response_payload["scan_progress"] = {
                "status": "running",
                "done": import_progress["done"],
                "total": import_progress["total"],
            }

    problem_data = get_quick_scan_problem_spotter(conn, username, site)
    if problem_data and problem_data.get("total_problems", 0) > 0:
        response_payload["problem_spotter"] = problem_data

    return InsightsProfileResponse(**response_payload)


@router.get("/insights/profile", response_model=InsightsProfileResponse)
async def get_insights_profile(
    username: str = Query(..., min_length=1, max_length=50),
    site: str = Query(default="all", pattern="^(all|lichess|chesscom)$"),
    conn: psycopg.Connection = Depends(get_db),
):
    """Get current AI insights snapshot and background status.
    
    Insights are shared per chess username - not owned by individual users.
    """
    site = validate_site(site)
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    state = get_insights_state(username, site)
    return _build_profile_response(state, conn)


@router.post("/insights/profile", response_model=InsightsProfileResponse)
async def refresh_insights_profile(
    request: InsightsRequest,
    conn: psycopg.Connection = Depends(get_db),
):
    """Queue or reuse an AI insights generation job.
    
    Insights are shared per chess username - not owned by individual users.
    """
    site = validate_site(request.site)
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    schedule_insights_refresh(
        username=username,
        site=site,
        reason="manual_refresh",
        force=request.force,
    )

    state = get_insights_state(username, site)
    return _build_profile_response(state, conn)


@router.get("/insights/problems-by-theme", response_model=ProblemsByThemeResponse)
async def get_problems_by_theme_endpoint(
    username: str = Query(..., min_length=1, max_length=50),
    theme: str = Query(..., min_length=1, max_length=100),
    site: str = Query(default="all", pattern="^(all|lichess|chesscom)$"),
    time_control: str | None = Query(default=None),
    phase: str | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    page_size: int = Query(default=8, ge=1, le=100),
    conn: psycopg.Connection = Depends(get_db),
):
    """Return paginated problems matching a specific tactic theme for a user."""
    site = validate_site(site)
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    return get_problems_by_theme(
        conn, username, theme, site,
        time_control=time_control,
        phase=phase,
        page=page,
        page_size=page_size,
    )
