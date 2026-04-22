"""
Utility script to wipe the Postgres database used by Korchess.

WARNING: This is destructive.

- It will remove ALL DATA from all tables in the `public` schema
  of the target database, except `openings` and `opening_moves`.
- Use only against a development or throwaway database.

Usage examples:

    # Use DATABASE_URL from environment
    python reset_db.py

    # Or pass the connection string explicitly
    python reset_db.py "postgresql://user:pass@host:5432/dbname"
"""

import os
import sys

import psycopg


def reset_database(database_url: str) -> None:
    """Truncate all tables in the public schema (CASCADE)."""
    print(f"[reset_db] Connecting to database...")
    conn = psycopg.connect(database_url, autocommit=False, connect_timeout=5)
    try:
        cur = conn.cursor()

        # Find all user tables in the public schema (excluding openings-related tables)
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
            print("[reset_db] No tables to truncate (only excluded tables exist or schema is empty).")
            conn.rollback()
            return

        if EXCLUDED_TABLES:
            print(f"[reset_db] Excluded from truncate: {', '.join(sorted(EXCLUDED_TABLES))}")
        print("[reset_db] The following tables will be truncated (CASCADE):")
        for name in table_names:
            print(f"  - {name}")

        joined = ", ".join(f'"{name}"' for name in table_names)
        sql = f"TRUNCATE TABLE {joined} CASCADE;"
        print("[reset_db] Executing:", sql)
        cur.execute(sql)

        conn.commit()
        print("[reset_db] Done. All data removed from public tables.")
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
            "Set the DATABASE_URL env var or run: python reset_db.py <connection_string>"
        )

    print(
        "[reset_db] WARNING: This will DELETE ALL DATA from all tables "
        "in the public schema of the target database."
    )
    print(f"[reset_db] Target: {database_url!r}")
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("[reset_db] Aborted by user.")
        return

    reset_database(database_url)


if __name__ == "__main__":
    main(sys.argv)

