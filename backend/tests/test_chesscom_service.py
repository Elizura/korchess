"""Chess.com game fetching keeps the newest games."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import httpx

from services import chesscom

_RealClient = httpx.Client
ARCHIVE_BASE = "https://api.chess.com/pub/player/someone/games"


def _game(game_id: int, end_time: int) -> dict:
    return {
        "url": f"https://www.chess.com/game/live/{game_id}",
        "rules": "chess",
        "time_class": "blitz",
        "end_time": end_time,
        "white": {"username": "Someone", "rating": 1500, "result": "win"},
        "black": {"username": "Other", "rating": 1500, "result": "resigned"},
        "pgn": "",
    }


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/games/archives"):
        return httpx.Response(200, json={"archives": [f"{ARCHIVE_BASE}/2024/02", f"{ARCHIVE_BASE}/2024/03"]})
    # Like the real API, each monthly archive lists its games oldest first.
    if path.endswith("/2024/02"):
        return httpx.Response(200, json={"games": [_game(1, 100), _game(2, 200)]})
    if path.endswith("/2024/03"):
        return httpx.Response(200, json={"games": [_game(3, 300), _game(4, 400), _game(5, 500)]})
    return httpx.Response(404)


class ChesscomNewestGamesTest(unittest.TestCase):
    def _fetch(self, max_games: int) -> list[str]:
        transport = httpx.MockTransport(_handler)
        with patch.object(httpx, "Client", new=lambda **kw: _RealClient(transport=transport, **kw)), \
                patch.object(chesscom.time, "sleep"):
            games = chesscom.fetch_chesscom_games("someone", max_games)
        return [g["site_game_id"] for g in games]

    def test_cap_keeps_the_newest_games_of_the_newest_month(self) -> None:
        self.assertEqual(self._fetch(2), ["5", "4"])

    def test_walks_months_and_games_newest_first(self) -> None:
        self.assertEqual(self._fetch(10), ["5", "4", "3", "2", "1"])


if __name__ == "__main__":
    unittest.main()
