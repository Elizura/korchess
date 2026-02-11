"""Authentication endpoints."""

import psycopg
from fastapi import APIRouter, Depends

from auth import get_current_user
from db import create_user_if_missing
from dependencies import get_db

router = APIRouter(tags=["auth"])


@router.post("/auth/register")
async def register_user(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create user record if missing and return profile."""
    create_user_if_missing(conn, current_user)
    conn.commit()
    return current_user
