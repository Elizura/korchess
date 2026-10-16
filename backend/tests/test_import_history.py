"""/api/v1/import/history only shows imports of the caller's own chess profiles."""

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
from routers import import_ as import_router


class ImportHistoryTest(unittest.TestCase):
    def test_endpoint_asks_for_the_callers_history(self) -> None:
        app = FastAPI()
        app.include_router(import_router.router, prefix="/api/v1/import")
        conn = MagicMock()
        app.dependency_overrides[get_db] = lambda: conn
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}
        rows = [{"username": "someplayer", "site": "lichess", "imported_at": "2024-03-05T12:00:00+00:00"}]

        with patch.object(import_router, "get_import_history", return_value=rows) as history:
            res = TestClient(app).get("/api/v1/import/history")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"history": rows})
        history.assert_called_once_with(conn, "user-1", limit=10)

    def test_query_is_scoped_to_the_users_profiles(self) -> None:
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = []
        db.get_import_history(conn, "user-1", limit=10)

        sql, params = cursor.execute.call_args.args
        sql = " ".join(sql.split())
        self.assertIn("JOIN chess_profiles p ON p.chess_username = i.username AND p.site = i.site", sql)
        self.assertIn("WHERE p.user_id = %s", sql)
        self.assertEqual(params, ("user-1", 10))


if __name__ == "__main__":
    unittest.main()
