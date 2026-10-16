"""Quick-scan endpoints for triggering batch tactical analysis.

Quick scans are shared by (username, site) - not owned by individual users.
Requires authentication.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db
from repository.db import user_has_chess_profile
from services.quick_scan import schedule_quick_scan
from schemas import InsightsRequest
from auth import get_current_user

router = APIRouter(tags=["quick-scan"])


@router.post("/quick-scan/refresh")
async def refresh_quick_scan(
    request: InsightsRequest,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Manually trigger a quick-scan batch for one of the caller's chess accounts."""
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if not user_has_chess_profile(conn, current_user["id"], username, request.site):
        raise HTTPException(
            status_code=403,
            detail="You can only scan your own chess accounts.",
        )

    result = schedule_quick_scan(username, site=request.site)
    return result
