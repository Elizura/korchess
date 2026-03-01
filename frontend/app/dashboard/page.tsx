"use client";

// This page uses client-side routing/searchParams; force dynamic rendering to
// avoid Next.js "useSearchParams() should be wrapped in a suspense boundary" build errors.
export const dynamic = "force-dynamic";

import { useState, useMemo, useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { signIn, signOut, useSession } from "next-auth/react";
import { trackEvent, withTrackingHeaders } from "@/lib/analytics/client";
import {
  loadGuestHistory,
  mergeHistory,
  saveGuestHistoryEntry,
} from "@/lib/guestHistory";
import {
  PAGE_DATA_CACHE_TTL_MS,
  buildDashboardCacheKey,
  buildDashboardInsightsCacheKey,
  clearAllCache,
  clearCacheByPrefix,
  getCached,
  isFresh,
  setCached,
} from "@/lib/pageDataCache";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

const SHOW_COACHING_SUMMARY = true;

const DASHBOARD_LAST_USER_KEY = "korchess_dashboard_last_user";

const persistLastUser = (user: string) => {
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(DASHBOARD_LAST_USER_KEY, user);
    } catch {
      // Ignore localStorage errors
    }
  }
};

interface OpeningStats {
  opening_key: string;
  opening_label: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  score_pct: number;
}

interface ImportResponse {
  username: string;
  imported: number;
  skipped: number;
}

interface ImportStatus {
  username: string;
  imported_at: string | null;
  last_imported: number | null;
  last_skipped: number | null;
  total_games: number;
}

interface ImportHistoryItem {
  username: string;
  site: string;
  imported_at: string;
}

interface InsightsClaim {
  text: string;
  fact_ids: string[];
}

interface InsightsProfile {
  username: string;
  site: string;
  lifecycle_status: "missing" | "queued" | "baseline_ready" | "enriching" | "complete" | "stale" | "not_enough_data" | "failed";
  feature_version: string;
  narrative_version: string;
  updated_at: string | null;
  coverage?: {
    games_total?: number;
    games_light?: number;
    games_deep?: number;
    deep_coverage?: number;
    games_with_clock?: number;
    clock_coverage?: number;
    has_enough_games?: boolean;
  };
  features?: {
    style?: {
      label?: string;
    };
    performance?: {
      overall?: {
        games?: number;
        wins?: number;
        draws?: number;
        losses?: number;
        score_pct?: number;
      };
      phase?: Record<
        string,
        {
          moves?: number;
          avg_cp_loss?: number | null;
          mistakes?: number;
          blunders?: number;
          mistake_rate?: number | null;
        }
      >;
      best_openings?: Array<{
        opening: string;
        games: number;
        score_pct: number;
      }>;
      weak_openings?: Array<{
        opening: string;
        games: number;
        score_pct: number;
      }>;
    };
    time_pressure?: {
      clock_coverage?: number;
      games_with_clock?: number;
      games_with_pressure?: number;
      score_pct_under_pressure?: number | null;
      score_pct_overall?: number | null;
      blunders_under_pressure?: number;
      blunders_total_with_clock?: number;
      blunders_under_pressure_pct?: number | null;
      low_time_moves_deep?: number;
      moves_with_clock_deep?: number;
    };
    confidence?: {
      value?: number;
    };
    recurring_themes?: Array<{
      theme: string;
      count: number;
    }>;
  };
  narrative?: {
    player_type?: InsightsClaim;
    strengths?: InsightsClaim[];
    weaknesses?: InsightsClaim[];
    phase_performance?: InsightsClaim;
    time_pressure?: InsightsClaim;
    recurring_mistakes?: InsightsClaim[];
    coaching_takeaways?: InsightsClaim[];
  };
  active_job?: {
    id?: string;
    status: string;
    stage: string;
  } | null;
}

type ColorFilter = "white" | "black";
type TimeClassFilter = "all" | "blitz" | "rapid" | "classical";

type DashboardReportCacheData = {
  reportWhite: OpeningStats[] | null;
  reportBlack: OpeningStats[] | null;
  importStatus: ImportStatus | null;
};

type DashboardImportHistoryCacheData = {
  history: ImportHistoryItem[];
};

const INSIGHTS_ACTIVE_STATUSES = new Set<InsightsProfile["lifecycle_status"]>([
  "queued",
  "baseline_ready",
  "enriching",
]);

// Helper to parse opening name
const parseOpeningName = (fullName: string) => {
  const separators = [': ', ' – ', ', '];
  for (const sep of separators) {
    if (fullName.includes(sep)) {
      const parts = fullName.split(sep);
      return { main: parts[0].trim(), variation: parts.slice(1).join(sep).trim() };
    }
  }
  return { main: fullName, variation: null };
};

const formatWholePercent = (value: number | null | undefined): string | null => {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `${Math.round(value)}%`;
};

const formatPhaseLabel = (phaseKey: string): string => {
  if (!phaseKey) return "Middlegame";
  return `${phaseKey.charAt(0).toUpperCase()}${phaseKey.slice(1).toLowerCase()}`;
};

const humanizeTheme = (theme: string): string => {
  const normalized = theme.trim().toLowerCase();
  if (!normalized) return "Recurring decision errors";
  if (normalized === "opening_blunder") return "costly opening mistakes";
  if (normalized === "middlegame_blunder") return "middlegame blunders";
  if (normalized === "endgame_blunder") return "endgame conversion errors";
  if (normalized === "tactical_oversight") return "missed tactical details";
  if (normalized === "hanging_piece") return "hanging pieces";
  if (normalized === "missed_tactic") return "missed tactics";
  return normalized.replace(/_/g, " ");
};

export default function DashboardPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session, status } = useSession();
  const userFromUrl = useMemo(() => searchParams.get("user") || "", [searchParams]);
  const authUserId = useMemo(
    () => (session?.user?.email || session?.user?.name || "anonymous").toLowerCase(),
    [session?.user?.email, session?.user?.name],
  );

  const authHeaders = useMemo((): Record<string, string> => {
    if (!session?.idToken) {
      return {};
    }
    return { Authorization: `Bearer ${session.idToken}` };
  }, [session?.idToken]);
  const isAuthenticated = status === "authenticated" && !!session?.idToken;
  
  const [username, setUsername] = useState("");
  const [lichessUsername, setLichessUsername] = useState("");
  const [chesscomUsername, setChesscomUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<OpeningStats[] | null>(null);
  const [reportBlack, setReportBlack] = useState<OpeningStats[] | null>(null);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [colorFilter, setColorFilter] = useState<ColorFilter>("white");
  const [timeClassFilter, setTimeClassFilter] =
    useState<TimeClassFilter>("all");
  const [currentUsername, setCurrentUsername] = useState<string | null>(null);
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [profileUsername, setProfileUsername] = useState<string>("");
  const [profileAvatar, setProfileAvatar] = useState<string>("pawn");
  const [initialized, setInitialized] = useState(false);
  const hasAutoLoadedFromHistory = useRef(false);
  const [guestImportHistory, setGuestImportHistory] = useState<ImportHistoryItem[]>([]);
  const [accountImportHistory, setAccountImportHistory] = useState<ImportHistoryItem[]>([]);
  const [insights, setInsights] = useState<InsightsProfile | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsRefreshing, setInsightsRefreshing] = useState(false);
  const [reportRefreshing, setReportRefreshing] = useState(false);
  const [reportRefreshNotice, setReportRefreshNotice] = useState<string | null>(null);

  const importHistory = useMemo(() => {
    if (isAuthenticated) {
      return mergeHistory(guestImportHistory, accountImportHistory);
    }
    return guestImportHistory;
  }, [isAuthenticated, guestImportHistory, accountImportHistory]);

  // Redirect authenticated users who haven't completed onboarding; fetch profile for nav
  useEffect(() => {
    if (!isAuthenticated) return;

    const checkOnboarding = async () => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/v1/auth/profile`,
          { headers: withTrackingHeaders({ Authorization: `Bearer ${session.idToken}` }) }
        );
        if (!res.ok) return;
        const profile = await res.json();
        setProfileUsername(profile.username || "");
        setProfileAvatar(profile.avatar || "pawn");
        if (!profile.onboarding_complete) {
          router.replace("/onboarding");
        }
      } catch {
        // Ignore; user can still use dashboard
      }
    };

    checkOnboarding();
  }, [isAuthenticated, router, session?.idToken]);

  // Always load guest-local "recently analyzed" history.
  useEffect(() => {
    setGuestImportHistory(loadGuestHistory());
  }, []);

  // Fetch import history when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      void fetchImportHistory();
      return;
    }
    setAccountImportHistory([]);
  }, [isAuthenticated, authUserId]);

  useEffect(() => {
    if (!currentUsername) {
      setInsights(null);
      return;
    }
    if (status === "loading") return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const cacheKey = getDashboardInsightsCacheKey(currentUsername);

    const loadInsights = async (force = false) => {
      const cached = force ? null : getCached<InsightsProfile | null>(cacheKey);
      const hasCached = Boolean(cached);
      const cachedData = cached?.data || null;
      const cachedLifecycleStatus = cachedData?.lifecycle_status;
      const shouldPollCached =
        cachedLifecycleStatus !== undefined &&
        INSIGHTS_ACTIVE_STATUSES.has(cachedLifecycleStatus);
      const shouldFetch =
        force ||
        !cached ||
        !isFresh(cached, PAGE_DATA_CACHE_TTL_MS) ||
        shouldPollCached;

      if (!cancelled && cached) {
        setInsights(cachedData);
        setInsightsLoading(false);
      }

      if (!shouldFetch) {
        return;
      }

      if (!cancelled && !hasCached) {
        setInsightsLoading(true);
      }

      try {
        const data = await fetchInsights(currentUsername);
        if (cancelled) return;
        setInsights(data);
        setCached<InsightsProfile | null>(cacheKey, data);
        const lifecycleStatus = data?.lifecycle_status;
        if (lifecycleStatus && INSIGHTS_ACTIVE_STATUSES.has(lifecycleStatus)) {
          timer = setTimeout(() => {
            void loadInsights();
          }, 8000);
        }
      } finally {
        if (!cancelled) {
          setInsightsLoading(false);
        }
      }
    };

    void loadInsights();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [status, session?.idToken, currentUsername, authHeaders, authUserId]);

  // Fetch combined report across all sites
  const fetchReport = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
  ): Promise<OpeningStats[]> => {
    const params = new URLSearchParams();
    params.set("color", color);
    params.set("time_class", timeClass);
    params.set("limit", "5");

    const response = await fetch(
      `${API_BASE_URL}/api/v1/openings/all/${encodeURIComponent(user)}?${params}`,
      { headers: withTrackingHeaders(authHeaders) }
    );

    if (!response.ok) {
      // Treat "no games for this filter" as an empty report, not a hard dashboard error.
      if (response.status === 404) {
        return [];
      }
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to fetch report: ${response.status}`);
    }

    return (await response.json()) as OpeningStats[];
  };

  const fetchImportStatus = async (user: string) => {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/import-status/all/${encodeURIComponent(user)}`,
      { headers: withTrackingHeaders(authHeaders) }
    );
    
    if (!response.ok) {
      return null; // Silently handle - not critical
    }
    
    return response.json();
  };

  const fetchImportHistory = async () => {
    if (!session?.idToken) {
      setAccountImportHistory([]);
      return;
    }
    const cacheKey = `dashboard:history:${authUserId}`;
    const cached = getCached<DashboardImportHistoryCacheData>(cacheKey);
    if (cached) {
      setAccountImportHistory(cached.data.history || []);
      if (isFresh(cached, PAGE_DATA_CACHE_TTL_MS)) {
        return;
      }
    }
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/import/history`, {
        headers: withTrackingHeaders(authHeaders),
      });
      if (response.ok) {
        const data = await response.json();
        const nextHistory = data.history || [];
        setAccountImportHistory(nextHistory);
        setCached<DashboardImportHistoryCacheData>(cacheKey, { history: nextHistory });
      }
    } catch {
      // Silently ignore - not critical
    }
  };

  const fetchInsights = async (user: string): Promise<InsightsProfile | null> => {
    const params = new URLSearchParams();
    params.set("username", user);
    params.set("site", "all");

    const response = await fetch(
      `${API_BASE_URL}/api/v1/insights/profile?${params.toString()}`,
      { headers: withTrackingHeaders(authHeaders) }
    );
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    return data as InsightsProfile;
  };

  const requestInsightsRefresh = async (user: string, force = true): Promise<InsightsProfile | null> => {
    const response = await fetch(`${API_BASE_URL}/api/v1/insights/profile`, {
      method: "POST",
      headers: withTrackingHeaders({
        "Content-Type": "application/json",
        ...authHeaders,
      } as Record<string, string>),
      body: JSON.stringify({
        username: user,
        site: "all",
        force,
      }),
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as InsightsProfile;
  };

  const getDashboardBundleCacheKey = (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
  ): string => buildDashboardCacheKey(user, color, timeClass, authUserId);

  const getDashboardInsightsCacheKey = (user: string): string =>
    buildDashboardInsightsCacheKey(user, authUserId);

  const loadDashboardBundle = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
    options?: { force?: boolean },
  ) => {
    const force = Boolean(options?.force);
    const cacheKey = getDashboardBundleCacheKey(user, color, timeClass);
    const cached = getCached<DashboardReportCacheData>(cacheKey);
    const hasCached = Boolean(cached);
    const shouldFetch = force || !cached || !isFresh(cached, PAGE_DATA_CACHE_TTL_MS);

    if (cached) {
      setReport(cached.data.reportWhite || null);
      setReportBlack(cached.data.reportBlack || null);
      setImportStatus(cached.data.importStatus || null);
      setLoading(false);
      setError(null);
      setReportRefreshNotice(null);
    } else if (!force) {
      setLoading(true);
    }

    if (!shouldFetch) {
      return;
    }

    if (hasCached) {
      setReportRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const [whiteReportData, blackReportData, statusData] = await Promise.all([
        fetchReport(user, "white", timeClass),
        fetchReport(user, "black", timeClass),
        fetchImportStatus(user),
      ]);
      setReport(whiteReportData);
      setReportBlack(blackReportData);
      setImportStatus(statusData);
      setCached<DashboardReportCacheData>(cacheKey, {
        reportWhite: whiteReportData,
        reportBlack: blackReportData,
        importStatus: statusData,
      });
      setError(null);
      setReportRefreshNotice(null);
    } catch (err) {
      if (hasCached) {
        setReportRefreshNotice("Showing cached data; background refresh failed.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load data");
      }
    } finally {
      setLoading(false);
      setReportRefreshing(false);
    }
  };

  // Restore state from URL on mount
  useEffect(() => {
    if (!userFromUrl) return;
    if (status === "loading") return;

    if (!initialized) {
      setInitialized(true);
      setUsername(userFromUrl);
      setCurrentUsername(userFromUrl);
      persistLastUser(userFromUrl);
    }

    setError(null);
    void loadDashboardBundle(userFromUrl, colorFilter, timeClassFilter);
  }, [userFromUrl, initialized, colorFilter, timeClassFilter, authUserId, status]);

  // Update URL and persist last selected user
  const updateUrl = (user: string | null) => {
    if (user) {
      router.replace(`/dashboard?user=${encodeURIComponent(user)}`, { scroll: false });
      persistLastUser(user);
    }
  };

  const handleImportLichess = async () => {
    if (!lichessUsername.trim()) {
      setError("Please enter a Lichess username");
      return;
    }

    trackEvent("import.start", {
      properties: {
        site: "lichess",
        max_games: 200,
      },
    });

    setLoading(true);
    setError(null);
    setReport(null);
    setReportBlack(null);
    setImportResult(null);

    const trimmedUsername = lichessUsername.trim();

    try {
      const importResponse = await fetch(`${API_BASE_URL}/api/v1/import/lichess`, {
        method: "POST",
        headers: withTrackingHeaders({ "Content-Type": "application/json", ...authHeaders } as Record<string, string>),
        body: JSON.stringify({ username: trimmedUsername, max_games: 200 }),
      });

      if (!importResponse.ok) {
        const data = await importResponse.json().catch(() => ({}));
        throw new Error(
          data.detail || `Import failed: ${importResponse.status}`
        );
      }

      const importData: ImportResponse = await importResponse.json();
      setImportResult(importData);
      trackEvent("import.success", {
        properties: {
          site: "lichess",
          imported: importData.imported,
          skipped: importData.skipped,
        },
      });

      setUsername(trimmedUsername);
      setCurrentUsername(trimmedUsername);
      updateUrl(trimmedUsername);
      setGuestImportHistory(
        saveGuestHistoryEntry({
          username: trimmedUsername,
          site: "lichess",
          imported_at: new Date().toISOString(),
        }),
      );
      setReportRefreshNotice(null);
      clearCacheByPrefix(`dashboard:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`dashboard:variations:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`dashboard:insights:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`opening:${trimmedUsername.toLowerCase()}:`);

      const [whiteReportData, blackReportData] = await Promise.all([
        fetchReport(trimmedUsername, "white", timeClassFilter),
        fetchReport(trimmedUsername, "black", timeClassFilter),
      ]);
      setReport(whiteReportData);
      setReportBlack(blackReportData);
      setCached<DashboardReportCacheData>(
        getDashboardBundleCacheKey(trimmedUsername, colorFilter, timeClassFilter),
        {
          reportWhite: whiteReportData,
          reportBlack: blackReportData,
          importStatus: null,
        },
      );

      const status = await fetchImportStatus(trimmedUsername);
      if (status) {
        setImportStatus(status);
        setCached<DashboardReportCacheData>(
          getDashboardBundleCacheKey(trimmedUsername, colorFilter, timeClassFilter),
          {
            reportWhite: whiteReportData,
            reportBlack: blackReportData,
            importStatus: status,
          },
        );
      }
      if (isAuthenticated) {
        void fetchImportHistory();
      }
      const insightsData = await fetchInsights(trimmedUsername);
      setInsights(insightsData);
      setCached<InsightsProfile | null>(getDashboardInsightsCacheKey(trimmedUsername), insightsData);
    } catch (err) {
      trackEvent("import.failed", {
        properties: {
          site: "lichess",
          reason: err instanceof Error ? err.message : "An error occurred",
        },
      });
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleImportChesscom = async () => {
    if (!chesscomUsername.trim()) {
      setError("Please enter a Chess.com username");
      return;
    }

    trackEvent("import.start", {
      properties: {
        site: "chesscom",
        max_games: 200,
      },
    });

    setLoading(true);
    setError(null);
    setReport(null);
    setReportBlack(null);
    setImportResult(null);

    const trimmedUsername = chesscomUsername.trim();

    try {
      const importResponse = await fetch(`${API_BASE_URL}/api/v1/import/chesscom`, {
        method: "POST",
        headers: withTrackingHeaders({ "Content-Type": "application/json", ...authHeaders } as Record<string, string>),
        body: JSON.stringify({ username: trimmedUsername, max_games: 200 }),
      });

      if (!importResponse.ok) {
        const data = await importResponse.json().catch(() => ({}));
        throw new Error(
          data.detail || `Import failed: ${importResponse.status}`
        );
      }

      const importData: ImportResponse = await importResponse.json();
      setImportResult(importData);
      trackEvent("import.success", {
        properties: {
          site: "chesscom",
          imported: importData.imported,
          skipped: importData.skipped,
        },
      });

      setUsername(trimmedUsername);
      setCurrentUsername(trimmedUsername);
      updateUrl(trimmedUsername);
      setGuestImportHistory(
        saveGuestHistoryEntry({
          username: trimmedUsername,
          site: "chesscom",
          imported_at: new Date().toISOString(),
        }),
      );
      setReportRefreshNotice(null);
      clearCacheByPrefix(`dashboard:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`dashboard:variations:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`dashboard:insights:${trimmedUsername.toLowerCase()}:`);
      clearCacheByPrefix(`opening:${trimmedUsername.toLowerCase()}:`);

      const [whiteReportData, blackReportData] = await Promise.all([
        fetchReport(trimmedUsername, "white", timeClassFilter),
        fetchReport(trimmedUsername, "black", timeClassFilter),
      ]);
      setReport(whiteReportData);
      setReportBlack(blackReportData);
      setCached<DashboardReportCacheData>(
        getDashboardBundleCacheKey(trimmedUsername, colorFilter, timeClassFilter),
        {
          reportWhite: whiteReportData,
          reportBlack: blackReportData,
          importStatus: null,
        },
      );

      const status = await fetchImportStatus(trimmedUsername);
      if (status) {
        setImportStatus(status);
        setCached<DashboardReportCacheData>(
          getDashboardBundleCacheKey(trimmedUsername, colorFilter, timeClassFilter),
          {
            reportWhite: whiteReportData,
            reportBlack: blackReportData,
            importStatus: status,
          },
        );
      }
      if (isAuthenticated) {
        void fetchImportHistory();
      }
      const insightsData = await fetchInsights(trimmedUsername);
      setInsights(insightsData);
      setCached<InsightsProfile | null>(getDashboardInsightsCacheKey(trimmedUsername), insightsData);
    } catch (err) {
      trackEvent("import.failed", {
        properties: {
          site: "chesscom",
          reason: err instanceof Error ? err.message : "An error occurred",
        },
      });
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleHistoryItemClick = (item: ImportHistoryItem) => {
    trackEvent("feature.usage", {
      properties: {
        feature: "recently_analyzed_select",
      },
    });
    setUsername(item.username);
    setCurrentUsername(item.username);
    updateUrl(item.username);
    setReportRefreshNotice(null);
    setError(null);
    void loadDashboardBundle(item.username, colorFilter, timeClassFilter);
  };

  const handleRefresh = () => {
    if (!currentUsername) return;
    trackEvent("feature.usage", {
      properties: {
        feature: "dashboard_refresh",
      },
    });

    setReportRefreshNotice(null);
    setError(null);
    void loadDashboardBundle(currentUsername, colorFilter, timeClassFilter, { force: true });
    setInsightsLoading(true);
    void fetchInsights(currentUsername)
      .then((insightsData) => {
        setInsights(insightsData);
        setCached<InsightsProfile | null>(
          getDashboardInsightsCacheKey(currentUsername),
          insightsData,
        );
      })
      .catch(() => {
        // keep existing insights when refresh fails
      })
      .finally(() => {
        setInsightsLoading(false);
      });
  };

  const handleRefreshInsights = async () => {
    if (!currentUsername) return;
    trackEvent("insights.refresh.requested", {
      properties: {
        source: "dashboard",
      },
    });
    setInsightsRefreshing(true);
    try {
      const refreshed = await requestInsightsRefresh(currentUsername, true);
      if (refreshed) {
        trackEvent("insights.refresh.completed", {
          properties: {
            lifecycle_status: refreshed.lifecycle_status,
          },
        });
        setInsights(refreshed);
        setCached<InsightsProfile | null>(
          getDashboardInsightsCacheKey(currentUsername),
          refreshed,
        );
      }
    } finally {
      setInsightsRefreshing(false);
    }
  };

  const handleFilterChange = async (
    newColor: ColorFilter,
    newTimeClass: TimeClassFilter
  ) => {
    trackEvent("feature.usage", {
      properties: {
        feature: "dashboard_filter_change",
        color: newColor,
        time_class: newTimeClass,
      },
    });
    setColorFilter(newColor);
    setTimeClassFilter(newTimeClass);
    setReportRefreshNotice(null);

    if (currentUsername) {
      setError(null);
      await loadDashboardBundle(currentUsername, newColor, newTimeClass);
    }
  };

  const handleSignOut = () => {
    clearAllCache();
    void signOut();
  };

  // Recent users list for quick reload.
  const uniqueHistoryByUser = useMemo(() => {
    return [...importHistory].sort(
      (a, b) =>
        new Date(b.imported_at).getTime() - new Date(a.imported_at).getTime()
    );
  }, [importHistory]);

  // When no user in URL but we have history: auto-load last selected or most recent user
  useEffect(() => {
    if (userFromUrl) {
      hasAutoLoadedFromHistory.current = false;
      return;
    }
    if (uniqueHistoryByUser.length === 0) return;
    if (hasAutoLoadedFromHistory.current) return;

    hasAutoLoadedFromHistory.current = true;

    let lastUser: string | null = null;
    if (typeof window !== "undefined") {
      try {
        lastUser = localStorage.getItem(DASHBOARD_LAST_USER_KEY);
      } catch {
        lastUser = null;
      }
    }
    const userToLoad =
      lastUser &&
      uniqueHistoryByUser.some(
        (h) => h.username.toLowerCase() === lastUser.toLowerCase()
      )
        ? uniqueHistoryByUser.find(
            (h) => h.username.toLowerCase() === lastUser.toLowerCase()
          )!
        : uniqueHistoryByUser[0];

    handleHistoryItemClick(userToLoad);
  }, [
    userFromUrl,
    uniqueHistoryByUser,
    // handleHistoryItemClick intentionally omitted to avoid re-running on every render
  ]);

  const processedWhiteReport = useMemo(() => {
    if (!report) return null;
    return report.filter(
      (row) => row.opening_key !== "unknown" && row.opening_label !== "Unknown"
    );
  }, [report]);

  const processedBlackReport = useMemo(() => {
    if (!reportBlack) return null;
    return reportBlack.filter(
      (row) => row.opening_key !== "unknown" && row.opening_label !== "Unknown"
    );
  }, [reportBlack]);

  const insightsStatusLabel = useMemo(() => {
    const statusValue = insights?.lifecycle_status;
    if (!statusValue) return "Unavailable";
    if (statusValue === "queued") return "Queued";
    if (statusValue === "baseline_ready") return "Baseline ready";
    if (statusValue === "enriching") return "Refining";
    if (statusValue === "complete") return "Complete";
    if (statusValue === "not_enough_data") return "Not enough data";
    if (statusValue === "stale") return "Stale";
    if (statusValue === "failed") return "Failed";
    return "Unavailable";
  }, [insights?.lifecycle_status]);

  const coachingSummaryReady = useMemo(() => {
    if (!insights) return false;
    const statusValue = insights.lifecycle_status;
    if (statusValue !== "complete" && statusValue !== "stale") return false;
    const games = insights.features?.performance?.overall?.games || 0;
    return games > 0;
  }, [insights]);

  const coachingTemplateData = useMemo(() => {
    if (!insights) return null;

    const overall = insights.features?.performance?.overall;
    const totalGames = Number(overall?.games || 0);
    const totalWins = Number(overall?.wins || 0);
    const winRatePct = totalGames > 0 ? (totalWins / totalGames) * 100 : null;
    const overallScorePct = overall?.score_pct;

    const bestOpening = insights.features?.performance?.best_openings?.[0] || null;
    const weakOpening = insights.features?.performance?.weak_openings?.[0] || null;

    const phaseEntries = Object.entries(insights.features?.performance?.phase || {}).filter(
      ([, stats]) => typeof stats?.avg_cp_loss === "number" && Number.isFinite(stats.avg_cp_loss as number)
    ) as Array<[string, { avg_cp_loss: number }]>;

    let bestPhase: string | null = null;
    let weakPhase: string | null = null;
    if (phaseEntries.length > 0) {
      const sorted = [...phaseEntries].sort((a, b) => a[1].avg_cp_loss - b[1].avg_cp_loss);
      bestPhase = sorted[0][0];
      weakPhase = sorted[sorted.length - 1][0];
    }

    const underPressurePct = insights.features?.time_pressure?.score_pct_under_pressure;
    const overallPressureBaselinePct = insights.features?.time_pressure?.score_pct_overall ?? overallScorePct ?? null;
    const pressureGames = Number(insights.features?.time_pressure?.games_with_pressure || 0);
    const blundersUnderPressure = Number(insights.features?.time_pressure?.blunders_under_pressure || 0);
    const blundersTotalWithClock = Number(insights.features?.time_pressure?.blunders_total_with_clock || 0);
    const blundersUnderPressurePct = insights.features?.time_pressure?.blunders_under_pressure_pct;
    const lowTimeMovesDeep = Number(insights.features?.time_pressure?.low_time_moves_deep || 0);
    const movesWithClockDeep = Number(insights.features?.time_pressure?.moves_with_clock_deep || 0);
    const pressureDelta =
      typeof underPressurePct === "number" && typeof overallPressureBaselinePct === "number"
        ? underPressurePct - overallPressureBaselinePct
        : null;

    return {
      totalGames,
      totalWins,
      winRatePct,
      overallScorePct,
      bestOpening,
      weakOpening,
      bestPhase,
      weakPhase,
      underPressurePct,
      overallPressureBaselinePct,
      pressureGames,
      blundersUnderPressure,
      blundersTotalWithClock,
      blundersUnderPressurePct,
      lowTimeMovesDeep,
      movesWithClockDeep,
      pressureDelta,
    };
  }, [insights]);

  const playerTypeDescription = useMemo(() => {
    const styleLabel = insights?.features?.style?.label || "Developing profile";
    return `They currently profile as ${styleLabel}.`;
  }, [insights?.features?.style?.label]);

  const timePressureSummary = useMemo(() => {
    if (!coachingTemplateData) return "Time-pressure signals are not ready yet.";
    const under = coachingTemplateData.underPressurePct;
    const baseline = coachingTemplateData.overallPressureBaselinePct;
    const delta = coachingTemplateData.pressureDelta;
    const pressureGames = coachingTemplateData.pressureGames;
    const blundersUnderPressure = coachingTemplateData.blundersUnderPressure;
    const blundersTotalWithClock = coachingTemplateData.blundersTotalWithClock;
    const blundersUnderPressurePct = coachingTemplateData.blundersUnderPressurePct;
    const lowTimeMovesDeep = coachingTemplateData.lowTimeMovesDeep;
    const movesWithClockDeep = coachingTemplateData.movesWithClockDeep;

    if (
      blundersTotalWithClock >= 5 &&
      typeof blundersUnderPressurePct === "number" &&
      Number.isFinite(blundersUnderPressurePct)
    ) {
      const pctText = formatWholePercent(blundersUnderPressurePct) || `${blundersUnderPressurePct.toFixed(1)}%`;
      const countText = `${blundersUnderPressure}/${blundersTotalWithClock}`;
      const movePressureSharePct =
        movesWithClockDeep > 0
          ? Math.round((lowTimeMovesDeep / movesWithClockDeep) * 100)
          : null;
      const moveShareText =
        movePressureSharePct !== null
          ? `while only ${movePressureSharePct}% of clocked moves were under time pressure`
          : "relative to how often low-time positions occurred";

      if (blundersUnderPressurePct >= 70) {
        return `Time pressure is the main blunder trigger: ${pctText} of blunders (${countText}) happened with low time, ${moveShareText}.`;
      }
      if (blundersUnderPressurePct >= 50) {
        return `A large share of blunders came under low time: ${pctText} (${countText}), ${moveShareText}.`;
      }
      if (blundersUnderPressurePct >= 30) {
        return `${pctText} of blunders (${countText}) happened under time pressure. It contributes, but it is not the only issue.`;
      }
      return `Only ${pctText} of blunders (${countText}) happened under time pressure, so most blunders came outside time trouble.`;
    }

    if (
      typeof under !== "number" ||
      !Number.isFinite(under) ||
      typeof baseline !== "number" ||
      !Number.isFinite(baseline) ||
      pressureGames <= 0
    ) {
      return "There is not enough clock data yet to judge how they handle time pressure.";
    }

    const underPct = formatWholePercent(under) || `${under.toFixed(1)}%`;
    const basePct = formatWholePercent(baseline) || `${baseline.toFixed(1)}%`;
    const dipText =
      typeof delta === "number" && Number.isFinite(delta)
        ? `${Math.abs(Math.round(delta))}-point`
        : null;
    const sampleSuffix =
      pressureGames > 0 ? ` across ${pressureGames} low-time games` : "";
    if (typeof delta !== "number" || !Number.isFinite(delta)) {
      return `In low-time moments${sampleSuffix}, they score ${underPct}, compared with ${basePct} overall.`;
    }
    if (delta <= -25) {
      return `In low-time moments${sampleSuffix}, they score ${underPct} versus ${basePct} overall. That is a ${dipText} drop and usually means time-pressure collapses are deciding results.`;
    }
    if (delta <= -15) {
      return `In low-time moments${sampleSuffix}, they score ${underPct} versus ${basePct} overall. That is a ${dipText} drop: noticeable, but still a competitive level under pressure.`;
    }
    if (delta <= -8) {
      return `In low-time moments${sampleSuffix}, they score ${underPct} versus ${basePct} overall. That ${dipText} dip is a mild pressure penalty.`;
    }
    if (delta < 4) {
      return `Their level is fairly stable under clock pressure: ${underPct} in low-time moments${sampleSuffix} versus ${basePct} overall.`;
    }
    return `They handle time pressure very well: ${underPct} in low-time moments${sampleSuffix} versus ${basePct} overall.`;
  }, [coachingTemplateData]);

  const strengthsBullets = useMemo(() => {
    if (!coachingTemplateData) return [];
    const bullets: string[] = [];

    if (coachingTemplateData.totalGames > 0 && coachingTemplateData.totalWins >= 0) {
      const winPct = formatWholePercent(coachingTemplateData.winRatePct);
      if (winPct) {
        bullets.push(`They have won ${winPct} of their last ${coachingTemplateData.totalGames} games.`);
      }
    }

    if (coachingTemplateData.bestOpening) {
      const scoreText = formatWholePercent(coachingTemplateData.bestOpening.score_pct);
      const gamesText = coachingTemplateData.bestOpening.games;
      bullets.push(
        `Their best-performing opening cluster starts with ${coachingTemplateData.bestOpening.opening}. This is where they have their highest scoring rate${scoreText ? ` (${scoreText}` : ""}${scoreText ? ` over ${gamesText} games)` : "."}`
      );
    }

    if (coachingTemplateData.bestPhase) {
      bullets.push(
        `${formatPhaseLabel(coachingTemplateData.bestPhase)} is their most stable phase in current analysis.`
      );
    }

    return bullets.slice(0, 3);
  }, [coachingTemplateData]);

  const weaknessesBullets = useMemo(() => {
    if (!coachingTemplateData) return [];
    const bullets: string[] = [];

    if (coachingTemplateData.weakOpening) {
      const weakScoreText = formatWholePercent(coachingTemplateData.weakOpening.score_pct);
      bullets.push(
        `Their toughest opening cluster starts with ${coachingTemplateData.weakOpening.opening}${weakScoreText ? ` (${weakScoreText})` : ""}, where results are less stable.`
      );
    }

    if (coachingTemplateData.weakPhase) {
      bullets.push(
        `${formatPhaseLabel(coachingTemplateData.weakPhase)} is the phase where they leak the most value.`
      );
    }

    if (typeof coachingTemplateData.pressureDelta === "number" && coachingTemplateData.pressureDelta <= -4) {
      bullets.push("They tend to lose control when the clock gets low.");
    }

    return bullets.slice(0, 3);
  }, [coachingTemplateData]);

  const recurringMistakesBullets = useMemo(() => {
    const themes = insights?.features?.recurring_themes || [];
    if (!themes.length) return [];
    return themes.slice(0, 3).map((item) => {
      const label = humanizeTheme(item.theme);
      return `Recurring pattern: ${label} (${item.count}).`;
    });
  }, [insights?.features?.recurring_themes]);

  const coachingFocusBullets = useMemo(() => {
    if (!coachingTemplateData) return [];
    const bullets: string[] = [];
    if (coachingTemplateData.weakPhase) {
      bullets.push(`Prioritize targeted ${formatPhaseLabel(coachingTemplateData.weakPhase).toLowerCase()} drills.`);
    }
    if (coachingTemplateData.weakOpening) {
      bullets.push(`Review core plans in ${coachingTemplateData.weakOpening.opening} structures.`);
    }
    if (typeof coachingTemplateData.pressureDelta === "number" && coachingTemplateData.pressureDelta <= -4) {
      bullets.push("Use simpler plans earlier when time gets tight to avoid late-clock collapses.");
    }
    if (!bullets.length && coachingTemplateData.bestPhase) {
      bullets.push(`Keep building on ${formatPhaseLabel(coachingTemplateData.bestPhase).toLowerCase()} consistency.`);
    }
    return bullets.slice(0, 3);
  }, [coachingTemplateData]);

  if (status === "loading") {
    return (
      <div className="opening-page min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest text-[color:var(--zen-muted)]">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div role="main" className="opening-page max-w-[1400px] mx-auto px-4 sm:px-6 py-10">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="opening-title text-3xl sm:text-4xl font-semibold tracking-tight">
            Korchess
          </h1>
          <p className="opening-subtitle mt-2 text-sm sm:text-base text-[color:var(--zen-muted)]">
            Analyze your chess opening performance from your games
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <a
            className="px-2.5 py-1.5 rounded-md border border-[color:var(--zen-border)] bg-transparent font-mono text-[11px] text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface)] transition-colors"
            href="https://buymeacoffee.com/elisurfz7"
            rel="noreferrer"
            target="_blank"
          >
            Buy me a coffee
          </a>
          {isAuthenticated ? (
            <>
              <button
                type="button"
                onClick={() => router.push("/profile/edit")}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[color:var(--zen-border)] bg-[color:var(--zen-surface)] hover:bg-[color:var(--zen-surface-2)] transition-colors cursor-pointer"
              >
                <div className="w-9 h-9 flex items-center justify-center shrink-0">
                  <span
                    className="material-symbols-outlined text-lg text-[color:var(--zen-text)]"
                    style={{
                      fontVariationSettings: "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 48",
                    }}
                  >
                    chess_{profileAvatar}
                  </span>
                </div>
                <span className="font-display text-[8px] uppercase tracking-wider text-[color:var(--zen-accent)]">
                  {profileUsername || "..."}
                </span>
              </button>
              <button
                onClick={handleSignOut}
                className="bg-primary text-white font-display text-[9px] uppercase tracking-wider px-5 py-2.5 rounded-lg border-2 border-[#7d8fd4] shadow-[0_4px_0_0_#3b4887] hover:bg-primary/90 active:translate-y-1 active:shadow-[0_2px_0_0_#3b4887] transition-all"
              >
                SIGN OUT
              </button>
            </>
          ) : (
            <button
              onClick={() => {
                trackEvent("auth.signin.clicked", {
                  properties: {
                    source: "dashboard_header",
                  },
                });
                signIn("google", { callbackUrl: "/dashboard" });
              }}
              className="bg-primary text-white font-display text-[9px] uppercase tracking-wider px-5 py-2.5 rounded-lg border-2 border-[#7d8fd4] shadow-[0_4px_0_0_#3b4887] hover:bg-primary/90 active:translate-y-1 active:shadow-[0_2px_0_0_#3b4887] transition-all"
            >
              SIGN IN
            </button>
          )}
        </div>
      </div>

      <div className="min-w-0 space-y-6">
        <div className="zen-surface opening-frame p-5 sm:p-6">
        {/* Import inputs row */}
        <div className="flex flex-col sm:flex-row gap-3 sm:gap-4">
          {/* Lichess import */}
          <div className="flex-1">
            <label
              htmlFor="lichess-username"
              className="block text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2"
            >
              Lichess username
            </label>
            <div className="flex items-center gap-3">
              <input
                id="lichess-username"
                type="text"
                value={lichessUsername}
                onChange={(e) => setLichessUsername(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleImportLichess()}
                placeholder="e.g. hikaru"
                className="zen-input w-full px-4 py-3 outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)] transition"
                disabled={loading}
              />
              <button
                onClick={handleImportLichess}
                disabled={loading || !lichessUsername.trim()}
                className="pixel-button shrink-0 px-5 py-3 rounded-xl font-medium text-sm border border-[color:var(--zen-border)] text-white hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                {loading ? "Importing..." : "Import"}
              </button>
            </div>
          </div>

          {/* Chess.com import */}
          <div className="flex-1">
            <label
              htmlFor="chesscom-username"
              className="block text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2"
            >
              Chess.com username
            </label>
            <div className="flex items-center gap-3">
              <input
                id="chesscom-username"
                type="text"
                value={chesscomUsername}
                onChange={(e) => setChesscomUsername(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleImportChesscom()}
                placeholder="e.g. hikaru"
                className="zen-input w-full px-4 py-3 outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)] transition"
                disabled={loading}
              />
              <button
                onClick={handleImportChesscom}
                disabled={loading || !chesscomUsername.trim()}
                className="pixel-button shrink-0 px-5 py-3 rounded-xl font-medium text-sm border border-[color:var(--zen-border)] text-white hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                {loading ? "Importing..." : "Import"}
              </button>
            </div>
          </div>
        </div>

        {/* Recently analyzed usernames (moved here to free the left side) */}
        <div className="mt-5 border-t border-[color:var(--zen-border)]/70 pt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
              Recently analyzed
            </p>
          </div>
          {uniqueHistoryByUser.length === 0 ? (
            <p className="text-sm text-[color:var(--zen-muted)] py-2">
              No users analyzed yet
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {uniqueHistoryByUser.map((item) => {
                const isSelected =
                  currentUsername &&
                  item.username.toLowerCase() === currentUsername.toLowerCase();
                return (
                  <button
                    key={item.username}
                    type="button"
                    onClick={() => handleHistoryItemClick(item)}
                    className={[
                      "zen-pill px-3 py-2 text-sm transition cursor-pointer flex items-center max-w-full",
                      isSelected
                        ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-accent)] border border-[color:var(--zen-accent)]"
                        : "text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)] hover:text-[color:var(--zen-accent)]",
                    ].join(" ")}
                  >
                    <span className="truncate">{item.username}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Import Result */}
        {importResult && (
          <div className="mt-4 zen-surface-flat px-4 py-3">
            <p className="text-sm">
              <span className="text-[color:var(--zen-success)] font-semibold">
                Imported {importResult.imported}
              </span>{" "}
              <span className="text-[color:var(--zen-muted)]">
                for <span className="text-[color:var(--zen-text)] font-medium">{importResult.username}</span>
                {importResult.skipped > 0 ? ` (${importResult.skipped} skipped)` : ""}
              </span>
            </p>
          </div>
        )}

        {/* Error Display */}
        {error && (
          <div className="mt-4 zen-surface-flat px-4 py-3 border-[color:var(--zen-danger)]/30">
            <p className="text-sm text-[color:var(--zen-danger)]">{error}</p>
          </div>
        )}

        {/* Data Freshness Line */}
        {importStatus?.imported_at && currentUsername && !loading && (
          <div className="mt-5 zen-surface-flat px-4 py-3">
            <p className="text-sm text-[color:var(--zen-muted)]">
              Report generated from{" "}
              <span className="text-[color:var(--zen-text)] font-medium">
                {importStatus.total_games}
              </span>{" "}
              games
              {importStatus.total_games > 0 && (
                <>
                  {" "}
                  (last import:{" "}
                  {new Date(importStatus.imported_at).toLocaleString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  {/* {importStatus.last_imported === 0 &&
                    `, imported: 0, skipped: ${importStatus.last_skipped}`} */}
                  )
                </>
              )}
            </p>
          </div>
        )}

        </div>

        {/* AI Coaching Summary - only render when insights are ready */}
        {SHOW_COACHING_SUMMARY && currentUsername && coachingSummaryReady && insights && (
          <div className="zen-surface opening-frame p-8 sm:p-10 border border-[color:var(--zen-border)] rounded-2xl">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-8">
              <div>
                <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
                  AI Insights
                </p>
                <h3 className="text-2xl sm:text-3xl font-semibold text-[color:var(--zen-text)] mt-1">
                  Coaching summary for {currentUsername}
                </h3>
              </div>
              <div className="flex items-center gap-3">
                <span className="zen-pill px-4 py-2 text-sm uppercase tracking-wide text-[color:var(--zen-muted)]">
                  {insightsStatusLabel}
                </span>
                <button
                  onClick={handleRefreshInsights}
                  disabled={insightsRefreshing || insightsLoading}
                  className="zen-pill px-4 py-2 text-sm font-medium text-[color:var(--zen-text)] hover:text-[color:var(--zen-accent)] transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {insightsRefreshing ? "Refreshing..." : "Refresh AI"}
                </button>
              </div>
            </div>

            <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-8">
              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2">
                  Player Type
                </p>
                <p className="text-2xl font-semibold text-[color:var(--zen-text)]">
                  {insights.features?.style?.label || "Developing profile"}
                </p>
                <p className="mt-3 text-base text-[color:var(--zen-muted)] leading-relaxed">
                  {playerTypeDescription}
                </p>
              </div>

              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-3">
                  Time Pressure
                </p>
                <p className="text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                  {timePressureSummary}
                </p>
              </div>

              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="flex items-center gap-2 mb-3">
                  <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                    Strengths
                  </p>
                </div>
                <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                  {strengthsBullets.map((line, idx) => (
                    <li key={`strength-${idx}`} className="flex gap-2">
                      <span className="text-[color:var(--zen-success)] shrink-0">✓</span>
                      <span>{line}</span>
                    </li>
                  ))}
                  {strengthsBullets.length === 0 && (
                    <li className="text-[color:var(--zen-muted)]">Strength signals are still being computed.</li>
                  )}
                </ul>
              </div>

              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="flex items-center gap-2 mb-3">
                  <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                    Weaknesses
                  </p>
                </div>
                <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                  {weaknessesBullets.map((line, idx) => (
                    <li key={`weakness-${idx}`} className="flex gap-2">
                      <span className="text-[color:var(--zen-danger)] shrink-0">✗</span>
                      <span>{line}</span>
                    </li>
                  ))}
                  {weaknessesBullets.length === 0 && (
                    <li className="text-[color:var(--zen-muted)]">No consistent weakness pattern has emerged yet.</li>
                  )}
                </ul>
              </div>

              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="flex items-center gap-2 mb-3">
                  <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                    Recurring Mistakes
                  </p>
                </div>
                <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                  {recurringMistakesBullets.map((line, idx) => (
                    <li key={`mistake-${idx}`} className="flex gap-2">
                      <span className="text-[color:var(--zen-accent)] shrink-0">⚠</span>
                      <span>{line}</span>
                    </li>
                  ))}
                  {recurringMistakesBullets.length === 0 && (
                    <li className="text-[color:var(--zen-muted)]">Recurring patterns are still being learned.</li>
                  )}
                </ul>
              </div>

              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="flex items-center gap-2 mb-3">
                  <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                    Coaching Focus
                  </p>
                </div>
                <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                  {coachingFocusBullets.map((line, idx) => (
                    <li key={`focus-${idx}`} className="flex gap-2">
                      <span className="text-[color:var(--zen-accent)] shrink-0">→</span>
                      <span>{line}</span>
                    </li>
                  ))}
                  {coachingFocusBullets.length === 0 && (
                    <li className="text-[color:var(--zen-muted)]">No specific coaching focus is available yet.</li>
                  )}
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* Top openings - split by color */}
        <div className="zen-surface opening-frame p-5 sm:p-6 border border-[color:var(--zen-border)] rounded-2xl">
        {currentUsername && (
          <div className="flex flex-col lg:flex-row gap-3 lg:items-center lg:justify-between mb-4">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={timeClassFilter}
                onChange={(e) =>
                  handleFilterChange("white", e.target.value as TimeClassFilter)
                }
                className="zen-input px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)]"
                disabled={loading}
              >
                <option value="all">All time controls</option>
                <option value="blitz">Blitz</option>
                <option value="rapid">Rapid</option>
                <option value="classical">Classical</option>
              </select>
            </div>

            <button
              onClick={handleRefresh}
              disabled={loading || reportRefreshing}
              className="zen-pill px-4 py-2.5 text-sm font-medium text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {reportRefreshing ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        )}

        {reportRefreshing && report && reportBlack && (
          <p className="mb-3 text-xs text-[color:var(--zen-muted)]">
            Refreshing ...
          </p>
        )}

        {reportRefreshNotice && report && reportBlack && (
          <p className="mb-3 text-xs text-[color:var(--zen-muted)]">
            {reportRefreshNotice}
          </p>
        )}

        {loading && (
          <div className="py-10 flex justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
          </div>
        )}

        {report && reportBlack && !loading && (
          <div>
            <div className="flex items-baseline justify-between gap-2 mb-4">
              <h2 className="text-lg sm:text-xl font-semibold text-[color:var(--zen-text)]">
                Top openings by color
              </h2>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {[
                {
                  title: "Top 5 as White",
                  rows: processedWhiteReport || [],
                  source: "dashboard_openings_white_table",
                  color: "white" as const,
                },
                {
                  title: "Top 5 as Black",
                  rows: processedBlackReport || [],
                  source: "dashboard_openings_black_table",
                  color: "black" as const,
                },
              ].map((section) => (
                <div
                  key={section.title}
                  className="overflow-hidden rounded-2xl border border-[color:var(--zen-border)]"
                >
                  <div className="px-4 py-3 bg-[color:var(--zen-surface-2)] border-b border-[color:var(--zen-border)]">
                    <h3 className="text-sm font-semibold uppercase tracking-wider text-[color:var(--zen-text)]">
                      {section.title}
                    </h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="opening-table min-w-full">
                      <thead className="bg-[color:var(--zen-surface-2)]">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
                            Opening
                          </th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">Games</th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">Wins</th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">Draws</th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">Losses</th>
                          <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">Score %</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[color:var(--zen-border)]">
                        {section.rows.map((opening) => {
                          const parsed = parseOpeningName(opening.opening_label);
                          const badgeText =
                            opening.opening_key === "unknown"
                              ? "UNK"
                              : opening.opening_key.slice(0, 3).toUpperCase();
                          return (
                            <tr
                              key={`${section.title}-${opening.opening_key}`}
                              onClick={() => {
                                if (currentUsername) {
                                  trackEvent("opening.view", {
                                    properties: {
                                      source: section.source,
                                    },
                                  });
                                  const detailParams = new URLSearchParams({
                                    site: "all",
                                    color: section.color,
                                    time_class: timeClassFilter,
                                  });
                                  router.push(
                                    `/opening/${encodeURIComponent(currentUsername)}/${encodeURIComponent(opening.opening_key)}?${detailParams.toString()}`
                                  );
                                }
                              }}
                              className="opening-list-row group cursor-pointer hover:bg-[color:var(--zen-surface)] transition"
                            >
                              <td className="px-4 py-4">
                                <div className="opening-row-main font-semibold text-base sm:text-lg flex items-center gap-2 sm:gap-3 flex-wrap">
                                  <span
                                    className="eco-badge"
                                    style={{
                                      borderColor:
                                        opening.score_pct >= 55
                                          ? "var(--zen-success)"
                                          : opening.score_pct <= 45
                                            ? "var(--zen-danger)"
                                            : "var(--zen-accent)",
                                      color:
                                        opening.score_pct >= 55
                                          ? "var(--zen-success)"
                                          : opening.score_pct <= 45
                                            ? "var(--zen-danger)"
                                            : "var(--zen-accent)",
                                    }}
                                  >
                                    {badgeText}
                                  </span>
                                  <span>{parsed.main}</span>
                                  {parsed.variation && (
                                    <span className="font-normal text-[color:var(--zen-muted)]">
                                      {" : "}
                                      {parsed.variation}
                                    </span>
                                  )}
                                </div>
                              </td>
                              <td className="opening-row-stat px-4 py-4 text-right tabular-nums">{opening.games}</td>
                              <td className="opening-row-stat px-4 py-4 text-right tabular-nums text-[color:var(--zen-success)] font-medium">{opening.wins}</td>
                              <td className="opening-row-stat px-4 py-4 text-right tabular-nums text-[color:var(--zen-muted)]">{opening.draws}</td>
                              <td className="opening-row-stat px-4 py-4 text-right tabular-nums text-[color:var(--zen-danger)] font-medium">{opening.losses}</td>
                              <td className="opening-row-stat px-4 py-4 text-right tabular-nums">
                                <span
                                  className="font-semibold"
                                  style={{
                                    color:
                                      opening.score_pct >= 55
                                        ? "var(--zen-success)"
                                        : opening.score_pct <= 45
                                          ? "var(--zen-danger)"
                                          : "var(--zen-text)",
                                  }}
                                >
                                  {opening.score_pct.toFixed(1)}%
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {section.rows.length === 0 && (
                      <div className="p-8 text-center text-[color:var(--zen-muted)]">
                        No openings found for this color.
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!report && !reportBlack && !loading && !error && (
          <div className="zen-surface-flat p-10 text-center">
            <p className="text-[color:var(--zen-muted)]">
              Select a source, enter a username, and click Import Games to see opening
              statistics.
            </p>
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
