"""Import progress counters reset per import, expire, and complete exactly once."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fake_redis import FakeRedis
from repository import tasks
from services import import_service

KEY = "import:someone:lichess:{}"


def _games(*ids: str) -> list[dict]:
    return [
        {"site": "lichess", "site_game_id": i, "username": "someone", "played_at": "2024-03-05T12:00:00Z"}
        for i in ids
    ]


class ImportProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        patches = [
            patch.object(import_service, "_redis", self.redis),
            patch.object(tasks, "_redis", self.redis),
            patch.object(import_service.process_game, "delay"),
            patch.object(import_service, "get_existing_game_ids", return_value=set()),
            patch.object(import_service, "upsert_import_status"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        aggregate_patch = patch.object(tasks, "_run_aggregation")
        self.aggregate = aggregate_patch.start()
        self.addCleanup(aggregate_patch.stop)

    def _start_import(self, *ids: str) -> None:
        import_service._enqueue_and_record(
            MagicMock(), "Someone", "lichess", _games(*ids), 0, 150, False, None,
        )

    def test_start_resets_done_and_sets_ttls(self) -> None:
        self.redis.data[KEY.format("done")] = "149"  # left behind, no TTL
        self.redis.data[KEY.format("complete")] = "1"
        self._start_import("a", "b")

        self.assertEqual(self.redis.get(KEY.format("done")), "0")
        self.assertEqual(self.redis.get(KEY.format("total")), "2")
        self.assertIsNone(self.redis.get(KEY.format("complete")))
        for field in ("done", "total", "status", "meta"):
            self.assertIn(KEY.format(field), self.redis.ttl, field)

    def test_stale_done_count_no_longer_blocks_completion(self) -> None:
        self.redis.data[KEY.format("done")] = "149"
        self._start_import("a", "b", "c")
        for _ in range(3):
            tasks._record_game_done("someone", "lichess")

        self.aggregate.assert_called_once_with("someone", "lichess")
        self.assertEqual(self.redis.get(KEY.format("status")), "complete")

    def test_overshoot_completes_and_aggregates_exactly_once(self) -> None:
        self._start_import("a", "b")
        for _ in range(5):  # e.g. tasks left over from an overlapping import
            tasks._record_game_done("someone", "lichess")

        self.aggregate.assert_called_once()
        self.assertIn(KEY.format("done"), self.redis.ttl)  # stragglers' counter still expires

    def test_overlapping_import_restarts_the_count(self) -> None:
        self._start_import("a", "b", "c")
        tasks._record_game_done("someone", "lichess")
        self._start_import("d")  # a sync while the first import is still running
        tasks._record_game_done("someone", "lichess")
        tasks._record_game_done("someone", "lichess")

        self.aggregate.assert_called_once()

    def test_each_import_completes_once(self) -> None:
        for ids in (("a",), ("b", "c")):
            self._start_import(*ids)
            for _ in ids:
                tasks._record_game_done("someone", "lichess")
        self.assertEqual(self.aggregate.call_count, 2)


class ProcessGameFailureTest(unittest.TestCase):
    def test_game_that_fails_after_retries_still_counts_toward_completion(self) -> None:
        with patch.object(tasks, "get_connection", MagicMock()), \
                patch.object(tasks, "bulk_upsert_games", side_effect=RuntimeError("db down")), \
                patch.object(tasks, "_record_game_done") as record_done:
            with self.assertRaises(RuntimeError):
                tasks.process_game.apply(
                    args=(_games("a")[0], "Someone", "lichess"),
                    retries=tasks.process_game.max_retries,
                    throw=True,
                )
        record_done.assert_called_once_with("someone", "lichess")


if __name__ == "__main__":
    unittest.main()
