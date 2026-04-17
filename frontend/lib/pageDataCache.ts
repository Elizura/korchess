const CACHE_PREFIX = "korchess:page-cache:";
const CACHE_VERSION = 1;
export const PAGE_DATA_CACHE_TTL_MS = 5 * 60 * 1000;

export type CacheEntry<T> = {
  data: T;
  storedAt: number;
  version: number;
};

const memoryCache = new Map<string, CacheEntry<unknown>>();

const storageKey = (key: string): string => `${CACHE_PREFIX}${key}`;

const isValidEntry = <T>(value: unknown): value is CacheEntry<T> => {
  if (!value || typeof value !== "object") {
    return false;
  }
  const maybe = value as Partial<CacheEntry<T>>;
  return (
    maybe.version === CACHE_VERSION &&
    typeof maybe.storedAt === "number" &&
    Number.isFinite(maybe.storedAt) &&
    "data" in maybe
  );
};

const getSessionStorage = (): Storage | null => {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

export const getCached = <T>(key: string): CacheEntry<T> | null => {
  const fromMemory = memoryCache.get(key);
  if (fromMemory && isValidEntry<T>(fromMemory)) {
    return fromMemory as CacheEntry<T>;
  }

  const storage = getSessionStorage();
  if (!storage) {
    return null;
  }

  try {
    const raw = storage.getItem(storageKey(key));
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!isValidEntry<T>(parsed)) {
      storage.removeItem(storageKey(key));
      return null;
    }
    memoryCache.set(key, parsed as CacheEntry<unknown>);
    return parsed as CacheEntry<T>;
  } catch {
    try {
      storage.removeItem(storageKey(key));
    } catch {
      // ignore storage failures
    }
    return null;
  }
};

export const setCached = <T>(key: string, data: T): void => {
  const entry: CacheEntry<T> = {
    data,
    storedAt: Date.now(),
    version: CACHE_VERSION,
  };
  memoryCache.set(key, entry as CacheEntry<unknown>);

  const storage = getSessionStorage();
  if (!storage) {
    return;
  }
  try {
    storage.setItem(storageKey(key), JSON.stringify(entry));
  } catch {
    // ignore storage failures
  }
};

export const isFresh = <T>(entry: CacheEntry<T> | null, ttlMs: number): boolean => {
  if (!entry || ttlMs <= 0) {
    return false;
  }
  return Date.now() - entry.storedAt <= ttlMs;
};

export const clearCacheKey = (key: string): void => {
  memoryCache.delete(key);
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    storage.removeItem(storageKey(key));
  } catch {
    // ignore storage failures
  }
};

export const clearCacheByPrefix = (prefix: string): void => {
  for (const key of Array.from(memoryCache.keys())) {
    if (key.startsWith(prefix)) {
      memoryCache.delete(key);
    }
  }

  const storage = getSessionStorage();
  if (!storage) {
    return;
  }

  try {
    const toDelete: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (!key) {
        continue;
      }
      if (key.startsWith(storageKey(prefix))) {
        toDelete.push(key);
      }
    }
    toDelete.forEach((key) => {
      storage.removeItem(key);
    });
  } catch {
    // ignore storage failures
  }
};

export const clearAllCache = (): void => {
  memoryCache.clear();

  const storage = getSessionStorage();
  if (!storage) {
    return;
  }

  try {
    const toDelete: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (!key) {
        continue;
      }
      if (key.startsWith(CACHE_PREFIX)) {
        toDelete.push(key);
      }
    }
    toDelete.forEach((key) => {
      storage.removeItem(key);
    });
  } catch {
    // ignore storage failures
  }
};

export const buildDashboardCacheKey = (
  username: string,
  color: string,
  timeClass: string,
  authUserId: string,
): string => {
  return `dashboard:${username.toLowerCase()}:${color}:${timeClass}:${authUserId}`;
};

export const buildDashboardVariationCacheKey = (
  username: string,
  openingKey: string,
  color: string,
  timeClass: string,
  authUserId: string,
): string => {
  return `dashboard:variations:${username.toLowerCase()}:${openingKey}:${color}:${timeClass}:${authUserId}`;
};

export const buildDashboardInsightsCacheKey = (
  username: string,
  authUserId: string,
): string => {
  return `dashboard:insights:${username.toLowerCase()}:${authUserId}`;
};

export const buildOpeningCacheKey = (
  username: string,
  openingKey: string,
  variationKey: string,
  color: string,
  timeClass: string,
  result: string,
  offset: number,
  authUserId: string,
): string => {
  return `opening:${username.toLowerCase()}:${openingKey}:${variationKey || "_"}:${color}:${timeClass}:${result}:${offset}:${authUserId}`;
};

export const buildAuthProfileCacheKey = (authUserId: string): string => {
  return `auth:profile:${authUserId}`;
};

export const buildChessProfilesCacheKey = (authUserId: string): string => {
  return `chess:profiles:${authUserId}`;
};
