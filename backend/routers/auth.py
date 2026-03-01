"""Authentication endpoints."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from analytics import track_server_event
from auth import get_current_user
from db import (
    LESSON_CONSENT_CHANNEL_EMAIL,
    LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY,
    create_user_if_missing,
    get_lesson_consent_status_payload,
    get_user_by_id,
    get_user_by_username,
    insert_lesson_consent_event,
    update_user_profile,
    update_user_profile_partial,
)
from dependencies import get_db
from schemas import LessonConsentRequest, LessonConsentResponse

router = APIRouter(tags=["auth"])

VALID_AVATARS = frozenset({"pawn", "knight", "bishop", "rook", "queen", "king"})


class OnboardingBody(BaseModel):
    avatar: str
    username: str


class ProfileUpdateBody(BaseModel):
    avatar: str | None = None
    username: str | None = None


class LessonConsentBody(LessonConsentRequest):
    pass


@router.post("/auth/register")
async def register_user(
    request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create user record if missing and return profile."""
    created = create_user_if_missing(conn, current_user)
    if created:
        await track_server_event(
            conn,
            event_name="auth.registered",
            user_id=current_user["id"],
            request=request,
            properties={
                "auth_provider": "google",
            },
        )
    conn.commit()
    return {
        **current_user,
        "created": created,
    }


@router.get("/auth/profile")
async def get_profile(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get current user profile including avatar and username."""
    user = get_user_by_id(conn, current_user["id"])
    if not user:
        return {"id": current_user["id"], "avatar": None, "username": None}
    return {
        "id": user["id"],
        "email": user.get("email"),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
        "avatar": user.get("avatar"),
        "username": user.get("username"),
        "updated_at": user.get("updated_at"),
        "onboarding_complete": bool(
            user.get("avatar") and user.get("username")
        ),
    }


@router.patch("/auth/profile")
async def update_profile(
    body: ProfileUpdateBody,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update profile (avatar and/or username). Partial updates allowed."""
    user = get_user_by_id(conn, current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    avatar = body.avatar if body.avatar is not None else user.get("avatar")
    username_raw = body.username if body.username is not None else user.get("username")

    if avatar is not None and avatar not in VALID_AVATARS:
        raise HTTPException(
            status_code=400,
            detail="Invalid avatar. Must be one of: pawn, knight, bishop, rook, queen, king",
        )

    username_to_update = None
    if username_raw is not None:
        username_str = str(username_raw).strip()
        if not username_str:
            raise HTTPException(status_code=400, detail="Username cannot be empty")
        if len(username_str) < 2:
            raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
        existing = get_user_by_username(conn, username_str)
        if existing and existing["id"] != current_user["id"]:
            raise HTTPException(status_code=409, detail="Username already taken")
        username_to_update = username_str

    update_user_profile_partial(
        conn,
        current_user["id"],
        avatar=body.avatar if body.avatar is not None else None,
        username=username_to_update,
    )
    conn.commit()
    return {"ok": True}


@router.patch("/auth/onboarding")
async def complete_onboarding(
    body: OnboardingBody,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update avatar and username (complete onboarding)."""
    avatar = body.avatar
    username = body.username

    if not avatar or avatar not in VALID_AVATARS:
        raise HTTPException(
            status_code=400,
            detail="Invalid avatar. Must be one of: pawn, knight, bishop, rook, queen, king",
        )

    if not username or not str(username).strip():
        raise HTTPException(status_code=400, detail="Username is required")

    username_str = str(username).strip()
    if len(username_str) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")

    existing = get_user_by_username(conn, username_str)
    if existing and existing["id"] != current_user["id"]:
        raise HTTPException(status_code=409, detail="Username already taken")

    update_user_profile(conn, current_user["id"], avatar, username_str)
    conn.commit()
    return {"ok": True}


@router.get("/auth/lesson-consent", response_model=LessonConsentResponse)
async def get_lesson_consent(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get lesson-consent status for the authenticated user."""
    return get_lesson_consent_status_payload(
        conn,
        current_user["id"],
        channel=LESSON_CONSENT_CHANNEL_EMAIL,
    )


@router.post("/auth/lesson-consent", response_model=LessonConsentResponse)
async def record_lesson_consent(
    body: LessonConsentBody,
    request: Request,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Record one append-only lesson-consent decision event."""
    source = body.source.strip().lower()
    if source != LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY:
        raise HTTPException(status_code=400, detail="Invalid source")

    site = body.site.strip().lower() if isinstance(body.site, str) else None
    if site and site not in {"lichess", "chesscom"}:
        raise HTTPException(status_code=400, detail="Invalid site")

    site_game_id = (body.site_game_id or "").strip() or None

    insert_lesson_consent_event(
        conn,
        current_user["id"],
        body.decision,
        source,
        site=site,
        site_game_id=site_game_id,
        analysis_depth=body.analysis_depth,
        analysis_multipv=body.analysis_multipv,
        channel=LESSON_CONSENT_CHANNEL_EMAIL,
    )

    await track_server_event(
        conn,
        event_name="feature.usage",
        user_id=current_user["id"],
        request=request,
        properties={
            "feature": "lesson_email_consent",
            "decision": body.decision,
            "source": source,
            "channel": LESSON_CONSENT_CHANNEL_EMAIL,
            "site": site,
        },
    )

    payload = get_lesson_consent_status_payload(
        conn,
        current_user["id"],
        channel=LESSON_CONSENT_CHANNEL_EMAIL,
    )
    conn.commit()
    return payload
