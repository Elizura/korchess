import csv
import io
import sqlite3
import sys
from pathlib import Path

import chess.pgn

from db import DATABASE_PATH


def init_db(conn: sqlite3.Connection) -> None:
  cur = conn.cursor()
  cur.execute(
    """
    CREATE TABLE IF NOT EXISTS openings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      eco TEXT NOT NULL,
      name TEXT NOT NULL,
      pgn TEXT NOT NULL,
      ply_count INTEGER NOT NULL,
      opening_key TEXT NOT NULL,
      opening_label TEXT NOT NULL,
      variation_key TEXT NOT NULL,
      variation_label TEXT NOT NULL
    );
    """
  )
  cur.execute(
    """
    CREATE TABLE IF NOT EXISTS opening_moves (
      opening_id INTEGER NOT NULL,
      ply_index INTEGER NOT NULL,
      uci TEXT NOT NULL,
      PRIMARY KEY (opening_id, ply_index),
      FOREIGN KEY (opening_id) REFERENCES openings(id)
    );
    """
  )
  cur.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_opening_moves_lookup
    ON opening_moves(ply_index, uci);
    """
  )
  conn.commit()
  print("finished initializing openings db")


def parse_pgn_to_uci(pgn_moves: str) -> list[str]:
  pgn_text = f"[Event \"?\"]\n\n{pgn_moves}\n"
  game = chess.pgn.read_game(io.StringIO(pgn_text))
  if game is None:
    raise ValueError("Unable to parse PGN")
  return [move.uci() for move in game.mainline_moves()]


def import_tsv_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
  openings_count = 0
  plies_count = 0
  cur = conn.cursor()

  with path.open("r", encoding="utf-8") as f:
    reader = csv.reader(f, delimiter="\t")
    for row_num, row in enumerate(reader, start=1):
      if not row or len(row) < 3:
        continue
      eco, name, pgn_moves = row[0].strip(), row[1].strip(), row[2].strip()
      if not eco or not name or not pgn_moves:
        continue
      try:
        uci_moves = parse_pgn_to_uci(pgn_moves)
      except Exception as exc:
        print(
          f"Warning: Skipping {path.name} line {row_num}: {exc}",
          file=sys.stderr,
        )
        continue

      parts = name.split(":")
      opening_part = parts[0]
      opening_key = (
        opening_part.lower().replace(" ", "_").replace("-", "_").strip()
      )
      opening_label = opening_part.strip()
      variation_key, variation_label = opening_key, opening_label
      if len(parts) > 1:
        variation_part = parts[1].split(",")[0].strip()
        variation_key = (
          variation_part.lower().replace(" ", "_").replace("-", "_").strip()
        )
        variation_label = variation_part.strip()
      cur.execute(
        "INSERT INTO openings (eco, name, pgn, ply_count, opening_key, opening_label, variation_key, variation_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
          eco,
          name,
          pgn_moves,
          len(uci_moves),
          opening_key,
          opening_label,
          variation_key,
          variation_label,
        ),
      )
      opening_id = cur.lastrowid
      cur.executemany(
        "INSERT INTO opening_moves (opening_id, ply_index, uci) VALUES (?, ?, ?)",
        [(opening_id, i, uci) for i, uci in enumerate(uci_moves)],
      )
      openings_count += 1
      plies_count += len(uci_moves)

  return openings_count, plies_count


def main() -> None:
  db_path = Path(DATABASE_PATH)
  conn = sqlite3.connect(db_path)
  try:
    init_db(conn)

    # Skip if already seeded
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM openings")
    (existing,) = cur.fetchone()
    if existing > 0:
      print("Openings already present; skipping seeding.")
      return

    total_openings = 0
    total_plies = 0

    base_dir = Path(__file__).resolve().parent
    for filename in ["a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv"]:
      path = base_dir / filename
      if not path.exists():
        print(f"Warning: Missing file {filename}, skipping.", file=sys.stderr)
        continue
      openings, plies = import_tsv_file(conn, path)
      total_openings += openings
      total_plies += plies

    conn.commit()
    print(f"Total openings imported: {total_openings}")
    print(f"Total plies stored: {total_plies}")
  finally:
    conn.close()

