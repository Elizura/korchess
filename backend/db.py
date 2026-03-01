"""Database initialization and helper functions for Korchess (Postgres)."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PUBLIC_USER_ID_PREFIX = "public:"
LESSON_CONSENT_CHANNEL_EMAIL = "email_lessons"
LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY = "game_ai_summary"
LESSON_CONSENT_DECISIONS = frozenset({"consented", "declined"})
RAW_OPENING_KEY_PREFIX = "raw__"


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


def public_user_id_for_username(username: str) -> str:
    """Build canonical public owner ID for shared username-scoped data."""
    canonical_username = username.strip().lower()
    if not canonical_username:
        raise ValueError("Username is required.")
    return f"{PUBLIC_USER_ID_PREFIX}{canonical_username}"


def get_public_user_id_for_username(conn: psycopg.Connection, username: str) -> str:
    """Resolve shared public owner ID for username (no writes)."""
    del conn  # keep backwards-compatible signature for existing call sites
    return public_user_id_for_username(username)


def ensure_public_user_for_username(conn: psycopg.Connection, username: str) -> str:
    """Ensure the shared public user exists and return its ID."""
    canonical_username = username.strip().lower()
    if not canonical_username:
        raise ValueError("Username is required.")

    public_user_id = public_user_id_for_username(canonical_username)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (id, name, username)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (public_user_id, f"Public {canonical_username}", canonical_username),
    )
    return public_user_id


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
            insights_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, site, site_game_id, depth, multipv),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'full_analysis' AND column_name = 'insights_json'
        """
    )
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE full_analysis ADD COLUMN insights_json TEXT")

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
        CREATE TABLE IF NOT EXISTS full_analysis_requests (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_full_analysis_requests_user_day
        ON full_analysis_requests(user_id, requested_at)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_game_insights (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            insights_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, site, site_game_id, depth, multipv),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_game_insights_lookup
        ON ai_game_insights(user_id, site, site_game_id, depth, multipv)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_insights_requests (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            username TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            status TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_insights_requests_user_day_status
        ON ai_insights_requests(user_id, requested_at, status)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lesson_consent_events (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            decision TEXT NOT NULL,
            source TEXT NOT NULL,
            site TEXT,
            site_game_id TEXT,
            analysis_depth INTEGER,
            analysis_multipv INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lesson_consent_events_user_created
        ON lesson_consent_events(user_id, created_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lesson_consent_events_channel_decision
        ON lesson_consent_events(channel, decision, created_at DESC)
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

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS insight_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            reason TEXT,
            error TEXT,
            feature_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            meta_json TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_jobs_lookup
        ON insight_jobs(user_id, username, site, status, updated_at DESC)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS insight_game_features (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            analysis_tier TEXT NOT NULL,
            light_json TEXT NOT NULL,
            deep_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, site, site_game_id, feature_version),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_game_features_lookup
        ON insight_game_features(user_id, username, site, feature_version)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS player_insights (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            status TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            narrative_version TEXT NOT NULL,
            coverage_json TEXT NOT NULL,
            features_json TEXT NOT NULL,
            fact_map_json TEXT NOT NULL,
            narrative_json TEXT NOT NULL,
            source_job_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, username, site),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_player_insights_lookup
        ON player_insights(user_id, username, site, updated_at DESC)
        """
    )

    # PostHog-only analytics mode:
    # remove legacy first-party analytics storage artifacts.
    cursor.execute("DROP VIEW IF EXISTS analytics_event_counts_daily")
    cursor.execute("DROP VIEW IF EXISTS analytics_daily_active_users")
    cursor.execute("DROP TABLE IF EXISTS analytics_events")
    cursor.execute("DROP TABLE IF EXISTS analytics_sessions")
    cursor.execute("DROP TABLE IF EXISTS analytics_identities")

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


def create_user_if_missing(conn: psycopg.Connection, user: dict) -> bool:
    """Insert a user record if it doesn't exist. Returns True if created."""
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
    return cursor.rowcount > 0


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
    opening_key_expr = _opening_key_expr_sql("g", "o")
    opening_label_expr = _opening_label_expr_sql("g", "o")

    query = f"""
        SELECT
            {opening_key_expr} as opening_key,
            {opening_label_expr} as opening_label,
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

    # Group by select-list positions so Postgres groups by the computed CASE expressions.
    query += " GROUP BY 1, 2 ORDER BY games DESC LIMIT %s"
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


def _opening_name_value_sql(game_alias: str = "g") -> str:
    return f"NULLIF(BTRIM({game_alias}.opening_name), '')"


def _opening_name_slug_sql(game_alias: str = "g") -> str:
    opening_name_sql = _opening_name_value_sql(game_alias)
    return (
        "COALESCE("
        "NULLIF("
        f"regexp_replace(regexp_replace(lower({opening_name_sql}), '[^a-z0-9]+', '_', 'g'), '^_+|_+$', '', 'g'),"
        "''"
        "),"
        "'unknown'"
        ")"
    )


def _opening_key_expr_sql(game_alias: str = "g", opening_alias: str = "o") -> str:
    opening_name_sql = _opening_name_value_sql(game_alias)
    opening_slug_sql = _opening_name_slug_sql(game_alias)
    return (
        "CASE "
        f"WHEN {opening_alias}.opening_key IS NOT NULL THEN {opening_alias}.opening_key "
        f"WHEN {opening_name_sql} IS NOT NULL AND LOWER({opening_name_sql}) <> 'unknown' "
        f"THEN '{RAW_OPENING_KEY_PREFIX}' || {opening_slug_sql} "
        "ELSE 'unknown' "
        "END"
    )


def _opening_label_expr_sql(game_alias: str = "g", opening_alias: str = "o") -> str:
    opening_name_sql = _opening_name_value_sql(game_alias)
    return (
        "CASE "
        f"WHEN {opening_alias}.opening_label IS NOT NULL THEN {opening_alias}.opening_label "
        f"WHEN {opening_name_sql} IS NOT NULL AND LOWER({opening_name_sql}) <> 'unknown' THEN {opening_name_sql} "
        "ELSE 'Unknown' "
        "END"
    )


def _raw_opening_key_suffix(opening_key: str) -> str:
    if not opening_key.startswith(RAW_OPENING_KEY_PREFIX):
        return ""
    raw_part = opening_key[len(RAW_OPENING_KEY_PREFIX):]
    normalized = re.sub(r"[^a-z0-9]+", "_", raw_part.strip().lower()).strip("_")
    return normalized or "unknown"


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


def get_import_history(
    conn: psycopg.Connection,
    user_id: str,
    limit: int = 10,
) -> list[dict]:
    """Get last N import records for a user, ordered by most recent first."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT username, site, imported_at
        FROM imports
        WHERE user_id = %s
        ORDER BY imported_at DESC
        LIMIT %s
        """,
        (user_id, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


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
    opening_name_value_sql = _opening_name_value_sql("g")
    opening_name_slug_sql = _opening_name_slug_sql("g")
    opening_label_expr = _opening_label_expr_sql("g", "o")

    base_where_conditions = [
        "g.user_id = %s",
        "LOWER(g.username) = %s",
        "g.site_game_id IS NOT NULL",
        "g.site_game_id != ''",
    ]
    base_params = [user_id, canonical_username]

    if opening_key == "unknown":
        base_where_conditions.append("g.opening_id IS NULL")
        base_where_conditions.append(
            f"({opening_name_value_sql} IS NULL OR LOWER({opening_name_value_sql}) = 'unknown')"
        )
    elif opening_key.startswith(RAW_OPENING_KEY_PREFIX):
        base_where_conditions.append("g.opening_id IS NULL")
        base_where_conditions.append(f"{opening_name_slug_sql} = %s")
        base_params.append(_raw_opening_key_suffix(opening_key))
    else:
        base_where_conditions.append("o.opening_key = %s")
        base_params.append(opening_key)

    if variation_key:
        if opening_key.startswith(RAW_OPENING_KEY_PREFIX):
            base_where_conditions.append("1 = 0")
        else:
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
            COALESCE(MAX({opening_label_expr}), 'Unknown') as opening_label,
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
            COALESCE(o.opening_label, {opening_name_value_sql}, 'Unknown') as opening_label
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
    if opening_key.startswith(RAW_OPENING_KEY_PREFIX):
        # Synthetic keys (from raw opening_name fallback) have no canonical variation mapping.
        return []

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
               opening_name, opening_ply_count, pgn, eco
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
        SELECT moves_json, summary_json, meta_json, insights_json, created_at
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
    insights_json: str | None,
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
         moves_json, summary_json, meta_json, insights_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id, depth, multipv)
        DO UPDATE SET
            moves_json = EXCLUDED.moves_json,
            summary_json = EXCLUDED.summary_json,
            meta_json = EXCLUDED.meta_json,
            insights_json = COALESCE(EXCLUDED.insights_json, full_analysis.insights_json),
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
            insights_json,
            created_at,
        ),
    )


def save_full_analysis_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
    insights_json: str,
) -> None:
    """Persist deterministic single-game insights for an existing full analysis."""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE full_analysis
        SET insights_json = %s
        WHERE user_id = %s
          AND LOWER(username) = %s
          AND site_game_id = %s
          AND depth = %s
          AND multipv = %s
          AND site = %s
        """,
        (
            insights_json,
            user_id,
            username.strip().lower(),
            site_game_id,
            depth,
            multipv,
            site,
        ),
    )


def get_ai_game_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> dict | None:
    """Get cached AI insights for a specific account + game + settings."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT insights_json, source, created_at, updated_at
        FROM ai_game_insights
        WHERE user_id = %s
          AND LOWER(username) = %s
          AND site_game_id = %s
          AND depth = %s
          AND multipv = %s
          AND site = %s
        """,
        (user_id, username.strip().lower(), site_game_id, depth, multipv, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_ai_game_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
    insights_json: str,
    source: str,
) -> None:
    """Upsert AI insights cache for a specific account + game + settings."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ai_game_insights
        (user_id, site, site_game_id, username, depth, multipv, insights_json, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id, depth, multipv)
        DO UPDATE SET
            insights_json = EXCLUDED.insights_json,
            source = EXCLUDED.source,
            updated_at = now()
        """,
        (
            user_id,
            site,
            site_game_id,
            username.strip().lower(),
            depth,
            multipv,
            insights_json,
            source,
        ),
    )


def log_ai_insights_request(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
    status: str,
) -> None:
    """Record one AI insights request attempt/result for quota accounting."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ai_insights_requests
        (user_id, site, site_game_id, username, depth, multipv, status, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        """,
        (
            user_id,
            site,
            site_game_id,
            username.strip().lower(),
            depth,
            multipv,
            status.strip().lower(),
        ),
    )


def insert_lesson_consent_event(
    conn: psycopg.Connection,
    user_id: str,
    decision: str,
    source: str,
    *,
    site: str | None = None,
    site_game_id: str | None = None,
    analysis_depth: int | None = None,
    analysis_multipv: int | None = None,
    channel: str = LESSON_CONSENT_CHANNEL_EMAIL,
) -> None:
    """Insert an append-only lesson consent decision event."""
    normalized_decision = decision.strip().lower()
    if normalized_decision not in LESSON_CONSENT_DECISIONS:
        raise ValueError("Invalid lesson consent decision.")

    normalized_source = source.strip().lower()
    if normalized_source != LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY:
        raise ValueError("Invalid lesson consent source.")

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO lesson_consent_events
        (
            user_id,
            channel,
            decision,
            source,
            site,
            site_game_id,
            analysis_depth,
            analysis_multipv
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            channel.strip().lower(),
            normalized_decision,
            normalized_source,
            (site or "").strip().lower() or None,
            (site_game_id or "").strip() or None,
            analysis_depth,
            analysis_multipv,
        ),
    )


def get_latest_lesson_consent_state(
    conn: psycopg.Connection,
    user_id: str,
    channel: str = LESSON_CONSENT_CHANNEL_EMAIL,
) -> dict | None:
    """Return the latest lesson consent event for a user/channel."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT decision, created_at
        FROM lesson_consent_events
        WHERE user_id = %s
          AND channel = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id, channel.strip().lower()),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def get_lesson_consent_status_payload(
    conn: psycopg.Connection,
    user_id: str,
    channel: str = LESSON_CONSENT_CHANNEL_EMAIL,
) -> dict[str, Any]:
    """Normalize lesson consent status response payload."""
    normalized_channel = channel.strip().lower()
    latest = get_latest_lesson_consent_state(conn, user_id, normalized_channel)
    if not latest:
        return {
            "channel": normalized_channel,
            "state": "unknown",
            "consented": False,
            "last_decision_at": None,
        }

    raw_decision = str(latest.get("decision") or "").strip().lower()
    state = raw_decision if raw_decision in LESSON_CONSENT_DECISIONS else "unknown"
    created_at = latest.get("created_at")
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()

    return {
        "channel": normalized_channel,
        "state": state,
        "consented": state == "consented",
        "last_decision_at": created_at,
    }


def count_user_ai_gemini_success_utc_day(
    conn: psycopg.Connection,
    user_id: str,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> int:
    """Count Gemini-success AI insights generations for a user in a UTC day."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM ai_insights_requests
        WHERE user_id = %s
          AND status = 'gemini_success'
          AND requested_at >= %s
          AND requested_at < %s
        """,
        (user_id, day_start_utc, day_end_utc),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    return int(row.get("total") or 0)


def count_user_full_analysis_completed_utc_day(
    conn: psycopg.Connection,
    user_id: str,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> int:
    """Count deep-analysis requests for a user within a UTC day window."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT GREATEST(
            (
                SELECT COUNT(*)
                FROM full_analysis_requests
                WHERE user_id = %s
                  AND requested_at >= %s
                  AND requested_at < %s
            ),
            (
                SELECT COUNT(*)
                FROM full_analysis
                WHERE user_id = %s
                  AND created_at::timestamptz >= %s
                  AND created_at::timestamptz < %s
            )
        ) AS total
        """,
        (user_id, day_start_utc, day_end_utc, user_id, day_start_utc, day_end_utc),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    return int(row.get("total") or 0)


def log_full_analysis_request(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> None:
    """Record a deep-analysis request for quota accounting."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO full_analysis_requests
        (user_id, site, site_game_id, username, depth, multipv, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        """,
        (
            user_id,
            site,
            site_game_id,
            username.strip().lower(),
            depth,
            multipv,
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


def create_insight_job(
    conn: psycopg.Connection,
    job_id: str,
    user_id: str,
    username: str,
    site: str,
    status: str,
    stage: str,
    reason: str,
    feature_version: str,
    meta: dict | None = None,
) -> None:
    """Create an insights background job."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO insight_jobs
        (id, user_id, username, site, status, stage, reason, error, feature_version,
         created_at, started_at, finished_at, updated_at, meta_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, NULL, NULL, %s, %s)
        """,
        (
            job_id,
            user_id,
            username.strip().lower(),
            site,
            status,
            stage,
            reason,
            feature_version,
            now,
            now,
            json.dumps(meta or {}),
        ),
    )


def get_active_insight_job(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str = "all",
) -> dict | None:
    """Get the latest active insights job for this user/username/site."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, stage, reason, error, feature_version, created_at,
               started_at, finished_at, updated_at, meta_json
        FROM insight_jobs
        WHERE user_id = %s
          AND LOWER(username) = %s
          AND site = %s
          AND status IN ('queued', 'running')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (user_id, username.strip().lower(), site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    data["meta"] = json.loads(data.get("meta_json") or "{}")
    return data


def get_insight_job_by_id(
    conn: psycopg.Connection,
    job_id: str,
) -> dict | None:
    """Fetch an insights job by ID."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, username, site, status, stage, reason, error, feature_version,
               created_at, started_at, finished_at, updated_at, meta_json
        FROM insight_jobs
        WHERE id = %s
        """,
        (job_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    data["meta"] = json.loads(data.get("meta_json") or "{}")
    return data


def update_insight_job(
    conn: psycopg.Connection,
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    meta: dict | None = None,
) -> None:
    """Update mutable fields of an insights job."""
    from datetime import datetime, timezone

    fields = ["updated_at = %s"]
    params: list = [datetime.now(timezone.utc).isoformat()]

    if status is not None:
        fields.append("status = %s")
        params.append(status)
    if stage is not None:
        fields.append("stage = %s")
        params.append(stage)
    if error is not None:
        fields.append("error = %s")
        params.append(error)
    if started_at is not None:
        fields.append("started_at = %s")
        params.append(started_at)
    if finished_at is not None:
        fields.append("finished_at = %s")
        params.append(finished_at)
    if meta is not None:
        fields.append("meta_json = %s")
        params.append(json.dumps(meta))

    params.append(job_id)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        UPDATE insight_jobs
        SET {", ".join(fields)}
        WHERE id = %s
        """,
        params,
    )


def upsert_insight_game_feature(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str,
    site_game_id: str,
    feature_version: str,
    light: dict,
    deep: dict | None = None,
) -> None:
    """Insert or update per-game insight features."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    analysis_tier = "deep" if deep else "light"
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO insight_game_features
        (user_id, username, site, site_game_id, feature_version, analysis_tier,
         light_json, deep_json, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, site, site_game_id, feature_version)
        DO UPDATE SET
            analysis_tier = CASE
                WHEN EXCLUDED.deep_json IS NOT NULL THEN 'deep'
                ELSE insight_game_features.analysis_tier
            END,
            light_json = EXCLUDED.light_json,
            deep_json = COALESCE(EXCLUDED.deep_json, insight_game_features.deep_json),
            updated_at = EXCLUDED.updated_at
        """,
        (
            user_id,
            username.strip().lower(),
            site,
            site_game_id,
            feature_version,
            analysis_tier,
            json.dumps(light),
            json.dumps(deep) if deep is not None else None,
            now,
            now,
        ),
    )


def get_insight_game_features(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str = "all",
    feature_version: str | None = None,
) -> list[dict]:
    """Fetch stored per-game insights feature artifacts."""
    cursor = conn.cursor()
    query = """
        SELECT site, site_game_id, feature_version, analysis_tier,
               light_json, deep_json, created_at, updated_at
        FROM insight_game_features
        WHERE user_id = %s AND LOWER(username) = %s
    """
    params: list = [user_id, username.strip().lower()]

    if site != "all":
        query += " AND site = %s"
        params.append(site)

    if feature_version is not None:
        query += " AND feature_version = %s"
        params.append(feature_version)

    cursor.execute(query + " ORDER BY updated_at DESC", params)
    rows = cursor.fetchall()
    results = []
    for row in rows:
        data = dict(row)
        data["light"] = json.loads(data.get("light_json") or "{}")
        data["deep"] = json.loads(data.get("deep_json") or "{}") if data.get("deep_json") else None
        results.append(data)
    return results


def get_games_for_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str = "all",
    limit: int = 500,
) -> list[dict]:
    """Fetch recent games with PGN and metadata for insights processing."""
    cursor = conn.cursor()
    query = """
        SELECT site, site_game_id, played_at, time_class, color, result, eco,
               opening_name, opponent, white_elo, black_elo, pgn
        FROM games
        WHERE user_id = %s AND LOWER(username) = %s
    """
    params: list = [user_id, username.strip().lower()]
    if site != "all":
        query += " AND site = %s"
        params.append(site)

    query += """
        ORDER BY
            CASE WHEN played_at IS NULL THEN 1 ELSE 0 END,
            played_at DESC,
            id DESC
        LIMIT %s
    """
    params.append(limit)
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def upsert_player_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str,
    status: str,
    feature_version: str,
    narrative_version: str,
    coverage: dict,
    features: dict,
    fact_map: dict,
    narrative: dict,
    source_job_id: str | None = None,
) -> None:
    """Insert or update latest user-level insights snapshot."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO player_insights
        (user_id, username, site, status, feature_version, narrative_version,
         coverage_json, features_json, fact_map_json, narrative_json,
         source_job_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, username, site)
        DO UPDATE SET
            status = EXCLUDED.status,
            feature_version = EXCLUDED.feature_version,
            narrative_version = EXCLUDED.narrative_version,
            coverage_json = EXCLUDED.coverage_json,
            features_json = EXCLUDED.features_json,
            fact_map_json = EXCLUDED.fact_map_json,
            narrative_json = EXCLUDED.narrative_json,
            source_job_id = EXCLUDED.source_job_id,
            updated_at = EXCLUDED.updated_at
        """,
        (
            user_id,
            username.strip().lower(),
            site,
            status,
            feature_version,
            narrative_version,
            json.dumps(coverage),
            json.dumps(features),
            json.dumps(fact_map),
            json.dumps(narrative),
            source_job_id,
            now,
            now,
        ),
    )


def get_player_insights(
    conn: psycopg.Connection,
    user_id: str,
    username: str,
    site: str = "all",
) -> dict | None:
    """Fetch latest user-level insights snapshot."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT status, feature_version, narrative_version,
               coverage_json, features_json, fact_map_json, narrative_json,
               source_job_id, created_at, updated_at
        FROM player_insights
        WHERE user_id = %s AND LOWER(username) = %s AND site = %s
        """,
        (user_id, username.strip().lower(), site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    data["coverage"] = json.loads(data.get("coverage_json") or "{}")
    data["features"] = json.loads(data.get("features_json") or "{}")
    data["fact_map"] = json.loads(data.get("fact_map_json") or "{}")
    data["narrative"] = json.loads(data.get("narrative_json") or "{}")
    return data
