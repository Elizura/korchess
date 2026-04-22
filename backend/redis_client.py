"""Shared Redis client for the application.

All modules should import `redis_client` from here instead of creating
their own redis connections. This ensures we use a single connection pool
with a capped number of connections.
"""

import os

import redis as redis_lib

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis_lib.from_url(
    REDIS_URL,
    decode_responses=True,
    max_connections=10,
)
