"""Production must only allow the HTTPS site origins to make credentialed requests."""

from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import main


class CorsOriginsTest(unittest.TestCase):
    def tearDown(self) -> None:
        importlib.reload(main)

    def test_production_allows_only_https_site_origins(self) -> None:
        with patch.dict(os.environ, {"ENV": "production"}):
            importlib.reload(main)
        self.assertEqual(sorted(main.CORS_ORIGINS), ["https://korchess.com", "https://www.korchess.com"])

    def test_development_keeps_local_origins(self) -> None:
        with patch.dict(os.environ, {"ENV": "development"}):
            importlib.reload(main)
        self.assertIn("http://localhost:3000", main.CORS_ORIGINS)
        self.assertIn("https://korchess.com", main.CORS_ORIGINS)


if __name__ == "__main__":
    unittest.main()
