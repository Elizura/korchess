"""Quick-scan endpoints for triggering batch tactical analysis.

Quick scans are shared by (username, site) - not owned by individual users.
Requires authentication.
"""

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db
from services.quick_scan import schedule_quick_scan
from schemas import InsightsRequest
from auth import get_current_user

router = APIRouter(tags=["quick-scan"])


@router.post("/quick-scan/refresh")
async def refresh_quick_scan(
    request: InsightsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Manually trigger a quick-scan batch for a user's games. Requires authentication."""
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    result = schedule_quick_scan(username, site=request.site)
    return result
