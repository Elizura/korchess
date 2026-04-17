# Event Tracking Architecture

This document explains how event tracking works in the En Passant chess analytics application.

## Overview

The app uses a **PostHog-only** analytics architecture where events are mirrored to PostHog for product analytics. There is no first-party database persistence for analytics events.

## Two Event Sources

### 1. Client-Side Events (Frontend)

Events triggered by user interactions in the browser.

**Endpoint:** `POST /api/v1/analytics/events`

**Common events:**
- `page.view` - Page navigation
- `button.click` - User interactions
- `feature.used` - Feature engagement

**Flow:**
```
Frontend → POST /analytics/events → Enrichment → PostHog (background)
```

### 2. Server-Side Events (Backend)

Events triggered by backend operations that the frontend can't observe.

**Common events:**
- `import.start` / `import.success` / `import.failed` - Import lifecycle
- `analysis.deep.requested` / `analysis.deep.started` / `analysis.deep.completed` / `analysis.deep.failed` - Analysis jobs
- `analysis.ai.requested` / `analysis.ai.completed` / `analysis.ai.failed` - AI insights
- `auth.registered` - User registration

**Flow:**
```
Backend Router → await track_server_event() → Enrichment → PostHog (background)
```

## Why Server-Side Tracking Exists

### 1. **Reliability**
- Frontend events can be blocked by ad blockers, privacy extensions, or disabled JavaScript
- Server events are guaranteed to fire - you know an operation actually happened
- Ad blocker proof

### 2. **Events Frontend Can't See**
- **Background job completion**: `analysis.deep.completed` fires when Stockfish finishes (user might have closed the tab)
- **Real error details**: `import.failed` includes actual HTTP status codes (404, 429, 502) and error reasons from Lichess/Chess.com APIs
- **OAuth callbacks**: `auth.registered` happens during server-side OAuth flow

### 3. **Accurate Data**
- Server knows exact timings for async operations
- Can't be manipulated by users
- Captures errors that never reach the frontend

## Event Processing Pipeline

### When `await track_server_event()` is Called

```python
await track_server_event(
    conn,
    event_name="import.success",
    user_id=current_user["id"] if current_user else None,
    request=http_request,
    properties={
        "site": "lichess",
        "imported": 42,
        "username": "hash123",
    },
)
```

**Step-by-step execution:**

1. **Validation** (~instant)
   - Check if analytics is enabled (`ANALYTICS_ENABLED`, production environment)
   - Check if host is localhost (skip for local dev)
   - If disabled, return immediately with `{"skipped": True}`

2. **Event Enrichment** (~50-500 microseconds, CPU-bound)
   - Parse user agent (browser, OS, device type)
   - Extract and hash client IP for privacy
   - Classify referrer (direct, search, social, etc.)
   - Add server-side metadata (country/city from headers if available)
   - Generate event_id and timestamp

3. **Database Persistence** (instant - no-op)
   - `persist_enriched_event()` is a **no-op** in PostHog-only mode
   - Previously stored events locally, now just `del conn; del event`

4. **PostHog Mirroring** (non-blocking, fire-and-forget)
   ```python
   asyncio.create_task(mirror_events_to_posthog([enriched]))
   ```
   - **Does NOT block** the request
   - Spawns background task to POST to PostHog API
   - If PostHog is down/slow, your API still responds immediately
   - Failures are silently swallowed (analytics never breaks product)

5. **Return** enriched event dict (typically ignored by callers)

## Performance Impact

### Current Behavior (Awaiting)
- **Blocks for:** Event enrichment (50-500μs)
- **Does NOT block for:** PostHog network call (happens in background)
- **Total overhead:** Sub-millisecond per event

### If Made Fire-and-Forget
```python
asyncio.create_task(track_server_event(...))
```
- Would save the enrichment time (~500μs max)
- But lose ability to catch validation errors synchronously
- Trade-off: slightly faster response vs immediate error feedback

## Event Schema

### Base Event Structure
```json
{
  "event_id": "uuid",
  "event_name": "import.success",
  "event_version": "v1",
  "occurred_at": "2026-04-17T12:00:00.000Z",
  "anonymous_id": "client-provided-or-server-generated",
  "session_id": "client-provided-or-server-generated",
  "user_id": "auth0|xyz..." // null for anonymous
}
```

### Enriched Properties
Added by server during enrichment:

```json
{
  "properties": {
    // Original event properties
    "site": "lichess",
    "imported": 42,
    
    // Server-enriched
    "$browser": "Chrome",
    "$os": "macOS",
    "$device_type": "Desktop",
    "referrer_type": "direct",
    "referrer_domain": null,
    "ip_prefix_hash": "abc123...",
    "$geoip_country_code": "US",
    "$geoip_city_name": "San Francisco"
  }
}
```

## PostHog Integration

### Batch Format
Events are sent to PostHog in their native batch format:

```python
POST https://us.i.posthog.com/batch/
{
  "api_key": "phc_xxxxx",
  "batch": [
    {
      "event": "import.success",
      "distinct_id": "auth0|user123",
      "timestamp": "2026-04-17T12:00:00.000Z",
      "properties": { ... }
    }
  ]
}
```

### Special PostHog Events
In addition to custom events, the system emits PostHog-native events for better integration:

- **`$pageview`** - Derived from `page.view` events
- **`$pageleave`** - Derived from `page.leave` events
- **`$identify`** - When user links identity

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANALYTICS_ENABLED` | (empty) | Set to `"0"` or `"false"` to disable |
| `ENVIRONMENT` | (empty) | Must be `"production"` to enable |
| `POSTHOG_API_KEY` | (empty) | PostHog project API key (required) |
| `POSTHOG_HOST` | `https://us.i.posthog.com` | PostHog instance URL |
| `POSTHOG_TIMEOUT_S` | `4` | HTTP timeout for PostHog requests |
| `ANALYTICS_HASH_SALT` | `"analytics-salt-change-me"` | Salt for hashing PII |

### When Analytics is Disabled
- All `track_server_event()` calls return immediately with `{"skipped": True}`
- No network calls to PostHog
- No CPU spent on enrichment
- Works in:
  - Non-production environments (dev, staging)
  - Localhost requests
  - When explicitly disabled via `ANALYTICS_ENABLED=0`

## Security & Privacy

### PII Protection
Sensitive fields are automatically filtered:
- `token`, `auth_token`, `access_token`, `id_token`
- `password`
- `email` (hashed when sent)
- `pgn` (chess game notation - can be large)

### IP Handling
- Client IP is extracted and hashed as `ip_prefix_hash`
- Only first 3 octets used (e.g., `192.168.1.x`)
- Full IP never stored or sent to PostHog

### Username Hashing
Usernames are hashed before being sent to PostHog:
```python
username_hash = hash_username(username)
# Used in properties: {"username": username_hash}
```

## Server Events by Category

### Import Events
| Event | When | Key Properties |
|-------|------|----------------|
| `import.start` | Import initiated | `site`, `max_games`, `is_sync` |
| `import.success` | Import completed | `imported`, `skipped`, `is_sync` |
| `import.failed` | Import error | `status_code`, `reason` |

### Analysis Events
| Event | When | Key Properties |
|-------|------|----------------|
| `analysis.deep.requested` | User requests full analysis | `depth`, `multipv`, `force` |
| `analysis.deep.started` | Background job begins | `job_id` |
| `analysis.deep.completed` | Stockfish finishes | `total_time_ms`, `positions_analyzed` |
| `analysis.deep.failed` | Analysis error | `reason` |

### AI Insights Events
| Event | When | Key Properties |
|-------|------|----------------|
| `analysis.ai.requested` | User requests AI insights | `quota_unlimited_email` |
| `analysis.ai.completed` | Gemini generates insights | (none) |
| `analysis.ai.failed` | Generation error or quota | `reason` |

### Auth Events
| Event | When | Key Properties |
|-------|------|----------------|
| `auth.registered` | New user signs up | `provider` |
| `identity.linked` | Anonymous → authenticated | (none) |

## Should You Use Server-Side Tracking?

### ✅ Keep Server Tracking For:
- **Background job completion** - Frontend can't detect when async jobs finish
- **Real error diagnostics** - Capture actual API errors (rate limits, 404s, 500s)
- **Quota enforcement** - Track AI insights usage for billing/limits
- **Critical business metrics** - When you need 100% accuracy (ad blocker proof)

### ❌ Could Remove Server Tracking For:
- **User-initiated actions** - `import.start`, `analysis.deep.requested` (frontend knows this)
- **Success responses** - `import.success` (frontend receives response)
- **Events that duplicate frontend tracking** - Adds complexity and latency

### 💡 Recommended Hybrid Approach
1. **Frontend tracks:** User actions, page views, UI interactions
2. **Server tracks only:** Background job completions, real errors, quota usage

## Alternative: Fire-and-Forget Server Tracking

If you want to eliminate the enrichment latency, make server tracking fully async:

```python
# Instead of:
await track_server_event(...)

# Do:
asyncio.create_task(track_server_event(...))
```

**Trade-offs:**
- ✅ Faster API responses (~500μs saved per event)
- ✅ Analytics never adds latency
- ❌ Lose synchronous validation error feedback
- ❌ Can't guarantee event fires before request completes (unlikely to matter)

## How PostHog Network Call is Non-Blocking

Looking at line 558 in `track_server_event`:

```python
async def track_server_event(...):
    # 1. Build enriched event (CPU work, ~500μs)
    enriched = await build_enriched_event(raw_event, request=request, user_id=user_id)
    
    # 2. Persist (no-op in PostHog-only mode)
    persist_enriched_event(conn, enriched)
    
    # 3. Mirror to PostHog - FIRE AND FORGET
    try:
        asyncio.create_task(mirror_events_to_posthog([enriched]))
    except Exception:
        pass
    
    # 4. Return immediately (PostHog task runs in background)
    return enriched
```

The **network I/O** (HTTP POST to PostHog) happens in a background task:

```python
async def mirror_events_to_posthog(events: list[dict[str, Any]]) -> None:
    async with httpx.AsyncClient(timeout=4) as client:
        response = await client.post(f"{POSTHOG_HOST}/batch/", json=payload)
        # This runs in background - doesn't block your API response
```

**So when you `await track_server_event()`:**
- ✅ Blocks: Event enrichment (CPU work)
- ❌ Does NOT block: PostHog HTTP request (background task)

Your API response returns **before** PostHog receives the data.

## Testing

Analytics tracking is automatically disabled in tests:
- Non-production environment → all tracking returns `{"skipped": True}`
- No network calls to PostHog
- No performance impact

## Monitoring

Since PostHog mirroring is fire-and-forget with silent failure:
- Check PostHog dashboard to verify events are arriving
- Server logs warnings for HTTP errors: `"PostHog batch mirror failed with status=XXX"`
- No alerts or retries - analytics never breaks the product

## Summary

**Current Design:**
- Server tracking adds ~500μs of CPU-bound enrichment per event
- PostHog network call is already non-blocking (fire-and-forget)
- Analytics failures never break product functionality

**Consider Removing:**
- Server tracking for user-initiated actions (duplicates frontend)
- Events that don't provide unique value (e.g., `import.start`)

**Keep:**
- Background job completions
- Real error details with status codes
- Quota/rate limit tracking
