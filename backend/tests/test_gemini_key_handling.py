"""The Gemini API key must travel in a header and never appear in URLs or logs."""

from __future__ import annotations

import os
import secrets
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

import httpx

from services import game_insights_narration as narration

API_KEY = secrets.token_urlsafe(24)


class GeminiKeyHandlingTest(unittest.TestCase):
    def _call(self, handler) -> tuple[list[httpx.Request], list[str]]:
        seen: list[httpx.Request] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        real_client = httpx.Client

        def client_factory(*args, **kwargs):
            return real_client(*args, transport=httpx.MockTransport(recording_handler), **kwargs)

        with patch.object(narration, "GEMINI_API_KEY", API_KEY), \
                patch.object(narration, "GEMINI_MIN_INTERVAL_MS", 0), \
                patch.object(narration.httpx, "Client", client_factory), \
                self.assertLogs(narration.LOGGER, level="WARNING") as logs:
            narration._gemini_request("system", "user", "game-1")
        return seen, logs.output

    def test_http_error_log_does_not_contain_key(self) -> None:
        seen, logs = self._call(lambda request: httpx.Response(500, json={"error": "boom"}))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].headers["x-goog-api-key"], API_KEY)
        self.assertNotIn(API_KEY, str(seen[0].url))
        self.assertTrue(logs)
        self.assertFalse(any(API_KEY in line for line in logs))

    def test_network_error_log_does_not_contain_key(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _, logs = self._call(fail)
        self.assertTrue(logs)
        self.assertFalse(any(API_KEY in line for line in logs))


if __name__ == "__main__":
    unittest.main()
