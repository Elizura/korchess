"""Deep-analysis slots must be released exactly once, whatever goes wrong.

Only MAX_CONCURRENT_ANALYSES slots exist per process. A slot that is never
released makes every later deep analysis answer 429 until a restart.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import HTTPException

from routers import analysis

GAME = {"pgn": "1. e4 e5 *", "opening_ply_count": 2}
ANALYSIS_RESULT = {"moves": [], "summary": {}, "meta": {}}


def _request() -> MagicMock:
    request = MagicMock()
    request.headers = {}
    return request


async def _start_analysis() -> None:
    await analysis.run_full_analysis_endpoint(
        site="lichess",
        username="someplayer",
        game_id="abc",
        request=_request(),
        depth=18,
        multipv=1,
        force=True,
        conn=MagicMock(),
        current_user={"id": "user-1"},
    )


class AnalysisSlotTest(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            patch.object(analysis, "active_analysis_count", 0),
            patch.object(analysis, "track_server_event", AsyncMock()),
            patch.object(analysis, "get_analysis_job", return_value=None),
            patch.object(analysis, "get_connection", MagicMock()),
            patch.object(analysis, "save_full_analysis"),
            patch.object(analysis, "delete_analysis_job"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, coro_factory) -> None:
        asyncio.run(coro_factory())

    def test_missing_game_releases_slot(self) -> None:
        with patch.object(analysis, "get_game_by_id", return_value=None):
            with self.assertRaises(HTTPException):
                self._run(_start_analysis)
        self.assertEqual(analysis.active_analysis_count, 0)

    def test_unexpected_error_before_task_releases_slot(self) -> None:
        with patch.object(analysis, "get_game_by_id", return_value=GAME), \
                patch.object(analysis, "create_analysis_job", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self._run(_start_analysis)
        self.assertEqual(analysis.active_analysis_count, 0)

    def test_task_keeps_slot_until_it_finishes(self) -> None:
        async def scenario() -> None:
            await _start_analysis()
            self.assertEqual(analysis.active_analysis_count, 1)
            self.assertEqual(len(analysis._analysis_tasks), 1)
            await asyncio.gather(*analysis._analysis_tasks)

        with patch.object(analysis, "get_game_by_id", return_value=GAME), \
                patch.object(analysis, "create_analysis_job"), \
                patch.object(analysis, "run_full_analysis", return_value=ANALYSIS_RESULT):
            self._run(scenario)
        self.assertEqual(analysis.active_analysis_count, 0)
        self.assertEqual(len(analysis._analysis_tasks), 0)

    def test_failed_task_releases_slot_once(self) -> None:
        async def scenario() -> None:
            await _start_analysis()
            await asyncio.gather(*analysis._analysis_tasks)

        with patch.object(analysis, "get_game_by_id", return_value=GAME), \
                patch.object(analysis, "create_analysis_job"), \
                patch.object(analysis, "run_full_analysis", side_effect=RuntimeError("engine died")):
            self._run(scenario)
        self.assertEqual(analysis.active_analysis_count, 0)
        analysis.delete_analysis_job.assert_called_once()


if __name__ == "__main__":
    unittest.main()
