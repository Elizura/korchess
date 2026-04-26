"""Encode/decode Lichess eval records for LMDB storage.

Value schema (msgpack):
    {
        "d": int,           # depth
        "k": int | None,    # knodes
        "p": [              # principal variations
            {
                "cp": int | None,   # centipawns (None if mate)
                "m": int | None,    # mate-in (None if cp)
                "l": str,           # UCI line, space-joined, trimmed to MAX_PV_PLIES
            },
        ],
    }
"""

import msgpack

from .keys import fen_to_4field

MAX_PV_PLIES = 8


def transform_record(rec: dict) -> tuple[bytes, bytes] | None:
    """Transform a raw JSONL record into an (lmdb_key, lmdb_value) pair.

    Picks the deepest eval from the record's eval list, trims PV lines
    to MAX_PV_PLIES, and msgpack-encodes the value.

    Returns None if the record has no usable evals.
    """
    fen_raw = rec.get("fen")
    if not fen_raw:
        return None

    evals = rec.get("evals") or []
    if not evals:
        return None

    deepest = max(evals, key=lambda e: e.get("depth", 0))

    trimmed_pvs = []
    for pv in deepest.get("pvs") or []:
        raw_line = pv.get("line") or ""
        trimmed_line = " ".join(raw_line.split()[:MAX_PV_PLIES])
        trimmed_pvs.append({
            "cp": pv.get("cp"),
            "m": pv.get("mate"),
            "l": trimmed_line,
        })

    payload = {
        "d": deepest.get("depth", 0),
        "k": deepest.get("knodes"),
        "p": trimmed_pvs,
    }

    key = fen_to_4field(fen_raw).encode("utf-8")
    val = msgpack.packb(payload, use_bin_type=True)
    return key, val


def decode_value(raw: bytes) -> dict:
    """Decode a msgpack-encoded LMDB value back to a dict."""
    return msgpack.unpackb(raw, raw=False)


def extract_depth(raw: bytes) -> int:
    """Fast extraction of just the depth from a packed value.

    Used by the writer for cross-line FEN dedup (only overwrite if
    the new record is deeper).
    """
    obj = msgpack.unpackb(raw, raw=False)
    return obj.get("d", 0)
