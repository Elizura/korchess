"use client";

// This page uses client-side routing/searchParams; force dynamic rendering to
// avoid Next.js "useSearchParams() should be wrapped in a suspense boundary" build errors.
export const dynamic = "force-dynamic";

import { useState, useMemo, useEffect, useRef, Fragment } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { signOut, useSession } from "next-auth/react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

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

interface VariationStats {
  variation_key: string;
  variation_label: string;
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

export default function DashboardPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session, status } = useSession();

  const authHeaders = useMemo((): Record<string, string> => {
    if (!session?.idToken) {
      return {};
    }
    return { Authorization: `Bearer ${session.idToken}` };
  }, [session?.idToken]);
  
  const [username, setUsername] = useState("");
  const [lichessUsername, setLichessUsername] = useState("");
  const [chesscomUsername, setChesscomUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<OpeningStats[] | null>(null);
  const [expandedOpenings, setExpandedOpenings] = useState<Record<string, boolean>>({});
  const [variationsByOpening, setVariationsByOpening] = useState<Record<string, VariationStats[]>>({});
  const [variationsLoading, setVariationsLoading] = useState<Record<string, boolean>>({});
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [colorFilter, setColorFilter] = useState<ColorFilter>("white");
  const [timeClassFilter, setTimeClassFilter] =
    useState<TimeClassFilter>("all");
  const [currentUsername, setCurrentUsername] = useState<string | null>(null);
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [hideUnknown, setHideUnknown] = useState(false);
  const [profileUsername, setProfileUsername] = useState<string>("");
  const [profileAvatar, setProfileAvatar] = useState<string>("pawn");
  const [sortConfig, setSortConfig] = useState<{
    key: keyof OpeningStats;
    direction: "asc" | "desc";
  }>({ key: "games", direction: "desc" });
  const [initialized, setInitialized] = useState(false);
  const hasAutoLoadedFromHistory = useRef(false);
  const [importHistory, setImportHistory] = useState<ImportHistoryItem[]>([]);
  const [insights, setInsights] = useState<InsightsProfile | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsRefreshing, setInsightsRefreshing] = useState(false);

  // Redirect unauthenticated users to signup
  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/signup");
    }
  }, [status, router]);

  // Redirect authenticated users who haven't completed onboarding; fetch profile for nav
  useEffect(() => {
    if (status !== "authenticated" || !session?.idToken) return;

    const checkOnboarding = async () => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/v1/auth/profile`,
          { headers: { Authorization: `Bearer ${session.idToken}` } }
        );
        if (res.status === 401) {
          router.replace("/signup");
          return;
        }
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
  }, [status, session?.idToken, router]);

  // Fetch import history when authenticated
  useEffect(() => {
    if (status === "authenticated" && session?.idToken) {
      fetchImportHistory();
    }
  }, [status, session?.idToken]);

  useEffect(() => {
    if (status !== "authenticated" || !session?.idToken || !currentUsername) {
      setInsights(null);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loadInsights = async () => {
      if (!cancelled) {
        setInsightsLoading(true);
      }
      try {
        const data = await fetchInsights(currentUsername);
        if (cancelled) return;
        setInsights(data);
        const lifecycleStatus = data?.lifecycle_status;
        if (
          lifecycleStatus === "queued" ||
          lifecycleStatus === "baseline_ready" ||
          lifecycleStatus === "enriching"
        ) {
          timer = setTimeout(loadInsights, 8000);
        }
      } finally {
        if (!cancelled) {
          setInsightsLoading(false);
        }
      }
    };

    loadInsights();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [status, session?.idToken, currentUsername, authHeaders]);

  // Fetch combined report across all sites
  const fetchReport = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
  ) => {
    const params = new URLSearchParams();
    params.set("color", color);
    params.set("time_class", timeClass);
    params.set("limit", "10");

    const response = await fetch(
      `${API_BASE_URL}/api/v1/openings/all/${encodeURIComponent(user)}?${params}`,
      { headers: authHeaders }
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to fetch report: ${response.status}`);
    }

    return response.json();
  };

  const fetchImportStatus = async (user: string) => {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/import-status/all/${encodeURIComponent(user)}`,
      { headers: authHeaders }
    );
    
    if (!response.ok) {
      return null; // Silently handle - not critical
    }
    
    return response.json();
  };

  const fetchImportHistory = async () => {
    if (!session?.idToken) return;
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/import/history`, {
        headers: authHeaders,
      });
      if (response.ok) {
        const data = await response.json();
        setImportHistory(data.history || []);
      }
    } catch {
      // Silently ignore - not critical
    }
  };

  const fetchInsights = async (user: string): Promise<InsightsProfile | null> => {
    if (!session?.idToken) {
      return null;
    }
    const params = new URLSearchParams();
    params.set("username", user);
    params.set("site", "all");

    const response = await fetch(
      `${API_BASE_URL}/api/v1/insights/profile?${params.toString()}`,
      { headers: authHeaders }
    );
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    return data as InsightsProfile;
  };

  const requestInsightsRefresh = async (user: string, force = true): Promise<InsightsProfile | null> => {
    if (!session?.idToken) {
      return null;
    }
    const response = await fetch(`${API_BASE_URL}/api/v1/insights/profile`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders,
      } as Record<string, string>,
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

  const fetchVariations = async (user: string, openingKey: string) => {
    if (!session?.idToken) {
      return [];
    }
    const params = new URLSearchParams();
    params.set("opening_key", openingKey);
    params.set("color", colorFilter);
    params.set("time_class", timeClassFilter);

    const response = await fetch(
      `${API_BASE_URL}/api/v1/openings/all/${encodeURIComponent(user)}/variations?${params}`,
      { headers: authHeaders }
    );

    if (!response.ok) {
      return [];
    }

    return response.json();
  };

  // Restore state from URL on mount and when session becomes available
  useEffect(() => {
    const userFromUrl = searchParams.get("user");
    if (!userFromUrl) return;

    if (!initialized) {
      setInitialized(true);
      setUsername(userFromUrl);
      setCurrentUsername(userFromUrl);
      persistLastUser(userFromUrl);
    }

    // Wait for session to be ready before loading - don't set error if session is still loading
    if (!session?.idToken) {
      return; // Session may still be hydrating; effect will re-run when it's ready
    }

    setError(null);
    const loadData = async () => {
      setLoading(true);
      try {
        const [reportData, statusData] = await Promise.all([
          fetchReport(userFromUrl, colorFilter, timeClassFilter),
          fetchImportStatus(userFromUrl)
        ]);
        setReport(reportData);
        if (statusData) {
          setImportStatus(statusData);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load data");
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [searchParams, initialized, colorFilter, timeClassFilter, router, session?.idToken]);

  // Update URL and persist last selected user
  const updateUrl = (user: string | null) => {
    if (user) {
      router.replace(`/dashboard?user=${encodeURIComponent(user)}`, { scroll: false });
      persistLastUser(user);
    }
  };

  const handleImportLichess = async () => {
    if (!session?.idToken) {
      setError("Please sign in with Google to continue.");
      return;
    }
    if (!lichessUsername.trim()) {
      setError("Please enter a Lichess username");
      return;
    }

    setLoading(true);
    setError(null);
    setReport(null);
    setImportResult(null);

    const trimmedUsername = lichessUsername.trim();

    try {
      const importResponse = await fetch(`${API_BASE_URL}/api/v1/import/lichess`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders } as Record<string, string>,
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

      setUsername(trimmedUsername);
      setCurrentUsername(trimmedUsername);
      updateUrl(trimmedUsername);

      const reportData = await fetchReport(
        trimmedUsername,
        colorFilter,
        timeClassFilter
      );
      setReport(reportData);

      const status = await fetchImportStatus(trimmedUsername);
      if (status) {
        setImportStatus(status);
      }
      fetchImportHistory();
      const insightsData = await fetchInsights(trimmedUsername);
      setInsights(insightsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleImportChesscom = async () => {
    if (!session?.idToken) {
      setError("Please sign in with Google to continue.");
      return;
    }
    if (!chesscomUsername.trim()) {
      setError("Please enter a Chess.com username");
      return;
    }

    setLoading(true);
    setError(null);
    setReport(null);
    setImportResult(null);

    const trimmedUsername = chesscomUsername.trim();

    try {
      const importResponse = await fetch(`${API_BASE_URL}/api/v1/import/chesscom`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders } as Record<string, string>,
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

      setUsername(trimmedUsername);
      setCurrentUsername(trimmedUsername);
      updateUrl(trimmedUsername);

      const reportData = await fetchReport(
        trimmedUsername,
        colorFilter,
        timeClassFilter
      );
      setReport(reportData);

      const status = await fetchImportStatus(trimmedUsername);
      if (status) {
        setImportStatus(status);
      }
      fetchImportHistory();
      const insightsData = await fetchInsights(trimmedUsername);
      setInsights(insightsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleHistoryItemClick = (item: ImportHistoryItem) => {
    setCurrentUsername(item.username);
    updateUrl(item.username);
    setReport(null);
    setInsights(null);
    setLoading(true);
    setInsightsLoading(true);
    setError(null);

    // Openings flow: report + import status (clears loading when done)
    Promise.all([
      fetchReport(item.username, colorFilter, timeClassFilter),
      fetchImportStatus(item.username),
    ])
      .then(([reportData, statusData]) => {
        setReport(reportData);
        if (statusData) {
          setImportStatus(statusData);
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load data");
      })
      .finally(() => {
        setLoading(false);
      });

    // Insights flow: run in parallel (clears insightsLoading when done)
    fetchInsights(item.username)
      .then((insightsData) => {
        setInsights(insightsData);
      })
      .catch(() => {
        setInsights(null);
      })
      .finally(() => {
        setInsightsLoading(false);
      });
  };

  const handleRefresh = () => {
    if (!currentUsername) return;

    setLoading(true);
    setInsightsLoading(true);
    setError(null);

    // Openings flow: report + import status
    Promise.all([
      fetchReport(currentUsername, colorFilter, timeClassFilter),
      fetchImportStatus(currentUsername),
    ])
      .then(([reportData, statusData]) => {
        setReport(reportData);
        if (statusData) {
          setImportStatus(statusData);
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "An error occurred");
      })
      .finally(() => {
        setLoading(false);
      });

    // Insights flow: run in parallel
    fetchInsights(currentUsername)
      .then((insightsData) => {
        setInsights(insightsData);
      })
      .catch(() => {
        setInsights(null);
      })
      .finally(() => {
        setInsightsLoading(false);
      });
  };

  const handleRefreshInsights = async () => {
    if (!currentUsername) return;
    setInsightsRefreshing(true);
    try {
      const refreshed = await requestInsightsRefresh(currentUsername, true);
      if (refreshed) {
        setInsights(refreshed);
      }
    } finally {
      setInsightsRefreshing(false);
    }
  };

  const handleFilterChange = async (
    newColor: ColorFilter,
    newTimeClass: TimeClassFilter
  ) => {
    setColorFilter(newColor);
    setTimeClassFilter(newTimeClass);

    if (currentUsername) {
      setLoading(true);
      setError(null);
      try {
        const reportData = await fetchReport(
          currentUsername,
          newColor,
          newTimeClass
        );
        setReport(reportData);
        
        // Also fetch status
        const status = await fetchImportStatus(currentUsername);
        if (status) {
          setImportStatus(status);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    }
  };

  const toggleOpening = async (openingKey: string) => {
    setExpandedOpenings((prev) => ({
      ...prev,
      [openingKey]: !prev[openingKey],
    }));

    if (!variationsByOpening[openingKey] && currentUsername) {
      setVariationsLoading((prev) => ({ ...prev, [openingKey]: true }));
      const variations = await fetchVariations(currentUsername, openingKey);
      setVariationsByOpening((prev) => ({ ...prev, [openingKey]: variations }));
      setVariationsLoading((prev) => ({ ...prev, [openingKey]: false }));
    }
  };

  const handleSort = (key: keyof OpeningStats) => {
    let direction: "asc" | "desc" = "desc";
    if (sortConfig.key === key && sortConfig.direction === "desc") {
      direction = "asc";
    }
    setSortConfig({ key, direction });
  };

  // Process report: filter -> sort (backend already returns top 10 by games)
  // Group import history by username (one entry per user, most recent first)
  const uniqueHistoryByUser = useMemo(() => {
    const byUser = new Map<string, ImportHistoryItem>();
    for (const item of importHistory) {
      const key = item.username.toLowerCase();
      const existing = byUser.get(key);
      if (
        !existing ||
        new Date(item.imported_at).getTime() > new Date(existing.imported_at).getTime()
      ) {
        byUser.set(key, item);
      }
    }
    return Array.from(byUser.values()).sort(
      (a, b) =>
        new Date(b.imported_at).getTime() - new Date(a.imported_at).getTime()
    );
  }, [importHistory]);

  // When no user in URL but we have history: auto-load last selected or most recent user
  useEffect(() => {
    if (status !== "authenticated" || !session?.idToken) return;
    if (searchParams.get("user")) {
      hasAutoLoadedFromHistory.current = false;
      return;
    }
    if (uniqueHistoryByUser.length === 0) return;
    if (hasAutoLoadedFromHistory.current) return;

    hasAutoLoadedFromHistory.current = true;

    const lastUser =
      typeof window !== "undefined"
        ? localStorage.getItem(DASHBOARD_LAST_USER_KEY)
        : null;
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
    status,
    session?.idToken,
    searchParams,
    uniqueHistoryByUser,
    // handleHistoryItemClick intentionally omitted to avoid re-running on every render
  ]);

  const processedReport = useMemo(() => {
    if (!report) return null;
    
    // Step 1: Filter (hide unknown)
    let filtered = hideUnknown
      ? report.filter(
          (row) => row.opening_key !== "unknown" && row.opening_label !== "Unknown"
        )
      : report;
    
    // Step 2: Sort (e.g. by games desc for "most played")
    const sorted = [...filtered].sort((a, b) => {
      const aVal = a[sortConfig.key];
      const bVal = b[sortConfig.key];
      
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortConfig.direction === "asc"
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }
      
      return sortConfig.direction === "asc"
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });
    
    return sorted;
  }, [report, hideUnknown, sortConfig]);

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

  if (status === "loading" || status === "unauthenticated") {
    return (
      <div className="opening-page min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest text-[color:var(--zen-muted)]">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div role="main" className="opening-page max-w-[1500px] mx-auto px-4 sm:px-6 py-10">
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
            onClick={() => signOut()}
            className="bg-primary text-white font-display text-[9px] uppercase tracking-wider px-5 py-2.5 rounded-lg border-2 border-[#7d8fd4] shadow-[0_4px_0_0_#3b4887] hover:bg-primary/90 active:translate-y-1 active:shadow-[0_2px_0_0_#3b4887] transition-all"
          >
            SIGN OUT
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
        {/* Recently analyzed - always visible */}
        <div className="zen-surface-flat px-4 py-4 h-fit rounded-xl border border-[color:var(--zen-border)]">
          <div className="flex items-baseline justify-between gap-2 mb-3">
            <p className="text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
              Recently analyzed
            </p>
            <span className="text-[10px] uppercase tracking-wider text-[color:var(--zen-muted)]/80">
              Last 10
            </span>
          </div>
          <div className="space-y-2">
            {uniqueHistoryByUser.length === 0 ? (
              <p className="text-sm text-[color:var(--zen-muted)] py-4 text-center">
                No users analyzed yet
              </p>
            ) : (
              uniqueHistoryByUser.map((item) => {
                const isSelected =
                  currentUsername &&
                  item.username.toLowerCase() === currentUsername.toLowerCase();
                return (
                  <button
                    key={item.username}
                    type="button"
                    onClick={() => handleHistoryItemClick(item)}
                    className={[
                      "w-full zen-pill px-3 py-2 text-sm transition cursor-pointer flex items-center justify-center",
                      isSelected
                        ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-accent)] border border-[color:var(--zen-accent)]"
                        : "text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface)] hover:text-[color:var(--zen-accent)]",
                    ].join(" ")}
                  >
                    <span className="truncate">{item.username}</span>
                  </button>
                );
              })
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
        {importStatus && currentUsername && !loading && (
          <div className="mt-5 zen-surface-flat px-4 py-3">
            {importStatus.imported_at ? (
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
            ) : (
              <p className="text-sm text-[color:var(--zen-muted)]">
                No imports yet for{" "}
                <span className="text-[color:var(--zen-text)] font-medium">
                  {currentUsername}
                </span>
              </p>
            )}
          </div>
        )}

        </div>

        {/* AI Coaching Summary - outside the upper box, with its own borders */}
        {currentUsername && (
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

            {insightsLoading && !insights && (
              <p className="mt-6 text-base text-[color:var(--zen-muted)]">
                Loading AI insights...
              </p>
            )}

            {!insightsLoading && !insights && (
              <p className="mt-6 text-base text-[color:var(--zen-muted)]">
                Insights are not available yet for this username.
              </p>
            )}

            {insights && (
              <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2">
                    Player Type
                  </p>
                  <p className="text-2xl font-semibold text-[color:var(--zen-text)]">
                    {insights.features?.style?.label || insights.narrative?.player_type?.text || "Building profile"}
                  </p>
                  {insights.narrative?.player_type?.text && (
                    <p className="mt-3 text-base text-[color:var(--zen-muted)] leading-relaxed">
                      {insights.narrative.player_type.text}
                    </p>
                  )}
                  <div className="mt-5 grid grid-cols-3 gap-4">
                    <div className="rounded-lg border border-[color:var(--zen-border)] px-4 py-3 text-center">
                      <p className="text-xs uppercase tracking-wider text-[color:var(--zen-muted)]">Confidence</p>
                      <p className="text-base font-semibold text-[color:var(--zen-accent)]">
                        {Math.round((insights.features?.confidence?.value || 0) * 100)}%
                      </p>
                    </div>
                    <div className="rounded-lg border border-[color:var(--zen-border)] px-4 py-3 text-center">
                      <p className="text-xs uppercase tracking-wider text-[color:var(--zen-muted)]">Deep</p>
                      <p className="text-base font-semibold text-[color:var(--zen-text)]">
                        {Math.round((insights.coverage?.deep_coverage || 0) * 100)}%
                      </p>
                    </div>
                    <div className="rounded-lg border border-[color:var(--zen-border)] px-4 py-3 text-center">
                      <p className="text-xs uppercase tracking-wider text-[color:var(--zen-muted)]">Clock</p>
                      <p className="text-base font-semibold text-[color:var(--zen-text)]">
                        {Math.round((insights.coverage?.clock_coverage || 0) * 100)}%
                      </p>
                    </div>
                  </div>
                </div>

                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-3">
                    Time Pressure
                  </p>
                  <p className="text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                    {insights.narrative?.time_pressure?.text || "Time-pressure insights are still being computed."}
                  </p>
                  {insights.updated_at && (
                    <p className="mt-4 text-sm text-[color:var(--zen-muted)]">
                      Updated:{" "}
                      {new Date(insights.updated_at).toLocaleString("en-US", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </p>
                  )}
                </div>

                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <div className="flex items-center gap-2 mb-3">
                    {/* <span className="text-[color:var(--zen-success)] text-lg" aria-hidden>✓</span> */}
                    <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                      Strengths
                    </p>
                  </div>
                  <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                    {(insights.narrative?.strengths || []).slice(0, 3).map((claim, idx) => (
                      <li key={`strength-${idx}`} className="flex gap-2">
                        <span className="text-[color:var(--zen-success)] shrink-0">✓</span>
                        <span>{claim.text}</span>
                      </li>
                    ))}
                    {(!insights.narrative?.strengths || insights.narrative.strengths.length === 0) && (
                      <li className="text-[color:var(--zen-muted)]">No strengths generated yet.</li>
                    )}
                  </ul>
                </div>

                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <div className="flex items-center gap-2 mb-3">
                    {/* <span className="text-[color:var(--zen-danger)] text-lg" aria-hidden>✗</span> */}
                    <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                      Weaknesses
                    </p>
                  </div>
                  <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                    {(insights.narrative?.weaknesses || []).slice(0, 3).map((claim, idx) => (
                      <li key={`weakness-${idx}`} className="flex gap-2">
                        <span className="text-[color:var(--zen-danger)] shrink-0">✗</span>
                        <span>{claim.text}</span>
                      </li>
                    ))}
                    {(!insights.narrative?.weaknesses || insights.narrative.weaknesses.length === 0) && (
                      <li className="text-[color:var(--zen-muted)]">No weaknesses generated yet.</li>
                    )}
                  </ul>
                </div>

                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <div className="flex items-center gap-2 mb-3">
                    {/* <span className="text-[color:var(--zen-accent)] text-lg" aria-hidden>⚠</span> */}
                    <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                      Recurring Mistakes
                    </p>
                  </div>
                  <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                    {(insights.narrative?.recurring_mistakes || []).slice(0, 3).map((claim, idx) => (
                      <li key={`mistake-${idx}`} className="flex gap-2">
                        <span className="text-[color:var(--zen-accent)] shrink-0">⚠</span>
                        <span>{claim.text}</span>
                      </li>
                    ))}
                    {(!insights.narrative?.recurring_mistakes ||
                      insights.narrative.recurring_mistakes.length === 0) && (
                      <li className="text-[color:var(--zen-muted)]">No recurring mistakes surfaced yet.</li>
                    )}
                  </ul>
                </div>

                <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                  <div className="flex items-center gap-2 mb-3">
                    {/* <span className="text-[color:var(--zen-accent)] text-lg" aria-hidden>⚡</span> */}
                    <p className="text-base sm:text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                      Coaching Focus
                    </p>
                  </div>
                  <ul className="space-y-3 text-base sm:text-lg text-[color:var(--zen-text)] leading-relaxed">
                    {(insights.narrative?.coaching_takeaways || []).slice(0, 3).map((claim, idx) => (
                      <li key={`focus-${idx}`} className="flex gap-2">
                        <span className="text-[color:var(--zen-accent)] shrink-0">→</span>
                        <span>{claim.text}</span>
                      </li>
                    ))}
                    {(!insights.narrative?.coaching_takeaways ||
                      insights.narrative.coaching_takeaways.length === 0) && (
                      <li className="text-[color:var(--zen-muted)]">Coaching recommendations are not ready yet.</li>
                    )}
                  </ul>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Top 10 Openings - outside the main box, with its own borders */}
        <div className="zen-surface opening-frame p-5 sm:p-6 border border-[color:var(--zen-border)] rounded-2xl">
        {/* Filters - inside the openings box */}
        {currentUsername && (
          <div className="flex flex-col lg:flex-row gap-3 lg:items-center lg:justify-between mb-4">
            <div className="flex flex-wrap items-center gap-2">
              <div className="zen-pill p-1 flex gap-1">
                {[
                  { value: "white", label: "As White" },
                  { value: "black", label: "As Black" },
                ].map((tab) => {
                  const active = colorFilter === tab.value;
                  return (
                    <button
                      key={tab.value}
                      onClick={() =>
                        handleFilterChange(tab.value as ColorFilter, timeClassFilter)
                      }
                      disabled={loading}
                      className={[
                        "opening-tab px-4 py-2 text-sm transition",
                        active
                          ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-text)]"
                          : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]",
                      ].join(" ")}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>

              <select
                value={timeClassFilter}
                onChange={(e) =>
                  handleFilterChange(colorFilter, e.target.value as TimeClassFilter)
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
              disabled={loading}
              className="zen-pill px-4 py-2.5 text-sm font-medium text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Refresh
            </button>
          </div>
        )}

        {/* Loading State - below filters when loading */}
        {loading && (
          <div className="py-10 flex justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
          </div>
        )}

        {report && !loading && (
          <div>
            <div className="flex items-baseline justify-between gap-2 mb-4">
              <h2 className="text-lg sm:text-xl font-semibold text-[color:var(--zen-text)]">
                Your top 10 openings as {colorFilter === "white" ? "White" : "Black"}
              </h2>
              <span className="text-[10px] uppercase tracking-wider text-[color:var(--zen-muted)]/80 shrink-0">
                Top 10
              </span>
            </div>
            <div className="overflow-hidden rounded-2xl border border-[color:var(--zen-border)]">
            <div className="overflow-x-auto">
              <table className="opening-table min-w-full">
                <thead className="bg-[color:var(--zen-surface-2)]">
                  <tr>
                    {[
                      { key: "opening_label" as const, label: "Opening", align: "left" },
                      { key: "games" as const, label: "Games", align: "right" },
                      { key: "wins" as const, label: "Wins", align: "right" },
                      { key: "draws" as const, label: "Draws", align: "right" },
                      { key: "losses" as const, label: "Losses", align: "right" },
                      { key: "score_pct" as const, label: "Score %", align: "right" },
                    ].map((col) => (
                      <th
                        key={col.key}
                        onClick={() => handleSort(col.key)}
                        className={`px-6 py-3 text-${col.align} text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] cursor-pointer hover:bg-[color:var(--zen-surface)] transition`}
                      >
                        <div
                          className={`flex items-center gap-1 ${
                            col.align === "right" ? "justify-end" : ""
                          }`}
                        >
                          <span>{col.label}</span>
                          {sortConfig.key === col.key && (
                            <span className="text-[color:var(--zen-accent)]">
                              {sortConfig.direction === "asc" ? "▲" : "▼"}
                            </span>
                          )}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[color:var(--zen-border)]">
                  {processedReport &&
                    processedReport.map((opening, idx) => (
                      <Fragment key={`${opening.opening_key}-${idx}`}>
                      <tr
                        onClick={() => {
                          if (currentUsername) {
                            router.push(
                              `/opening/${encodeURIComponent(currentUsername)}/${encodeURIComponent(opening.opening_key)}?site=all`
                            );
                          }
                        }}
                        className="cursor-pointer hover:bg-[color:var(--zen-surface)] transition"
                      >
                        <td className="px-6 py-4">
                          {(() => {
                            const parsed = parseOpeningName(opening.opening_label);
                            const badgeText =
                              opening.opening_key === "unknown"
                                ? "UNK"
                                : opening.opening_key.slice(0, 3).toUpperCase();
                            return (
                              <div className="font-semibold text-xl flex items-center gap-3 flex-wrap">
                                <span
                                  role="button"
                                  tabIndex={0}
                                  aria-expanded={!!expandedOpenings[opening.opening_key]}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    toggleOpening(opening.opening_key);
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.preventDefault();
                                      e.stopPropagation();
                                      toggleOpening(opening.opening_key);
                                    }
                                  }}
                                  className="opening-chevron w-6 shrink-0 flex items-center justify-center cursor-pointer text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition select-none"
                                >
                                  {expandedOpenings[opening.opening_key] ? "▾" : "▸"}
                                </span>
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
                            );
                          })()}
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums">
                          {opening.games}
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums text-[color:var(--zen-success)] font-medium">
                          {opening.wins}
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums text-[color:var(--zen-muted)]">
                          {opening.draws}
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums text-[color:var(--zen-danger)] font-medium">
                          {opening.losses}
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums">
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
                      {expandedOpenings[opening.opening_key] && (
                        <tr className="opening-variation-row">
                          <td colSpan={6} className="px-6 pb-6">
                            <div className="opening-variation-panel">
                              <div className="opening-variation-header">
                                <span>Variation</span>
                                <span className="opening-variation-header-cell">Games</span>
                                <span className="opening-variation-header-cell">Wins</span>
                                <span className="opening-variation-header-cell">Draws</span>
                                <span className="opening-variation-header-cell">Losses</span>
                                <span className="opening-variation-header-cell">Score %</span>
                              </div>
                              {variationsLoading[opening.opening_key] && (
                                <div className="text-xs text-[color:var(--zen-muted)] py-3">Loading variations...</div>
                              )}
                              {!variationsLoading[opening.opening_key] &&
                                (variationsByOpening[opening.opening_key] || []).map((variation) => {
                                  const variationBadge =
                                    variation.variation_key === "unknown"
                                      ? "UNK"
                                      : variation.variation_key.slice(0, 3).toUpperCase();
                                  return (
                                    <div
                                      key={`${variation.variation_key}-${opening.opening_key}`}
                                      className="opening-variation-item"
                                      onClick={() => {
                                        if (currentUsername) {
                                          router.push(
                                            `/opening/${encodeURIComponent(
                                              currentUsername
                                            )}/${encodeURIComponent(opening.opening_key)}/${encodeURIComponent(variation.variation_key)}`
                                          );
                                        }
                                      }}
                                    >
                                      <div className="opening-variation-name">
                                        <span className="eco-badge variation-badge">{variationBadge}</span>
                                        <span className="opening-variation-label">{variation.variation_label}</span>
                                      </div>
                                      <span className="opening-variation-stat text-right tabular-nums">{variation.games}</span>
                                      <span className="opening-variation-stat text-right tabular-nums text-[color:var(--zen-success)]">{variation.wins}</span>
                                      <span className="opening-variation-stat text-right tabular-nums text-[color:var(--zen-muted)]">{variation.draws}</span>
                                      <span className="opening-variation-stat text-right tabular-nums text-[color:var(--zen-danger)]">{variation.losses}</span>
                                      <span
                                        className="opening-variation-stat text-right tabular-nums"
                                        style={{
                                          color:
                                            variation.score_pct >= 55
                                              ? "var(--zen-success)"
                                              : variation.score_pct <= 45
                                                ? "var(--zen-danger)"
                                                : "var(--zen-text)",
                                        }}
                                      >
                                        {variation.score_pct.toFixed(1)}%
                                      </span>
                                    </div>
                                  );
                                })}
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                </tbody>
              </table>
            </div>
            {processedReport && processedReport.length === 0 && (
              <div className="p-8 text-center text-[color:var(--zen-muted)]">
                No games found with the selected filters.
              </div>
            )}
            </div>
          </div>
        )}

        {!report && !loading && !error && (
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
    </div>
  );
}
