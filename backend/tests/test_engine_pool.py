"""A Stockfish crash must leave the engine pool with the same number of live engines."""

from __future__ import annotations

import os
import shutil
import signal
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import chess

from services import full_analysis

START_FEN = chess.STARTING_FEN
AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _kill(engine) -> None:
    os.kill(engine.protocol.transport.get_pid(), signal.SIGKILL)
    time.sleep(0.2)


@unittest.skipUnless(
    shutil.which(full_analysis.STOCKFISH_PATH) or os.path.exists(full_analysis.STOCKFISH_PATH),
    "Stockfish not installed",
)
class EnginePoolCrashRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = full_analysis.init_engine_pool(size=1)
        self.addCleanup(full_analysis.shutdown_engine_pool)

    def _crash_pooled_engine(self):
        engine = self.pool.acquire(timeout=5)
        _kill(engine)
        self.pool.release(engine)
        return engine

    def _assert_one_live_engine(self, dead) -> None:
        self.assertEqual(self.pool._pool.qsize(), 1)
        self.assertEqual(len(self.pool._engines), 1)
        engine = self.pool.acquire(timeout=5)
        try:
            self.assertIsNot(engine, dead)
            self.assertNotIn(dead, self.pool._engines)
            engine.ping()
        finally:
            self.pool.release(engine)

    def test_chunk_analysis_replaces_crashed_engine(self) -> None:
        dead = self._crash_pooled_engine()
        results = full_analysis._analyse_fen_chunk([START_FEN, AFTER_E4_FEN], 6, 200, 1)
        self.assertTrue(all(results[fen] for fen in (START_FEN, AFTER_E4_FEN)))
        self._assert_one_live_engine(dead)

    def test_single_analysis_replaces_crashed_engine(self) -> None:
        dead = self._crash_pooled_engine()
        info = full_analysis._analyse_with_recovery(chess.Board(), 6, 200)
        self.assertIn("score", info[0] if isinstance(info, list) else info)
        self._assert_one_live_engine(dead)

    def test_failed_respawn_keeps_pool_size(self) -> None:
        dead = self._crash_pooled_engine()
        with patch.object(full_analysis, "STOCKFISH_PATH", "/nonexistent/stockfish"):
            results = full_analysis._analyse_fen_chunk([START_FEN], 6, 200, 1)
        self.assertIsNone(results[START_FEN])
        # The dead engine holds the slot, and the next user swaps it out
        self.assertEqual(self.pool._pool.qsize(), 1)
        results = full_analysis._analyse_fen_chunk([START_FEN], 6, 200, 1)
        self.assertTrue(results[START_FEN])
        self._assert_one_live_engine(dead)


if __name__ == "__main__":
    unittest.main()
