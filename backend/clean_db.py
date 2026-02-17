"""
Utility script to DROP all tables in the Postgres database used by Korchess.

WARNING: This is destructive.

- It will DROP (delete) ALL TABLES in the `public` schema of the target database,
  except `openings` and `opening_moves`. Tables are removed entirely, not just data.
- The app will recreate dropped tables on next connection (CREATE TABLE IF NOT EXISTS).
- Use only against a development or throwaway database.

Usage examples:

    # Use DATABASE_URL from environment
    python clean_db.py

    # Or pass the connection string explicitly
    python clean_db.py "postgresql://user:pass@host:5432/dbname"
"""

import os
import sys

import psycopg


def clean_database(database_url: str) -> None:
    """Drop all tables in the public schema except openings and opening_moves."""
    print("[clean_db] Connecting to database...")
    conn = psycopg.connect(database_url, autocommit=False, connect_timeout=5)
    try:
        cur = conn.cursor()

        EXCLUDED_TABLES = {"opening_moves", "openings"}
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        rows = cur.fetchall()
        table_names = [row[0] for row in rows if row[0] not in EXCLUDED_TABLES]

        if not table_names:
            print("[clean_db] No tables to drop (only excluded tables exist or schema is empty).")
            conn.rollback()
            return

        print(f"[clean_db] Excluded (kept): {', '.join(sorted(EXCLUDED_TABLES))}")
        print("[clean_db] The following tables will be DROPPED:")
        for name in table_names:
            print(f"  - {name}")

        joined = ", ".join(f'"{name}"' for name in table_names)
        sql = f"DROP TABLE IF EXISTS {joined} CASCADE;"
        print("[clean_db] Executing:", sql)
        cur.execute(sql)

        conn.commit()
        print("[clean_db] Done. All tables dropped except openings and opening_moves.")
    finally:
        conn.close()


def main(argv: list[str]) -> None:
    """Entry point for CLI usage."""
    if len(argv) > 1:
        database_url = argv[1]
    else:
        database_url = os.environ.get("DATABASE_URL", "")

    if not database_url:
        raise SystemExit(
            "DATABASE_URL is not set and no connection string was provided.\n"
            "Set the DATABASE_URL env var or run: python clean_db.py <connection_string>"
        )

    print(
        "[clean_db] WARNING: This will DROP (delete) ALL TABLES in the public schema "
        "of the target database, except 'openings' and 'opening_moves'."
    )
    print("[clean_db] Tables will be removed entirely. The app will recreate them on next run.")
    print(f"[clean_db] Target: {database_url!r}")
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("[clean_db] Aborted by user.")
        return

    clean_database(database_url)


if __name__ == "__main__":
    main(sys.argv)
