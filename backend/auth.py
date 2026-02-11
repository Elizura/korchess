"""Authentication helpers for Google ID tokens."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """Verify Google ID token and return user profile."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID is not set.")

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token.")

    token = credentials.credentials
    try:
        id_info = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    return {
        "id": id_info.get("sub"),
        "email": id_info.get("email"),
        "name": id_info.get("name"),
        "picture": id_info.get("picture"),
    }


def get_registered_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """Verify token and require that the user is registered."""
    user = get_current_user(credentials)
    from db import get_connection, get_user_by_id

    conn = get_connection()
    try:
        existing = get_user_by_id(conn, user["id"])
    finally:
        conn.close()

    if not existing:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Call /api/auth/register",
        )

    return user
