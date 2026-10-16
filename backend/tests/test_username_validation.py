"""Usernames are validated before they reach upstream URLs, and Chess.com
redirects are only followed within the Chess.com API host."""

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
from schemas import is_valid_chess_username
from services import chesscom, lichess
from services.chesscom import ChesscomAPIError
from services.lichess import LichessAPIError

_RealClient = httpx.Client
BAD_USERNAMES = ["a", "../admin", "foo/bar", "user name", "x" * 31, "fü", "a?b=c", "%2e%2e", "a.b"]


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: _RealClient(transport=transport, **kwargs)


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request to {request.url}")


def _build_app(router, prefix: str) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}
    return app


class UsernameRuleTest(unittest.TestCase):
    def test_accepts_real_style_usernames(self) -> None:
        for name in ["DrNykterstein", "Hikaru", "ab", "a_b-c", "x" * 30, "Player-2_0"]:
            self.assertTrue(is_valid_chess_username(name), name)

    def test_rejects_anything_unsafe_for_a_url_path(self) -> None:
        for name in BAD_USERNAMES + [""]:
            self.assertFalse(is_valid_chess_username(name), name)


class ServiceBackstopTest(unittest.TestCase):
    def test_services_refuse_bad_usernames_without_a_request(self) -> None:
        calls = [
            (LichessAPIError, lambda u: lichess.fetch_lichess_profile(u)),
            (LichessAPIError, lambda u: lichess.fetch_lichess_pgn(u, 10)),
            (ChesscomAPIError, lambda u: chesscom.fetch_chesscom_profile(u)),
            (ChesscomAPIError, lambda u: chesscom.fetch_chesscom_games(u, 10)),
        ]
        with patch.object(httpx, "Client", new=_client_factory(_no_network)):
            for error_cls, call in calls:
                with self.assertRaises(error_cls) as err:
                    call("../admin")
                self.assertEqual(err.exception.status_code, 400)


class EndpointValidationTest(unittest.TestCase):
    def test_import_rejects_bad_username_with_400(self) -> None:
        client = TestClient(_build_app(import_router.router, "/api/v1/import"))
        with patch.object(import_router, "import_lichess_games") as importer, \
                patch.object(import_router, "get_import_status", return_value={}), \
                patch.object(import_router, "track_server_event", AsyncMock()):
            for name in BAD_USERNAMES:
                res = client.post("/api/v1/import/lichess", json={"username": name})
                self.assertEqual(res.status_code, 400, name)
                self.assertIn("Invalid username", res.json()["detail"])
        importer.assert_not_called()

    def test_profile_endpoints_reject_bad_username_with_400(self) -> None:
        client = TestClient(_build_app(profiles_router.router, "/api/v1/profiles"))
        with patch.object(profiles_router, "get_chess_profile", return_value=None), \
                patch.object(profiles_router, "fetch_lichess_profile") as fetch_profile, \
                patch.object(profiles_router, "import_lichess_games") as importer:
            res = client.post("/api/v1/profiles", json={"username": "a.b", "site": "lichess"})
            self.assertEqual(res.status_code, 400)
            for action in ("import", "sync"):
                res = client.post(f"/api/v1/profiles/lichess/a.b/{action}")
                self.assertEqual(res.status_code, 400, action)
        fetch_profile.assert_not_called()
        importer.assert_not_called()


class ChesscomRedirectTest(unittest.TestCase):
    def _fetch_profile(self, location: str) -> tuple[list[str], Exception | dict]:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path == "/pub/player/Someone":
                return httpx.Response(301, headers={"Location": location})
            if request.url.host == "api.chess.com" and request.url.path == "/pub/player/someone":
                return httpx.Response(200, json={"username": "someone"})
            if request.url.path.endswith("/stats"):
                return httpx.Response(404)
            return httpx.Response(200, json={"username": "attacker"})

        with patch.object(httpx, "Client", new=_client_factory(handler)):
            try:
                return seen, chesscom.fetch_chesscom_profile("Someone")
            except ChesscomAPIError as e:
                return seen, e

    def test_follows_redirect_within_the_api_host(self) -> None:
        for location in ("https://api.chess.com/pub/player/someone", "/pub/player/someone"):
            seen, result = self._fetch_profile(location)
            self.assertIsInstance(result, dict, location)
            self.assertEqual(result["username"], "someone")
            self.assertTrue(all("api.chess.com" in url for url in seen))

    def test_does_not_follow_redirect_off_the_api_host(self) -> None:
        for location in (
            "https://evil.example/pub/player/someone",
            "http://api.chess.com/pub/player/someone",
            "//evil.example/x",
        ):
            seen, result = self._fetch_profile(location)
            self.assertIsInstance(result, ChesscomAPIError, location)
            self.assertEqual(len(seen), 1, seen)


if __name__ == "__main__":
    unittest.main()
