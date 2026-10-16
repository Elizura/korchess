"""Lichess fetching and PGN parsing behaviour."""

from __future__ import annotations

import os
import secrets
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import httpx

from services import lichess

_RealClient = httpx.Client


class LichessTokenTest(unittest.TestCase):
    def _captured_auth_headers(self) -> list[str | None]:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            if request.url.path.startswith("/api/user/"):
                return httpx.Response(200, json={"username": "someone", "perfs": {}})
            return httpx.Response(200, text="")

        transport = httpx.MockTransport(handler)
        with patch.object(httpx, "Client", new=lambda **kw: _RealClient(transport=transport, **kw)):
            lichess.fetch_lichess_profile("someone")
            lichess.fetch_lichess_pgn("someone", 10)
        return seen

    def test_sends_bearer_token_when_configured(self) -> None:
        token = secrets.token_urlsafe()
        with patch.dict(os.environ, {"LICHESS_API_TOKEN": token}):
            seen = self._captured_auth_headers()
        self.assertEqual(seen, [f"Bearer {token}"] * 2)

    def test_anonymous_without_token(self) -> None:
        with patch.dict(os.environ, {"LICHESS_API_TOKEN": ""}):
            seen = self._captured_auth_headers()
        self.assertEqual(seen, [None, None])


if __name__ == "__main__":
    unittest.main()
