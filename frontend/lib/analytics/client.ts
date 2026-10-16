import { AnalyticsEventPayload, AnalyticsEventName, TrackEventOptions } from "@/lib/analytics/types";
import { API_BASE_URL } from "@/lib/api-url";

const ANALYTICS_EVENTS_PATH = "/api/v1/analytics/events";
const ANALYTICS_IDENTIFY_PATH = "/api/v1/analytics/identify";
const ANALYTICS_EVENT_VERSION = "v1";

const ANON_ID_KEY = "korchess_analytics_anonymous_id";
const SESSION_ID_KEY = "korchess_analytics_session_id";
const SESSION_LAST_ACTIVITY_KEY = "korchess_analytics_session_last_activity";
const FIRST_SEEN_AT_KEY = "korchess_analytics_first_seen_at";

const SESSION_TIMEOUT_MS = 30 * 60 * 1000;
const FLUSH_INTERVAL_MS = 5000;
const MAX_BATCH_SIZE = 25;
const MAX_QUEUE_SIZE = 500;
// After a failed send, wait before trying again: 5s, 10s, 20s... up to 5 min.
// Past a few failures in a row the failed batch is dropped instead of re-queued,
// so a broken endpoint costs at most one request per backoff period.
const RETRY_BASE_DELAY_MS = 5000;
const RETRY_MAX_DELAY_MS = 5 * 60 * 1000;
const MAX_FAILURES_BEFORE_DROP = 3;
const BACKLOG_DRAIN_DELAY_MS = 250;
const ANALYTICS_ENV_FLAG = (process.env.NEXT_PUBLIC_ENABLE_ANALYTICS || "").toLowerCase();

let queue: AnalyticsEventPayload[] = [];
let flushIntervalId: number | null = null;
let initialized = false;
let inflight = false;
let consecutiveFailures = 0;
let nextAttemptAt = 0;
let getAuthToken: (() => string | null) | null = null;

function envAnalyticsEnabled(): boolean {
  // Never enable client tracking outside production.
  if (process.env.NODE_ENV !== "production") return false;
  if (ANALYTICS_ENV_FLAG === "false" || ANALYTICS_ENV_FLAG === "0") return false;
  return true;
}

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

export function isAnalyticsEnabled(): boolean {
  if (!envAnalyticsEnabled()) return false;
  if (typeof window === "undefined") return true;
  return !isLocalHostname(window.location.hostname);
}

function nowIso(): string {
  return new Date().toISOString();
}

function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getLocalStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function getSessionStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function setAnonymousCookie(value: string): void {
  if (typeof document === "undefined") return;
  const maxAge = 60 * 60 * 24 * 365 * 2;
  document.cookie = `${ANON_ID_KEY}=${encodeURIComponent(value)}; Max-Age=${maxAge}; Path=/; SameSite=Lax`;
}

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const entries = document.cookie.split(";").map((entry) => entry.trim());
  const found = entries.find((entry) => entry.startsWith(`${name}=`));
  if (!found) return null;
  return decodeURIComponent(found.slice(name.length + 1));
}

function ensureAnonymousId(): string {
  const storage = getLocalStorage();
  const fromStorage = storage?.getItem(ANON_ID_KEY);
  const fromCookie = getCookie(ANON_ID_KEY);
  const existing = fromStorage || fromCookie;

  if (existing) {
    storage?.setItem(ANON_ID_KEY, existing);
    setAnonymousCookie(existing);
    return existing;
  }

  const created = randomId();
  storage?.setItem(ANON_ID_KEY, created);
  setAnonymousCookie(created);
  return created;
}

function ensureSessionId(): string {
  const session = getSessionStorage();
  const now = Date.now();
  const currentId = session?.getItem(SESSION_ID_KEY);
  const lastActivityRaw = session?.getItem(SESSION_LAST_ACTIVITY_KEY);
  const lastActivity = Number(lastActivityRaw || "0");

  let nextId = currentId;
  if (!nextId || !lastActivity || now - lastActivity > SESSION_TIMEOUT_MS) {
    nextId = randomId();
    session?.setItem(SESSION_ID_KEY, nextId);
  }

  session?.setItem(SESSION_LAST_ACTIVITY_KEY, String(now));
  return nextId;
}

function isFirstTimeVisit(): boolean {
  const storage = getLocalStorage();
  if (!storage) return false;
  const existing = storage.getItem(FIRST_SEEN_AT_KEY);
  if (existing) return false;
  storage.setItem(FIRST_SEEN_AT_KEY, nowIso());
  return true;
}

export function getTrackingHeaders(): Record<string, string> {
  if (typeof window === "undefined" || !isAnalyticsEnabled()) return {};
  return {
    "X-Anonymous-Id": ensureAnonymousId(),
    "X-Session-Id": ensureSessionId(),
  };
}

export function withTrackingHeaders(headers: Record<string, string> = {}): Record<string, string> {
  return {
    ...headers,
    ...getTrackingHeaders(),
  };
}

/**
 * Give analytics a way to read the current access token at send time, rather
 * than a copy that can go stale between renewals.
 */
export function setAnalyticsAuthTokenGetter(getter: (() => string | null) | null): void {
  getAuthToken = getter;
}

function isTokenExpired(token: string): boolean {
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return typeof payload.exp === "number" && Date.now() / 1000 >= payload.exp;
  } catch {
    return true;
  }
}

function currentAuthToken(): string | null {
  const token = getAuthToken?.() ?? null;
  // An expired token gets the whole batch rejected with 401; events are
  // accepted anonymously, so send them without one instead
  if (!token || isTokenExpired(token)) return null;
  return token;
}

async function postEvents(batch: AnalyticsEventPayload[], useBeacon = false): Promise<boolean> {
  if (!isAnalyticsEnabled()) return true;
  if (!batch.length) return true;

  const body = JSON.stringify({ events: batch });
  const endpoint = `${API_BASE_URL}${ANALYTICS_EVENTS_PATH}`;

  if (useBeacon && typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    const blob = new Blob([body], { type: "application/json" });
    const sent = navigator.sendBeacon(endpoint, blob);
    if (sent) return true;
  }

  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...getTrackingHeaders(),
    };

    const authToken = currentAuthToken();
    if (authToken) {
      headers.Authorization = `Bearer ${authToken}`;
    }

    const response = await fetch(endpoint, {
      method: "POST",
      headers,
      body,
      keepalive: true,
    });

    return response.ok;
  } catch {
    return false;
  }
}

export async function flushAnalytics(options?: { useBeacon?: boolean }): Promise<void> {
  if (!isAnalyticsEnabled()) return;
  if (inflight || queue.length === 0) return;
  const useBeacon = options?.useBeacon === true;
  // Backing off after failures; the page-hide beacon still gets its one try
  if (!useBeacon && Date.now() < nextAttemptAt) return;

  inflight = true;
  const batch = queue.splice(0, MAX_BATCH_SIZE);
  const sent = await postEvents(batch, useBeacon);
  inflight = false;

  if (sent) {
    consecutiveFailures = 0;
    nextAttemptAt = 0;
    // Drain any backlog promptly while the endpoint is healthy
    if (!useBeacon && queue.length > 0) {
      window.setTimeout(() => {
        void flushAnalytics();
      }, BACKLOG_DRAIN_DELAY_MS);
    }
    return;
  }

  consecutiveFailures++;
  if (consecutiveFailures < MAX_FAILURES_BEFORE_DROP) {
    queue = [...batch, ...queue].slice(-MAX_QUEUE_SIZE);
  }
  const delay = Math.min(
    RETRY_MAX_DELAY_MS,
    RETRY_BASE_DELAY_MS * 2 ** (consecutiveFailures - 1),
  );
  nextAttemptAt = Date.now() + delay;
}

export function trackEvent(eventName: AnalyticsEventName | string, options: TrackEventOptions = {}): void {
  if (typeof window === "undefined" || !isAnalyticsEnabled()) return;

  if (!initialized) {
    initAnalytics();
  }

  const anonymousId = ensureAnonymousId();
  const sessionId = ensureSessionId();

  const payload: AnalyticsEventPayload = {
    event_id: randomId(),
    event_name: eventName,
    event_version: options.eventVersion || ANALYTICS_EVENT_VERSION,
    occurred_at: nowIso(),
    anonymous_id: anonymousId,
    session_id: sessionId,
    path: options.path || window.location.pathname,
    url: options.url || window.location.href,
    referrer: options.referrer ?? document.referrer,
    user_agent: typeof navigator !== "undefined" ? navigator.userAgent : undefined,
    is_first_time: options.isFirstTime,
    properties: options.properties || {},
  };

  queue.push(payload);
  if (queue.length > MAX_QUEUE_SIZE) {
    queue = queue.slice(-MAX_QUEUE_SIZE);
  }

  if (queue.length >= MAX_BATCH_SIZE) {
    void flushAnalytics();
  }
}

export async function identifyAnalyticsUser(idToken: string): Promise<boolean> {
  if (typeof window === "undefined" || !isAnalyticsEnabled()) return false;

  const anonymousId = ensureAnonymousId();
  const sessionId = ensureSessionId();

  try {
    const response = await fetch(`${API_BASE_URL}${ANALYTICS_IDENTIFY_PATH}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
        ...getTrackingHeaders(),
      },
      body: JSON.stringify({
        anonymous_id: anonymousId,
        session_id: sessionId,
      }),
      keepalive: true,
    });

    if (!response.ok) {
      return false;
    }

    trackEvent("identity.linked", {
      properties: {
        link_source: "client_identify",
      },
    });
    return true;
  } catch {
    return false;
  }
}

export function initAnalytics(): void {
  if (typeof window === "undefined" || initialized || !isAnalyticsEnabled()) return;
  initialized = true;

  ensureAnonymousId();
  ensureSessionId();
  const firstVisit = isFirstTimeVisit();

  if (firstVisit) {
    trackEvent("feature.usage", {
      properties: {
        feature: "first_visit",
      },
      isFirstTime: true,
    });
  }

  flushIntervalId = window.setInterval(() => {
    void flushAnalytics();
  }, FLUSH_INTERVAL_MS);

  const flushWithBeacon = () => {
    void flushAnalytics({ useBeacon: true });
  };

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushWithBeacon();
    }
  });

  window.addEventListener("pagehide", flushWithBeacon);
  window.addEventListener("beforeunload", flushWithBeacon);
}

export function teardownAnalytics(): void {
  if (flushIntervalId !== null) {
    window.clearInterval(flushIntervalId);
    flushIntervalId = null;
  }
  initialized = false;
}
