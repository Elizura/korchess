"""Shared Redis client for the application.

All modules should import `redis_client` from here instead of creating
their own redis connections. This ensures we use a single connection pool
with a capped number of connections.
"""

import os

import redis as redis_lib

REDIS_URL = os.environ.get("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL environment variable is required")

redis_client = redis_lib.from_url(
    REDIS_URL,
    decode_responses=True,
    max_connections=10,
)
