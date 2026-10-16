"""Regression tests for DB connection guardrails."""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import BackgroundTasks, HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from fake_redis import FakeRedis
from services.analytics import AnalyticsValidationError
from auth import get_registered_user
from dependencies import get_db
from repository import tasks
from routers import analytics as analytics_router
from schemas import AnalyticsEventItem, AnalyticsEventsIngestRequest


class AnalyticsIngestConnectionTest(unittest.TestCase):
    def _build_request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/analytics/events",
                "headers": [(b"host", b"korchess.com")],
            }
        )

    def test_ingest_events_route_does_not_depend_on_get_db(self) -> None:
        route = next(
            route
            for route in analytics_router.router.routes
            if isinstance(route, APIRoute)
            and route.path == "/analytics/events"
            and "POST" in route.methods
        )
        dependency_calls = [dep.call for dep in route.dependant.dependencies]
        self.assertNotIn(get_db, dependency_calls)

    def test_ingest_events_accepts_valid_payload_without_db_connection(self) -> None:
        body = AnalyticsEventsIngestRequest(
            events=[
                AnalyticsEventItem(
                    event_name="page.view",
                    anonymous_id="anon-1",
                    session_id="sess-1",
                    path="/",
                )
            ]
        )
        request = self._build_request()
        background_tasks = BackgroundTasks()

        with patch(
            "routers.analytics.ingest_client_events",
            new=AsyncMock(return_value=[{"event_name": "page.view"}]),
        ) as ingest_mock:
            response = asyncio.run(
                analytics_router.ingest_events_endpoint(
                    body=body,
                    background_tasks=background_tasks,
                    request=request,
                    current_user=None,
                )
            )

        self.assertEqual(response.accepted, 1)
        ingest_mock.assert_awaited_once()
        self.assertEqual(ingest_mock.await_args.kwargs["user_id"], None)
        self.assertNotIn("conn", ingest_mock.await_args.kwargs)

    def test_ingest_events_validation_error_returns_http_400(self) -> None:
        body = AnalyticsEventsIngestRequest(
            events=[
                AnalyticsEventItem(
                    event_name="page.view",
                    anonymous_id="anon-1",
                    session_id="sess-1",
                    path="/",
                )
            ]
        )
        request = self._build_request()

        with patch(
            "routers.analytics.ingest_client_events",
            new=AsyncMock(side_effect=AnalyticsValidationError("invalid payload")),
        ):
            with self.assertRaises(HTTPException) as err:
                asyncio.run(
                    analytics_router.ingest_events_endpoint(
                        body=body,
                        background_tasks=BackgroundTasks(),
                        request=request,
                        current_user=None,
                    )
                )

        self.assertEqual(err.exception.status_code, 400)
        self.assertEqual(err.exception.detail, "invalid payload")


class RegisteredUserConnectionTest(unittest.TestCase):
    def test_get_registered_user_uses_injected_connection_only(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")
        conn = object()
        unexpected = AssertionError("unexpected get_connection() call")

        with patch("auth.verify_access_token", return_value={"id": "user-1", "email": "a@b.com"}), patch(
            "repository.db.get_user_by_id", return_value={"id": "user-1"}
        ) as get_user_by_id_mock, patch(
            "repository.db.get_connection", side_effect=unexpected
        ) as get_connection_mock, patch(
            "repository.db_connection.get_connection", side_effect=unexpected
        ) as pool_connection_mock:
            user = get_registered_user(credentials=credentials, conn=conn)

        self.assertEqual(user["id"], "user-1")
        get_user_by_id_mock.assert_called_once_with(conn, "user-1")
        get_connection_mock.assert_not_called()
        pool_connection_mock.assert_not_called()

    def test_get_registered_user_preserves_unregistered_user_403(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")
        conn = object()

        with patch("auth.verify_access_token", return_value={"id": "user-2", "email": "a@b.com"}), patch(
            "repository.db.get_user_by_id", return_value=None
        ):
            with self.assertRaises(HTTPException) as err:
                get_registered_user(credentials=credentials, conn=conn)

        self.assertEqual(err.exception.status_code, 403)
        self.assertIn("User not registered", err.exception.detail)


class PostImportInsightsTest(unittest.TestCase):
    """Post-import insights work is keyed by chess username only.

    Insights are shared per chess username - not owned by individual users.
    This used to be scheduled by routers.import_._schedule_insights; the
    process_game task that completes an import now runs the aggregation.
    """

    def _complete_import(self, scan_error=None, aggregation_error=None):
        redis = FakeRedis()
        redis.set("import:testuser:lichess:total", 1)
        redis.set("import:testuser:lichess:done", 0)
        game = {"site": "lichess", "site_game_id": "g1", "username": "TestUser", "pgn": ""}

        with patch.object(tasks, "_redis", redis), \
                patch.object(tasks, "get_connection", MagicMock()), \
                patch.object(tasks, "bulk_upsert_games", return_value=(1, 0)), \
                patch.object(tasks, "extract_light_game_features", return_value={}), \
                patch.object(tasks, "upsert_insight_game_feature"), \
                patch.object(tasks, "has_game_quick_scan", return_value=False), \
                patch.object(tasks, "upsert_game_quick_scan"), \
                patch.object(tasks, "run_quick_scan_single", side_effect=scan_error,
                             return_value={"summary": {}}) as scan_mock, \
                patch.object(tasks, "_run_aggregation", side_effect=aggregation_error) as aggregate_mock:
            result = tasks.process_game(game, "TestUser", "lichess")

        return result, scan_mock, aggregate_mock, redis

    def test_post_import_aggregation_uses_username_only(self) -> None:
        """The completing game aggregates with just the canonical username (no user_id)."""
        result, _, aggregate_mock, redis = self._complete_import()

        aggregate_mock.assert_called_once_with("testuser", "lichess")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(redis.get("import:testuser:lichess:status"), "complete")

    def test_post_import_handles_aggregation_exception(self) -> None:
        """process_game finishes the import even if the aggregation fails."""
        # Should not raise
        result, _, aggregate_mock, redis = self._complete_import(aggregation_error=Exception("test error"))

        aggregate_mock.assert_called_once()
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(redis.get("import:testuser:lichess:status"), "complete")

    def test_post_import_handles_scan_exception(self) -> None:
        """A failed quick scan still counts the game, so aggregation still runs."""
        # Should not raise
        _, scan_mock, aggregate_mock, _ = self._complete_import(scan_error=Exception("scan error"))

        scan_mock.assert_called_once()
        aggregate_mock.assert_called_once_with("testuser", "lichess")


if __name__ == "__main__":
    unittest.main()
