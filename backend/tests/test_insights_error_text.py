"""Insights responses never expose stored exception text."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from routers import insights as insights_router

RAW_ERROR = 'psycopg.OperationalError: connection to server at "10.0.0.5", port 5432 failed'


def _state(error: str | None) -> dict:
    return {
        "username": "someplayer",
        "site": "all",
        "lifecycle_status": "queued",
        "feature_version": "1",
        "narrative_version": "1",
        "snapshot": None,
        "active_job": {"id": "job-1", "status": "running", "stage": "light", "error": error},
    }


class ActiveJobErrorTest(unittest.TestCase):
    def _build(self, error: str | None):
        with patch.object(insights_router, "get_latest_scan_job", return_value=None), \
                patch.object(insights_router, "get_quick_scan_problem_spotter", return_value=None), \
                patch.object(insights_router, "_get_import_progress", return_value=None):
            return insights_router._build_profile_response(_state(error), MagicMock())

    def test_stored_exception_text_is_replaced_with_generic_message(self) -> None:
        response = self._build(RAW_ERROR)
        self.assertEqual(response.active_job.error, "Insights generation failed.")
        self.assertNotIn("10.0.0.5", response.model_dump_json())

    def test_no_error_stays_empty(self) -> None:
        for error in (None, ""):
            self.assertIsNone(self._build(error).active_job.error)


if __name__ == "__main__":
    unittest.main()
