"""Quick-scan endpoints for triggering batch tactical analysis."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from auth import get_optional_user
from db import ensure_public_user_for_username
from dependencies import get_db
from quick_scan import schedule_quick_scan
from schemas import InsightsRequest

router = APIRouter(tags=["quick-scan"])


@router.post("/quick-scan/refresh")
async def refresh_quick_scan(
    request: InsightsRequest,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """Manually trigger a quick-scan batch for a user's games."""
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    public_user_id = ensure_public_user_for_username(conn, username)
    user_id = current_user["id"] if current_user else public_user_id

    result = schedule_quick_scan(user_id, username, site=request.site)
    return result
