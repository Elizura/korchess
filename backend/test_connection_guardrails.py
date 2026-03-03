"""Regression tests for DB connection guardrails."""

from __future__ import annotations

import asyncio
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

sys.path.append(str(pathlib.Path(__file__).resolve().parent))

try:
    import google.oauth2.id_token  # type: ignore  # noqa: F401
    import google.auth.transport.requests  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    google_mod = types.ModuleType("google")
    oauth2_mod = types.ModuleType("google.oauth2")
    id_token_mod = types.ModuleType("google.oauth2.id_token")

    def _verify_oauth2_token(*_args, **_kwargs):  # pragma: no cover - test stub
        return {}

    id_token_mod.verify_oauth2_token = _verify_oauth2_token  # type: ignore[attr-defined]

    auth_mod = types.ModuleType("google.auth")
    transport_mod = types.ModuleType("google.auth.transport")
    requests_mod = types.ModuleType("google.auth.transport.requests")

    class _DummyGoogleRequest:  # pragma: no cover - test stub
        def __init__(self, *_args, **_kwargs):
            pass

    requests_mod.Request = _DummyGoogleRequest  # type: ignore[attr-defined]

    google_mod.oauth2 = oauth2_mod  # type: ignore[attr-defined]
    oauth2_mod.id_token = id_token_mod  # type: ignore[attr-defined]
    google_mod.auth = auth_mod  # type: ignore[attr-defined]
    auth_mod.transport = transport_mod  # type: ignore[attr-defined]
    transport_mod.requests = requests_mod  # type: ignore[attr-defined]

    sys.modules["google"] = google_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.id_token"] = id_token_mod
    sys.modules["google.auth"] = auth_mod
    sys.modules["google.auth.transport"] = transport_mod
    sys.modules["google.auth.transport.requests"] = requests_mod

from analytics import AnalyticsValidationError
from auth import get_registered_user
from dependencies import get_db
from routers import analytics as analytics_router
from routers import import_ as import_router
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

        with patch("auth._verify_google_token", return_value={"id": "user-1"}), patch(
            "db.get_user_by_id", return_value={"id": "user-1"}
        ) as get_user_by_id_mock, patch(
            "db.get_connection", side_effect=AssertionError("unexpected get_connection() call")
        ) as get_connection_mock:
            user = get_registered_user(credentials=credentials, conn=conn)

        self.assertEqual(user["id"], "user-1")
        get_user_by_id_mock.assert_called_once_with(conn, "user-1")
        get_connection_mock.assert_not_called()

    def test_get_registered_user_preserves_unregistered_user_403(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")
        conn = object()

        with patch("auth._verify_google_token", return_value={"id": "user-2"}), patch(
            "db.get_user_by_id", return_value=None
        ):
            with self.assertRaises(HTTPException) as err:
                get_registered_user(credentials=credentials, conn=conn)

        self.assertEqual(err.exception.status_code, 403)
        self.assertIn("User not registered", err.exception.detail)


class CachedImportInsightsRefreshTest(unittest.TestCase):
    def _build_request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/import/chesscom",
                "headers": [(b"host", b"korchess.com")],
            }
        )

    def test_cached_import_schedules_public_insights_refresh(self) -> None:
        conn = Mock()
        conn.commit = Mock()

        with patch("routers.import_.ensure_public_user_for_username", return_value="public:magnuscarlsen"), patch(
            "routers.import_.get_import_status",
            return_value={
                "total_games": 200,
                "last_imported": 200,
                "last_skipped": 0,
                "imported_at": "2026-03-03T00:00:00+00:00",
            },
        ), patch(
            "routers.import_.get_insights_state",
            return_value={"lifecycle_status": "missing"},
        ), patch(
            "routers.import_.track_server_event",
            new=AsyncMock(return_value=None),
        ) as track_mock, patch(
            "routers.import_.schedule_insights_refresh"
        ) as schedule_mock:
            response = asyncio.run(
                import_router._maybe_return_existing_import(
                    conn=conn,
                    current_user=None,
                    http_request=self._build_request(),
                    username="MagnusCarlsen",
                    site="chesscom",
                    max_games=200,
                    username_hash="hash",
                )
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.username, "MagnusCarlsen")
        self.assertEqual(response.imported, 200)
        conn.commit.assert_called_once()
        track_mock.assert_awaited_once()
        schedule_mock.assert_called_once_with(
            user_id="public:magnuscarlsen",
            username="MagnusCarlsen",
            site="all",
            reason="import_cache_hit",
            allow_deep=False,
            allow_llm=False,
            source_user_id="public:magnuscarlsen",
        )

    def test_cached_import_schedules_public_and_authenticated_refresh(self) -> None:
        conn = Mock()
        conn.commit = Mock()
        current_user = {"id": "user-123"}

        with patch("routers.import_.ensure_public_user_for_username", return_value="public:magnuscarlsen"), patch(
            "routers.import_.get_import_status",
            return_value={
                "total_games": 200,
                "last_imported": 200,
                "last_skipped": 0,
                "imported_at": "2026-03-03T00:00:00+00:00",
            },
        ), patch(
            "routers.import_.get_insights_state",
            side_effect=[
                {"lifecycle_status": "missing"},
                {"lifecycle_status": "missing"},
            ],
        ), patch(
            "routers.import_.upsert_import_status"
        ) as upsert_status_mock, patch(
            "routers.import_.track_server_event",
            new=AsyncMock(return_value=None),
        ), patch(
            "routers.import_.schedule_insights_refresh"
        ) as schedule_mock:
            response = asyncio.run(
                import_router._maybe_return_existing_import(
                    conn=conn,
                    current_user=current_user,
                    http_request=self._build_request(),
                    username="MagnusCarlsen",
                    site="chesscom",
                    max_games=200,
                    username_hash="hash",
                )
            )

        self.assertIsNotNone(response)
        upsert_status_mock.assert_called_once()
        schedule_mock.assert_has_calls(
            [
                call(
                    user_id="public:magnuscarlsen",
                    username="MagnusCarlsen",
                    site="all",
                    reason="import_cache_hit",
                    allow_deep=False,
                    allow_llm=False,
                    source_user_id="public:magnuscarlsen",
                ),
                call(
                    user_id="user-123",
                    username="MagnusCarlsen",
                    site="all",
                    reason="import_cache_hit",
                    allow_deep=True,
                    allow_llm=True,
                    source_user_id="public:magnuscarlsen",
                ),
            ]
        )

    def test_cached_import_skips_refresh_when_insights_complete(self) -> None:
        conn = Mock()
        conn.commit = Mock()

        with patch("routers.import_.ensure_public_user_for_username", return_value="public:magnuscarlsen"), patch(
            "routers.import_.get_import_status",
            return_value={
                "total_games": 200,
                "last_imported": 200,
                "last_skipped": 0,
                "imported_at": "2026-03-03T00:00:00+00:00",
            },
        ), patch(
            "routers.import_.get_insights_state",
            return_value={"lifecycle_status": "complete"},
        ), patch(
            "routers.import_.track_server_event",
            new=AsyncMock(return_value=None),
        ), patch(
            "routers.import_.schedule_insights_refresh"
        ) as schedule_mock:
            response = asyncio.run(
                import_router._maybe_return_existing_import(
                    conn=conn,
                    current_user=None,
                    http_request=self._build_request(),
                    username="MagnusCarlsen",
                    site="chesscom",
                    max_games=200,
                    username_hash="hash",
                )
            )

        self.assertIsNotNone(response)
        schedule_mock.assert_not_called()

    def test_cached_import_refreshes_only_missing_context(self) -> None:
        conn = Mock()
        conn.commit = Mock()
        current_user = {"id": "user-123"}

        with patch("routers.import_.ensure_public_user_for_username", return_value="public:magnuscarlsen"), patch(
            "routers.import_.get_import_status",
            return_value={
                "total_games": 200,
                "last_imported": 200,
                "last_skipped": 0,
                "imported_at": "2026-03-03T00:00:00+00:00",
            },
        ), patch(
            "routers.import_.get_insights_state",
            side_effect=[
                {"lifecycle_status": "complete"},
                {"lifecycle_status": "missing"},
            ],
        ), patch(
            "routers.import_.upsert_import_status"
        ), patch(
            "routers.import_.track_server_event",
            new=AsyncMock(return_value=None),
        ), patch(
            "routers.import_.schedule_insights_refresh"
        ) as schedule_mock:
            response = asyncio.run(
                import_router._maybe_return_existing_import(
                    conn=conn,
                    current_user=current_user,
                    http_request=self._build_request(),
                    username="MagnusCarlsen",
                    site="chesscom",
                    max_games=200,
                    username_hash="hash",
                )
            )

        self.assertIsNotNone(response)
        schedule_mock.assert_called_once_with(
            user_id="user-123",
            username="MagnusCarlsen",
            site="all",
            reason="import_cache_hit",
            allow_deep=True,
            allow_llm=True,
            source_user_id="public:magnuscarlsen",
        )


if __name__ == "__main__":
    unittest.main()
