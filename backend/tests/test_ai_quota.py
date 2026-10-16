"""AI insights quota: the slot is reserved before Gemini is called, then settled."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from repository import db
from routers import analysis

DEEP_ANALYSIS = {"moves_json": "[]", "summary_json": "{}", "meta_json": "{}"}
NARRATED = {"narration_meta": {"source": "gemini"}}


def _request_insights(email: str = "player@example.com"):
    request = MagicMock()
    request.headers = {}
    return asyncio.run(
        analysis.request_ai_insights_endpoint(
            site="lichess",
            username="someplayer",
            game_id="abc",
            request=request,
            depth=18,
            multipv=1,
            force=False,
            conn=MagicMock(),
            current_user={"id": "user-1", "email": email},
        )
    )


class AIQuotaReservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ensure_narration = MagicMock(return_value=NARRATED)
        self.reserve = MagicMock(return_value=7)
        self.finish = MagicMock()
        self.log = MagicMock()
        patches = [
            patch.object(analysis, "AI_INSIGHTS_DAILY_LIMIT", 2),
            patch.object(analysis, "AI_INSIGHTS_UNLIMITED_EMAILS", {"unlimited@example.com"}),
            patch.object(analysis, "track_server_event", AsyncMock()),
            patch.object(analysis, "get_ai_game_insights", return_value=None),
            patch.object(analysis, "get_full_analysis", return_value=DEEP_ANALYSIS),
            patch.object(analysis, "get_game_by_id", return_value={"pgn": "1. e4 *"}),
            patch.object(analysis, "compute_single_game_insights", return_value={}),
            patch.object(analysis, "ensure_narration", self.ensure_narration),
            patch.object(analysis, "save_ai_game_insights"),
            patch.object(analysis, "reserve_ai_insights_request", self.reserve),
            patch.object(analysis, "finish_ai_insights_request", self.finish),
            patch.object(analysis, "log_ai_insights_request", self.log),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_quota_used_up_never_calls_gemini(self) -> None:
        self.reserve.return_value = None
        res = _request_insights()
        self.assertEqual(res.status, "quota_exceeded")
        self.ensure_narration.assert_not_called()
        self.finish.assert_not_called()

    def test_success_settles_reservation(self) -> None:
        res = _request_insights()
        self.assertEqual(res.status, "ready")
        self.assertEqual(self.reserve.call_args.kwargs["daily_limit"], 2)
        self.finish.assert_called_once_with(ANY, 7, "gemini_success")
        self.log.assert_not_called()

    def test_gemini_error_releases_reservation(self) -> None:
        self.ensure_narration.side_effect = RuntimeError("gemini down")
        res = _request_insights()
        self.assertEqual(res.status, "generation_failed")
        self.finish.assert_called_once_with(ANY, 7, "failed")

    def test_fallback_narration_releases_reservation(self) -> None:
        self.ensure_narration.return_value = {"narration_meta": {"source": "fallback"}}
        res = _request_insights()
        self.assertEqual(res.status, "generation_failed")
        self.finish.assert_called_once_with(ANY, 7, "failed")

    def test_unlimited_user_skips_reservation(self) -> None:
        res = _request_insights(email="Unlimited@example.com")
        self.assertEqual(res.status, "ready")
        self.reserve.assert_not_called()
        self.log.assert_called_once()
        self.assertEqual(self.log.call_args.kwargs["status"], "gemini_success")


class ReserveAIInsightsRequestTest(unittest.TestCase):
    def _reserve(self, used_today: int):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.side_effect = [{"total": used_today}, {"id": 42}]
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = db.reserve_ai_insights_request(
            conn, "user-1", "SomePlayer", "abc", 18, 1, "lichess",
            daily_limit=2, day_start_utc=start, day_end_utc=start + timedelta(days=1),
        )
        statements = [" ".join(c.args[0].split()) for c in cursor.execute.call_args_list]
        return result, statements

    def test_takes_user_lock_before_counting(self) -> None:
        _, statements = self._reserve(used_today=0)
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("COUNT(*)", statements[1])
        self.assertIn("status = 'pending'", statements[1])

    def test_inserts_pending_row_under_limit(self) -> None:
        result, statements = self._reserve(used_today=1)
        self.assertEqual(result, 42)
        self.assertTrue(statements[2].startswith("INSERT INTO ai_insights_requests"))
        self.assertIn("'pending'", statements[2])

    def test_refuses_at_limit(self) -> None:
        result, statements = self._reserve(used_today=2)
        self.assertIsNone(result)
        self.assertEqual(len(statements), 2)


if __name__ == "__main__":
    unittest.main()
