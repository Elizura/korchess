"""Imports record where they synced to, and re-imports skip known work."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fake_redis import FakeRedis
from repository import tasks
from services import import_service


def _game(game_id: str, played_at: str | None, site: str = "lichess") -> dict:
    return {"site": site, "site_game_id": game_id, "username": "someone", "played_at": played_at, "pgn": ""}


class ImportServiceSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.status = {"total_games": 0, "last_synced_at": None}
        self.known: set[str] = set()
        self.conn = MagicMock()
        patches = {
            "redis": patch.object(import_service, "_redis", MagicMock()),
            "delay": patch.object(import_service.process_game, "delay"),
            "status": patch.object(import_service, "get_import_status", side_effect=lambda *a: self.status),
            "known": patch.object(
                import_service, "get_existing_game_ids",
                side_effect=lambda conn, u, s, ids: {i for i in ids if i in self.known},
            ),
            "record": patch.object(import_service, "upsert_import_status"),
            "fetch_lichess": patch.object(import_service, "fetch_lichess_pgn", return_value="pgn"),
            "parse_lichess": patch.object(import_service, "parse_pgn_games"),
            "fetch_chesscom": patch.object(import_service, "fetch_chesscom_games"),
        }
        self.mocks = {name: p.start() for name, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)

    def _lichess(self, games: list[dict], skipped: int = 0):
        self.mocks["parse_lichess"].return_value = (games, skipped)
        return import_service.import_lichess_games("Someone", self.conn, 150)

    def _enqueued_ids(self) -> list[str]:
        return [c.args[0]["site_game_id"] for c in self.mocks["delay"].call_args_list]

    def test_first_import_records_newest_game_time_not_now(self) -> None:
        result = self._lichess([
            _game("g2", "2024-03-05T12:00:00Z"),
            _game("g1", "2024-03-04T09:00:00Z"),
        ], skipped=1)

        self.mocks["fetch_lichess"].assert_called_once_with("Someone", 150, since=None)
        self.assertEqual(self._enqueued_ids(), ["g2", "g1"])
        self.assertEqual((result.imported, result.skipped, result.is_sync), (2, 1, False))
        record = self.mocks["record"].call_args
        self.assertEqual(record.kwargs["last_synced_at"], datetime(2024, 3, 5, 12, tzinfo=timezone.utc))
        self.assertEqual(record.kwargs["imported"], 2)
        self.conn.commit.assert_called()

    def test_sync_fetches_since_last_sync_and_skips_known_games(self) -> None:
        self.status = {"total_games": 40, "last_synced_at": "2024-03-05T12:00:00+00:00"}
        self.known = {"g2"}  # boundary game, fetched again because `since` is inclusive
        result = self._lichess([
            _game("g3", "2024-03-06T08:00:00Z"),
            _game("g2", "2024-03-05T12:00:00Z"),
        ])

        since_ms = int(datetime(2024, 3, 5, 12, tzinfo=timezone.utc).timestamp() * 1000)
        self.mocks["fetch_lichess"].assert_called_once_with("Someone", 150, since=since_ms)
        self.assertEqual(self._enqueued_ids(), ["g3"])
        self.assertEqual((result.imported, result.is_sync), (1, True))
        self.assertEqual(
            self.mocks["record"].call_args.kwargs["last_synced_at"],
            datetime(2024, 3, 6, 8, tzinfo=timezone.utc),
        )

    def test_sync_with_nothing_new_keeps_last_synced_at(self) -> None:
        self.status = {"total_games": 40, "last_synced_at": "2024-03-05T12:00:00+00:00"}
        self.known = {"g2"}
        result = self._lichess([_game("g2", "2024-03-05T12:00:00Z")])

        self.mocks["delay"].assert_not_called()
        self.assertEqual((result.imported, result.is_sync), (0, True))
        self.assertEqual(
            self.mocks["record"].call_args.kwargs["last_synced_at"],
            datetime(2024, 3, 5, 12, tzinfo=timezone.utc),
        )

    def test_no_games_and_no_history_records_nothing(self) -> None:
        result = self._lichess([])
        self.assertEqual((result.imported, result.is_sync), (0, False))
        self.mocks["record"].assert_not_called()

    def test_reimport_of_legacy_games_without_import_record_is_a_sync(self) -> None:
        # Games stored before imports were recorded: full fetch, nothing new, no 404.
        self.status = {"total_games": 2, "last_synced_at": None}
        self.known = {"g1", "g2"}
        result = self._lichess([_game("g2", "2024-03-05T12:00:00Z"), _game("g1", "2024-03-04T12:00:00Z")])

        self.mocks["fetch_lichess"].assert_called_once_with("Someone", 150, since=None)
        self.assertEqual((result.imported, result.is_sync), (0, True))
        self.assertIsNotNone(self.mocks["record"].call_args.kwargs["last_synced_at"])

    def test_chesscom_sync_passes_last_synced_at(self) -> None:
        self.status = {"total_games": 3, "last_synced_at": "2024-03-05T12:00:00+00:00"}
        self.mocks["fetch_chesscom"].return_value = [
            _game("c9", "2024-03-07T10:00:00+00:00", site="chesscom"),
        ]
        result = import_service.import_chesscom_games("Someone", self.conn, 150)

        self.assertEqual(
            self.mocks["fetch_chesscom"].call_args.kwargs["since"],
            datetime(2024, 3, 5, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(result.imported, 1)
        self.assertEqual(self.mocks["delay"].call_args.args[2], "chesscom")


class ProcessGameScanSkipTest(unittest.TestCase):
    def _run(self, already_scanned: bool) -> MagicMock:
        with patch.object(tasks, "get_connection", MagicMock()), \
                patch.object(tasks, "bulk_upsert_games", return_value=(1, 0)), \
                patch.object(tasks, "upsert_insight_game_feature"), \
                patch.object(tasks, "extract_light_game_features", return_value={}), \
                patch.object(tasks, "has_game_quick_scan", return_value=already_scanned), \
                patch.object(tasks, "upsert_game_quick_scan"), \
                patch.object(tasks, "_redis", FakeRedis()), \
                patch.object(tasks, "run_quick_scan_single", return_value={"summary": {}}) as scan:
            tasks.process_game(_game("g1", "2024-03-05T12:00:00Z"), "Someone", "lichess")
        return scan

    def test_skips_quick_scan_for_already_scanned_game(self) -> None:
        self._run(already_scanned=True).assert_not_called()

    def test_scans_new_game(self) -> None:
        self._run(already_scanned=False).assert_called_once()


if __name__ == "__main__":
    unittest.main()
