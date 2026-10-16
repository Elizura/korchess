"""Health check endpoint."""

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from repository.db_connection import get_connection
from repository.redis_client import redis_client

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 3.0


def _check_database() -> None:
    with get_connection() as conn:
        conn.execute("SELECT 1")


def _check_redis() -> None:
    redis_client.ping()


async def _passes(name: str, check: Callable[[], None]) -> bool:
    # Run in a thread with a deadline so a hung dependency can't hang the check
    try:
        await asyncio.wait_for(asyncio.to_thread(check), timeout=CHECK_TIMEOUT_S)
        return True
    except Exception:
        logger.warning("Health check: %s is unavailable", name, exc_info=True)
        return False


@router.get("/health")
async def health_check():
    """Healthy only when the database and Redis both answer; 503 otherwise."""
    database_ok, redis_ok = await asyncio.gather(
        _passes("database", _check_database),
        _passes("redis", _check_redis),
    )
    healthy = database_ok and redis_ok
    return JSONResponse(
        {
            "status": "healthy" if healthy else "unhealthy",
            "database": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
        status_code=200 if healthy else 503,
    )
