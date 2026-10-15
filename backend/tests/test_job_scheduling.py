"""Regression tests: manual refreshes must enqueue their Celery tasks.

Both schedulers lazily import their Celery task. A stale module path there
used to raise ModuleNotFoundError *after* the job row was created (and, for a
forced insights refresh, after existing data was cleared), leaving the job
stuck in "queued" and the endpoint returning 500.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from services import insights as insights_service
from services import quick_scan as quick_scan_service


class ScheduleInsightsRefreshTest(unittest.TestCase):
    def test_forced_refresh_enqueues_run_insights(self) -> None:
        with patch.object(insights_service, "get_connection", MagicMock()), patch.object(
            insights_service, "clear_insights_data"
        ), patch.object(insights_service, "clear_quick_scan_data"), patch.object(
            insights_service, "create_insight_job"
        ), patch("repository.tasks.run_insights") as run_insights:
            result = insights_service.schedule_insights_refresh(
                " SomePlayer ", site="lichess", force=True
            )

        self.assertTrue(result["scheduled"])
        run_insights.delay.assert_called_once_with(
            result["job"]["id"], "someplayer", "lichess", trigger_quick_scan=True
        )


class ScheduleQuickScanTest(unittest.TestCase):
    def test_refresh_enqueues_run_scan(self) -> None:
        games = [{"site": "lichess", "site_game_id": "abc"}]
        with patch.object(quick_scan_service, "get_connection", MagicMock()), patch.object(
            quick_scan_service, "get_active_scan_job", return_value=None
        ), patch.object(
            quick_scan_service, "get_games_for_insights", return_value=games
        ), patch.object(
            quick_scan_service, "get_scanned_game_ids", return_value=set()
        ), patch.object(quick_scan_service, "create_scan_job"), patch(
            "repository.tasks.run_scan"
        ) as run_scan:
            result = quick_scan_service.schedule_quick_scan("SomePlayer", site="lichess")

        self.assertTrue(result["scheduled"])
        run_scan.delay.assert_called_once_with(
            result["job"]["id"], "someplayer", "lichess"
        )


if __name__ == "__main__":
    unittest.main()
