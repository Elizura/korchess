"""Analytics must use the IP nginx saw, validate it, and keep the geo cache bounded."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from starlette.requests import Request

from services import analytics


def _request(headers: dict[str, str], client_host: str = "172.18.0.5") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (client_host, 12345),
        }
    )


class ExtractClientIpTest(unittest.TestCase):
    def test_prefers_x_real_ip_over_spoofed_forwarded_for(self) -> None:
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9", "X-Real-IP": "203.0.113.9"})
        self.assertEqual(analytics._extract_client_ip(request), "203.0.113.9")

    def test_uses_last_forwarded_for_hop_without_x_real_ip(self) -> None:
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
        self.assertEqual(analytics._extract_client_ip(request), "203.0.113.9")

    def test_falls_back_to_socket_peer(self) -> None:
        self.assertEqual(analytics._extract_client_ip(_request({})), "172.18.0.5")

    def test_rejects_values_that_are_not_ip_addresses(self) -> None:
        request = _request(
            {"X-Real-IP": "8.8.8.8/../../evil", "X-Forwarded-For": "x?token=1"},
            client_host="testclient",
        )
        self.assertIsNone(analytics._extract_client_ip(request))

    def test_normalizes_ipv6(self) -> None:
        request = _request({"X-Real-IP": "2001:DB8:0:0::1"})
        self.assertEqual(analytics._extract_client_ip(request), "2001:db8::1")


class GeoLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        analytics._GEO_CACHE.clear()
        self.addCleanup(analytics._GEO_CACHE.clear)

    def _lookup(self, ip: str | None, client: MagicMock) -> dict:
        with patch.object(analytics, "IPINFO_TOKEN", "t"), \
                patch.object(analytics.httpx, "AsyncClient", return_value=client):
            return asyncio.run(analytics._lookup_geo_from_ip(ip))

    def _client(self) -> MagicMock:
        response = MagicMock(status_code=200)
        response.json.return_value = {"country": "NL", "city": "Amsterdam"}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        return client

    def test_invalid_or_private_ips_are_never_looked_up(self) -> None:
        client = self._client()
        for ip in ("not-an-ip", "8.8.8.8/json?x=", "10.0.0.1", "127.0.0.1", None):
            self.assertEqual(self._lookup(ip, client), {"country": None, "city": None})
        client.get.assert_not_called()

    def test_public_ip_lookup_uses_normalized_address(self) -> None:
        client = self._client()
        self.assertEqual(self._lookup("8.8.8.8", client)["country"], "NL")
        self.assertEqual(client.get.call_args.args[0], "https://ipinfo.io/8.8.8.8/json")

    def test_cache_is_bounded(self) -> None:
        with patch.object(analytics, "_GEO_CACHE_MAX_ENTRIES", 3), \
                patch.object(analytics, "IPINFO_TOKEN", ""):
            for last_octet in range(1, 6):
                asyncio.run(analytics._lookup_geo_from_ip(f"8.8.8.{last_octet}"))
        self.assertEqual(list(analytics._GEO_CACHE), ["8.8.8.3", "8.8.8.4", "8.8.8.5"])


if __name__ == "__main__":
    unittest.main()
