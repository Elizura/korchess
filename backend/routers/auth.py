"""Authentication endpoints."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from db import create_user_if_missing, get_user_by_id, get_user_by_username, update_user_profile
from dependencies import get_db

router = APIRouter(tags=["auth"])

VALID_AVATARS = frozenset({"pawn", "knight", "bishop", "rook", "queen", "king"})


class OnboardingBody(BaseModel):
    avatar: str
    username: str


@router.post("/auth/register")
async def register_user(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create user record if missing and return profile."""
    create_user_if_missing(conn, current_user)
    conn.commit()
    return current_user


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
        "onboarding_complete": bool(
            user.get("avatar") and user.get("username")
        ),
    }


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
