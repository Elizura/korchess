"""Dump all entries from the Lichess eval LMDB for inspection.

Usage:
    cd backend
    export LICHESS_EVAL_DB_PATH=/path/to/output/lmdb_dir
    python -m lmdb_magic.dump [--limit 20]
"""

import argparse
import os
import sys

import lmdb

from .codec import decode_value

DB_NAME = b"evals"
MAP_SIZE = 64 * 1024**3


def run_dump(db_path: str, *, limit: int | None = None) -> None:
    env = lmdb.open(
        db_path,
        map_size=MAP_SIZE,
        subdir=True,
        max_dbs=2,
        readonly=True,
        lock=False,
        readahead=False,
    )
    evals_db = env.open_db(DB_NAME)

    with env.begin(db=evals_db) as txn:
        stat = txn.stat()
        total = stat["entries"]
        print(f"Total entries in DB: {total:,}")
        print()

        cursor = txn.cursor()
        count = 0
        for key_bytes, val_bytes in cursor:
            if limit is not None and count >= limit:
                break

            fen = key_bytes.decode("utf-8")
            val = decode_value(val_bytes)

            count += 1
            print(f"--- Record {count}/{total} ---")
            print(f"KEY (4-field FEN): {fen}")
            print(f"VALUE:")
            print(f"  depth:  {val.get('d')}")
            print(f"  knodes: {val.get('k')}")
            pvs = val.get("p") or []
            for i, pv in enumerate(pvs):
                cp_str = f"cp={pv.get('cp')}" if pv.get("cp") is not None else "cp=None"
                mate_str = f"mate={pv.get('m')}" if pv.get("m") is not None else "mate=None"
                line = pv.get("l", "")
                ply_count = len(line.split()) if line else 0
                print(f"  pvs[{i}]: {cp_str}  {mate_str}  plies={ply_count}  line=\"{line}\"")
            print(f"  raw value size: {len(val_bytes)} bytes")
            print()

    env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump Lichess eval LMDB entries")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to dump")
    args = parser.parse_args()

    db_path = os.environ.get("LICHESS_EVAL_DB_PATH")
    if not db_path:
        print("Error: LICHESS_EVAL_DB_PATH env var not set")
        sys.exit(1)
    if not os.path.exists(db_path):
        print(f"Error: DB not found at {db_path}")
        sys.exit(1)

    run_dump(db_path, limit=args.limit)


if __name__ == "__main__":
    main()
