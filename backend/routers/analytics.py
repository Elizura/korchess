"""Product analytics ingestion endpoints."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from services.analytics import (
    AnalyticsValidationError,
    ingest_client_events,
    link_identity,
    mirror_events_to_posthog,
    track_server_event,
)
from auth import get_optional_user, get_registered_user
from dependencies import get_db
from schemas import AnalyticsEventsIngestRequest, AnalyticsEventsIngestResponse, AnalyticsIdentifyRequest

router = APIRouter(tags=["analytics"])


@router.post(
    "/analytics/events",
    response_model=AnalyticsEventsIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_events_endpoint(
    body: AnalyticsEventsIngestRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: dict | None = Depends(get_optional_user),
):
    """Ingest a batch of product analytics events."""
    try:
        enriched = await ingest_client_events(
            raw_events=[event.model_dump() for event in body.events],
            request=request,
            user_id=current_user["id"] if current_user else None,
        )
    except AnalyticsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if enriched:
        background_tasks.add_task(mirror_events_to_posthog, enriched)

    return AnalyticsEventsIngestResponse(accepted=len(enriched))


@router.post("/analytics/identify")
async def identify_endpoint(
    body: AnalyticsIdentifyRequest,
    request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Link an anonymous analytics identity to an authenticated account."""
    anonymous_id = body.anonymous_id.strip()
    if not anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id is required")

    link_identity(conn, anonymous_id=anonymous_id, user_id=current_user["id"])

    await track_server_event(
        conn,
        event_name="identity.linked",
        user_id=current_user["id"],
        request=request,
        anonymous_id=anonymous_id,
        session_id=body.session_id,
        properties={
            "link_method": "identify_endpoint",
        },
    )

    conn.commit()
    return {"ok": True}
