"""Ingest lichess_db_eval.jsonl.zst into an LMDB store.

Two-pass sort-then-insert pipeline:
    Pass 1: [Decompressor] -> [N Workers] -> accumulate & sort chunks to disk
    Pass 2: K-way merge sorted chunks -> LMDB cursor.putmulti(append=True)

Usage:
    cd backend
    export LICHESS_EVAL_SRC_PATH=/path/to/lichess_db_eval.jsonl.zst
    export LICHESS_EVAL_DB_PATH=/path/to/output/lmdb_dir
    python -m lmdb_magic.ingest [--workers 6] [--batch-size 100000] [--limit 10] [--restart]
"""

from __future__ import annotations

import argparse
import glob
import heapq
import io
import multiprocessing as mp
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from multiprocessing import Process, Queue
from typing import Any, Generator

import lmdb
import orjson
import zstandard

from .codec import transform_record, extract_depth

DB_NAME = b"evals"
META_DB_NAME = b"meta"
MAP_SIZE = 64 * 1024**3

SENTINEL = None
CHUNKS_SUBDIR = "_chunks"

DEFAULT_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))
DEFAULT_BATCH_SIZE = 100_000
DEFAULT_CHUNK_LINES = 10_000
DEFAULT_CHUNK_RECORDS = 5_000_000


# ---------------------------------------------------------------------------
# LMDB helpers
# ---------------------------------------------------------------------------

def _open_env(db_path: str, *, readonly: bool = False) -> lmdb.Environment:
    return lmdb.open(
        db_path,
        map_size=MAP_SIZE,
        subdir=True,
        max_dbs=2,
        readonly=readonly,
        lock=not readonly,
        readahead=False,
        writemap=not readonly,
        map_async=not readonly,
        sync=False,
        metasync=False,
    )


# ---------------------------------------------------------------------------
# Sorted chunk file I/O
# ---------------------------------------------------------------------------

def _chunk_dir(db_path: str) -> str:
    return os.path.join(db_path, CHUNKS_SUBDIR)


def _chunk_path(db_path: str, index: int) -> str:
    return os.path.join(_chunk_dir(db_path), f"chunk_{index:04d}.bin")


def _write_sorted_chunk(path: str, records: list[tuple[bytes, bytes]]) -> None:
    """Write a list of (key, val) pairs to a binary chunk file.

    Format per record: [4B key_len][key][4B val_len][val]
    Records must already be sorted by key.
    """
    with open(path, "wb") as f:
        for key, val in records:
            f.write(struct.pack("<I", len(key)))
            f.write(key)
            f.write(struct.pack("<I", len(val)))
            f.write(val)


def _read_sorted_chunk_iter(path: str) -> Generator[tuple[bytes, bytes], None, None]:
    """Yield (key, val) pairs from a sorted binary chunk file."""
    with open(path, "rb") as f:
        while True:
            hdr = f.read(4)
            if not hdr:
                return
            key_len = struct.unpack("<I", hdr)[0]
            key = f.read(key_len)
            val_len = struct.unpack("<I", f.read(4))[0]
            val = f.read(val_len)
            yield key, val


def _list_chunk_files(db_path: str) -> list[str]:
    """Return sorted list of chunk file paths."""
    pattern = os.path.join(_chunk_dir(db_path), "chunk_*.bin")
    return sorted(glob.glob(pattern))


# ---------------------------------------------------------------------------
# Stage 1: Decompressor process
# ---------------------------------------------------------------------------

def _decompressor(
    src_path: str,
    out_queue: Queue,
    chunk_lines: int,
    limit: int | None,
    stop_event: mp.Event,
):
    """Read .jsonl.zst, group lines into chunks, push to out_queue."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    with open(src_path, "rb") as fh:
        file_size = fh.seek(0, 2)
        fh.seek(0)

        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(fh)
        text_reader = io.TextIOWrapper(reader, encoding="utf-8")

        chunk: list[bytes] = []
        total_emitted_lines = 0

        for raw_line in text_reader:
            if stop_event.is_set():
                break

            stripped = raw_line.strip()
            if not stripped:
                continue

            chunk.append(stripped.encode("utf-8"))

            if len(chunk) >= chunk_lines:
                compressed_pos = fh.tell()
                pct = (compressed_pos / file_size * 100) if file_size > 0 else 0
                out_queue.put((chunk, compressed_pos, pct))
                total_emitted_lines += len(chunk)
                if limit is not None and total_emitted_lines >= limit:
                    break
                chunk = []

        if chunk and not stop_event.is_set():
            if limit is None or total_emitted_lines < limit:
                compressed_pos = fh.tell()
                pct = (compressed_pos / file_size * 100) if file_size > 0 else 0
                out_queue.put((chunk, compressed_pos, pct))

    out_queue.put(SENTINEL)


# ---------------------------------------------------------------------------
# Stage 2: Worker processes
# ---------------------------------------------------------------------------

def _worker(
    in_queue: Queue,
    out_queue: Queue,
    worker_id: int,
):
    """Pull line-chunks, parse JSON, transform, push (key, val) batches."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    while True:
        item = in_queue.get()
        if item is SENTINEL:
            in_queue.put(SENTINEL)
            break

        lines, compressed_pos, pct = item
        results: list[tuple[bytes, bytes]] = []

        for raw in lines:
            try:
                rec = orjson.loads(raw)
            except Exception:
                continue
            pair = transform_record(rec)
            if pair is not None:
                results.append(pair)

        out_queue.put((results, compressed_pos, pct))

    out_queue.put(SENTINEL)


# ---------------------------------------------------------------------------
# Pass 1: Extract, transform, sort chunks, write to disk
# ---------------------------------------------------------------------------

def _run_pass1(
    src_path: str,
    db_path: str,
    *,
    num_workers: int,
    chunk_lines: int,
    chunk_records: int,
    limit: int | None,
    stop_event: mp.Event,
) -> int:
    """Run pass 1: decompress -> parse -> accumulate -> sort -> write chunk files.

    Returns the number of chunk files written.
    """
    print("=== Pass 1: Extract, transform, sort chunks ===")
    print()

    chunks_dir = _chunk_dir(db_path)
    os.makedirs(chunks_dir, exist_ok=True)

    decomp_queue: Queue = Queue(maxsize=64)
    worker_queue: Queue = Queue(maxsize=32)

    decomp_proc = Process(
        target=_decompressor,
        args=(src_path, decomp_queue, chunk_lines, limit, stop_event),
        daemon=True,
    )
    decomp_proc.start()

    workers: list[Process] = []
    for i in range(num_workers):
        p = Process(target=_worker, args=(decomp_queue, worker_queue, i), daemon=True)
        p.start()
        workers.append(p)

    t0 = time.time()
    accumulator: list[tuple[bytes, bytes]] = []
    chunk_index = 0
    total_records = 0
    sentinels_received = 0
    last_report = time.time()
    last_pct = 0.0

    while sentinels_received < num_workers:
        if stop_event.is_set():
            break

        item = worker_queue.get()
        if item is SENTINEL:
            sentinels_received += 1
            continue

        results, compressed_pos, pct = item
        accumulator.extend(results)
        total_records += len(results)
        last_pct = pct

        if len(accumulator) >= chunk_records:
            t_sort = time.time()
            accumulator.sort(key=lambda pair: pair[0])
            sort_sec = time.time() - t_sort

            path = _chunk_path(db_path, chunk_index)
            _write_sorted_chunk(path, accumulator)
            print(
                f"  Chunk {chunk_index:>3}: {len(accumulator):>10,} records | "
                f"sort {sort_sec:.1f}s | total {total_records:>12,} | [{last_pct:5.1f}%]"
            )
            chunk_index += 1
            accumulator = []

        now = time.time()
        if now - last_report >= 30.0:
            elapsed = now - t0
            rate = total_records / elapsed if elapsed > 0 else 0
            print(
                f"  [{last_pct:5.1f}%]  {total_records:>12,} extracted | "
                f"{rate:>10,.0f} rec/s | {chunk_index} chunks written"
            )
            last_report = now

    # Flush remaining accumulator
    if accumulator and not stop_event.is_set():
        accumulator.sort(key=lambda pair: pair[0])
        path = _chunk_path(db_path, chunk_index)
        _write_sorted_chunk(path, accumulator)
        print(
            f"  Chunk {chunk_index:>3}: {len(accumulator):>10,} records (final) | "
            f"total {total_records:>12,} | [{last_pct:5.1f}%]"
        )
        chunk_index += 1
        accumulator = []

    # Clean up processes
    decomp_proc.join(timeout=10)
    for p in workers:
        p.join(timeout=10)

    elapsed = time.time() - t0
    rate = total_records / elapsed if elapsed > 0 else 0
    print()
    print(f"Pass 1 {'stopped' if stop_event.is_set() else 'completed'} in {elapsed:.1f}s")
    print(f"  Total records: {total_records:,}")
    print(f"  Chunk files:   {chunk_index}")
    print(f"  Avg rate:      {rate:,.0f} rec/s")
    print()

    return chunk_index


# ---------------------------------------------------------------------------
# Pass 2: K-way merge sorted chunks into LMDB with append=True
# ---------------------------------------------------------------------------

MERGE_GROUP_SIZE = 10


def _merge_chunk_group(
    input_paths: list[str],
    output_path: str,
) -> int:
    """Merge a group of sorted chunk files into one sorted output file.

    Returns the number of records written. Deletes each input file
    as soon as its iterator is exhausted.
    """
    live_files: set[str] = set(input_paths)

    def _iter_and_delete(path: str):
        yield from _read_sorted_chunk_iter(path)
        try:
            os.remove(path)
        except OSError:
            pass
        live_files.discard(path)

    iterators = [_iter_and_delete(p) for p in input_paths]
    merged = heapq.merge(*iterators, key=lambda pair: pair[0])

    count = 0
    with open(output_path, "wb") as f:
        for key, val in merged:
            f.write(struct.pack("<I", len(key)))
            f.write(key)
            f.write(struct.pack("<I", len(val)))
            f.write(val)
            count += 1

    # Safety: delete any input files not yet removed
    for p in list(live_files):
        try:
            os.remove(p)
        except OSError:
            pass

    return count


def _reduce_chunks(
    db_path: str,
    stop_event: mp.Event,
) -> list[str]:
    """Reduce many chunk files down to a small number via multi-round merges.

    Merges groups of MERGE_GROUP_SIZE into intermediate files, deleting
    originals immediately. Repeats rounds until <= MERGE_GROUP_SIZE files remain.

    Returns the list of final chunk file paths ready for LMDB insertion.
    """
    current_files = _list_chunk_files(db_path)
    # Also pick up any intermediate files from a previous interrupted reduce
    inter_pattern = os.path.join(_chunk_dir(db_path), "inter_*.bin")
    current_files.extend(sorted(glob.glob(inter_pattern)))
    current_files = sorted(set(current_files))

    if len(current_files) <= MERGE_GROUP_SIZE:
        return current_files

    round_num = 0
    inter_index = 0

    while len(current_files) > MERGE_GROUP_SIZE:
        if stop_event.is_set():
            return current_files

        round_num += 1
        groups = [
            current_files[i:i + MERGE_GROUP_SIZE]
            for i in range(0, len(current_files), MERGE_GROUP_SIZE)
        ]
        print(f"  Reduce round {round_num}: {len(current_files)} files -> {len(groups)} merged files")

        next_files: list[str] = []
        for gi, group in enumerate(groups):
            if stop_event.is_set():
                next_files.extend(group)
                continue

            if len(group) == 1:
                next_files.append(group[0])
                continue

            out_path = os.path.join(_chunk_dir(db_path), f"inter_{inter_index:04d}.bin")
            inter_index += 1

            t0 = time.time()
            count = _merge_chunk_group(group, out_path)
            elapsed = time.time() - t0
            freed_names = [os.path.basename(p) for p in group]
            print(
                f"    Group {gi}: merged {len(group)} files ({count:,} records) "
                f"in {elapsed:.1f}s — deleted {freed_names[0]}..{freed_names[-1]}"
            )
            next_files.append(out_path)

        current_files = sorted(next_files)

    return current_files


def _run_pass2(
    db_path: str,
    *,
    batch_size: int,
    stop_event: mp.Event,
) -> int:
    """Run pass 2: reduce chunk files, then merge into LMDB with append=True.

    Returns total records written to LMDB.
    """
    chunk_files = _list_chunk_files(db_path)
    inter_pattern = os.path.join(_chunk_dir(db_path), "inter_*.bin")
    inter_files = sorted(glob.glob(inter_pattern))
    all_files = sorted(set(chunk_files + inter_files))

    if not all_files:
        print("No chunk files found — nothing to merge.")
        return 0

    print(f"=== Pass 2: Merge {len(all_files)} sorted chunks into LMDB ===")
    print()

    # Step 1: Reduce to a small number of files (deletes originals as it goes)
    final_files = _reduce_chunks(db_path, stop_event)
    if stop_event.is_set():
        return 0

    print()
    print(f"  Final merge: {len(final_files)} files into LMDB")
    print()

    # Step 2: Remove existing LMDB data if present (pass 2 always rebuilds)
    os.makedirs(db_path, exist_ok=True)
    data_mdb = os.path.join(db_path, "data.mdb")
    lock_mdb = os.path.join(db_path, "lock.mdb")
    if os.path.exists(data_mdb):
        os.remove(data_mdb)
    if os.path.exists(lock_mdb):
        os.remove(lock_mdb)

    env = _open_env(db_path)
    evals_db = env.open_db(DB_NAME)

    # Step 3: K-way merge the small set of final files into LMDB,
    # deleting each input file as its iterator exhausts to free disk.
    live_files: set[str] = set(final_files)

    def _iter_and_delete(path: str):
        yield from _read_sorted_chunk_iter(path)
        try:
            os.remove(path)
        except OSError:
            pass
        live_files.discard(path)

    iterators = [_iter_and_delete(f) for f in final_files]
    merged = heapq.merge(*iterators, key=lambda pair: pair[0])

    t0 = time.time()
    written = 0
    dedup_skipped = 0
    batch: list[tuple[bytes, bytes]] = []
    prev_key: bytes | None = None
    prev_val: bytes | None = None
    last_report = time.time()

    for key, val in merged:
        if stop_event.is_set():
            break

        if key == prev_key:
            if prev_val is not None and extract_depth(val) > extract_depth(prev_val):
                prev_val = val
                if batch:
                    batch[-1] = (key, val)
            dedup_skipped += 1
            continue

        if prev_key is not None and prev_val is not None:
            batch.append((prev_key, prev_val))

        prev_key = key
        prev_val = val

        if len(batch) >= batch_size:
            with env.begin(write=True) as txn:
                txn.cursor(db=evals_db).putmulti(batch, append=True)
            written += len(batch)
            batch = []

        now = time.time()
        if now - last_report >= 15.0:
            elapsed = now - t0
            rate = written / elapsed if elapsed > 0 else 0
            remaining_files = len(live_files)
            print(
                f"  {written:>12,} written | {rate:>10,.0f} rec/s | "
                f"dedup: {dedup_skipped:,} | files remaining: {remaining_files}"
            )
            last_report = now

    if prev_key is not None and prev_val is not None:
        batch.append((prev_key, prev_val))

    if batch:
        with env.begin(write=True) as txn:
            txn.cursor(db=evals_db).putmulti(batch, append=True)
        written += len(batch)

    env.sync(True)
    env.close()

    # Safety: delete any remaining input files
    for path in list(live_files):
        try:
            os.remove(path)
        except OSError:
            pass

    chunks_dir = _chunk_dir(db_path)
    try:
        os.rmdir(chunks_dir)
    except OSError:
        pass

    elapsed = time.time() - t0
    rate = written / elapsed if elapsed > 0 else 0
    print()
    print(f"Pass 2 {'stopped' if stop_event.is_set() else 'completed'} in {elapsed:.1f}s")
    print(f"  Records written: {written:,}")
    print(f"  Dedup skipped:   {dedup_skipped:,}")
    print(f"  Avg rate:        {rate:,.0f} rec/s")
    print()

    return written


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN_FENS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -",
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq -",
    "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkb1r/pppppppp/5n2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppppppp/8/8/2P5/8/PP1PPPPP/RNBQKBNR b KQkq -",
    "rnbqkbnr/pppp1ppp/4p3/8/3PP3/8/PPP2PPP/RNBQKBNR b KQkq -",
    "r1bqkbnr/pppppppp/2n5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppppppp/8/8/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq -",
    "r1bqkb1r/pppppppp/2n2n2/8/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq -",
    "rnbqkbnr/pppppp1p/6p1/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pp1ppppp/2p5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppppppp/8/8/6P1/8/PPPPPP1P/RNBQKBNR b KQkq -",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "8/8/8/8/8/8/8/8 w - -",
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq -",
    "rnbqkb1r/pppppppp/5n2/8/3P4/8/PPP1PPPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
    "rnbqkbnr/pppp1ppp/8/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR b KQkq -",
]


def _run_validation(db_path: str) -> None:
    print("--- Validation ---")
    env = _open_env(db_path, readonly=True)
    evals_db = env.open_db(DB_NAME)

    with env.begin(db=evals_db) as txn:
        stat = txn.stat()
        print(f"DB entries: {stat['entries']:,}")

        hits = 0
        for fen in KNOWN_FENS:
            raw = txn.get(fen.encode("utf-8"))
            if raw:
                depth = extract_depth(raw)
                hits += 1
                print(f"  HIT  depth={depth:>3}  {fen[:60]}")
            else:
                print(f"  MISS              {fen[:60]}")
        print(f"Known-FEN hits: {hits}/{len(KNOWN_FENS)}")

    env.close()

    try:
        result = subprocess.run(
            ["du", "-sh", db_path], capture_output=True, text=True, timeout=10,
        )
        size_str = result.stdout.split()[0] if result.stdout else "?"
        print(f"On-disk size: {size_str}")
    except Exception:
        print("On-disk size: (could not determine)")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_ingest(
    src_path: str,
    db_path: str,
    *,
    num_workers: int = DEFAULT_WORKERS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    chunk_records: int = DEFAULT_CHUNK_RECORDS,
    limit: int | None = None,
) -> None:
    print(f"Source:         {src_path}")
    print(f"DB dir:         {db_path}")
    print(f"Workers:        {num_workers}")
    print(f"Batch size:     {batch_size:,}")
    print(f"Chunk lines:    {chunk_lines:,}")
    print(f"Chunk records:  {chunk_records:,}")
    print(f"Limit:          {limit or 'none (full ingest)'}")
    print()

    os.makedirs(db_path, exist_ok=True)

    stop_event = mp.Event()
    original_sigint = signal.getsignal(signal.SIGINT)

    def _handle_sigint(signum, frame):
        print("\n  SIGINT received — shutting down after current operation...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    t_total = time.time()

    # Check if chunk files already exist (resume into pass 2)
    existing_chunks = _list_chunk_files(db_path)
    if existing_chunks:
        print(f"Found {len(existing_chunks)} existing chunk files — skipping to pass 2")
        print()
    else:
        _run_pass1(
            src_path,
            db_path,
            num_workers=num_workers,
            chunk_lines=chunk_lines,
            chunk_records=chunk_records,
            limit=limit,
            stop_event=stop_event,
        )

    if not stop_event.is_set():
        _run_pass2(
            db_path,
            batch_size=batch_size,
            stop_event=stop_event,
        )

    signal.signal(signal.SIGINT, original_sigint)

    elapsed_total = time.time() - t_total
    print(f"Total time: {elapsed_total:.1f}s ({elapsed_total / 60:.1f}m)")
    print()

    if not stop_event.is_set():
        _run_validation(db_path)


def main() -> None:
    mp.set_start_method("spawn", force=True)

    parser = argparse.ArgumentParser(description="Ingest Lichess eval JSONL into LMDB")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Number of parser workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"LMDB write batch size (default: {DEFAULT_BATCH_SIZE:,})")
    parser.add_argument("--chunk-lines", type=int, default=DEFAULT_CHUNK_LINES,
                        help=f"Lines per decompressor chunk (default: {DEFAULT_CHUNK_LINES:,})")
    parser.add_argument("--chunk-records", type=int, default=DEFAULT_CHUNK_RECORDS,
                        help=f"Records per sort chunk (default: {DEFAULT_CHUNK_RECORDS:,})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after ~N lines read from source")
    parser.add_argument("--restart", action="store_true",
                        help="Wipe existing DB and chunk files, start fresh")
    args = parser.parse_args()

    src_path = os.environ.get("LICHESS_EVAL_SRC_PATH")
    db_path = os.environ.get("LICHESS_EVAL_DB_PATH")

    if not src_path:
        print("Error: LICHESS_EVAL_SRC_PATH env var not set")
        sys.exit(1)
    if not db_path:
        print("Error: LICHESS_EVAL_DB_PATH env var not set")
        sys.exit(1)
    if not os.path.exists(src_path):
        print(f"Error: source file not found: {src_path}")
        sys.exit(1)

    if args.restart and os.path.exists(db_path):
        print(f"--restart: wiping {db_path}")
        shutil.rmtree(db_path)

    run_ingest(
        src_path,
        db_path,
        num_workers=args.workers,
        batch_size=args.batch_size,
        chunk_lines=args.chunk_lines,
        chunk_records=args.chunk_records,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
