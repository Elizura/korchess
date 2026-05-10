"""Read-only LMDB lookup for Lichess eval positions.

Provides a lazy-singleton LMDB environment and batch lookup that
returns results in the same ``chess.engine.InfoDict`` shape that
Stockfish ``engine.analyse()`` produces, so callers can treat LMDB
hits and Stockfish results interchangeably.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import chess
import chess.engine
import lmdb

from .codec import decode_value
from .keys import fen_to_4field

logger = logging.getLogger(__name__)

DB_NAME = b"evals"
MAP_SIZE = 64 * 1024**3

_env_lock = threading.Lock()
_env: lmdb.Environment | None = None
_evals_db: Any = None


def _get_env() -> tuple[lmdb.Environment, Any] | None:
    """
    Returns the singleton (env, evals_db) pair, opening on first call.
    """
    global _env, _evals_db

    if _env is not None:
        return _env, _evals_db

    with _env_lock:
        if _env is not None:
            return _env, _evals_db

        db_path = os.environ.get("LICHESS_EVAL_DB_PATH")
        if not db_path or not os.path.exists(db_path):
            return None

        _env = lmdb.open(
            db_path,
            map_size=MAP_SIZE,
            subdir=True,
            max_dbs=2,
            readonly=True,
            lock=False,
            readahead=False,
        )
        _evals_db = _env.open_db(DB_NAME)
        logger.info("LMDB eval cache opened at %s", db_path)
        return _env, _evals_db


def close() -> None:
    """Close the singleton LMDB environment (call on app shutdown)."""
    global _env, _evals_db
    with _env_lock:
        if _env is not None:
            _env.close()
            _env = None
            _evals_db = None
            logger.info("LMDB eval cache closed")


def _side_from_fen(fen: str) -> chess.Color:
    """Extract the side-to-move from a FEN string."""
    parts = fen.split()
    if len(parts) >= 2 and parts[1] == "b":
        return chess.BLACK
    return chess.WHITE


def lmdb_eval_to_info_dict(record: dict, fen: str) -> dict[str, Any]:
    """Convert a decoded LMDB eval record to ``chess.engine.InfoDict`` shape.

    The Lichess eval dataset stores scores from the side-to-move's
    perspective, so the constructed ``PovScore`` uses the FEN's active
    color as its point-of-view.
    """
    pvs = record.get("p") or []
    if not pvs:
        return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE), "pv": []}

    pv0 = pvs[0]
    side = _side_from_fen(fen)

    cp = pv0.get("cp")
    mate = pv0.get("m")
    if mate is not None:
        score = chess.engine.PovScore(chess.engine.Mate(mate), side)
    elif cp is not None:
        score = chess.engine.PovScore(chess.engine.Cp(cp), side)
    else:
        score = chess.engine.PovScore(chess.engine.Cp(0), side)

    line_str = pv0.get("l") or ""
    pv_moves: list[chess.Move] = []
    for token in line_str.split():
        try:
            pv_moves.append(chess.Move.from_uci(token))
        except ValueError:
            break

    return {"score": score, "pv": pv_moves}


def lookup_fens(fens: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-lookup FENs in the LMDB eval cache.

    Returns a dict mapping each *original* (full) FEN to a
    ``chess.engine.InfoDict``-compatible dict for every cache hit.
    FENs not found in the cache are simply absent from the result.
    """
    handle = _get_env()
    if handle is None:
        return {}

    env, evals_db = handle
    results: dict[str, dict[str, Any]] = {}

    with env.begin(db=evals_db, buffers=True) as txn:
        for fen in fens:
            key = fen_to_4field(fen).encode("utf-8")
            raw = txn.get(key)
            if raw is None:
                continue
            record = decode_value(bytes(raw))
            results[fen] = lmdb_eval_to_info_dict(record, fen)

    return results
