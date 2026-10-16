"""A missing or unreadable LMDB eval cache must fall back to Stockfish, not break scans."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import chess
import lmdb

from lmdb_magic import reader
from lmdb_magic.codec import transform_record

START_FEN = chess.STARTING_FEN


def _write_cache(path: str, db_name: bytes = reader.DB_NAME) -> None:
    key, value = transform_record(
        {"fen": START_FEN, "evals": [{"depth": 30, "pvs": [{"cp": 18, "line": "e2e4 e7e5"}]}]}
    )
    env = lmdb.open(path, map_size=10 * 1024**2, subdir=True, max_dbs=2)
    with env.begin(write=True, db=env.open_db(db_name)) as txn:
        txn.put(key, value)
    env.close()


class LmdbReaderFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        reader.close()
        self.addCleanup(reader.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def _lookup(self, path: str | None):
        with patch.dict(os.environ):
            os.environ.pop("LICHESS_EVAL_DB_PATH", None)
            if path is not None:
                os.environ["LICHESS_EVAL_DB_PATH"] = path
            return reader.lookup_fens([START_FEN])

    def test_reads_hits_from_a_valid_cache(self) -> None:
        _write_cache(self.dir)
        hits = self._lookup(self.dir)
        self.assertEqual(hits[START_FEN]["score"].white().score(), 18)

    def test_defaults_to_compose_mount_path(self) -> None:
        _write_cache(self.dir)
        with patch.object(reader, "DEFAULT_DB_PATH", self.dir):
            hits = self._lookup(None)
        self.assertIn(START_FEN, hits)

    def test_directory_without_data_file_disables_cache_with_one_warning(self) -> None:
        with self.assertLogs(reader.logger, level="WARNING") as logs:
            self.assertEqual(self._lookup(self.dir), {})
            self.assertEqual(self._lookup(self.dir), {})
        self.assertEqual(len(logs.output), 1)

    def test_missing_directory_disables_cache(self) -> None:
        with self.assertLogs(reader.logger, level="WARNING"):
            self.assertEqual(self._lookup(os.path.join(self.dir, "nope")), {})

    def test_unopenable_cache_disables_cache_instead_of_raising(self) -> None:
        _write_cache(self.dir, db_name=b"something-else")
        with self.assertLogs(reader.logger, level="WARNING") as logs:
            self.assertEqual(self._lookup(self.dir), {})
            self.assertEqual(self._lookup(self.dir), {})
        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
