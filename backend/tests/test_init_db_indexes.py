"""init_db must not create an index and then drop it again on every boot."""

from __future__ import annotations

import os
import re
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from repository import db

CREATE_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)
DROP_RE = re.compile(r"DROP\s+INDEX\s+IF\s+EXISTS\s+(\w+)", re.IGNORECASE)


class InitDbIndexesTest(unittest.TestCase):
    def test_no_index_is_both_created_and_dropped(self) -> None:
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.return_value = {"exists": 1}
        db._init_db_schema(conn)

        statements = [c.args[0] for c in cursor.execute.call_args_list]
        created = {name for sql in statements for name in CREATE_RE.findall(sql)}
        dropped = {name for sql in statements for name in DROP_RE.findall(sql)}
        self.assertIn("idx_scan_jobs_active", created)
        self.assertEqual(created & dropped, set())


if __name__ == "__main__":
    unittest.main()
