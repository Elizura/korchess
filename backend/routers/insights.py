"""AI insights endpoints."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from db import (
    ensure_public_user_for_username,
    get_latest_scan_job,
    get_problems_by_theme,
    get_public_user_id_for_username,
    get_quick_scan_problem_spotter,
)
from dependencies import get_db, validate_site
from insights import get_insights_state, schedule_insights_refresh
from schemas import InsightsProfileResponse, InsightsRequest, ProblemsByThemeResponse

router = APIRouter(tags=["insights"])


def _build_profile_response(state: dict, conn: psycopg.Connection) -> InsightsProfileResponse:
    snapshot = state.get("snapshot") or {}
    active_job = state.get("active_job")
    username = state["username"]
    site = state["site"]
    user_id = state.get("user_id") or ""

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

    scan_job = get_latest_scan_job(conn, user_id, username, site)
    if scan_job:
        response_payload["scan_progress"] = {
            "status": scan_job["status"],
            "done": scan_job.get("games_done", 0),
            "total": scan_job.get("total_games", 0),
        }

    problem_data = get_quick_scan_problem_spotter(conn, user_id, username, site)
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

    public_user_id = get_public_user_id_for_username(conn, username)
    state = get_insights_state(public_user_id, username, site)
    state["user_id"] = public_user_id
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

    public_user_id = ensure_public_user_for_username(conn, username)

    schedule_insights_refresh(
        user_id=public_user_id,
        username=username,
        site=site,
        reason="manual_refresh",
        force=request.force,
    )

    state = get_insights_state(public_user_id, username, site)
    state["user_id"] = public_user_id
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

    public_user_id = get_public_user_id_for_username(conn, username)
    return get_problems_by_theme(
        conn, public_user_id, username, theme, site,
        time_control=time_control,
        phase=phase,
        page=page,
        page_size=page_size,
    )
