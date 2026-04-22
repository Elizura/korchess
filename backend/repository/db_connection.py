"""Database connection and configuration for Korchess.

This module provides the core database connection functionality and
constants that are used throughout the application.
"""

import os

import psycopg
from psycopg.rows import dict_row


# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# User ID prefixes
PUBLIC_USER_ID_PREFIX = "public:"

# Lesson consent configuration
LESSON_CONSENT_CHANNEL_EMAIL = "email_lessons"
LESSON_CONSENT_SOURCE_GAME_AI_SUMMARY = "game_ai_summary"
LESSON_CONSENT_DECISIONS = frozenset({"consented", "declined"})

# Opening key configuration
RAW_OPENING_KEY_PREFIX = "raw__"


def get_connection() -> psycopg.Connection:
    """Get a database connection with standard settings.
    
    Returns a psycopg connection with:
    - autocommit=False (caller must commit)
    - dict_row factory for dict-style row access
    - 5 second connect timeout
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
        row_factory=dict_row,
        connect_timeout=5,
        prepare_threshold=None,
    )


def public_user_id_for_username(username: str) -> str:
    """Build canonical public owner ID for shared username-scoped data.
    
    Public users are synthetic user records that own imported game data
    for a given username, allowing data to be shared across authenticated users.
    """
    canonical_username = username.strip().lower()
    if not canonical_username:
        raise ValueError("Username is required.")
    return f"{PUBLIC_USER_ID_PREFIX}{canonical_username}"
