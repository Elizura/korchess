"""Shared FastAPI dependencies."""

from collections.abc import Generator

import psycopg
from fastapi import HTTPException

from repository.db import get_connection

VALID_SITES = {"lichess", "chesscom", "all"}


def get_db() -> Generator[psycopg.Connection, None, None]:
    """Provide a database connection per request. Closes on exit."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def validate_site(site: str) -> str:
    """Validate and normalize site parameter."""
    site = site.lower()
    if site not in VALID_SITES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid site. Must be one of: {', '.join(VALID_SITES)}"
        )
    return site
