import { AnalyticsEventPayload, AnalyticsEventName, TrackEventOptions } from "@/lib/analytics/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

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
const ANALYTICS_ENV_FLAG = (process.env.NEXT_PUBLIC_ENABLE_ANALYTICS || "").toLowerCase();

let queue: AnalyticsEventPayload[] = [];
let flushIntervalId: number | null = null;
let initialized = false;
let inflight = false;
let authToken: string | null = null;

function envAnalyticsEnabled(): boolean {
  if (ANALYTICS_ENV_FLAG === "true" || ANALYTICS_ENV_FLAG === "1") return true;
  if (ANALYTICS_ENV_FLAG === "false" || ANALYTICS_ENV_FLAG === "0") return false;
  return process.env.NODE_ENV === "production";
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

export function setAnalyticsAuthToken(token: string | null): void {
  if (!isAnalyticsEnabled()) {
    authToken = null;
    return;
  }
  authToken = token;
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

  inflight = true;
  const batch = queue.splice(0, MAX_BATCH_SIZE);
  const sent = await postEvents(batch, options?.useBeacon === true);

  if (!sent) {
    queue = [...batch, ...queue].slice(-MAX_QUEUE_SIZE);
  }

  inflight = false;

  if (!options?.useBeacon && queue.length > 0) {
    window.setTimeout(() => {
      void flushAnalytics();
    }, 250);
  }
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
