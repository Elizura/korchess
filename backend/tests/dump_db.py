"""Fetch Lichess games and store them in raw_games table for testing."""

import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Add parent directory to path so we can import from scripts
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.explore import fetch_lichess_pgn_stream


def get_connection() -> psycopg.Connection:
    """Get database connection."""
    database_url = "postgresql://postgres:postgres@db:5432/korchess"
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(database_url, autocommit=False, connect_timeout=5)


def create_raw_games_table(conn: psycopg.Connection) -> None:
    """Create raw_games table if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_games (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            pgn TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(username, site, site_game_id)
        )
    """)
    conn.commit()
    print("Created raw_games table (if not exists)")


def extract_game_id_from_pgn(pgn: str) -> str | None:
    """Extract game ID from Lichess PGN Site header."""
    match = re.search(r'\[Site "https://lichess\.org/([a-zA-Z0-9]+)"', pgn)
    if match:
        return match.group(1)
    return None


def parse_individual_games(pgn_chunk: str) -> list[str]:
    """Split a PGN chunk into individual games."""
    games = []
    current_game = []
    
    for line in pgn_chunk.split("\n"):
        if line.startswith("[Event ") and current_game:
            games.append("\n".join(current_game))
            current_game = []
        current_game.append(line)
    
    if current_game:
        games.append("\n".join(current_game))
    
    return [g.strip() for g in games if g.strip()]


def store_games(conn: psycopg.Connection, username: str, site: str, games: list[str]) -> int:
    """Store games in raw_games table. Returns count of inserted games."""
    cur = conn.cursor()
    inserted = 0
    print(games[0], games[1], games[2])
    
    for pgn in games:
        game_id = extract_game_id_from_pgn(pgn)
        if not game_id:
            continue
        
        try:
            cur.execute("""
                INSERT INTO raw_games (username, site, site_game_id, pgn)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (username, site, site_game_id) DO NOTHING
            """, (username.lower(), site, game_id, pgn))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            print(f"Error inserting game {game_id}: {e}")
            continue
    
    conn.commit()
    return inserted


def fetch_and_store_lichess_games(username: str) -> None:
    """Fetch games from Lichess and store in raw_games table."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    
    conn = get_connection()
    try:
        create_raw_games_table(conn)
        
        total_inserted = 0
        for chunk in fetch_lichess_pgn_stream(username):
            games = parse_individual_games(chunk)
            inserted = store_games(conn, username, "lichess", games)
            total_inserted += inserted
            print(f"  Inserted {inserted} games from chunk")
        
        print(f"\nTotal: inserted {total_inserted} games for {username}")
        
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM raw_games WHERE username = %s AND site = %s",
            (username.lower(), "lichess")
        )
        total_in_db = cur.fetchone()[0]
        print(f"Total games in DB for {username}: {total_in_db}")
        
    finally:
        conn.close()


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "elizura"
    print(f"Fetching Lichess games for: {username}")
    fetch_and_store_lichess_games(username)
