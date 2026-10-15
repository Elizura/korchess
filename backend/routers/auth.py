"""Authentication endpoints — email/password and Google OAuth with JWT tokens."""

import logging
import math
import os
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
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
    consume_email_code_attempt,
    create_auth_user,
    delete_refresh_token,
    delete_user_refresh_tokens,
    get_refresh_token_by_hash,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    mark_email_verified,
    reset_user_password,
    set_email_code,
    store_refresh_token,
    update_user_profile,
    update_user_profile_partial,
)
from dependencies import get_db
from services.analytics import track_server_event
from services.email import send_password_reset_email, send_verification_email

router = APIRouter(tags=["auth"])

VALID_AVATARS = frozenset({"pawn", "knight", "bishop", "rook", "queen", "king"})
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
VERIFICATION_CODE_EXPIRY_MINUTES = 10
CODE_RESEND_COOLDOWN_SECONDS = 60
MAX_CODE_ATTEMPTS = 5
BCRYPT_ROUNDS = 12
IS_PROD = os.environ.get("ENV", "development").lower() == "production"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

logger = logging.getLogger(__name__)

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def _generate_verification_code() -> str:
    return str(secrets.randbelow(900000) + 100000)


async def _hash_password(password: str) -> str:
    # bcrypt is deliberately slow; keep it off the event loop
    return await run_in_threadpool(
        lambda: bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()
    )


async def _check_password(password: str, password_hash: str) -> bool:
    return await run_in_threadpool(bcrypt.checkpw, password.encode(), password_hash.encode())


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _code_cooldown_remaining(user: dict) -> int:
    """Seconds until another code may be emailed to this user (0 if none is pending)."""
    expires_at = user.get("verification_code_expires_at")
    if not user.get("verification_code") or not expires_at:
        return 0
    issued_at = _as_utc(expires_at) - timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    elapsed = (datetime.now(timezone.utc) - issued_at).total_seconds()
    return max(0, math.ceil(CODE_RESEND_COOLDOWN_SECONDS - elapsed))


async def _send_code_or_raise(send: Callable[[str, str], dict], email: str, code: str) -> None:
    result = await run_in_threadpool(send, email, code)
    if not result.get("success"):
        raise HTTPException(
            status_code=503,
            detail="We couldn't send the email. Please try again in a few minutes.",
        )


async def _email_new_code(
    conn: psycopg.Connection,
    user: dict,
    send: Callable[[str, str], dict],
    password_hash: str | None = None,
) -> None:
    """Issue a fresh code to the user and email it. Raises if rate-limited or unsendable."""
    wait = _code_cooldown_remaining(user)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {wait} seconds before requesting another code.",
        )
    code = _generate_verification_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    set_email_code(conn, user["id"], code, expires_at, password_hash)
    conn.commit()
    await _send_code_or_raise(send, user["email"], code)


def _check_email_code(conn: psycopg.Connection, user_id: str, code: str) -> None:
    """Validate an emailed code. Every call uses up one of the code's attempts."""
    state = consume_email_code_attempt(conn, user_id)
    conn.commit()
    if not state or not state.get("verification_code") or not state.get("verification_code_expires_at"):
        raise HTTPException(status_code=400, detail="No active code. Request a new one.")
    if state["verification_attempts"] > MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many incorrect attempts. Request a new code.")
    if datetime.now(timezone.utc) > _as_utc(state["verification_code_expires_at"]):
        raise HTTPException(status_code=400, detail="This code has expired. Request a new one.")
    if not secrets.compare_digest(state["verification_code"], code):
        raise HTTPException(status_code=400, detail="Invalid code.")


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=IS_PROD,
        samesite="lax",
        path="/",
        max_age=COOKIE_MAX_AGE,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=IS_PROD,
        samesite="lax",
    )


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
    password: str = ""


class SigninBody(BaseModel):
    email: str
    password: str


class EmailBody(BaseModel):
    email: str


class ResetPasswordBody(BaseModel):
    email: str
    code: str
    password: str


SIGNUP_MESSAGE = "Account created. Please check your email for the verification code."


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
    if existing and existing.get("email_verified"):
        raise HTTPException(status_code=409, detail="Email already registered.")

    password_hash = await _hash_password(password)

    if existing:
        # Unverified account (code expired or never arrived): start over with a fresh code
        await _email_new_code(conn, existing, send_verification_email, password_hash)
        return {"message": SIGNUP_MESSAGE}

    code = _generate_verification_code()
    code_expires = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    user_id = generate_user_id()

    create_auth_user(conn, user_id, email, password_hash, code, code_expires)
    conn.commit()

    await _send_code_or_raise(send_verification_email, email, code)

    await track_server_event(
        conn,
        event_name="auth.signup",
        user_id=user_id,
        request=request,
        properties={"auth_provider": "email"},
    )

    return {"message": SIGNUP_MESSAGE}


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
    if not body.password:
        raise HTTPException(status_code=400, detail="Password is required.")

    user = get_user_by_email(conn, email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.get("email_verified"):
        raise HTTPException(status_code=400, detail="Email already verified.")

    _check_email_code(conn, user["id"], code)
    # Re-signup replaces an unverified account's password, so the code alone
    # can't show who chose it; require the password too before activating
    if not user["password_hash"] or not await _check_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=400,
            detail="Password doesn't match this sign-up. Check it, or sign up again.",
        )

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


@router.post("/auth/resend-code")
async def resend_verification_code(
    body: EmailBody,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    user = get_user_by_email(conn, email) if email else None
    if user and not user.get("email_verified"):
        await _email_new_code(conn, user, send_verification_email)
    return {"message": "If that account is awaiting verification, a new code is on its way."}


@router.post("/auth/forgot-password")
async def forgot_password(
    body: EmailBody,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    user = get_user_by_email(conn, email) if email else None
    if user:
        await _email_new_code(conn, user, send_password_reset_email)
    return {"message": "If an account exists for that email, a reset code is on its way."}


@router.post("/auth/reset-password")
async def reset_password(
    body: ResetPasswordBody,
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
):
    email = body.email.strip().lower()
    code = body.code.strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and code are required.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    user = get_user_by_email(conn, email)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid code.")

    _check_email_code(conn, user["id"], code)

    password_hash = await _hash_password(body.password)
    reset_user_password(conn, user["id"], password_hash)
    # Sign out every existing session; only the person who reset keeps access
    delete_user_refresh_tokens(conn, user["id"])
    access_token = _issue_tokens(response, conn, user["id"], email)
    conn.commit()

    return {"access_token": access_token}


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

    if not user["password_hash"] or not await _check_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email before signing in.")

    access_token = _issue_tokens(response, conn, user["id"], email)
    conn.commit()

    return {"access_token": access_token}


class GoogleBody(BaseModel):
    id_token: str


@router.post("/auth/google")
async def google_auth(
    body: GoogleBody,
    request: Request,
    response: Response,
    conn: psycopg.Connection = Depends(get_db),
):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google auth is not configured.")

    try:
        id_info = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token.")

    email = (id_info.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email.")

    name = id_info.get("name")
    picture = id_info.get("picture")

    user = get_user_by_email(conn, email)

    if user:
        access_token = _issue_tokens(response, conn, user["id"], email)
        conn.commit()
        return {"access_token": access_token}

    user_id = generate_user_id()
    create_auth_user(
        conn,
        user_id,
        email,
        email_verified=True,
        name=name,
        avatar_url=picture,
    )
    access_token = _issue_tokens(response, conn, user_id, email)
    conn.commit()

    await track_server_event(
        conn,
        event_name="auth.registered",
        user_id=user_id,
        request=request,
        properties={"auth_provider": "google"},
    )

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
# Profile / Onboarding (kept from original, use get_current_user)
# ---------------------------------------------------------------------------

class OnboardingBody(BaseModel):
    avatar: str
    username: str


class ProfileUpdateBody(BaseModel):
    avatar: str | None = None
    username: str | None = None


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
