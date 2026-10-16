"""Only users who saved a chess account may rebuild its shared insights/scans."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from dependencies import get_db
from repository import db
from routers import insights as insights_router
from routers import quick_scan as quick_scan_router
from schemas import InsightsProfileResponse

CALLER = "user-1"


def _client(conn: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(insights_router.router, prefix="/api/v1")
    app.include_router(quick_scan_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_current_user] = lambda: {"id": CALLER}
    return TestClient(app)


class RefreshOwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = MagicMock()
        self.client = _client(self.conn)
        response = InsightsProfileResponse(
            username="someplayer", site="all", lifecycle_status="queued",
            feature_version="1", narrative_version="1",
        )
        patches = [
            patch.object(insights_router, "get_insights_state", return_value={}),
            patch.object(insights_router, "_build_profile_response", return_value=response),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _post_insights(self, owns: bool):
        with patch.object(insights_router, "user_has_chess_profile", return_value=owns) as owns_check, \
                patch.object(insights_router, "schedule_insights_refresh") as schedule:
            res = self.client.post(
                "/api/v1/insights/profile",
                json={"username": " SomePlayer ", "site": "all", "force": True},
            )
        return res, schedule, owns_check

    def test_force_refresh_of_someone_elses_account_is_forbidden(self) -> None:
        res, schedule, _ = self._post_insights(owns=False)
        self.assertEqual(res.status_code, 403)
        schedule.assert_not_called()

    def test_refresh_of_own_account_is_scheduled(self) -> None:
        res, schedule, owns_check = self._post_insights(owns=True)
        self.assertEqual(res.status_code, 200)
        owns_check.assert_called_once_with(self.conn, CALLER, "someplayer", "all")
        schedule.assert_called_once_with(
            username="someplayer", site="all", reason="manual_refresh", force=True,
        )

    def test_quick_scan_refresh_requires_own_account(self) -> None:
        for owns, expected in ((False, 403), (True, 200)):
            with patch.object(quick_scan_router, "user_has_chess_profile", return_value=owns), \
                    patch.object(quick_scan_router, "schedule_quick_scan", return_value={"scheduled": True}) as schedule:
                res = self.client.post(
                    "/api/v1/quick-scan/refresh", json={"username": "SomePlayer", "site": "lichess"},
                )
            self.assertEqual(res.status_code, expected)
            self.assertEqual(schedule.called, owns)


class UserHasChessProfileQueryTest(unittest.TestCase):
    def _query(self, site: str) -> tuple[str, list]:
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.return_value = None
        self.assertFalse(db.user_has_chess_profile(conn, CALLER, " SomePlayer ", site))
        sql, params = cursor.execute.call_args.args
        return " ".join(sql.split()), params

    def test_all_matches_any_site(self) -> None:
        sql, params = self._query("all")
        self.assertNotIn("site", sql)
        self.assertEqual(params, [CALLER, "someplayer"])

    def test_specific_site_is_filtered(self) -> None:
        sql, params = self._query("chesscom")
        self.assertIn("site = %s", sql)
        self.assertEqual(params, [CALLER, "someplayer", "chesscom"])


if __name__ == "__main__":
    unittest.main()
