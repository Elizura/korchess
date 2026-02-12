"""Database initialization and helper functions for Korchess (Postgres)."""

import os
from typing import Optional

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_connection() -> psycopg.Connection:
    """Get a database connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
        row_factory=dict_row,
        connect_timeout=5,
    )


def init_db() -> None:
    """Initialize the database schema and indexes."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            avatar_url TEXT,
            avatar TEXT,
            username TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'updated_at'
        """
    )
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE users ADD COLUMN updated_at TIMESTAMPTZ DEFAULT now()")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            played_at TEXT,
            time_class TEXT,
            color TEXT,
            result TEXT,
            eco TEXT,
            opening_name TEXT,
            opening_id INTEGER,
            opening_ply_count INTEGER,
            opponent TEXT,
            white_elo INTEGER,
            black_elo INTEGER,
            pgn TEXT,
            UNIQUE(user_id, site, site_game_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_user
        ON games(user_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_user_username
        ON games(user_id, username)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_user_username_color_time_class
        ON games(user_id, username, color, time_class)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_user_site_username
        ON games(user_id, site, username)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            imported INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            max_games INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (user_id, site, username),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            engine_name TEXT NOT NULL,
            engine_version TEXT,
            settings_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            UNIQUE(user_id, site, site_game_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_user
        ON analysis(user_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_lookup
        ON analysis(user_id, site, site_game_id)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS full_analysis (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            moves_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, site, site_game_id, depth, multipv),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_full_analysis_user
        ON full_analysis(user_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_full_analysis_lookup
        ON full_analysis(user_id, site, site_game_id, depth, multipv)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, site, site_game_id, depth, multipv),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_jobs_lookup
        ON analysis_jobs(user_id, site_game_id, depth, multipv)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS openings (
            id SERIAL PRIMARY KEY,
            eco TEXT NOT NULL,
            name TEXT NOT NULL,
            pgn TEXT NOT NULL,
            ply_count INTEGER NOT NULL,
            opening_key TEXT NOT NULL,
            opening_label TEXT NOT NULL,
            variation_key TEXT NOT NULL,
            variation_label TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS opening_moves (
            opening_id INTEGER NOT NULL,
            ply_index INTEGER NOT NULL,
            uci TEXT NOT NULL,
            PRIMARY KEY (opening_id, ply_index),
            FOREIGN KEY (opening_id) REFERENCES openings(id)
        )
        """
    )

    conn.commit()
    conn.close()


def ensure_openings_table(conn: psycopg.Connection) -> None:
    """Ensure openings table exists and is accessible."""
    try:
        conn.execute("SELECT 1 FROM openings LIMIT 1")
    except psycopg.Error as exc:
        raise RuntimeError("Openings table is missing or inaccessible.") from exc


def upsert_user(conn: psycopg.Connection, user: dict) -> None:
    """Insert or update a user record."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (id, email, name, avatar_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            email = EXCLUDED.email,
            name = EXCLUDED.name,
            avatar_url = EXCLUDED.avatar_url
        """,
        (
            user.get("id"),
            user.get("email"),
            user.get("name"),
            user.get("picture"),
        ),
    )


def create_user_if_missing(conn: psycopg.Connection, user: dict) -> None:
    """Insert a user record if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (id, email, name, avatar_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            user.get("id"),
            user.get("email"),
            user.get("name"),
            user.get("picture"),
        ),
    )


def get_user_by_id(conn: psycopg.Connection, user_id: str) -> dict | None:
    """Fetch a user by ID."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, email, name, avatar_url, avatar, username, created_at, updated_at
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def update_user_profile(
    conn: psycopg.Connection, user_id: str, avatar: str, username: str
) -> None:
    """Update user avatar and username."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    cursor.execute(
        """
        UPDATE users
        SET avatar = %s, username = %s, updated_at = now()
        WHERE id = %s
        """,
        (avatar, canonical_username, user_id),
    )


def update_user_profile_partial(
    conn: psycopg.Connection,
    user_id: str,
    avatar: str | None = None,
    username: str | None = None,
) -> None:
    """Update user avatar and/or username (only provided fields)."""
    if avatar is None and username is None:
        return
    cursor = conn.cursor()
    updates = []
    params: list = []
    if avatar is not None:
        updates.append("avatar = %s")
        params.append(avatar)
    if username is not None:
        updates.append("username = %s")
        params.append(username.strip().lower())
    updates.append("updated_at = now()")
    params.append(user_id)
    cursor.execute(
        f"""
        UPDATE users
        SET {", ".join(updates)}
        WHERE id = %s
        """,
        params,
    )


def get_user_by_username(conn: psycopg.Connection, username: str) -> dict | None:
    """Fetch a user by username (for uniqueness check)."""
    cursor = conn.cursor()
    canonical = username.strip().lower()
    cursor.execute(
        """
        SELECT id, email, name, avatar_url, avatar, username, created_at
        FROM users
        WHERE LOWER(username) = %s
        """,
        (canonical,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def upsert_game(conn: psycopg.Connection, game_row: dict) -> bool:
    """
    Insert a game, ignoring if it already exists (by site + site_game_id).
    Returns True if inserted, False if skipped (duplicate).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO games (
            user_id, site, site_game_id, username, played_at, time_class,
            color, result, eco, opening_name, opening_id, opening_ply_count,
            opponent, white_elo, black_elo, pgn
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id) DO NOTHING
        """,
        (
            game_row["user_id"],
            game_row["site"],
            game_row["site_game_id"],
            game_row["username"].strip().lower(),
            game_row.get("played_at"),
            game_row.get("time_class"),
            game_row.get("color"),
            game_row.get("result"),
            game_row.get("eco"),
            game_row.get("opening_name"),
            game_row.get("opening_id"),
            game_row.get("opening_ply_count"),
            game_row.get("opponent"),
            game_row.get("white_elo"),
            game_row.get("black_elo"),
            game_row.get("pgn"),
        ),
    )
    return cursor.rowcount > 0


def get_openings_stats(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    color: str = "all",
    time_class: str = "all",
    site: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Aggregate opening statistics for a user with optional filters.
    Returns list of dicts with opening_key, opening_label, games, wins, draws, losses, score_pct.
    If site is None or "all", includes games from all sites.
    Limited to top `limit` openings by games played (default 10).
    """
    ensure_openings_table(conn)
    cursor = conn.cursor()

    query = """
        SELECT 
            COALESCE(o.opening_key, 'unknown') as opening_key,
            COALESCE(o.opening_label, 'Unknown') as opening_label,
            COUNT(*) as games,
            SUM(CASE WHEN g.result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN g.result = 'draw' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN g.result = 'loss' THEN 1 ELSE 0 END) as losses
        FROM games g
        LEFT JOIN openings o ON g.opening_id = o.id
        WHERE g.user_id = %s AND LOWER(g.username) = LOWER(%s)
    """
    params: list = [user_id, username]

    if site and site != "all":
        query += " AND g.site = %s"
        params.append(site)

    if color != "all":
        query += " AND g.color = %s"
        params.append(color)

    if time_class != "all":
        query += " AND g.time_class = %s"
        params.append(time_class)

    query += " GROUP BY opening_key, opening_label ORDER BY games DESC LIMIT %s"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        games = row["games"]
        wins = row["wins"]
        draws = row["draws"]
        losses = row["losses"]

        if games > 0:
            score_pct = round((wins + 0.5 * draws) / games * 100, 1)
        else:
            score_pct = 0.0

        results.append({
            "opening_key": row["opening_key"],
            "opening_label": row["opening_label"],
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score_pct": score_pct,
        })

    return results


def upsert_import_status(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str,
    imported: int,
    skipped: int,
    max_games: int,
    imported_at: str,
) -> None:
    """Record import status for a user."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    cursor.execute(
        """
        INSERT INTO imports 
        (user_id, username, site, imported, skipped, max_games, imported_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, username)
        DO UPDATE SET
            imported = EXCLUDED.imported,
            skipped = EXCLUDED.skipped,
            max_games = EXCLUDED.max_games,
            imported_at = EXCLUDED.imported_at
        """,
        (user_id, canonical_username, site, imported, skipped, max_games, imported_at),
    )


def get_import_status(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str = "lichess",
) -> dict:
    """Get last import status + total games count for a user."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()

    cursor.execute(
        """
        SELECT imported, skipped, max_games, imported_at
        FROM imports
        WHERE user_id = %s AND LOWER(username) = %s AND site = %s
        """,
        (user_id, canonical_username, site),
    )
    import_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT COUNT(*) as total
        FROM games
        WHERE user_id = %s AND LOWER(username) = %s AND site = %s
        """,
        (user_id, canonical_username, site),
    )
    total_row = cursor.fetchone()

    return {
        "username": username,
        "imported_at": import_row["imported_at"] if import_row else None,
        "last_imported": import_row["imported"] if import_row else None,
        "last_skipped": import_row["skipped"] if import_row else None,
        "total_games": total_row["total"] if total_row else 0,
    }


def get_games_by_opening(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    opening_key: str,
    variation_key: str | None = None,
    color: str = "all",
    time_class: str = "all",
    result: str = "all",
    offset: int = 0,
    limit: int = 10,
    site: str | None = None,
) -> dict:
    """
    Get games and summary stats for a user and opening.
    Returns both summary and paginated games.
    If site is None or "all", includes games from all sites.
    """
    ensure_openings_table(conn)
    cursor = conn.cursor()
    canonical_username = username.strip().lower()

    base_where_conditions = [
        "g.user_id = %s",
        "LOWER(g.username) = %s",
        "g.site_game_id IS NOT NULL",
        "g.site_game_id != ''",
    ]
    base_params = [user_id, canonical_username]

    if opening_key == "unknown":
        base_where_conditions.append("g.opening_id IS NULL")
    else:
        base_where_conditions.append("o.opening_key = %s")
        base_params.append(opening_key)

    if variation_key:
        base_where_conditions.append("o.variation_key = %s")
        base_params.append(variation_key)

    if site and site != "all":
        base_where_conditions.append("g.site = %s")
        base_params.append(site)

    if color != "all":
        base_where_conditions.append("g.color = %s")
        base_params.append(color)

    if time_class != "all":
        base_where_conditions.append("g.time_class = %s")
        base_params.append(time_class)

    base_where_clause = " AND ".join(base_where_conditions)

    summary_query = f"""
        SELECT 
            COUNT(*) as total_games,
            SUM(CASE WHEN g.result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN g.result = 'draw' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN g.result = 'loss' THEN 1 ELSE 0 END) as losses,
            COALESCE(MAX(o.opening_label), 'Unknown') as opening_label,
            MAX(o.variation_label) as variation_label
        FROM games g
        LEFT JOIN openings o ON g.opening_id = o.id
        WHERE {base_where_clause}
    """

    cursor.execute(summary_query, base_params)
    summary_row = cursor.fetchone()

    total_games = summary_row["total_games"] or 0
    wins = summary_row["wins"] or 0
    draws = summary_row["draws"] or 0
    losses = summary_row["losses"] or 0
    opening_label = summary_row["opening_label"] if summary_row else "Unknown"
    variation_label = summary_row["variation_label"] if summary_row else None
    score_pct = ((wins + 0.5 * draws) / total_games * 100) if total_games > 0 else 0.0

    games_where_conditions = base_where_conditions.copy()
    games_params = base_params.copy()

    if result != "all":
        games_where_conditions.append("result = %s")
        games_params.append(result)

    games_where_clause = " AND ".join(games_where_conditions)

    games_query = f"""
        SELECT 
            g.site,
            g.site_game_id,
            g.played_at,
            g.color,
            g.result,
            g.opponent,
            COALESCE(o.opening_label, g.opening_name) as opening_label
        FROM games g
        LEFT JOIN openings o ON g.opening_id = o.id
        WHERE {games_where_clause}
        ORDER BY 
            CASE WHEN g.played_at IS NULL THEN 1 ELSE 0 END,
            g.played_at DESC,
            g.id DESC
        LIMIT %s OFFSET %s
    """

    cursor.execute(games_query, games_params + [limit, offset])
    rows = cursor.fetchall()

    games = []
    for row in rows:
        site_val = row["site"]
        gid = row["site_game_id"]
        if site_val == "lichess":
            game_url = f"https://lichess.org/{gid}"
        elif site_val == "chesscom":
            game_url = f"https://www.chess.com/game/live/{gid}"
        else:
            game_url = ""
        games.append({
            "site": site_val,
            "site_game_id": gid,
            "played_at": row["played_at"],
            "color": row["color"],
            "result": row["result"],
            "opponent": row["opponent"],
            "opening_name": row["opening_label"],
            "lichess_url": game_url,
        })

    summary: dict = {
        "total_games": total_games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "score_pct": round(score_pct, 1),
        "opening_label": opening_label,
        "opening_key": opening_key,
    }
    if variation_key and variation_label:
        summary["variation_label"] = variation_label

    return {"summary": summary, "games": games}


def get_variations_stats(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    opening_key: str,
    color: str = "all",
    time_class: str = "all",
    site: str | None = None,
) -> list[dict]:
    """
    Aggregate variation statistics for a user and opening_key.
    Returns list of dicts with variation_key, variation_label, games, wins, draws, losses, score_pct.
    """
    ensure_openings_table(conn)
    cursor = conn.cursor()

    if opening_key == "unknown":
        return [
            {
                "variation_key": "unknown",
                "variation_label": "Unknown",
                "games": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "score_pct": 0.0,
            }
        ]

    query = """
        SELECT 
            COALESCE(o.variation_key, 'unknown') as variation_key,
            COALESCE(o.variation_label, 'Unknown') as variation_label,
            COUNT(*) as games,
            SUM(CASE WHEN g.result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN g.result = 'draw' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN g.result = 'loss' THEN 1 ELSE 0 END) as losses
        FROM games g
        LEFT JOIN openings o ON g.opening_id = o.id
        WHERE g.user_id = %s AND LOWER(g.username) = LOWER(%s)
          AND o.opening_key = %s
    """
    params: list = [user_id, username, opening_key]

    if site and site != "all":
        query += " AND g.site = %s"
        params.append(site)

    if color != "all":
        query += " AND g.color = %s"
        params.append(color)

    if time_class != "all":
        query += " AND g.time_class = %s"
        params.append(time_class)

    query += " GROUP BY variation_key, variation_label ORDER BY games DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        games = row["games"]
        wins = row["wins"]
        draws = row["draws"]
        losses = row["losses"]

        if games > 0:
            score_pct = round((wins + 0.5 * draws) / games * 100, 1)
        else:
            score_pct = 0.0

        results.append({
            "variation_key": row["variation_key"],
            "variation_label": row["variation_label"],
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score_pct": score_pct,
        })

    return results


def get_game_by_id(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    site: str,
) -> dict | None:
    """Get a single game by username, game ID, and site."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT site, site_game_id, played_at, color, result, opponent, 
               opening_name, pgn, eco
        FROM games
        WHERE user_id = %s AND LOWER(username) = %s AND site_game_id = %s AND site = %s
        """,
        (user_id, username.strip().lower(), site_game_id, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def get_analysis(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    site: str,
) -> dict | None:
    """Get cached analysis for a game."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT result_json, created_at, engine_name, engine_version
        FROM analysis
        WHERE user_id = %s AND LOWER(username) = %s AND site_game_id = %s AND site = %s
        """,
        (user_id, username.strip().lower(), site_game_id, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_analysis(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    site: str,
    engine_name: str,
    engine_version: str,
    settings_json: str,
    result_json: str,
) -> None:
    """Save analysis result to cache."""
    cursor = conn.cursor()
    from datetime import datetime, timezone

    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO analysis 
        (user_id, site, site_game_id, username, created_at, engine_name, 
         engine_version, settings_json, result_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id)
        DO UPDATE SET
            created_at = EXCLUDED.created_at,
            engine_name = EXCLUDED.engine_name,
            engine_version = EXCLUDED.engine_version,
            settings_json = EXCLUDED.settings_json,
            result_json = EXCLUDED.result_json
        """,
        (
            user_id,
            site,
            site_game_id,
            username.strip().lower(),
            created_at,
            engine_name,
            engine_version,
            settings_json,
            result_json,
        ),
    )


def get_full_analysis(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> dict | None:
    """Get cached full analysis for a game."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT moves_json, summary_json, meta_json, created_at
        FROM full_analysis
        WHERE user_id = %s AND LOWER(username) = %s AND site_game_id = %s 
        AND depth = %s AND multipv = %s AND site = %s
        """,
        (user_id, username.strip().lower(), site_game_id, depth, multipv, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_full_analysis(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    moves_json: str,
    summary_json: str,
    meta_json: str,
    site: str,
) -> None:
    """Save full analysis result to cache."""
    cursor = conn.cursor()
    from datetime import datetime, timezone

    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO full_analysis 
        (user_id, site, site_game_id, username, depth, multipv, 
         moves_json, summary_json, meta_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id, depth, multipv)
        DO UPDATE SET
            moves_json = EXCLUDED.moves_json,
            summary_json = EXCLUDED.summary_json,
            meta_json = EXCLUDED.meta_json,
            created_at = EXCLUDED.created_at
        """,
        (
            user_id,
            site,
            site_game_id,
            username.strip().lower(),
            depth,
            multipv,
            moves_json,
            summary_json,
            meta_json,
            created_at,
        ),
    )


def create_analysis_job(
    conn: psycopg.Connection,
    job_id: str,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> None:
    """Create a new analysis job record."""
    from datetime import datetime, timezone

    cursor = conn.cursor()
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO analysis_jobs (id, user_id, site, site_game_id, username, depth, multipv, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (job_id, user_id, site, site_game_id, username.strip().lower(), depth, multipv, created_at),
    )


def get_analysis_job(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> dict | None:
    """Get an analysis job by game/user/params. Returns None if not found."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, site, site_game_id, username, depth, multipv, created_at
        FROM analysis_jobs
        WHERE user_id = %s AND LOWER(username) = %s AND site_game_id = %s AND depth = %s AND multipv = %s AND site = %s
        """,
        (user_id, username.strip().lower(), site_game_id, depth, multipv, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def delete_analysis_job(conn: psycopg.Connection, job_id: str) -> None:
    """Delete an analysis job by ID."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM analysis_jobs WHERE id = %s", (job_id,))


def count_analysis_jobs(conn: psycopg.Connection) -> int:
    """Count total number of in-progress analysis jobs."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM analysis_jobs")
    row = cursor.fetchone()
    return row["total"] if row else 0
