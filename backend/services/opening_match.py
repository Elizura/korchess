"""Opening matching helpers using canonical UCI plies."""

from __future__ import annotations

from typing import Optional

import chess.pgn
import psycopg


def game_to_uci_plies(
    game: chess.pgn.Game,
    max_plies: Optional[int] = None
) -> list[str]:
    """Convert a parsed game into a list of UCI plies (mainline only)."""
    plies: list[str] = []
    for move in game.mainline_moves():
        plies.append(move.uci())
        if max_plies is not None and len(plies) >= max_plies:
            break
    return plies


def best_opening_match(
    con: psycopg.Connection,
    game_uci: list[str]
) -> dict | None:
    """Find the deepest prefix match from openings based on UCI plies."""
    if not game_uci:
        return None

    values = ", ".join(["(%s, %s)"] * len(game_uci))
    params: list = []
    for idx, uci in enumerate(game_uci):
        params.extend([idx, uci])

    query = f"""
        WITH game(ply_index, uci) AS (VALUES {values})
        SELECT o.id, o.eco, o.name, o.pgn, o.ply_count
        FROM openings o
        JOIN opening_moves om ON om.opening_id = o.id
        JOIN game g ON g.ply_index = om.ply_index AND g.uci = om.uci
        WHERE o.ply_count <= %s
        GROUP BY o.id
        HAVING COUNT(*) = o.ply_count
        ORDER BY o.ply_count DESC
        LIMIT 1;
    """

    cursor = con.cursor()
    cursor.execute(query, params + [len(game_uci)])
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "opening_id": row["id"] if isinstance(row, dict) else row[0],
        "eco": row["eco"] if isinstance(row, dict) else row[1],
        "name": row["name"] if isinstance(row, dict) else row[2],
        "pgn": row["pgn"] if isinstance(row, dict) else row[3],
        "ply_count": row["ply_count"] if isinstance(row, dict) else row[4],
    }
