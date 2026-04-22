import csv
import io
import os
import sys
from pathlib import Path

import chess.pgn
import psycopg
from dotenv import load_dotenv


def parse_pgn_to_uci(pgn_moves: str) -> list[str]:
  pgn_text = f"[Event \"?\"]\n\n{pgn_moves}\n"
  game = chess.pgn.read_game(io.StringIO(pgn_text))
  if game is None:
    raise ValueError("Unable to parse PGN")
  return [move.uci() for move in game.mainline_moves()]


def import_tsv_file(conn: psycopg.Connection, path: Path) -> tuple[int, int]:
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
      print(f"inserting [{eco}] {name}")
      cur.execute(
        """
        INSERT INTO openings
          (eco, name, pgn, ply_count, opening_key, opening_label, variation_key, variation_label)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
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
      print(f"done instering inserted [{eco}] {name} {row_num}")
      opening_id = cur.fetchone()[0]
      cur.executemany(
        "INSERT INTO opening_moves (opening_id, ply_index, uci) VALUES (%s, %s, %s)",
        [(opening_id, i, uci) for i, uci in enumerate(uci_moves)],
      )
      openings_count += 1
      plies_count += len(uci_moves)

  return openings_count, plies_count


def main() -> None:
  load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

  database_url = os.environ.get("DATABASE_URL", "")
  if not database_url:
    raise RuntimeError("DATABASE_URL is not set.")
  conn = psycopg.connect(database_url, autocommit=False, connect_timeout=5)
  try:
    # Skip if already seeded
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM openings")
    existing = cur.fetchone()[0]
    if existing > 0:
      print("Openings already present; skipping seeding.")
      return

    total_openings = 0
    total_plies = 0

    base_dir = Path(__file__).resolve().parent.parent / "openings"
    ct = 0
    for filename in ["a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv"]:
      ct += 1
      path = base_dir / filename
      if not path.exists():
        print(f"Warning: Missing file {filename}, skipping.", file=sys.stderr)
        continue
      print(f"starting to import [{ct}] {filename}")
      openings, plies = import_tsv_file(conn, path)
      print(f"finished importing [{ct}] {filename}")
      total_openings += openings
      total_plies += plies

    conn.commit()
    print(f"Total openings imported: {total_openings}", flush=True)
    print(f"Total plies stored: {total_plies}", flush=True)
  finally:
    conn.close()


if __name__ == "__main__":
  main()
