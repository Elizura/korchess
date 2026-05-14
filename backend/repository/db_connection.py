"""Database connection and configuration for Korchess.

This module provides the core database connection functionality and
constants that are used throughout the application.

Connection pooling is used to reuse connections across requests,
avoiding the overhead of creating new TCP connections on every call.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    import psycopg


# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Pool configuration (per-process)
POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN", "2"))
POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX", "10"))

# User ID prefixes
PUBLIC_USER_ID_PREFIX = "public:"

# Opening key configuration
RAW_OPENING_KEY_PREFIX = "raw__"

# Global connection pool (initialized per-process)
_pool: ConnectionPool | None = None


def init_pool() -> None:
    """Initialize the connection pool.
    
    Must be called once per process (FastAPI startup, Celery worker init).
    """
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    if _pool is not None:
        return
    _pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        kwargs={
            "row_factory": dict_row,
            "autocommit": False,
            "prepare_threshold": None,
        },
        timeout=10.0,
    )


def close_pool() -> None:
    """Close the connection pool and release all connections.
    
    Should be called on process shutdown.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """Get a database connection from the pool.
    
    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
            conn.commit()
    
    The connection is automatically returned to the pool on exit.
    If no commit was issued, the transaction is rolled back.
    """
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")
    with _pool.connection() as conn:
        yield conn


def public_user_id_for_username(username: str) -> str:
    """Build canonical public owner ID for shared username-scoped data.
    
    Public users are synthetic user records that own imported game data
    for a given username, allowing data to be shared across authenticated users.
    """
    canonical_username = username.strip().lower()
    if not canonical_username:
        raise ValueError("Username is required.")
    return f"{PUBLIC_USER_ID_PREFIX}{canonical_username}"
