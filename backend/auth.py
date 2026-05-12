"""Authentication helpers — JWT-based email/password auth."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
import psycopg
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dependencies import get_db

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30

security = HTTPBearer(auto_error=False)


def create_access_token(user_id: str, email: str) -> str:
    """Create a short-lived JWT access token."""
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token. Returns {"id", "email"}."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    return {"id": payload["sub"], "email": payload.get("email", "")}


def create_refresh_token() -> str:
    """Generate a cryptographically secure opaque refresh token."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 hash of a token for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_user_id() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# FastAPI dependencies (same interface as before)
# ---------------------------------------------------------------------------

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """Verify JWT access token and return user info."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token.")

    return verify_access_token(credentials.credentials)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any] | None:
    """Return authenticated user if bearer token exists; otherwise None."""
    if credentials is None:
        return None
    if not credentials.credentials:
        raise HTTPException(status_code=401, detail="Invalid authorization token.")
    return verify_access_token(credentials.credentials)


def get_registered_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    conn: psycopg.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Verify token and require that the user is registered."""
    user = get_current_user(credentials)
    from repository.db import get_user_by_id

    existing = get_user_by_id(conn, user["id"])

    if not existing:
        raise HTTPException(
            status_code=403,
            detail="User not registered.",
        )

    return user
