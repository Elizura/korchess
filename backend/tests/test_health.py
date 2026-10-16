"""/health must fail when the database or Redis is down, so the container is marked unhealthy."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import health


def _get_health():
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app).get("/health")


def _ok() -> None:
    return None


def _down() -> None:
    raise ConnectionError("down")


class HealthCheckTest(unittest.TestCase):
    def test_healthy_when_both_answer(self) -> None:
        with patch.object(health, "_check_database", _ok), patch.object(health, "_check_redis", _ok):
            res = _get_health()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "healthy")

    def test_database_down_is_503(self) -> None:
        with patch.object(health, "_check_database", _down), patch.object(health, "_check_redis", _ok):
            res = _get_health()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["database"], "error")
        self.assertEqual(res.json()["redis"], "ok")

    def test_redis_down_is_503(self) -> None:
        with patch.object(health, "_check_database", _ok), patch.object(health, "_check_redis", _down):
            res = _get_health()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["redis"], "error")

    def test_hung_dependency_times_out(self) -> None:
        async def timed_check():
            started = time.monotonic()
            res = await health.health_check()
            return res, time.monotonic() - started

        with patch.object(health, "CHECK_TIMEOUT_S", 0.1), \
                patch.object(health, "_check_database", lambda: time.sleep(1)), \
                patch.object(health, "_check_redis", _ok):
            res, elapsed = asyncio.run(timed_check())
        self.assertEqual(res.status_code, 503)
        self.assertLess(elapsed, 0.9)

    def test_unreachable_services_report_unhealthy(self) -> None:
        # The real checks: no DB pool, and the test REDIS_URL points at a closed port
        with patch.object(health, "get_connection", side_effect=RuntimeError("pool not initialized")):
            res = _get_health()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json(), {"status": "unhealthy", "database": "error", "redis": "error"})


if __name__ == "__main__":
    unittest.main()
