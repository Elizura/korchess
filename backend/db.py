"""Database initialization and helper functions for Openingscope."""

import os
import sqlite3
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH", "/data/openingscope.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize the database schema and indexes."""
    path = db_path or DATABASE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Create games table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            played_at TEXT,
            time_class TEXT,
            color TEXT,
            result TEXT,
            eco TEXT,
            opening_name TEXT,
            opponent TEXT,
            white_elo INTEGER,
            black_elo INTEGER,
            pgn TEXT,
            UNIQUE(site, site_game_id)
        )
    """)

    # Create indexes for fast aggregations
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_games_username 
        ON games(username)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_games_username_eco 
        ON games(username, eco)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_games_username_color_time_class 
        ON games(username, color, time_class)
    """)

    # Create imports table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS imports (
            username TEXT PRIMARY KEY,
            site TEXT NOT NULL,
            imported INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            max_games INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)

    # Create analysis table for cached engine analysis
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            engine_name TEXT NOT NULL,
            engine_version TEXT,
            settings_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            UNIQUE(site, site_game_id, username)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_analysis_username 
        ON analysis(username)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_analysis_lookup 
        ON analysis(username, site, site_game_id)
    """)

    # Create full_analysis table for comprehensive move-by-move analysis
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS full_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            moves_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(site, site_game_id, username, depth, multipv)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_full_analysis_username 
        ON full_analysis(username)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_full_analysis_lookup 
        ON full_analysis(username, site, site_game_id, depth, multipv)
    """)

    conn.commit()
    conn.close()


def upsert_game(conn: sqlite3.Connection, game_row: dict) -> bool:
    """
    Insert a game, ignoring if it already exists (by site + site_game_id).
    Returns True if inserted, False if skipped (duplicate).
    """
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO games (
                site, site_game_id, username, played_at, time_class,
                color, result, eco, opening_name, opponent,
                white_elo, black_elo, pgn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            game_row["site"],
            game_row["site_game_id"],
            game_row["username"].strip().lower(),
            game_row.get("played_at"),
            game_row.get("time_class"),
            game_row.get("color"),
            game_row.get("result"),
            game_row.get("eco"),
            game_row.get("opening_name"),
            game_row.get("opponent"),
            game_row.get("white_elo"),
            game_row.get("black_elo"),
            game_row.get("pgn"),
        ))
        return cursor.rowcount > 0
    except sqlite3.Error:
        return False


def get_openings_stats(
    conn: sqlite3.Connection,
    username: str,
    color: str = "all",
    time_class: str = "all"
) -> list[dict]:
    """
    Aggregate opening statistics for a user with optional filters.
    Returns list of dicts with eco, opening_name, games, wins, draws, losses, score_pct.
    """
    cursor = conn.cursor()

    # Build query with filters
    query = """
        SELECT 
            eco,
            opening_name,
            COUNT(*) as games,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses
        FROM games
        WHERE LOWER(username) = LOWER(?)
    """
    params: list = [username]

    # Color filter
    if color != "all":
        query += " AND color = ?"
        params.append(color)

    # Time class filter
    if time_class != "all":
        query += " AND time_class = ?"
        params.append(time_class)

    query += " GROUP BY eco, opening_name ORDER BY games DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        games = row["games"]
        wins = row["wins"]
        draws = row["draws"]
        losses = row["losses"]

        # Calculate score percentage
        if games > 0:
            score_pct = round((wins + 0.5 * draws) / games * 100, 1)
        else:
            score_pct = 0.0

        results.append({
            "eco": row["eco"],
            "opening_name": row["opening_name"],
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score_pct": score_pct
        })

    return results


def upsert_import_status(
    conn: sqlite3.Connection,
    username: str,
    site: str,
    imported: int,
    skipped: int,
    max_games: int,
    imported_at: str
) -> None:
    """Record import status for a user."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    cursor.execute("""
        INSERT OR REPLACE INTO imports 
        (username, site, imported, skipped, max_games, imported_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (canonical_username, site, imported, skipped, max_games, imported_at))


def get_import_status(
    conn: sqlite3.Connection,
    username: str,
    site: str = "lichess"
) -> dict:
    """Get last import status + total games count for a user."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    
    # Get import record
    cursor.execute("""
        SELECT imported, skipped, max_games, imported_at
        FROM imports
        WHERE LOWER(username) = ? AND site = ?
    """, (canonical_username, site))
    import_row = cursor.fetchone()
    
    # Get total games count
    cursor.execute("""
        SELECT COUNT(*) as total
        FROM games
        WHERE LOWER(username) = ? AND site = ?
    """, (canonical_username, site))
    total_row = cursor.fetchone()
    
    return {
        "username": username,
        "imported_at": import_row["imported_at"] if import_row else None,
        "last_imported": import_row["imported"] if import_row else None,
        "last_skipped": import_row["skipped"] if import_row else None,
        "total_games": total_row["total"] if total_row else 0
    }


def get_games_by_opening(
    conn: sqlite3.Connection,
    username: str,
    eco: str,
    color: str = "all",
    time_class: str = "all",
    result: str = "all",
    offset: int = 0,
    limit: int = 10
) -> dict:
    """
    Get games and summary stats for a user and opening.
    Returns both summary and paginated games.
    """
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    
    # Build base WHERE clause (for summary - no result filter)
    base_where_conditions = [
        "LOWER(username) = ?",
        "eco = ?",
        "site_game_id IS NOT NULL",
        "site_game_id != ''"
    ]
    base_params = [canonical_username, eco]
    
    if color != "all":
        base_where_conditions.append("color = ?")
        base_params.append(color)
    
    if time_class != "all":
        base_where_conditions.append("time_class = ?")
        base_params.append(time_class)
    
    base_where_clause = " AND ".join(base_where_conditions)
    
    # Get summary stats (UNFILTERED by result)
    summary_query = f"""
        SELECT 
            COUNT(*) as total_games,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses
        FROM games
        WHERE {base_where_clause}
    """
    
    cursor.execute(summary_query, base_params)
    summary_row = cursor.fetchone()
    
    total_games = summary_row["total_games"] or 0
    wins = summary_row["wins"] or 0
    draws = summary_row["draws"] or 0
    losses = summary_row["losses"] or 0
    score_pct = ((wins + 0.5 * draws) / total_games * 100) if total_games > 0 else 0.0
    
    # Build games WHERE clause (includes result filter)
    games_where_conditions = base_where_conditions.copy()
    games_params = base_params.copy()
    
    if result != "all":
        games_where_conditions.append("result = ?")
        games_params.append(result)
    
    games_where_clause = " AND ".join(games_where_conditions)
    
    # Get paginated games (FILTERED by result)
    games_query = f"""
        SELECT 
            site_game_id,
            played_at,
            color,
            result,
            opponent,
            opening_name
        FROM games
        WHERE {games_where_clause}
        ORDER BY 
            CASE WHEN played_at IS NULL THEN 1 ELSE 0 END,
            played_at DESC,
            id DESC
        LIMIT ? OFFSET ?
    """
    
    cursor.execute(games_query, games_params + [limit, offset])
    rows = cursor.fetchall()
    
    games = []
    for row in rows:
        games.append({
            "site_game_id": row["site_game_id"],
            "played_at": row["played_at"],
            "color": row["color"],
            "result": row["result"],
            "opponent": row["opponent"],
            "opening_name": row["opening_name"],
            "lichess_url": f"https://lichess.org/{row['site_game_id']}"
        })
    
    return {
        "summary": {
            "total_games": total_games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score_pct": round(score_pct, 1)
        },
        "games": games
    }


def get_game_by_id(
    conn: sqlite3.Connection,
    username: str,
    site_game_id: str
) -> dict | None:
    """Get a single game by username and game ID."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT site_game_id, played_at, color, result, opponent, 
               opening_name, pgn, eco
        FROM games
        WHERE LOWER(username) = ? AND site_game_id = ?
    """, (username.strip().lower(), site_game_id))
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def get_analysis(
    conn: sqlite3.Connection,
    username: str,
    site_game_id: str
) -> dict | None:
    """Get cached analysis for a game."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT result_json, created_at, engine_name, engine_version
        FROM analysis
        WHERE LOWER(username) = ? AND site_game_id = ?
    """, (username.strip().lower(), site_game_id))
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_analysis(
    conn: sqlite3.Connection,
    username: str,
    site_game_id: str,
    engine_name: str,
    engine_version: str,
    settings_json: str,
    result_json: str
) -> None:
    """Save analysis result to cache."""
    cursor = conn.cursor()
    from datetime import datetime, timezone
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT OR REPLACE INTO analysis 
        (site, site_game_id, username, created_at, engine_name, 
         engine_version, settings_json, result_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("lichess", site_game_id, username.strip().lower(), 
          created_at, engine_name, engine_version, settings_json, result_json))


def get_full_analysis(
    conn: sqlite3.Connection,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int
) -> dict | None:
    """Get cached full analysis for a game."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT moves_json, summary_json, meta_json, created_at
        FROM full_analysis
        WHERE LOWER(username) = ? AND site_game_id = ? 
        AND depth = ? AND multipv = ?
    """, (username.strip().lower(), site_game_id, depth, multipv))
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_full_analysis(
    conn: sqlite3.Connection,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    moves_json: str,
    summary_json: str,
    meta_json: str
) -> None:
    """Save full analysis result to cache."""
    cursor = conn.cursor()
    from datetime import datetime, timezone
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT OR REPLACE INTO full_analysis 
        (site, site_game_id, username, depth, multipv, 
         moves_json, summary_json, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("lichess", site_game_id, username.strip().lower(), 
          depth, multipv, moves_json, summary_json, meta_json, created_at))

