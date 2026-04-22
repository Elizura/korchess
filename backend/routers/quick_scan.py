"""Quick-scan endpoints for triggering batch tactical analysis.

Quick scans are shared by (username, site) - not owned by individual users.
"""

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db
from services.quick_scan import schedule_quick_scan
from schemas import InsightsRequest

router = APIRouter(tags=["quick-scan"])


@router.post("/quick-scan/refresh")
async def refresh_quick_scan(
    request: InsightsRequest,
):
    """Manually trigger a quick-scan batch for a user's games.
    
    Quick scans are shared per chess username - not owned by individual users.
    """
    username = request.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    result = schedule_quick_scan(username, site=request.site)
    return result
