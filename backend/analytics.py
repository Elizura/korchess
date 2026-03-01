"""Analytics ingestion, enrichment, and PostHog mirroring."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Request

ANALYTICS_EVENT_VERSION = "v1"
EVENT_NAME_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_]+){1,3}$")
SENSITIVE_PROPERTY_KEYS = {
    "token",
    "auth_token",
    "access_token",
    "id_token",
    "password",
    "email",
    "pgn",
}

HASH_SALT = os.environ.get("ANALYTICS_HASH_SALT", "analytics-salt-change-me")
POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "").strip()
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").rstrip("/")
POSTHOG_TIMEOUT_S = max(2, int(os.environ.get("POSTHOG_TIMEOUT_S", "4")))
IPINFO_TOKEN = os.environ.get("ANALYTICS_IPINFO_TOKEN", "").strip()
IPINFO_TIMEOUT_S = max(1, int(os.environ.get("ANALYTICS_IPINFO_TIMEOUT_S", "2")))
ANALYTICS_ENABLED_ENV = os.environ.get("ANALYTICS_ENABLED", "").strip().lower()

_GEO_CACHE_TTL = timedelta(hours=6)
_GEO_CACHE: dict[str, tuple[datetime, dict[str, str | None]]] = {}
logger = logging.getLogger(__name__)


class AnalyticsValidationError(ValueError):
    """Raised when incoming analytics payload is invalid."""


def _is_production_environment() -> bool:
    for key in ("ENVIRONMENT", "APP_ENV", "NODE_ENV"):
        if os.environ.get(key, "").strip().lower() == "production":
            return True
    return False


def _analytics_env_enabled() -> bool:
    # Never emit analytics outside production, regardless of explicit env toggles.
    if not _is_production_environment():
        return False
    if ANALYTICS_ENABLED_ENV in {"0", "false", "no", "off"}:
        return False
    return True


def _is_local_hostname(hostname: str) -> bool:
    value = (hostname or "").strip().lower()
    if not value:
        return False
    if ":" in value:
        value = value.split(":", 1)[0]
    return value in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _analytics_enabled_for_request(request: Request | None) -> bool:
    if not _analytics_env_enabled():
        return False
    if request is None:
        return True
    host = request.headers.get("host", "")
    return not _is_local_hostname(host)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso_datetime(value: str | None) -> datetime:
    if not value:
        return _utc_now()
    candidate = value
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return _utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_value(raw: str) -> str:
    material = f"{HASH_SALT}:{raw}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def hash_username(username: str | None) -> str | None:
    if not username:
        return None
    normalized = username.strip().lower()
    if not normalized:
        return None
    return normalized


def _extract_client_ip(request: Request | None) -> str | None:
    if request is None:
        return None

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return None


def _ip_prefix_hash(ip_text: str | None) -> str | None:
    if not ip_text:
        return None
    try:
        parsed = ipaddress.ip_address(ip_text)
    except ValueError:
        return None

    if isinstance(parsed, ipaddress.IPv4Address):
        network = ipaddress.IPv4Network(f"{parsed}/24", strict=False)
    else:
        network = ipaddress.IPv6Network(f"{parsed}/48", strict=False)

    return _hash_value(f"ip-prefix:{network.network_address}/{network.prefixlen}")


def _classify_referrer(referrer: str | None) -> tuple[str, str | None]:
    if not referrer:
        return "direct", None

    try:
        parsed = urlparse(referrer)
    except Exception:
        return "other", None

    domain = (parsed.netloc or "").lower()
    if not domain:
        return "direct", None

    if "google." in domain:
        return "google", domain
    if "twitter.com" in domain or "x.com" in domain:
        return "twitter", domain
    if "reddit.com" in domain:
        return "reddit", domain
    if "facebook.com" in domain:
        return "facebook", domain
    if "linkedin.com" in domain:
        return "linkedin", domain

    return "referral", domain


def _classify_device_and_browser(user_agent: str | None) -> dict[str, str | None]:
    ua = (user_agent or "").lower()

    if not ua:
        return {
            "device_type": "unknown",
            "browser": "unknown",
            "os": "unknown",
        }

    if "ipad" in ua or "tablet" in ua:
        device_type = "tablet"
    elif "mobi" in ua or "iphone" in ua or "android" in ua:
        device_type = "mobile"
    else:
        device_type = "desktop"

    if "edg/" in ua:
        browser = "edge"
    elif "chrome/" in ua and "safari/" in ua:
        browser = "chrome"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "safari"
    elif "firefox/" in ua:
        browser = "firefox"
    else:
        browser = "other"

    if "windows" in ua:
        os_name = "windows"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macos"
    elif "android" in ua:
        os_name = "android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        os_name = "ios"
    elif "linux" in ua:
        os_name = "linux"
    else:
        os_name = "other"

    return {
        "device_type": device_type,
        "browser": browser,
        "os": os_name,
    }


async def _lookup_geo_from_ip(ip_text: str | None) -> dict[str, str | None]:
    if not ip_text:
        return {"country": None, "city": None}

    now = _utc_now()
    cached = _GEO_CACHE.get(ip_text)
    if cached and cached[0] > now:
        return cached[1]

    if not IPINFO_TOKEN:
        result = {"country": None, "city": None}
        _GEO_CACHE[ip_text] = (now + _GEO_CACHE_TTL, result)
        return result

    try:
        async with httpx.AsyncClient(timeout=IPINFO_TIMEOUT_S) as client:
            res = await client.get(
                f"https://ipinfo.io/{ip_text}/json",
                params={"token": IPINFO_TOKEN},
            )
        if res.status_code >= 400:
            raise RuntimeError("geo lookup failed")
        data = res.json()
        result = {
            "country": data.get("country"),
            "city": data.get("city"),
        }
    except Exception:
        result = {"country": None, "city": None}

    _GEO_CACHE[ip_text] = (now + _GEO_CACHE_TTL, result)
    return result


def _sanitize_properties(properties: Any) -> dict[str, Any]:
    if not isinstance(properties, dict):
        return {}

    sanitized: dict[str, Any] = {}
    for key, value in properties.items():
        key_str = str(key)
        lower_key = key_str.lower()
        if lower_key in SENSITIVE_PROPERTY_KEYS:
            continue
        if "token" in lower_key or "password" in lower_key:
            continue
        if lower_key in {"username_hash", "username"}:
            username = hash_username(str(value) if value is not None else None)
            if username:
                sanitized["username"] = username
            continue

        sanitized[key_str] = value
    return sanitized


def _normalize_base_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    event_name = str(raw_event.get("event_name") or "").strip().lower()
    if not EVENT_NAME_RE.match(event_name):
        raise AnalyticsValidationError("Invalid event_name format.")

    anonymous_id = str(raw_event.get("anonymous_id") or "").strip()
    session_id = str(raw_event.get("session_id") or "").strip()
    if not anonymous_id or not session_id:
        raise AnalyticsValidationError("anonymous_id and session_id are required.")

    return {
        "event_id": str(raw_event.get("event_id") or uuid.uuid4()),
        "event_name": event_name,
        "event_version": str(raw_event.get("event_version") or ANALYTICS_EVENT_VERSION),
        "occurred_at": _parse_iso_datetime(raw_event.get("occurred_at")),
        "anonymous_id": anonymous_id,
        "session_id": session_id,
        "path": (raw_event.get("path") or "")[:512] or None,
        "url": (raw_event.get("url") or "")[:2048] or None,
        "referrer": (raw_event.get("referrer") or "")[:2048] or None,
        "is_first_time": bool(raw_event.get("is_first_time")),
        "properties": _sanitize_properties(raw_event.get("properties")),
    }


async def build_enriched_event(
    raw_event: dict[str, Any],
    request: Request | None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Validate and enrich a raw event with server-derived metadata."""
    base = _normalize_base_event(raw_event)

    referrer = base.get("referrer") or (request.headers.get("referer") if request else None)
    referrer_type, referrer_domain = _classify_referrer(referrer)

    user_agent = (raw_event.get("user_agent") or (request.headers.get("user-agent") if request else ""))
    device_ctx = _classify_device_and_browser(user_agent)

    ip = _extract_client_ip(request)
    ip_prefix_hash = _ip_prefix_hash(ip)

    country_from_headers = None
    city_from_headers = None
    if request:
        country_from_headers = (
            request.headers.get("cf-ipcountry")
            or request.headers.get("x-country")
            or request.headers.get("x-geo-country")
        )
        city_from_headers = (
            request.headers.get("x-city")
            or request.headers.get("x-geo-city")
            or request.headers.get("cf-ipcity")
        )

    geo = await _lookup_geo_from_ip(ip)
    country = country_from_headers or geo.get("country")
    city = city_from_headers or geo.get("city")

    enriched = {
        **base,
        "user_id": user_id,
        "referrer": referrer,
        "referrer_type": referrer_type,
        "referrer_domain": referrer_domain,
        "country": country,
        "city": city,
        "device_type": device_ctx["device_type"],
        "browser": device_ctx["browser"],
        "os": device_ctx["os"],
        "ip_prefix_hash": ip_prefix_hash,
    }
    return enriched


def persist_enriched_event(conn, event: dict[str, Any]) -> None:
    """PostHog-only mode: skip all database analytics persistence."""
    del conn
    del event


def _posthog_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _build_posthog_base_properties(event: dict[str, Any], distinct_id: str) -> dict[str, Any]:
    return {
        **(event.get("properties") or {}),
        "distinct_id": distinct_id,
        "$session_id": event.get("session_id"),
        "$device_id": event.get("anonymous_id"),
        "$set_once": {
            "first_referrer_type": event.get("referrer_type"),
        },
        "$set": {
            "referrer_type": event.get("referrer_type"),
            "country": event.get("country"),
            "city": event.get("city"),
            "device_type": event.get("device_type"),
            "browser": event.get("browser"),
            "os": event.get("os"),
        },
        "$lib": "korchess-web",
        "event_id": event.get("event_id"),
        "event_version": event.get("event_version"),
        "anonymous_id": event.get("anonymous_id"),
        "session_id": event.get("session_id"),
        "$current_url": event.get("url"),
        "$pathname": event.get("path"),
        "$referrer": event.get("referrer"),
        "referrer_type": event.get("referrer_type"),
        "country": event.get("country"),
        "city": event.get("city"),
        "device_type": event.get("device_type"),
        "browser": event.get("browser"),
        "os": event.get("os"),
        "is_first_time": event.get("is_first_time"),
    }


def build_posthog_batch(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate internal events to PostHog batch payload.

    Notes:
    - Keep custom events for debugging/exploration.
    - Also emit PostHog-native events/properties so sessions + lifecycle metrics work.
    """
    payloads: list[dict[str, Any]] = []

    for event in events:
        distinct_id = event.get("user_id") or event.get("anonymous_id")
        if not distinct_id:
            continue

        timestamp = _posthog_timestamp(event.get("occurred_at"))
        base_properties = _build_posthog_base_properties(event, str(distinct_id))

        # 1) Always keep original custom event.
        payloads.append(
            {
                "event": event.get("event_name"),
                "distinct_id": distinct_id,
                "timestamp": timestamp,
                "properties": base_properties,
            }
        )

        # 2) Emit PostHog-native pageview event so Sessions/DAU/WAU can build.
        if event.get("event_name") == "page.view":
            payloads.append(
                {
                    "event": "$pageview",
                    "distinct_id": distinct_id,
                    "timestamp": timestamp,
                    "properties": {
                        **base_properties,
                        "source_event_name": "page.view",
                    },
                }
            )

        # 3) Emit PostHog identify event to merge anon+auth histories.
        if event.get("event_name") == "identity.linked" and event.get("user_id") and event.get("anonymous_id"):
            payloads.append(
                {
                    "event": "$identify",
                    "distinct_id": event.get("user_id"),
                    "timestamp": timestamp,
                    "properties": {
                        **base_properties,
                        "distinct_id": event.get("user_id"),
                        "$anon_distinct_id": event.get("anonymous_id"),
                        "source_event_name": "identity.linked",
                    },
                }
            )

    return payloads


async def mirror_events_to_posthog(events: list[dict[str, Any]]) -> None:
    """Mirror events asynchronously to PostHog."""
    if not _analytics_env_enabled() or not POSTHOG_API_KEY or not events:
        return

    payload = {
        "api_key": POSTHOG_API_KEY,
        "batch": build_posthog_batch(events),
    }

    try:
        async with httpx.AsyncClient(timeout=POSTHOG_TIMEOUT_S) as client:
            response = await client.post(f"{POSTHOG_HOST}/batch/", json=payload)
            if response.status_code >= 300:
                logger.warning(
                    "PostHog batch mirror failed with status=%s body=%s",
                    response.status_code,
                    response.text[:300],
                )
    except Exception:
        # Analytics mirror should never break product paths.
        return


async def ingest_client_events(
    conn,
    *,
    raw_events: list[dict[str, Any]],
    request: Request | None,
    user_id: str | None,
) -> list[dict[str, Any]]:
    """Validate, enrich, and persist a batch of client events."""
    if not _analytics_enabled_for_request(request):
        return []

    enriched_events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        enriched = await build_enriched_event(raw_event, request=request, user_id=user_id)
        persist_enriched_event(conn, enriched)
        enriched_events.append(enriched)
    return enriched_events


async def track_server_event(
    conn,
    *,
    event_name: str,
    user_id: str | None,
    request: Request | None = None,
    properties: dict[str, Any] | None = None,
    anonymous_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist a server-originated event and mirror it to PostHog."""
    if not _analytics_enabled_for_request(request):
        return {
            "event_name": event_name,
            "user_id": user_id,
            "skipped": True,
        }

    anon = anonymous_id or (request.headers.get("x-anonymous-id") if request else None)
    sess = session_id or (request.headers.get("x-session-id") if request else None)

    if not anon:
        if user_id:
            anon = f"srv:{_hash_value(f'user:{user_id}')[:24]}"
        else:
            anon = f"srv:{uuid.uuid4()}"

    if not sess:
        sess = f"srv-session:{uuid.uuid4()}"

    raw_event = {
        "event_id": str(uuid.uuid4()),
        "event_name": event_name,
        "event_version": ANALYTICS_EVENT_VERSION,
        "occurred_at": _utc_now_iso(),
        "anonymous_id": anon,
        "session_id": sess,
        "path": request.url.path if request else None,
        "url": str(request.url) if request else None,
        "referrer": request.headers.get("referer") if request else None,
        "is_first_time": False,
        "properties": properties or {},
    }

    enriched = await build_enriched_event(raw_event, request=request, user_id=user_id)
    persist_enriched_event(conn, enriched)

    try:
        asyncio.create_task(mirror_events_to_posthog([enriched]))
    except Exception:
        pass

    return enriched


def link_identity(conn, anonymous_id: str, user_id: str) -> None:
    """PostHog-only mode: identity linkage is handled by mirrored $identify events."""
    del conn
    del anonymous_id
    del user_id
