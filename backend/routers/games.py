"""Single game endpoint."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from db import get_game_by_id
from schemas import GameResponse
from dependencies import get_db, validate_site
from auth import get_registered_user

router = APIRouter(tags=["games"])


@router.get("/game/{site}/{username}/{game_id}", response_model=GameResponse)
async def get_game(
    site: str,
    username: str,
    game_id: str,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get game metadata and PGN."""
    site = validate_site(site)
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    game = get_game_by_id(conn, current_user["id"], username, game_id, site)

    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game["site"] == "lichess":
        game_url = f"https://lichess.org/{game_id}"
    elif game["site"] == "chesscom":
        game_url = f"https://www.chess.com/game/live/{game_id}"
    else:
        game_url = ""

    return GameResponse(
        username=username,
        game_id=game_id,
        pgn=game["pgn"] or "",
        played_at=game["played_at"],
        color=game["color"],
        result=game["result"],
        opponent=game["opponent"],
        lichess_url=game_url,
        eco=game.get("eco"),
        opening_name=game.get("opening_name")
    )
