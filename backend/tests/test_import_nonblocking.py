"""A slow upstream import must not stall other requests on the event loop."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import httpx
from fastapi import FastAPI

from auth import get_current_user
from dependencies import get_db
from routers import import_ as import_router
from schemas import ImportResponse

IMPORT_SECONDS = 1.0


def _slow_import(username, conn, max_games):
    time.sleep(IMPORT_SECONDS)  # stands in for the blocking Lichess download
    return ImportResponse(username=username, imported=1, skipped=0, is_sync=False)


class ImportDoesNotBlockTest(unittest.IsolatedAsyncioTestCase):
    async def test_other_requests_are_served_during_import(self) -> None:
        app = FastAPI()
        app.include_router(import_router.router, prefix="/api/v1/import")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        with patch.object(import_router, "import_lichess_games", _slow_import), \
                patch.object(import_router, "get_import_status", return_value={}), \
                patch.object(import_router, "track_server_event", AsyncMock()):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                started = time.monotonic()
                import_task = asyncio.create_task(
                    client.post("/api/v1/import/lichess", json={"username": "someone"})
                )
                await asyncio.sleep(0.1)  # let the import start
                ping = await client.get("/ping")
                ping_seconds = time.monotonic() - started
                import_still_running = not import_task.done()

                import_res = await import_task

        self.assertEqual(ping.status_code, 200)
        self.assertEqual(import_res.status_code, 200)
        self.assertTrue(import_still_running)
        self.assertLess(ping_seconds, IMPORT_SECONDS / 2)

    def test_import_size_is_capped(self) -> None:
        from pydantic import ValidationError
        from schemas import ImportRequest

        with self.assertRaises(ValidationError):
            ImportRequest(username="someone", max_games=501)


if __name__ == "__main__":
    unittest.main()
