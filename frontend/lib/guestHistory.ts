export interface GuestHistoryEntry {
  username: string;
  site: string;
  imported_at: string;
}

export const GUEST_HISTORY_STORAGE_KEY = "korchess_guest_import_history_v1";

const GUEST_HISTORY_MAX_ENTRIES = 20;
const GUEST_HISTORY_RETENTION_MS = 90 * 24 * 60 * 60 * 1000;

type HistorySource = "local" | "account";

const getLocalStorage = (): Storage | null => {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const toTimestamp = (value: string): number | null => {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return parsed;
};

const sanitizeEntry = (value: unknown): GuestHistoryEntry | null => {
  if (!value || typeof value !== "object") {
    return null;
  }
  const maybe = value as Partial<GuestHistoryEntry>;
  if (
    typeof maybe.username !== "string" ||
    typeof maybe.site !== "string" ||
    typeof maybe.imported_at !== "string"
  ) {
    return null;
  }
  const username = maybe.username.trim();
  const site = maybe.site.trim().toLowerCase();
  const importedAt = maybe.imported_at.trim();
  const timestamp = toTimestamp(importedAt);
  if (!username || !site || timestamp === null) {
    return null;
  }
  return {
    username,
    site,
    imported_at: new Date(timestamp).toISOString(),
  };
};

const normalizeGuestHistory = (entries: GuestHistoryEntry[]): GuestHistoryEntry[] => {
  const now = Date.now();
  const cutoff = now - GUEST_HISTORY_RETENTION_MS;
  const byUser = new Map<string, { entry: GuestHistoryEntry; timestamp: number }>();

  for (const item of entries) {
    const timestamp = toTimestamp(item.imported_at);
    if (timestamp === null || timestamp < cutoff) {
      continue;
    }
    const key = item.username.toLowerCase();
    const existing = byUser.get(key);
    if (!existing || timestamp > existing.timestamp) {
      byUser.set(key, { entry: item, timestamp });
    }
  }

  return Array.from(byUser.values())
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, GUEST_HISTORY_MAX_ENTRIES)
    .map((row) => row.entry);
};

const persistGuestHistory = (entries: GuestHistoryEntry[]): void => {
  const storage = getLocalStorage();
  if (!storage) {
    return;
  }
  try {
    storage.setItem(GUEST_HISTORY_STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // ignore storage failures (quota, privacy mode, etc.)
  }
};

const clearStoredGuestHistory = (): void => {
  const storage = getLocalStorage();
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(GUEST_HISTORY_STORAGE_KEY);
  } catch {
    // ignore storage failures
  }
};

export const loadGuestHistory = (): GuestHistoryEntry[] => {
  const storage = getLocalStorage();
  if (!storage) {
    return [];
  }

  const raw = storage.getItem(GUEST_HISTORY_STORAGE_KEY);
  if (!raw) {
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    clearStoredGuestHistory();
    return [];
  }

  if (!Array.isArray(parsed)) {
    clearStoredGuestHistory();
    return [];
  }

  const sanitized = parsed
    .map((item) => sanitizeEntry(item))
    .filter((item): item is GuestHistoryEntry => item !== null);
  const normalized = normalizeGuestHistory(sanitized);

  // Keep storage canonical and recover from malformed/old payloads.
  persistGuestHistory(normalized);

  return normalized;
};

export const saveGuestHistoryEntry = (
  entry: GuestHistoryEntry,
): GuestHistoryEntry[] => {
  const sanitizedEntry = sanitizeEntry(entry);
  if (!sanitizedEntry) {
    return loadGuestHistory();
  }

  const current = loadGuestHistory();
  const byUser = new Map<string, GuestHistoryEntry>();
  for (const item of current) {
    byUser.set(item.username.toLowerCase(), item);
  }

  const key = sanitizedEntry.username.toLowerCase();
  const existing = byUser.get(key);
  if (!existing) {
    byUser.set(key, sanitizedEntry);
  } else {
    const existingTs = toTimestamp(existing.imported_at);
    const nextTs = toTimestamp(sanitizedEntry.imported_at);
    if (
      existingTs === null ||
      (nextTs !== null && nextTs >= existingTs)
    ) {
      byUser.set(key, sanitizedEntry);
    }
  }

  const next = normalizeGuestHistory(Array.from(byUser.values()));
  persistGuestHistory(next);
  return next;
};

export const mergeHistory = (
  local: GuestHistoryEntry[],
  account: GuestHistoryEntry[],
): GuestHistoryEntry[] => {
  const byUser = new Map<
    string,
    { entry: GuestHistoryEntry; timestamp: number; source: HistorySource }
  >();

  const upsert = (entry: GuestHistoryEntry, source: HistorySource) => {
    const sanitized = sanitizeEntry(entry);
    if (!sanitized) {
      return;
    }
    const timestamp = toTimestamp(sanitized.imported_at);
    if (timestamp === null) {
      return;
    }
    const key = sanitized.username.toLowerCase();
    const existing = byUser.get(key);
    if (!existing) {
      byUser.set(key, { entry: sanitized, timestamp, source });
      return;
    }

    if (
      timestamp > existing.timestamp ||
      (timestamp === existing.timestamp &&
        source === "account" &&
        existing.source !== "account")
    ) {
      byUser.set(key, { entry: sanitized, timestamp, source });
    }
  };

  local.forEach((entry) => upsert(entry, "local"));
  account.forEach((entry) => upsert(entry, "account"));

  return Array.from(byUser.values())
    .sort((a, b) => b.timestamp - a.timestamp)
    .map((row) => row.entry);
};
