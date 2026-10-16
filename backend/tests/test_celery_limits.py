"""Celery tasks have time limits, and hitting the soft limit fails the job cleanly."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from celery.exceptions import SoftTimeLimitExceeded

from repository import tasks
from repository.celery_app import app


class TimeLimitConfigTest(unittest.TestCase):
    def test_app_has_default_limits(self) -> None:
        self.assertLess(app.conf.task_soft_time_limit, app.conf.task_time_limit)

    def test_each_task_has_soft_limit_below_hard_limit(self) -> None:
        for task in (tasks.process_game, tasks.run_insights, tasks.run_scan):
            self.assertIsNotNone(task.soft_time_limit, task.name)
            self.assertLess(task.soft_time_limit, task.time_limit, task.name)

    def test_per_game_task_is_shorter_than_batch_tasks(self) -> None:
        self.assertLess(tasks.process_game.time_limit, tasks.run_insights.soft_time_limit)
        self.assertLess(tasks.process_game.time_limit, tasks.run_scan.soft_time_limit)


class RunScanSoftLimitTest(unittest.TestCase):
    def test_soft_limit_stops_the_batch_and_marks_the_job_failed(self) -> None:
        games = [{"site": "lichess", "site_game_id": g} for g in ("a", "b", "c")]
        with patch.object(tasks, "get_connection", MagicMock()), \
                patch.object(tasks, "get_games_for_insights", return_value=games), \
                patch.object(tasks, "get_scanned_game_ids", return_value=set()), \
                patch.object(tasks, "update_scan_job") as update_job, \
                patch.object(tasks, "_merge_scan_into_insights"), \
                patch.object(tasks, "run_quick_scan_single", side_effect=SoftTimeLimitExceeded()) as scan:
            result = tasks.run_scan("job-1", "someone", "all")

        scan.assert_called_once()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(update_job.call_args.kwargs["status"], "failed")


if __name__ == "__main__":
    unittest.main()
