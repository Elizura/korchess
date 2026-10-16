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


def _pgn(game_id: str, eco: str, opening: str) -> str:
    return (
        f'[Site "https://lichess.org/{game_id}"]\n'
        '[White "Someone"]\n[Black "Other"]\n[Result "1-0"]\n'
        '[UTCDate "2024.03.05"]\n[UTCTime "12:34:56"]\n[Speed "blitz"]\n'
        f'[ECO "{eco}"]\n[Opening "{opening}"]\n\n'
        "1. e4 c5 2. Nf3 d6 1-0\n\n"
    )


class LichessOpeningFallbackTest(unittest.TestCase):
    def test_uses_pgn_headers_when_openings_table_has_no_match(self) -> None:
        pgn = _pgn("abcd1234", "B50", "Sicilian Defense: Modern Variations") + _pgn("abcd5678", "?", "?")
        with patch.object(lichess, "best_opening_match", return_value=None):
            games, skipped = lichess.parse_pgn_games(pgn, "someone", object())
        self.assertEqual(skipped, 0)
        self.assertEqual(
            [(g["eco"], g["opening_name"], g["opening_id"]) for g in games],
            [("B50", "Sicilian Defense: Modern Variations", None), ("UNKNOWN", "Unknown", None)],
        )

    def test_prefers_the_openings_table_match(self) -> None:
        match = {"eco": "B50", "name": "Sicilian Defense", "opening_id": 7, "ply_count": 4}
        with patch.object(lichess, "best_opening_match", return_value=match):
            games, _ = lichess.parse_pgn_games(_pgn("abcd1234", "B51", "Other"), "someone", object())
        self.assertEqual((games[0]["eco"], games[0]["opening_name"], games[0]["opening_id"]),
                         ("B50", "Sicilian Defense", 7))


if __name__ == "__main__":
    unittest.main()
