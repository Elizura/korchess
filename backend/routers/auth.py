"""Authentication endpoints — email/password auth with JWT tokens."""

import re
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    generate_user_id,
    get_current_user,
    hash_token,
)
from repository.db import (
    LESSON_CONSENT_CHANNEL_EMAIL,
    LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY,
    create_auth_user,
    delete_refresh_token,
    get_lesson_consent_status_payload,
    get_refresh_token_by_hash,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    insert_lesson_consent_event,
    mark_email_verified,
    store_refresh_token,
    update_user_profile,
    update_user_profile_partial,
)
from dependencies import get_db
from schemas import LessonConsentRequest, LessonConsentResponse
from services.analytics import track_server_event
from services.email import send_verification_email

router = APIRouter(tags=["auth"])

VALID_AVATARS = frozenset({"pawn", "knight", "bishop", "rook", "queen", "king"})
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
VERIFICATION_CODE_EXPIRY_MINUTES = 10
BCRYPT_ROUNDS = 12
IS_PROD = True  # Override via env if needed

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def _generate_verification_code() -> str:
    import random
    return str(random.randint(100000, 999999))


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _issue_tokens(
    response: Response,
    conn: psycopg.Connection,
    user_id: str,
    email: str,
) -> str:
    """Create access + refresh tokens, store refresh in DB and cookie. Returns access token."""
    access_token = create_access_token(user_id, email)

    raw_refresh = create_refresh_token()
    token_hash = hash_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token_id = generate_user_id()
    store_refresh_token(conn, token_id, user_id, token_hash, expires_at)
    _set_refresh_cookie(response, raw_refresh)

    return access_token


# ---------------------------------------------------------------------------
# Signup / Verify / Signin / Refresh / Signout
# ---------------------------------------------------------------------------

class SignupBody(BaseModel):
    email: str
    password: str


class VerifyBody(BaseModel):
    email: str
    code: str


class SigninBody(BaseModel):
    email: str
    password: str


@router.post("/auth/signup", status_code=201)
async def signup(
    body: SignupBody,
    request: Request,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email format.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    existing = get_user_by_email(conn, email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered.")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()
    code = _generate_verification_code()
    code_expires = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    user_id = generate_user_id()

    create_auth_user(conn, user_id, email, password_hash, code, code_expires)
    conn.commit()

    email_result = send_verification_email(email, code)
    if not email_result.get("success"):
        pass  # logged inside send_verification_email

    await track_server_event(
        conn,
        event_name="auth.signup",
        user_id=user_id,
        request=request,
        properties={"auth_provider": "email"},
    )

    return {"message": "Account created. Please check your email for the verification code."}


@router.post("/auth/verify")
async def verify_email(
    body: VerifyBody,
    request: Request,
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    code = body.code.strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and code are required.")

    user = get_user_by_email(conn, email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.get("email_verified"):
        raise HTTPException(status_code=400, detail="Email already verified.")

    if not user.get("verification_code") or not user.get("verification_code_expires_at"):
        raise HTTPException(status_code=400, detail="No verification code found. Please sign up again.")

    expires_at = user["verification_code_expires_at"]
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Verification code has expired. Please sign up again.")

    if user["verification_code"] != code:
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    mark_email_verified(conn, user["id"])
    access_token = _issue_tokens(response, conn, user["id"], email)
    conn.commit()

    await track_server_event(
        conn,
        event_name="auth.registered",
        user_id=user["id"],
        request=request,
        properties={"auth_provider": "email"},
    )

    return {"access_token": access_token, "message": "Email verified successfully."}


@router.post("/auth/signin")
async def signin(
    body: SigninBody,
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    user = get_user_by_email(conn, email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email before signing in.")

    access_token = _issue_tokens(response, conn, user["id"], email)
    conn.commit()

    return {"access_token": access_token}


@router.post("/auth/refresh")
async def refresh(
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
    refresh_token: str | None = Cookie(None, alias="refresh_token"),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token.")

    token_hash = hash_token(refresh_token)
    stored = get_refresh_token_by_hash(conn, token_hash)
    if not stored:
        raise HTTPException(status_code=401, detail="Invalid refresh token.")

    expires_at = stored["expires_at"]
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        delete_refresh_token(conn, token_hash)
        conn.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh token expired.")

    user = get_user_by_id(conn, stored["user_id"])
    if not user:
        delete_refresh_token(conn, token_hash)
        conn.commit()
        raise HTTPException(status_code=401, detail="User not found.")

    # Rotate: delete old, issue new
    delete_refresh_token(conn, token_hash)
    access_token = _issue_tokens(response, conn, user["id"], user.get("email", ""))
    conn.commit()

    return {"access_token": access_token}


@router.post("/auth/signout")
async def signout(
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
    refresh_token: str | None = Cookie(None, alias="refresh_token"),
):
    if refresh_token:
        token_hash = hash_token(refresh_token)
        delete_refresh_token(conn, token_hash)
        conn.commit()
    _clear_refresh_cookie(response)
    return {"message": "Signed out successfully."}


# ---------------------------------------------------------------------------
# Profile / Onboarding / Lesson-consent (kept from original, use get_current_user)
# ---------------------------------------------------------------------------

class OnboardingBody(BaseModel):
    avatar: str
    username: str


class ProfileUpdateBody(BaseModel):
    avatar: str | None = None
    username: str | None = None


class LessonConsentBody(LessonConsentRequest):
    pass


@router.get("/auth/me")
async def get_me(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return current user profile."""
    user = get_user_by_id(conn, current_user["id"])
    if not user:
        return {"id": current_user["id"], "email": current_user.get("email"), "avatar": None, "username": None}
    return {
        "id": user["id"],
        "email": user.get("email"),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
        "avatar": user.get("avatar"),
        "username": user.get("username"),
        "updated_at": user.get("updated_at"),
        "onboarding_complete": bool(user.get("avatar") and user.get("username")),
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
