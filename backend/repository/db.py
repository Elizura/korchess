"""Database initialization and helper functions for Korchess (Postgres).

This module contains:
- Schema initialization (init_db)
- User management (upsert_user, get_user_by_id, etc.)
- Game management (upsert_game, get_games_by_opening, etc.)
- Analysis caching (get_full_analysis, save_full_analysis, etc.)
- Insights storage (player_insights, insight_jobs, etc.)
- Import tracking (imports table)

For connection utilities, see db_connection.py.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

import psycopg

# Re-export core connection utilities for backwards compatibility
from repository.db_connection import (
    get_connection,
    LESSON_CONSENT_CHANNEL_EMAIL,
    LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY,
    LESSON_CONSENT_DECISIONS,
    RAW_OPENING_KEY_PREFIX,
)


def init_db() -> None:
    """Initialize the database schema and indexes.
    
    Shared data (games, imports, insights, analysis) is keyed by (username, site).
    User-specific data (AI quotas, consent) is keyed by user_id.
    """
    with get_connection() as conn:
        _init_db_schema(conn)


def _init_db_schema(conn: psycopg.Connection) -> None:
    """Internal helper to create all schema elements."""
    cursor = conn.cursor()

    # Users table - for authenticated users only
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            name TEXT,
            avatar_url TEXT,
            avatar TEXT,
            username TEXT,
            password_hash TEXT,
            email_verified BOOLEAN DEFAULT FALSE,
            verification_code TEXT,
            verification_code_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    # Migrations for existing databases
    for col, col_def in [
        ("updated_at", "TIMESTAMPTZ DEFAULT now()"),
        ("password_hash", "TEXT"),
        ("email_verified", "BOOLEAN DEFAULT FALSE"),
        ("verification_code", "TEXT"),
        ("verification_code_expires_at", "TIMESTAMPTZ"),
    ]:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = %s
            """,
            (col,),
        )
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")

    cursor.execute(
        """
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'users' AND indexname = 'users_email_key'
        """
    )
    if not cursor.fetchone():
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users(email) WHERE email IS NOT NULL"
        )

    # Refresh tokens for persistent sessions
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)"
    )

    # Games table - shared by (username, site, site_game_id)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
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
            UNIQUE(username, site, site_game_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_username_site
        ON games(username, site)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_username_color_time_class
        ON games(username, color, time_class)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_username_played_at_id
        ON games(username, played_at DESC NULLS LAST, id DESC)
        """
    )

    # Imports table - shared by (username, site)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            imported INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            max_games INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            last_synced_at TIMESTAMPTZ,
            PRIMARY KEY (username, site)
        )
        """
    )

    # Full analysis cache - shared by (username, site, site_game_id, depth, multipv)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS full_analysis (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            moves_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            insights_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(username, site, site_game_id, depth, multipv)
        )
        """
    )

    # Analysis jobs - tracks in-flight analysis, shared by (username, site, site_game_id)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            depth INTEGER NOT NULL,
            multipv INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(username, site, site_game_id, depth, multipv)
        )
        """
    )

    # AI game insights - user-specific (for quota tracking), keyed by user_id
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

    # AI insights requests - user-specific (for quota tracking), keyed by user_id
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
        CREATE INDEX IF NOT EXISTS idx_ai_insights_requests_success_user_time
        ON ai_insights_requests(user_id, requested_at)
        WHERE status = 'gemini_success'
        """
    )

    # Lesson consent events - user-specific, keyed by user_id
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
        CREATE INDEX IF NOT EXISTS idx_opening_moves_ply_uci_opening
        ON opening_moves(ply_index, uci, opening_id)
        """
    )

    # Insight jobs - shared by (username, site)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS insight_jobs (
            id TEXT PRIMARY KEY,
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
            meta_json TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_jobs_active_username_site
        ON insight_jobs(username, site, updated_at DESC)
        WHERE status IN ('queued', 'running')
        """
    )

    # Game-level insight features - shared by (username, site, site_game_id)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS insight_game_features (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            analysis_tier TEXT NOT NULL,
            light_json TEXT NOT NULL,
            deep_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(username, site, site_game_id, feature_version)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_game_features_lookup
        ON insight_game_features(username, site, feature_version)
        """
    )

    # Player insights (aggregated) - shared by (username, site)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS player_insights (
            id SERIAL PRIMARY KEY,
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
            UNIQUE(username, site)
        )
        """
    )

    # Quick scan jobs - shared by (username, site)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            status TEXT NOT NULL,
            total_games INTEGER NOT NULL,
            games_done INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scan_jobs_active
        ON scan_jobs(username, site, updated_at DESC)
        WHERE status IN ('queued', 'running')
        """
    )

    # Game quick scans - shared by (username, site, site_game_id)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS game_quick_scans (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            site TEXT NOT NULL,
            site_game_id TEXT NOT NULL,
            problems_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(username, site, site_game_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_game_quick_scans_username_site
        ON game_quick_scans(username, site)
        """
    )

    # Chess profiles - user-specific saved profiles for Lichess/Chess.com accounts
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chess_profiles (
            user_id TEXT NOT NULL REFERENCES users(id),
            chess_username TEXT NOT NULL,
            site TEXT NOT NULL,
            bullet_rating INTEGER,
            blitz_rating INTEGER,
            rapid_rating INTEGER,
            classical_rating INTEGER,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (user_id, chess_username, site)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chess_profiles_user_id
        ON chess_profiles(user_id)
        """
    )

    # Drop redundant/legacy indexes replaced by constraints or better-targeted indexes.
    cursor.execute("DROP INDEX IF EXISTS idx_analysis_user")
    cursor.execute("DROP INDEX IF EXISTS idx_analysis_lookup")
    cursor.execute("DROP INDEX IF EXISTS idx_full_analysis_user")
    cursor.execute("DROP INDEX IF EXISTS idx_full_analysis_lookup")
    cursor.execute("DROP INDEX IF EXISTS idx_analysis_jobs_lookup")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_game_insights_lookup")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_insights_requests_user_day_status")
    cursor.execute("DROP INDEX IF EXISTS idx_insight_jobs_lookup")
    cursor.execute("DROP INDEX IF EXISTS idx_player_insights_lookup")
    # Drop old user_id-based indexes from refactored tables
    cursor.execute("DROP INDEX IF EXISTS idx_games_user")
    cursor.execute("DROP INDEX IF EXISTS idx_games_user_username")
    cursor.execute("DROP INDEX IF EXISTS idx_games_user_username_color_time_class")
    cursor.execute("DROP INDEX IF EXISTS idx_games_user_site_username")
    cursor.execute("DROP INDEX IF EXISTS idx_games_user_username_played_at_id")
    cursor.execute("DROP INDEX IF EXISTS idx_game_quick_scans_user")
    cursor.execute("DROP INDEX IF EXISTS idx_scan_jobs_active")
    cursor.execute("DROP INDEX IF EXISTS idx_insight_jobs_active_user_site_updated")

    # PostHog-only analytics mode:
    # remove legacy first-party analytics storage artifacts.
    cursor.execute("DROP VIEW IF EXISTS analytics_event_counts_daily")
    cursor.execute("DROP VIEW IF EXISTS analytics_daily_active_users")
    cursor.execute("DROP TABLE IF EXISTS analytics_events")
    cursor.execute("DROP TABLE IF EXISTS analytics_sessions")
    cursor.execute("DROP TABLE IF EXISTS analytics_identities")

    conn.commit()


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


def get_user_by_email(conn: psycopg.Connection, email: str) -> dict | None:
    """Fetch a user by email."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, email, name, avatar_url, avatar, username, password_hash,
               email_verified, verification_code, verification_code_expires_at,
               created_at, updated_at
        FROM users
        WHERE LOWER(email) = LOWER(%s)
        """,
        (email,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def create_auth_user(
    conn: psycopg.Connection,
    user_id: str,
    email: str,
    password_hash: str,
    verification_code: str,
    verification_code_expires_at,
) -> None:
    """Create a new user with email/password auth."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified,
                           verification_code, verification_code_expires_at)
        VALUES (%s, %s, %s, FALSE, %s, %s)
        """,
        (user_id, email.lower(), password_hash,
         verification_code, verification_code_expires_at),
    )


def mark_email_verified(conn: psycopg.Connection, user_id: str) -> None:
    """Mark user email as verified and clear the verification code."""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE users
        SET email_verified = TRUE,
            verification_code = NULL,
            verification_code_expires_at = NULL,
            updated_at = now()
        WHERE id = %s
        """,
        (user_id,),
    )


def store_refresh_token(
    conn: psycopg.Connection,
    token_id: str,
    user_id: str,
    token_hash: str,
    expires_at,
) -> None:
    """Store a hashed refresh token."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
        VALUES (%s, %s, %s, %s)
        """,
        (token_id, user_id, token_hash, expires_at),
    )


def get_refresh_token_by_hash(conn: psycopg.Connection, token_hash: str) -> dict | None:
    """Look up a refresh token by its hash."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, token_hash, expires_at, created_at
        FROM refresh_tokens
        WHERE token_hash = %s
        """,
        (token_hash,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def delete_refresh_token(conn: psycopg.Connection, token_hash: str) -> None:
    """Delete a specific refresh token."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (token_hash,))


def delete_user_refresh_tokens(conn: psycopg.Connection, user_id: str) -> None:
    """Delete all refresh tokens for a user (logout everywhere)."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


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


def upsert_chess_profile(
    conn: psycopg.Connection,
    user_id: str,
    chess_username: str,
    site: str,
    bullet_rating: int | None = None,
    blitz_rating: int | None = None,
    rapid_rating: int | None = None,
    classical_rating: int | None = None,
) -> dict:
    """Insert or update a chess profile. Returns the upserted profile."""
    cursor = conn.cursor()
    canonical_username = chess_username.strip().lower()
    cursor.execute(
        """
        INSERT INTO chess_profiles (
            user_id, chess_username, site,
            bullet_rating, blitz_rating, rapid_rating, classical_rating,
            created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (user_id, chess_username, site) DO UPDATE SET
            bullet_rating = EXCLUDED.bullet_rating,
            blitz_rating = EXCLUDED.blitz_rating,
            rapid_rating = EXCLUDED.rapid_rating,
            classical_rating = EXCLUDED.classical_rating,
            updated_at = now()
        RETURNING
            user_id, chess_username, site,
            bullet_rating, blitz_rating, rapid_rating, classical_rating,
            created_at, updated_at
        """,
        (
            user_id,
            canonical_username,
            site,
            bullet_rating,
            blitz_rating,
            rapid_rating,
            classical_rating,
        ),
    )
    row = cursor.fetchone()
    return dict(row) if row else {}


def get_chess_profiles(conn: psycopg.Connection, user_id: str) -> list[dict]:
    """Get all chess profiles for a user, ordered by most recently updated."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            user_id, chess_username, site,
            bullet_rating, blitz_rating, rapid_rating, classical_rating,
            created_at, updated_at
        FROM chess_profiles
        WHERE user_id = %s
        ORDER BY updated_at DESC
        """,
        (user_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_chess_profile(
    conn: psycopg.Connection, user_id: str, chess_username: str, site: str
) -> dict | None:
    """Get a specific chess profile."""
    cursor = conn.cursor()
    canonical_username = chess_username.strip().lower()
    cursor.execute(
        """
        SELECT
            user_id, chess_username, site,
            bullet_rating, blitz_rating, rapid_rating, classical_rating,
            created_at, updated_at
        FROM chess_profiles
        WHERE user_id = %s AND chess_username = %s AND site = %s
        """,
        (user_id, canonical_username, site),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def delete_chess_profile(
    conn: psycopg.Connection, user_id: str, chess_username: str, site: str
) -> bool:
    """Delete a chess profile. Returns True if deleted."""
    cursor = conn.cursor()
    canonical_username = chess_username.strip().lower()
    cursor.execute(
        """
        DELETE FROM chess_profiles
        WHERE user_id = %s AND chess_username = %s AND site = %s
        """,
        (user_id, canonical_username, site),
    )
    return cursor.rowcount > 0


def upsert_game(conn: psycopg.Connection, game_row: dict) -> bool:
    """
    Insert a game, ignoring if it already exists (by username + site + site_game_id).
    Returns True if inserted, False if skipped (duplicate).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO games (
            username, site, site_game_id, played_at, time_class,
            color, result, eco, opening_name, opening_id, opening_ply_count,
            opponent, white_elo, black_elo, pgn
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site, site_game_id) DO NOTHING
        """,
        (
            game_row["username"].strip().lower(),
            game_row["site"],
            game_row["site_game_id"],
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


def bulk_upsert_games(conn: psycopg.Connection, games: list[dict]) -> tuple[int, int]:
    """
    Bulk insert games, skipping duplicates (by username + site + site_game_id).
    Returns (inserted_count, skipped_count).
    
    Uses a single INSERT with multiple VALUES for better performance.
    """
    if not games:
        return 0, 0

    cursor = conn.cursor()
    
    values_list = [
        (
            game["username"].strip().lower(),
            game["site"],
            game["site_game_id"],
            game.get("played_at"),
            game.get("time_class"),
            game.get("color"),
            game.get("result"),
            game.get("eco"),
            game.get("opening_name"),
            game.get("opening_id"),
            game.get("opening_ply_count"),
            game.get("opponent"),
            game.get("white_elo"),
            game.get("black_elo"),
            game.get("pgn"),
        )
        for game in games
    ]

    cursor.executemany(
        """
        INSERT INTO games (
            username, site, site_game_id, played_at, time_class,
            color, result, eco, opening_name, opening_id, opening_ply_count,
            opponent, white_elo, black_elo, pgn
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site, site_game_id) DO NOTHING
        """,
        values_list,
    )

    inserted = cursor.rowcount
    skipped = len(games) - inserted
    return inserted, skipped


def get_openings_stats(
    conn: psycopg.Connection,
    username: str,
    color: str = "all",
    time_class: str = "all",
    site: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Aggregate opening statistics for a username with optional filters.
    Returns list of dicts with opening_key, opening_label, games, wins, draws, losses, score_pct.
    If site is None or "all", includes games from all sites.
    Limited to top `limit` openings by games played (default 10).
    """
    ensure_openings_table(conn)
    cursor = conn.cursor()
    opening_key_expr = _opening_key_expr_sql("g", "o")
    opening_label_expr = _opening_label_expr_sql("g", "o")

    canonical_username = username.strip().lower()

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
        WHERE g.username = %s
    """
    params: list = [canonical_username]

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
    username: str,
    site: str,
    imported: int,
    skipped: int,
    max_games: int,
    imported_at: str,
    last_synced_at: str | None = None,
) -> None:
    """Record import status for a username/site."""
    cursor = conn.cursor()
    canonical_username = username.strip().lower()
    cursor.execute(
        """
        INSERT INTO imports
        (username, site, imported, skipped, max_games, imported_at, last_synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site)
        DO UPDATE SET
            imported = EXCLUDED.imported,
            skipped = EXCLUDED.skipped,
            max_games = EXCLUDED.max_games,
            imported_at = EXCLUDED.imported_at,
            last_synced_at = EXCLUDED.last_synced_at
        """,
        (canonical_username, site, imported, skipped, max_games, imported_at, last_synced_at),
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
    username: str,
    site: str = "lichess",
) -> dict:
    """Get last import status + total games count for a username.

    When site="all", aggregates across all sites: sums games, uses the most
    recent imported_at / last_synced_at, and sums imported/skipped.
    """
    cursor = conn.cursor()
    canonical_username = username.strip().lower()

    if site and site != "all":
        cursor.execute(
            """
            SELECT imported, skipped, max_games, imported_at, last_synced_at
            FROM imports
            WHERE username = %s AND site = %s
            """,
            (canonical_username, site),
        )
        import_row = cursor.fetchone()

        cursor.execute(
            """
            SELECT COUNT(*) as total
            FROM games
            WHERE username = %s AND site = %s
            """,
            (canonical_username, site),
        )
        total_row = cursor.fetchone()
    else:
        cursor.execute(
            """
            SELECT SUM(imported) as imported, SUM(skipped) as skipped,
                   MAX(max_games) as max_games,
                   MAX(imported_at) as imported_at,
                   MAX(last_synced_at) as last_synced_at
            FROM imports
            WHERE username = %s
            """,
            (canonical_username,),
        )
        import_row = cursor.fetchone()
        if import_row and import_row["imported"] is None:
            import_row = None

        cursor.execute(
            """
            SELECT COUNT(*) as total
            FROM games
            WHERE username = %s
            """,
            (canonical_username,),
        )
        total_row = cursor.fetchone()

    last_synced_at = None
    if import_row and import_row["last_synced_at"]:
        raw = import_row["last_synced_at"]
        last_synced_at = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)

    return {
        "username": username,
        "imported_at": import_row["imported_at"] if import_row else None,
        "last_imported": import_row["imported"] if import_row else None,
        "last_skipped": import_row["skipped"] if import_row else None,
        "total_games": total_row["total"] if total_row else 0,
        "last_synced_at": last_synced_at,
    }


def get_import_history(
    conn: psycopg.Connection,
    limit: int = 10,
) -> list[dict]:
    """Get last N import records, ordered by most recent first."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT username, site, imported_at
        FROM imports
        ORDER BY imported_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_games_by_opening(
    conn: psycopg.Connection,
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
    Get games and summary stats for a username and opening.
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
        "g.username = %s",
        "g.site_game_id IS NOT NULL",
        "g.site_game_id != ''",
    ]
    base_params = [canonical_username]

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
            g.played_at DESC NULLS LAST,
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
    username: str,
    opening_key: str,
    color: str = "all",
    time_class: str = "all",
    site: str | None = None,
) -> list[dict]:
    """
    Aggregate variation statistics for a username and opening_key.
    Returns list of dicts with variation_key, variation_label, games, wins, draws, losses, score_pct.
    """
    if opening_key.startswith(RAW_OPENING_KEY_PREFIX):
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

    canonical_username = username.strip().lower()

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
        WHERE g.username = %s
          AND o.opening_key = %s
    """
    params: list = [canonical_username, opening_key]

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
        WHERE username = %s AND site_game_id = %s AND site = %s
        """,
        (username.strip().lower(), site_game_id, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def get_full_analysis(
    conn: psycopg.Connection,
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
        WHERE username = %s AND site_game_id = %s
        AND depth = %s AND multipv = %s AND site = %s
        """,
        (username.strip().lower(), site_game_id, depth, multipv, site),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def save_full_analysis(
    conn: psycopg.Connection,
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

    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO full_analysis 
        (username, site, site_game_id, depth, multipv, 
         moves_json, summary_json, meta_json, insights_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site, site_game_id, depth, multipv)
        DO UPDATE SET
            moves_json = EXCLUDED.moves_json,
            summary_json = EXCLUDED.summary_json,
            meta_json = EXCLUDED.meta_json,
            insights_json = COALESCE(EXCLUDED.insights_json, full_analysis.insights_json),
            created_at = EXCLUDED.created_at
        """,
        (
            username.strip().lower(),
            site,
            site_game_id,
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
        WHERE username = %s
          AND site_game_id = %s
          AND depth = %s
          AND multipv = %s
          AND site = %s
        """,
        (
            insights_json,
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
          AND username = %s
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
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> None:
    """Create a new analysis job record."""

    cursor = conn.cursor()
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO analysis_jobs (id, username, site, site_game_id, depth, multipv, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (job_id, username.strip().lower(), site, site_game_id, depth, multipv, created_at),
    )


def get_analysis_job(
    conn: psycopg.Connection,
    username: str,
    site_game_id: str,
    depth: int,
    multipv: int,
    site: str,
) -> dict | None:
    """Get an analysis job by game/params. Returns None if not found."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, site, site_game_id, username, depth, multipv, created_at
        FROM analysis_jobs
        WHERE username = %s AND site_game_id = %s AND depth = %s AND multipv = %s AND site = %s
        """,
        (username.strip().lower(), site_game_id, depth, multipv, site),
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
    username: str,
    site: str,
    status: str,
    stage: str,
    reason: str,
    feature_version: str,
    meta: dict | None = None,
) -> None:
    """Create an insights background job."""

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO insight_jobs
        (id, username, site, status, stage, reason, error, feature_version,
         created_at, started_at, finished_at, updated_at, meta_json)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, NULL, NULL, %s, %s)
        """,
        (
            job_id,
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
    username: str,
    site: str = "all",
) -> dict | None:
    """Get the latest active insights job for this username/site."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, stage, reason, error, feature_version, created_at,
               started_at, finished_at, updated_at, meta_json
        FROM insight_jobs
        WHERE username = %s
          AND site = %s
          AND status IN ('queued', 'running')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (username.strip().lower(), site),
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
        SELECT id, username, site, status, stage, reason, error, feature_version,
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
    username: str,
    site: str,
    site_game_id: str,
    feature_version: str,
    light: dict,
    deep: dict | None = None,
) -> None:
    """Insert or update per-game insight features."""

    now = datetime.now(timezone.utc).isoformat()
    analysis_tier = "deep" if deep else "light"
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO insight_game_features
        (username, site, site_game_id, feature_version, analysis_tier,
         light_json, deep_json, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site, site_game_id, feature_version)
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
        WHERE username = %s
    """
    params: list = [username.strip().lower()]

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


def get_featured_game_ids(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
    feature_version: str | None = None,
) -> set[tuple[str, str]]:
    """Return set of (site, site_game_id) that already have light features extracted.
    
    Used to skip re-extracting features for games that were already processed.
    """
    cursor = conn.cursor()
    query = """
        SELECT site, site_game_id
        FROM insight_game_features
        WHERE username = %s
          AND light_json IS NOT NULL
    """
    params: list = [username.strip().lower()]

    if site != "all":
        query += " AND site = %s"
        params.append(site)

    if feature_version is not None:
        query += " AND feature_version = %s"
        params.append(feature_version)

    cursor.execute(query, params)
    return {(row["site"], row["site_game_id"]) for row in cursor.fetchall()}


def get_games_for_insights(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
    limit: int = 250,
) -> list[dict]:
    """Fetch recent games with PGN and metadata for insights processing."""
    cursor = conn.cursor()
    query = """
        SELECT site, site_game_id, played_at, time_class, color, result, eco,
               opening_name, opponent, white_elo, black_elo, pgn
        FROM games
        WHERE username = %s
    """
    params: list = [username.strip().lower()]
    if site != "all":
        query += " AND site = %s"
        params.append(site)

    query += """
        ORDER BY
            played_at DESC NULLS LAST,
            id DESC
        LIMIT %s
    """
    params.append(limit)
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def upsert_player_insights(
    conn: psycopg.Connection,
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
    """Insert or update latest player insights snapshot."""

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO player_insights
        (username, site, status, feature_version, narrative_version,
         coverage_json, features_json, fact_map_json, narrative_json,
         source_job_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username, site)
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
    username: str,
    site: str = "all",
) -> dict | None:
    """Fetch latest player insights snapshot."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT status, feature_version, narrative_version,
               coverage_json, features_json, fact_map_json, narrative_json,
               source_job_id, created_at, updated_at
        FROM player_insights
        WHERE username = %s AND site = %s
        """,
        (username.strip().lower(), site),
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


# ---------------------------------------------------------------------------
# Quick-scan job & result helpers
# ---------------------------------------------------------------------------


def create_scan_job(
    conn: psycopg.Connection,
    job_id: str,
    username: str,
    site: str,
    total_games: int,
) -> None:
    """Insert a new quick-scan background job."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO scan_jobs
        (id, username, site, status, total_games, games_done,
         created_at, updated_at)
        VALUES (%s, %s, %s, 'queued', %s, 0, %s, %s)
        """,
        (job_id, username.strip().lower(), site, total_games, now, now),
    )


def update_scan_job(
    conn: psycopg.Connection,
    job_id: str,
    *,
    status: str | None = None,
    games_done: int | None = None,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Update mutable fields of a scan job."""
    fields = ["updated_at = %s"]
    params: list = [datetime.now(timezone.utc).isoformat()]
    if status is not None:
        fields.append("status = %s")
        params.append(status)
    if games_done is not None:
        fields.append("games_done = %s")
        params.append(games_done)
    if error is not None:
        fields.append("error = %s")
        params.append(error)
    if started_at is not None:
        fields.append("started_at = %s")
        params.append(started_at)
    if finished_at is not None:
        fields.append("finished_at = %s")
        params.append(finished_at)
    params.append(job_id)
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE scan_jobs SET {', '.join(fields)} WHERE id = %s",
        params,
    )


def get_active_scan_job(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> dict | None:
    """Get the latest active scan job for a username/site."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, total_games, games_done, error,
               created_at, started_at, finished_at, updated_at
        FROM scan_jobs
        WHERE username = %s AND site = %s
          AND status IN ('queued', 'running')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (username.strip().lower(), site),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_latest_scan_job(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> dict | None:
    """Get the most recent scan job (any status) for a username/site."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, total_games, games_done, error,
               created_at, started_at, finished_at, updated_at
        FROM scan_jobs
        WHERE username = %s AND site = %s
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (username.strip().lower(), site),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def upsert_game_quick_scan(
    conn: psycopg.Connection,
    username: str,
    site: str,
    site_game_id: str,
    problems_json: str,
    summary_json: str,
) -> None:
    """Save or update one game's quick-scan results."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO game_quick_scans
        (username, site, site_game_id, problems_json, summary_json)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (username, site, site_game_id)
        DO UPDATE SET
            problems_json = EXCLUDED.problems_json,
            summary_json = EXCLUDED.summary_json,
            scanned_at = now()
        """,
        (username.strip().lower(), site, site_game_id,
         problems_json, summary_json),
    )


def get_scanned_game_ids(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> set[tuple[str, str]]:
    """Return set of (site, site_game_id) already scanned for the username."""
    cursor = conn.cursor()
    query = """
        SELECT site, site_game_id
        FROM game_quick_scans
        WHERE username = %s
    """
    params: list = [username.strip().lower()]
    if site != "all":
        query += " AND site = %s"
        params.append(site)
    cursor.execute(query, params)
    return {(row["site"], row["site_game_id"]) for row in cursor.fetchall()}


def clear_quick_scan_data(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> None:
    """Delete all quick scan results and jobs for a username so they can be re-scanned."""
    canonical = username.strip().lower()
    cursor = conn.cursor()

    if site == "all":
        cursor.execute(
            "DELETE FROM game_quick_scans WHERE username = %s",
            (canonical,),
        )
        cursor.execute(
            "DELETE FROM scan_jobs WHERE username = %s",
            (canonical,),
        )
    else:
        cursor.execute(
            "DELETE FROM game_quick_scans WHERE username = %s AND site = %s",
            (canonical, site),
        )
        cursor.execute(
            "DELETE FROM scan_jobs WHERE username = %s AND site = %s",
            (canonical, site),
        )


def clear_insights_data(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> None:
    """Delete player insights, insight game features, and insight jobs for a fresh rebuild."""
    canonical = username.strip().lower()
    cursor = conn.cursor()

    if site == "all":
        cursor.execute(
            "DELETE FROM player_insights WHERE username = %s",
            (canonical,),
        )
        cursor.execute(
            "DELETE FROM insight_game_features WHERE username = %s",
            (canonical,),
        )
        cursor.execute(
            "DELETE FROM insight_jobs WHERE username = %s",
            (canonical,),
        )
    else:
        cursor.execute(
            "DELETE FROM player_insights WHERE username = %s AND site = %s",
            (canonical, site),
        )
        cursor.execute(
            "DELETE FROM insight_game_features WHERE username = %s AND site = %s",
            (canonical, site),
        )
        cursor.execute(
            "DELETE FROM insight_jobs WHERE username = %s AND site = %s",
            (canonical, site),
        )


def delete_all_user_site_data(
    conn: psycopg.Connection,
    username: str,
    site: str,
) -> dict[str, int]:
    """Delete ALL data for a username/site combination.

    This is called when a user removes a profile. Returns counts of deleted rows.
    """
    canonical = username.strip().lower()
    cursor = conn.cursor()
    counts: dict[str, int] = {}

    cursor.execute(
        "DELETE FROM game_quick_scans WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["game_quick_scans"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM scan_jobs WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["scan_jobs"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM insight_game_features WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["insight_game_features"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM insight_jobs WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["insight_jobs"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM player_insights WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["player_insights"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM full_analysis WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["full_analysis"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM analysis_jobs WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["analysis_jobs"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM ai_game_insights WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["ai_game_insights"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM ai_insights_requests WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["ai_insights_requests"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM imports WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["imports"] = cursor.rowcount

    cursor.execute(
        "DELETE FROM games WHERE username = %s AND site = %s",
        (canonical, site),
    )
    counts["games"] = cursor.rowcount

    return counts


def get_quick_scan_results(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
) -> list[dict]:
    """Fetch all quick-scan results for aggregation, joined with game info."""
    cursor = conn.cursor()
    canonical = username.strip().lower()
    query = """
        SELECT qs.site, qs.site_game_id, qs.problems_json, qs.summary_json, qs.scanned_at,
               g.time_class, g.opponent, g.played_at
        FROM game_quick_scans qs
        LEFT JOIN games g ON qs.username = g.username AND qs.site = g.site AND qs.site_game_id = g.site_game_id
        WHERE qs.username = %s
    """
    params: list = [canonical]
    if site != "all":
        query += " AND qs.site = %s"
        params.append(site)
    cursor.execute(query, params)
    rows = []
    for row in cursor.fetchall():
        data = dict(row)
        data["problems"] = json.loads(data.get("problems_json") or "{}") 
        data["summary"] = json.loads(data.get("summary_json") or "{}")
        rows.append(data)
    return rows


def get_quick_scan_problem_spotter(
    conn: psycopg.Connection,
    username: str,
    site: str = "all",
    recent_limit: int = 250,
) -> dict:
    """Build aggregated problem-spotter data from all quick-scan results.
    
    Only includes blunders and mistakes (not inaccuracies) in the problems list.
    """
    results = get_quick_scan_results(conn, username, site)

    by_theme: dict[str, int] = {}
    by_phase: dict[str, int] = {"opening": 0, "middlegame": 0, "endgame": 0}
    tactical_blunders = 0
    tactical_mistakes = 0
    all_problems: list[dict] = []

    for row in results:
        problems_data = row.get("problems") or {}
        problems_list = problems_data.get("problems", [])

        for problem in problems_list:
            classification = problem.get("classification")
            if classification not in ("blunder", "mistake"):
                continue

            tactic_type = problem.get("tactic_type")
            if not tactic_type:
                continue

            if classification == "blunder":
                tactical_blunders += 1
            else:
                tactical_mistakes += 1

            by_theme[tactic_type] = by_theme.get(tactic_type, 0) + 1

            phase = problem.get("phase", "middlegame")
            if phase in by_phase:
                by_phase[phase] += 1

            problem_data = {**problem}
            problem_data.pop("cp_loss", None)
            all_problems.append({
                **problem_data,
                "site": row["site"],
                "site_game_id": row["site_game_id"],
                "time_class": row.get("time_class"),
                "opponent": row.get("opponent"),
                "played_at": row.get("played_at"),
            })

    # Sort by severity: blunders first, then mistakes
    def sort_key(p):
        classification = p.get("classification", "")
        if classification == "blunder":
            return 0
        elif classification == "mistake":
            return 1
        else:
            return 2
    
    all_problems.sort(key=sort_key)

    theme_items = sorted(
        [{"theme": t, "count": c} for t, c in by_theme.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "total_problems": tactical_blunders + tactical_mistakes,
        "by_theme": theme_items,
        "by_phase": by_phase,
        "by_classification": {
            "blunders": tactical_blunders,
            "mistakes": tactical_mistakes,
        },
        "recent_problems": all_problems[:recent_limit],
    }


def get_problems_by_theme(
    conn: psycopg.Connection,
    username: str,
    theme: str,
    site: str = "all",
    time_control: str | None = None,
    phase: str | None = None,
    page: int = 0,
    page_size: int = 8,
) -> dict:
    """Return paginated problems matching a specific tactic theme with filters."""
    results = get_quick_scan_results(conn, username, site)
    matched: list[dict] = []
    theme_lower = theme.strip().lower()
    time_controls_set: set[str] = set()
    phases_set: set[str] = set()

    for row in results:
        problems_data = row.get("problems") or {}
        problems_list = problems_data.get("problems", [])

        for problem in problems_list:
            classification = problem.get("classification")
            if classification not in ("blunder", "mistake"):
                continue

            tactic_type = (problem.get("tactic_type") or "").lower()
            tactic_types = [t.lower() for t in (problem.get("tactic_types") or [])]
            if tactic_type != theme_lower and theme_lower not in tactic_types:
                continue

            problem_data = {**problem}
            problem_data.pop("cp_loss", None)
            item = {
                **problem_data,
                "site": row["site"],
                "site_game_id": row["site_game_id"],
                "time_class": row.get("time_class"),
                "opponent": row.get("opponent"),
                "played_at": row.get("played_at"),
            }
            matched.append(item)

            if item.get("time_class"):
                time_controls_set.add(item["time_class"])
            if item.get("phase"):
                phases_set.add(item["phase"])

    matched.sort(
        key=lambda p: (0 if p.get("classification") == "blunder" else 1),
    )

    total_count = len(matched)

    if time_control:
        matched = [p for p in matched if p.get("time_class") == time_control]
    if phase:
        matched = [p for p in matched if p.get("phase") == phase]

    filtered_count = len(matched)

    start = page * page_size
    end = start + page_size
    paginated = matched[start:end]

    return {
        "items": paginated,
        "total_count": total_count,
        "filtered_count": filtered_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (filtered_count + page_size - 1) // page_size if filtered_count > 0 else 0,
        "available_time_controls": sorted(time_controls_set),
        "available_phases": sorted(phases_set),
    }
