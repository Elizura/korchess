"""Import games from Lichess and Chess.com."""

import io
from datetime import datetime, timezone

import chess.pgn
import psycopg
from fastapi import APIRouter, Depends, HTTPException

from db import upsert_game, upsert_import_status, get_import_history
from lichess import fetch_lichess_pgn, parse_pgn_games, LichessAPIError
from chesscom import fetch_chesscom_games, ChesscomAPIError
from opening_match import game_to_uci_plies, best_opening_match

from schemas import ImportRequest, ImportResponse, ImportHistoryResponse, ImportHistoryItem
from dependencies import get_db
from auth import get_registered_user

router = APIRouter(tags=["import"])


@router.get("/history", response_model=ImportHistoryResponse)
async def get_import_history_endpoint(
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """Get last 10 import records for the authenticated user."""
    rows = get_import_history(conn, current_user["id"], limit=10)
    return ImportHistoryResponse(
        history=[ImportHistoryItem(**r) for r in rows]
    )


@router.post("/lichess", response_model=ImportResponse)
async def import_lichess_games(
    request: ImportRequest,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    username = request.username.strip()
    max_games = request.max_games
    user_id = current_user["id"]

    try:
        pgn_text = fetch_lichess_pgn(username, max_games)
    except LichessAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    if not pgn_text.strip():
        raise HTTPException(
            status_code=404,
            detail=f"No rated games found for user '{username}'."
        )

    imported = 0
    games, skipped = parse_pgn_games(pgn_text, username, conn)

    if not games and skipped == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'."
        )

    for game in games:
        game["user_id"] = user_id
        if upsert_game(conn, game):
            imported += 1
        else:
            skipped += 1
    conn.commit()

    imported_at = datetime.now(timezone.utc).isoformat()
    upsert_import_status(
        conn, user_id, username, "lichess",
        imported, skipped, max_games, imported_at
    )
    conn.commit()

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped
    )


@router.post("/chesscom", response_model=ImportResponse)
async def import_chesscom_games(
    request: ImportRequest,
    conn: psycopg.Connection = Depends(get_db),
    current_user: dict = Depends(get_registered_user),
):
    """
    Import games from Chess.com for a user.
    Fetches games via Chess.com API, parses, and stores in database.
    """
    username = request.username.strip()
    max_games = request.max_games
    user_id = current_user["id"]

    try:
        games = fetch_chesscom_games(username, max_games)
    except ChesscomAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=e.message)
        elif e.status_code == 429:
            raise HTTPException(status_code=429, detail=e.message)
        else:
            raise HTTPException(status_code=502, detail=e.message)

    if not games:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}' on Chess.com."
        )

    imported = 0
    skipped = 0
    for game in games:
        opening = None
        try:
            pgn_text = game.get("pgn", "")
            game_obj = chess.pgn.read_game(io.StringIO(pgn_text)) if pgn_text else None
            if game_obj:
                uci_plies = game_to_uci_plies(game_obj, max_plies=40)
                opening = best_opening_match(conn, uci_plies)
        except Exception:
            opening = None

        game["eco"] = opening["eco"] if opening else "UNKNOWN"
        game["opening_name"] = opening["name"] if opening else "Unknown"
        game["opening_id"] = opening["opening_id"] if opening else None
        game["opening_ply_count"] = opening["ply_count"] if opening else None

        game["user_id"] = user_id
        if upsert_game(conn, game):
            imported += 1
        else:
            skipped += 1
    conn.commit()

    imported_at = datetime.now(timezone.utc).isoformat()
    upsert_import_status(
        conn, user_id, username, "chesscom",
        imported, skipped, max_games, imported_at
    )
    conn.commit()

    return ImportResponse(
        username=username,
        imported=imported,
        skipped=skipped
    )
