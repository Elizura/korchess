"""Upstream (Lichess/Chess.com) failures must map to clear 4xx/502s, never 500s,
and must not leak internal error text to clients."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from dependencies import get_db
from routers import import_ as import_router
from routers import profiles as profiles_router
from services import chesscom, lichess
from services.chesscom import ChesscomAPIError
from services.lichess import LichessAPIError

_RealClient = httpx.Client
SECRET_DETAIL = "proxy 10.0.0.7:3128 refused CONNECT"


def _client_factory(handler):
    """Stand-in for httpx.Client that routes requests to `handler`."""
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: _RealClient(transport=transport, **kwargs)


def _raise_proxy_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ProxyError(SECRET_DETAIL, request=request)


def _build_app(router, prefix: str) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}
    return app


class ServiceNetworkErrorTest(unittest.TestCase):
    def test_lichess_network_error_is_generic_502(self) -> None:
        for call in (
            lambda: lichess.fetch_lichess_profile("someone"),
            lambda: lichess.fetch_lichess_pgn("someone", 10),
        ):
            with patch.object(httpx, "Client", new=_client_factory(_raise_proxy_error)):
                with self.assertRaises(LichessAPIError) as err:
                    call()
            self.assertEqual(err.exception.status_code, 502)
            self.assertNotIn(SECRET_DETAIL, err.exception.message)

    def test_chesscom_network_error_is_generic_502(self) -> None:
        for call in (
            lambda: chesscom.fetch_chesscom_profile("someone"),
            lambda: chesscom.fetch_chesscom_games("someone", 10),
        ):
            with patch.object(httpx, "Client", new=_client_factory(_raise_proxy_error)):
                with self.assertRaises(ChesscomAPIError) as err:
                    call()
            self.assertEqual(err.exception.status_code, 502)
            self.assertNotIn(SECRET_DETAIL, err.exception.message)

    def test_lichess_404_and_429_keep_their_status(self) -> None:
        for status in (404, 429):
            handler = lambda request, status=status: httpx.Response(
                status, headers={"Retry-After": "0"}
            )
            with patch.object(httpx, "Client", new=_client_factory(handler)):
                with self.assertRaises(LichessAPIError) as err:
                    lichess.fetch_lichess_pgn("someone", 10)
            self.assertEqual(err.exception.status_code, status)


class ImportEndpointUpstreamErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_app(import_router.router, "/api/v1/import"))
        patches = [
            patch.object(import_router, "get_import_status", return_value={}),
            patch.object(import_router, "track_server_event", AsyncMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _post_with_error(self, site: str, error: Exception):
        target = "import_lichess_games" if site == "lichess" else "import_chesscom_games"
        with patch.object(import_router, target, side_effect=error):
            return self.client.post(f"/api/v1/import/{site}", json={"username": "someone"})

    def test_lichess_errors_map_to_http_status(self) -> None:
        cases = [(404, 404), (429, 429), (502, 502), (503, 502), (None, 502)]
        for upstream, expected in cases:
            res = self._post_with_error("lichess", LichessAPIError("upstream says no", upstream))
            self.assertEqual(res.status_code, expected, upstream)
            self.assertEqual(res.json()["detail"], "upstream says no")

    def test_chesscom_errors_map_to_http_status(self) -> None:
        cases = [(404, 404), (429, 429), (500, 502)]
        for upstream, expected in cases:
            res = self._post_with_error("chesscom", ChesscomAPIError("upstream says no", upstream))
            self.assertEqual(res.status_code, expected, upstream)


class ProfileEndpointUpstreamErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_app(profiles_router.router, "/api/v1/profiles"))
        patches = [
            patch.object(profiles_router, "track_server_event", AsyncMock()),
            patch.object(
                profiles_router,
                "get_chess_profile",
                return_value={"chess_username": "someone", "site": "lichess"},
            ),
            patch.object(
                profiles_router,
                "upsert_chess_profile",
                return_value={"chess_username": "someone", "site": "lichess"},
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_import_maps_rate_limit_to_429(self) -> None:
        with patch.object(
            profiles_router, "import_lichess_games",
            side_effect=LichessAPIError("Rate limited by Lichess.", 429),
        ):
            res = self.client.post("/api/v1/profiles/lichess/someone/import")
        self.assertEqual(res.status_code, 429)

    def test_import_maps_not_found_to_404(self) -> None:
        with patch.object(
            profiles_router, "import_chesscom_games",
            side_effect=ChesscomAPIError("User not found.", 404),
        ):
            res = self.client.post("/api/v1/profiles/chesscom/someone/import")
        self.assertEqual(res.status_code, 404)

    def test_sync_keeps_ratings_when_game_fetch_is_rate_limited(self) -> None:
        with patch.object(
            profiles_router, "fetch_lichess_profile",
            return_value={"username": "someone", "blitz_rating": 1500},
        ), patch.object(
            profiles_router, "import_lichess_games",
            side_effect=LichessAPIError("Rate limited by Lichess.", 429),
        ):
            res = self.client.post("/api/v1/profiles/lichess/someone/sync")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["sync_result"]["imported"], 0)

    def test_add_profile_network_error_is_generic_502(self) -> None:
        with patch.object(profiles_router, "get_chess_profile", return_value=None), \
                patch.object(httpx, "Client", new=_client_factory(_raise_proxy_error)):
            res = self.client.post(
                "/api/v1/profiles", json={"username": "someone", "site": "lichess"}
            )
        self.assertEqual(res.status_code, 502)
        self.assertNotIn(SECRET_DETAIL, res.text)


if __name__ == "__main__":
    unittest.main()
