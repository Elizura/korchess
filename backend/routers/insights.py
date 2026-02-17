"""AI insights endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_registered_user
from dependencies import validate_site
from insights import get_insights_state, schedule_insights_refresh
from schemas import InsightsProfileResponse, InsightsRequest

router = APIRouter(tags=["insights"])


def _build_profile_response(state: dict) -> InsightsProfileResponse:
    snapshot = state.get("snapshot") or {}
    active_job = state.get("active_job")
    response_payload = {
        "username": state["username"],
        "site": state["site"],
        "lifecycle_status": state["lifecycle_status"],
        "feature_version": state["feature_version"],
        "narrative_version": state["narrative_version"],
        "updated_at": snapshot.get("updated_at"),
        "coverage": snapshot.get("coverage"),
        "features": snapshot.get("features"),
        "narrative": snapshot.get("narrative"),
        "active_job": None,
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
    return InsightsProfileResponse(**response_payload)


@router.get("/insights/profile", response_model=InsightsProfileResponse)
async def get_insights_profile(
    username: str = Query(..., min_length=1, max_length=50),
    site: str = Query(default="all", pattern="^(all|lichess|chesscom)$"),
    current_user: dict = Depends(get_registered_user),
):
    """Get current AI insights snapshot and background status."""
    site = validate_site(site)
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    state = get_insights_state(current_user["id"], username, site)
    return _build_profile_response(state)


@router.post("/insights/profile", response_model=InsightsProfileResponse)
async def refresh_insights_profile(
    request: InsightsRequest,
    current_user: dict = Depends(get_registered_user),
):
    """Queue or reuse an AI insights generation job for the user."""
    site = validate_site(request.site)
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    schedule_insights_refresh(
        user_id=current_user["id"],
        username=username,
        site=site,
        reason="manual_refresh",
        force=request.force,
    )

    state = get_insights_state(current_user["id"], username, site)
    return _build_profile_response(state)
