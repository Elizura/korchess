"""Job lookups must expire jobs whose worker died, or the job blocks reruns forever."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from repository import db


def _conn() -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = None
    return conn, cursor


class StaleJobLookupTest(unittest.TestCase):
    def _assert_expires_before_select(self, cursor: MagicMock, statement: str, max_age) -> None:
        self.assertEqual(cursor.execute.call_count, 2)
        expire_sql, expire_params = cursor.execute.call_args_list[0].args
        select_sql, _ = cursor.execute.call_args_list[1].args
        self.assertIn(statement, expire_sql)
        self.assertIn("SELECT", select_sql)
        cutoff = expire_params[-1]
        age = datetime.now(timezone.utc) - cutoff
        self.assertAlmostEqual(age.total_seconds(), max_age.total_seconds(), delta=5)

    def test_scan_job_lookup_fails_stale_jobs_first(self) -> None:
        conn, cursor = _conn()
        self.assertIsNone(db.get_active_scan_job(conn, " SomePlayer ", "lichess"))
        self._assert_expires_before_select(cursor, "UPDATE scan_jobs", db.BACKGROUND_JOB_STALE_AFTER)
        self.assertIn("someplayer", cursor.execute.call_args_list[0].args[1])

    def test_insight_job_lookup_fails_stale_jobs_first(self) -> None:
        conn, cursor = _conn()
        self.assertIsNone(db.get_active_insight_job(conn, "SomePlayer", "lichess"))
        self._assert_expires_before_select(cursor, "UPDATE insight_jobs", db.BACKGROUND_JOB_STALE_AFTER)

    def test_analysis_job_lookup_deletes_stale_jobs_first(self) -> None:
        conn, cursor = _conn()
        self.assertIsNone(db.get_analysis_job(conn, "SomePlayer", "abc", 18, 1, "lichess"))
        self._assert_expires_before_select(cursor, "DELETE FROM analysis_jobs", db.ANALYSIS_JOB_STALE_AFTER)


if __name__ == "__main__":
    unittest.main()
