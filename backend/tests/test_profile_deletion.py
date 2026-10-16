"""Deleting a chess profile only removes the caller's rows, and shared account
data only when no other user still has a profile for that account."""

from __future__ import annotations

import os
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from dependencies import get_db
from repository import db
from routers import profiles as profiles_router

CALLER = "user-1"
SHARED_TABLES = {
    "game_quick_scans", "scan_jobs", "insight_game_features", "insight_jobs",
    "player_insights", "full_analysis", "analysis_jobs", "imports", "games",
}


class _RecordingCursor:
    """Records SQL; answers the "does anyone else have this profile" check."""

    def __init__(self, other_profile_exists: bool) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.rowcount = 0
        self._other_profile_exists = other_profile_exists

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.statements.append((" ".join(sql.split()), tuple(params)))
        self.rowcount = 1

    def fetchone(self):
        sql, _ = self.statements[-1]
        if sql.startswith("SELECT 1 FROM chess_profiles"):
            return {"?column?": 1} if self._other_profile_exists else None
        return None


class _RecordingConn:
    def __init__(self, other_profile_exists: bool) -> None:
        self.cur = _RecordingCursor(other_profile_exists)

    def cursor(self) -> _RecordingCursor:
        return self.cur


def _deleted_tables(conn: _RecordingConn) -> dict[str, tuple]:
    deleted = {}
    for sql, params in conn.cur.statements:
        match = re.match(r"DELETE FROM (\w+)", sql)
        if match:
            deleted[match.group(1)] = (sql, params)
    return deleted


class DeleteChessProfileDataTest(unittest.TestCase):
    def test_other_users_keep_shared_data_and_their_own_rows(self) -> None:
        conn = _RecordingConn(other_profile_exists=True)
        counts = db.delete_chess_profile_data(conn, CALLER, " SomePlayer ", "lichess")

        deleted = _deleted_tables(conn)
        self.assertEqual(set(deleted), {"chess_profiles", "ai_game_insights"})
        for sql, params in deleted.values():
            self.assertIn("user_id = %s", sql)
            self.assertEqual(params, (CALLER, "someplayer", "lichess"))
        self.assertNotIn("games", counts)

    def test_last_profile_removes_shared_data_but_not_other_users_rows(self) -> None:
        conn = _RecordingConn(other_profile_exists=False)
        counts = db.delete_chess_profile_data(conn, CALLER, "SomePlayer", "chesscom")

        deleted = _deleted_tables(conn)
        self.assertEqual(set(deleted), SHARED_TABLES | {"chess_profiles", "ai_game_insights"})
        for table in SHARED_TABLES:
            self.assertEqual(deleted[table][1], ("someplayer", "chesscom"))
        # Per-user tables are only ever deleted for the caller.
        for table in ("chess_profiles", "ai_game_insights"):
            self.assertIn("user_id = %s", deleted[table][0])
            self.assertEqual(deleted[table][1][0], CALLER)
        self.assertIn("games", counts)

    def test_ai_quota_log_is_never_deleted(self) -> None:
        for other in (True, False):
            conn = _RecordingConn(other_profile_exists=other)
            db.delete_chess_profile_data(conn, CALLER, "someplayer", "lichess")
            self.assertFalse(
                any("ai_insights_requests" in sql for sql, _ in conn.cur.statements)
            )


class RemoveProfileEndpointTest(unittest.TestCase):
    def test_delete_is_scoped_to_the_caller_and_keeps_response_shape(self) -> None:
        app = FastAPI()
        app.include_router(profiles_router.router, prefix="/api/v1/profiles")
        conn = MagicMock()
        app.dependency_overrides[get_db] = lambda: conn
        app.dependency_overrides[get_current_user] = lambda: {"id": CALLER}
        client = TestClient(app)

        counts = {"chess_profiles": 1, "ai_game_insights": 0}
        with patch.object(profiles_router, "get_chess_profile", return_value={"chess_username": "x"}), \
                patch.object(profiles_router, "delete_chess_profile_data", return_value=counts) as delete, \
                patch.object(profiles_router, "track_server_event", AsyncMock()):
            res = client.delete("/api/v1/profiles/lichess/SomePlayer")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "deleted", "deleted": counts})
        delete.assert_called_once_with(conn, CALLER, "SomePlayer", "lichess")


if __name__ == "__main__":
    unittest.main()
