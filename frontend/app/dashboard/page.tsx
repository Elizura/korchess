"use client";

// This page uses client-side routing/searchParams; force dynamic rendering to
// avoid Next.js "useSearchParams() should be wrapped in a suspense boundary" build errors.
export const dynamic = "force-dynamic";

import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { trackEvent, withTrackingHeaders } from "@/lib/analytics/client";
import { PlatformSelector, type Platform } from "@/components/PlatformSelector";
import { ChessProfileCard, type ChessProfile } from "@/components/ChessProfileCard";
import {
  fetchProfiles,
  addProfile,
  syncProfile,
  deleteProfile,
} from "@/lib/profiles";
import { importGames, type ImportResponse } from "@/lib/import";
import { API_BASE_URL } from "@/lib/api-url";
import { FaChessPawn, FaChessKnight, FaChessBishop, FaChessRook, FaChessQueen, FaChessKing } from "react-icons/fa";

const CHESS_PIECE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  pawn: FaChessPawn,
  knight: FaChessKnight,
  bishop: FaChessBishop,
  rook: FaChessRook,
  queen: FaChessQueen,
  king: FaChessKing,
};

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


interface ImportStatus {
  username: string;
  imported_at: string | null;
  last_imported: number | null;
  last_skipped: number | null;
  total_games: number;
  last_synced_at: string | null;
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
  lifecycle_status: "missing" | "queued" | "baseline_ready" | "complete" | "stale" | "not_enough_data" | "failed";
  feature_version: string;
  narrative_version: string;
  updated_at: string | null;
  coverage?: {
    games_total?: number;
    games_light?: number;
    games_scanned?: number;
    scan_coverage?: number;
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
      blunders_total?: number;
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
  scan_progress?: {
    status: "queued" | "running" | "completed" | "failed";
    done: number;
    total: number;
  } | null;
  problem_spotter?: {
    total_problems: number;
    by_theme: Array<{ theme: string; label?: string; count: number }>;
    by_phase: Record<string, number>;
    by_classification: { blunders: number; mistakes: number };
    recent_problems: Array<{
      site?: string;
      site_game_id?: string;
      ply: number;
      san: string;
      classification: string;
      phase: string;
      tactic_type: string | null;
      tactic_types: string[];
      played_at?: string | null;
      opponent?: string | null;
      time_class?: string | null;
    }>;
  } | null;
}

type ColorFilter = "white" | "black";
type TimeClassFilter = "all" | "blitz" | "rapid" | "classical";

interface ProblemSpotterData {
  total_problems: number;
  by_theme: Array<{ theme: string; label?: string; count: number }>;
  by_phase: Record<string, number>;
  by_classification: { blunders: number; mistakes: number };
  recent_problems: Array<{
    classification: string;
    tactic_type?: string;
    phase?: string;
    site: string;
    site_game_id: string;
    time_class?: string;
    opponent?: string;
    played_at?: string;
    ply?: number;
  }>;
}

const INSIGHTS_ACTIVE_STATUSES = new Set<InsightsProfile["lifecycle_status"]>([
  "queued",
  "baseline_ready",
]);

const shouldKeepPolling = (data: InsightsProfile | null): boolean => {
  if (!data) return false;
  if (INSIGHTS_ACTIVE_STATUSES.has(data.lifecycle_status)) return true;
  const scanStatus = data.scan_progress?.status;
  return scanStatus === "queued" || scanStatus === "running";
};

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

const getTimeControlIcon = (timeClass: string | null | undefined): string | null => {
  if (!timeClass) return null;
  const normalized = timeClass.toLowerCase();
  const iconMap: Record<string, string> = {
    bullet: "/time-controls/bullet.png",
    blitz: "/time-controls/blitz.png",
    rapid: "/time-controls/rapid.png",
    classical: "/time-controls/classical.png",
  };
  return iconMap[normalized] || null;
};

const humanizeTheme = (theme: string): string => {
  const normalized = theme.trim().toLowerCase();
  if (!normalized) return "Recurring decision errors";
  const map: Record<string, string> = {
    opening_blunder: "costly opening mistakes",
    middlegame_blunder: "middlegame blunders",
    endgame_blunder: "endgame conversion errors",
    tactical_oversight: "missed tactical details",
    hanging_piece: "hanging pieces",
    missed_tactic: "missed tactics",
    fork: "missed forks",
    double_attack: "missed double attacks",
    skewer: "missed skewers",
    forced_mate: "forced mates found",
    missed_forced_mate: "missed forced mates",
    pin: "missed pins",
    discovered_attack: "missed discovered attacks",
    critical_inaccuracy: "critical inaccuracies",
    conversion_miss: "conversion misses",
    defensive_slip: "defensive slips",
    small_technique_error: "small technique errors",
  };
  return map[normalized] || normalized.replace(/_/g, " ");
};

const TACTIC_SPRITE_MAP: Record<string, string> = {
  hanging_piece: "/tactics/hanging-piece.png",
  fork: "/tactics/fork.png",
  double_attack: "/tactics/double-attack.png",
  skewer: "/tactics/skewer.png",
  pin: "/tactics/skewer.png",
  forced_mate: "/tactics/forced-mate.png",
  missed_forced_mate: "/tactics/forced-mate.png",
  discovered_attack: "/tactics/discovery-attack.png",
};

const TACTIC_CARD_LABEL: Record<string, string> = {
  hanging_piece: "HANGING PIECES",
  fork: "MISSED FORKS",
  double_attack: "DOUBLE ATTACKS",
  skewer: "MISSED SKEWERS",
  pin: "PINS AND SKEWERS",
  forced_mate: "FORCED MATES",
  missed_forced_mate: "FORCED MATES MISSED",
  discovered_attack: "DISCOVERY ATTACKS",
  missed_tactic: "MISSED TACTICS",
  tactical_oversight: "TACTICAL OVERSIGHT",
  critical_inaccuracy: "CRITICAL INACCURACY",
  conversion_miss: "CONVERSION MISS",
  defensive_slip: "DEFENSIVE SLIPS",
};

const TACTIC_BLURB: Record<string, string> = {
  hanging_piece: "Left a piece undefended, giving your opponent a free capture.",
  fork: "Missed a move that attacks two pieces at once.",
  double_attack: "Overlooked a move threatening two targets simultaneously.",
  skewer: "Fell for an attack through a high-value piece to one behind it.",
  pin: "Missed a pin holding a piece to your king or a more valuable target.",
  forced_mate: "Had a forced checkmate sequence on the board.",
  missed_forced_mate: "Missed a checkmate sequence that was available.",
  discovered_attack: "Overlooked an attack revealed by moving a blocking piece.",
  missed_tactic: "Had a tactical shot available but played something else.",
  tactical_oversight: "Missed a concrete tactical detail in the position.",
  critical_inaccuracy: "Played an imprecise move in a critical moment.",
  conversion_miss: "Failed to convert a winning or clearly better position.",
  defensive_slip: "Missed a defensive resource that could have held the position.",
};

function getTacticBorderClass(count: number, maxCount: number): string {
  const ratio = maxCount > 0 ? count / maxCount : 0;
  if (ratio >= 0.7 || count >= 15) return "tactical-card-red";
  if (ratio >= 0.4 || count >= 8) return "tactical-card-orange";
  return "tactical-card-cyan";
}

function TacticalCategoryCard({ theme, count, maxCount, username }: { theme: string; count: number; maxCount: number; username: string }) {
  const normalized = theme.trim().toLowerCase();
  const sprite = TACTIC_SPRITE_MAP[normalized];
  const label = TACTIC_CARD_LABEL[normalized] || normalized.replace(/_/g, " ").toUpperCase();
  const blurb = TACTIC_BLURB[normalized] || "A recurring pattern in your games.";
  const borderClass = getTacticBorderClass(count, maxCount);

  return (
    <a
      href={`/problems?user=${encodeURIComponent(username)}&theme=${encodeURIComponent(normalized)}`}
      className={`tactical-card tactical-card-clickable ${borderClass} block no-underline`}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <span className="tactical-card-label">{label}</span>
        {sprite && (
          <img
            src={sprite}
            alt={humanizeTheme(theme)}
            className="w-10 h-10 object-contain opacity-80"
            loading="lazy"
          />
        )}
      </div>
      <span className="tactical-card-count mb-3">{String(count).padStart(2, "0")}</span>
      <p className="text-base font-semibold leading-relaxed text-[color:var(--zen-muted)] mt-auto">
        {blurb}
      </p>
    </a>
  );
}

export default function DashboardPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, accessToken, isLoading, signout } = useAuth();
  const userFromUrl = useMemo(() => searchParams.get("user") || "", [searchParams]);
  const authUserId = useMemo(
    () => (user?.email || user?.username || "anonymous").toLowerCase(),
    [user?.email, user?.username],
  );

  const authHeaders = useMemo((): Record<string, string> => {
    if (!accessToken) {
      return {};
    }
    return { Authorization: `Bearer ${accessToken}` };
  }, [accessToken]);
  
  const [username, setUsername] = useState("");
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
  const hasAutoLoadedFromProfile = useRef(false);
  const profilesFetchedRef = useRef(false);
  const [accountImportHistory, setAccountImportHistory] = useState<ImportHistoryItem[]>([]);
  const [insights, setInsights] = useState<InsightsProfile | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsRefreshing, setInsightsRefreshing] = useState(false);
  const [reportRefreshing, setReportRefreshing] = useState(false);
  const [reportRefreshNotice, setReportRefreshNotice] = useState<string | null>(null);

  const [inputUsername, setInputUsername] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState<Platform>("lichess");
  const [chessProfiles, setChessProfiles] = useState<ChessProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [syncingProfile, setSyncingProfile] = useState<string | null>(null);
  const [importingProfile, setImportingProfile] = useState<string | null>(null);
  const [deletingProfile, setDeletingProfile] = useState<string | null>(null);
  const [importProgress, setImportProgress] = useState<{
    site: string;
    status: string;
    done: number;
    total: number;
  } | null>(null);
  const [confirmDeleteProfile, setConfirmDeleteProfile] = useState<ChessProfile | null>(null);
  const [problemSpotter, setProblemSpotter] = useState<ProblemSpotterData | null>(null);
  const [problemSpotterLoading, setProblemSpotterLoading] = useState(false);

  // Fetch profile for nav
  useEffect(() => {
    if (!accessToken) return;

    const fetchProfile = async () => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/v1/auth/profile`,
          { headers: withTrackingHeaders({ Authorization: `Bearer ${accessToken}` }) }
        );
        if (!res.ok) return;
        const profile = await res.json();
        setProfileUsername(profile.username || "");
        setProfileAvatar(profile.avatar || "pawn");
      } catch {
        // Ignore; user can still use dashboard
      }
    };

    fetchProfile();
  }, [accessToken]);

  // Poll Redis for import progress, then fetch reports when complete
  const pollAfterImport = useCallback(
    async (targetUsername: string, site: "lichess" | "chesscom") => {
      let attempts = 0;
      const maxAttempts = 300; // Up to 5 minutes at 1s intervals
      
      while (attempts < maxAttempts) {
        attempts++;
        try {
          const progress = await fetchImportProgress(targetUsername, site);
          
          if (progress) {
            setImportProgress(progress);
            
            // When complete, fetch final data and stop polling
              if (progress.status === "complete") {
              setImportProgress(null);
              
              // Fetch final reports, import status, problem spotter, and insights
              const [whiteData, blackData, statusData, problemSpotterData, insightsData] = await Promise.all([
                fetchReport(targetUsername, "white", timeClassFilter),
                fetchReport(targetUsername, "black", timeClassFilter),
                fetchImportStatus(targetUsername),
                fetchProblemSpotter(targetUsername),
                fetchInsights(targetUsername),
              ]);
              setReport(whiteData);
              setReportBlack(blackData);
              if (statusData) setImportStatus(statusData);
              setProblemSpotter(problemSpotterData);
              setInsights(insightsData);
              break;
            }
          } else {
            // No progress data means import might not have started or already finished
            setImportProgress(null);
            break;
          }
        } catch {
          setImportProgress(null);
          break;
        }
        await new Promise(r => setTimeout(r, 1000)); // Poll every 1 second
      }
      
      // Clear progress if we hit max attempts
      setImportProgress(null);
    },
    [timeClassFilter]
  );

  // Shared: refresh reports and import status after any import (insights disabled)
  const refreshDashboardAfterImport = useCallback(
    async (targetUsername: string, site: "lichess" | "chesscom", importResult: ImportResponse) => {
      void fetchImportHistory();

      if (importResult.imported > 0) {
        void pollAfterImport(targetUsername, site);
      } else {
        const [whiteReportData, blackReportData, statusData] = await Promise.all([
          fetchReport(targetUsername, "white", timeClassFilter),
          fetchReport(targetUsername, "black", timeClassFilter),
          fetchImportStatus(targetUsername),
        ]);
        setReport(whiteReportData);
        setReportBlack(blackReportData);
        if (statusData) setImportStatus(statusData);
      }
    },
    [timeClassFilter, pollAfterImport]
  );

  // Handle adding a new profile (authenticated) or importing (guest)
  const handleAddOrImport = useCallback(async () => {
    const trimmedUsername = inputUsername.trim();
    if (!trimmedUsername) {
      setError("Please enter a username");
      return;
    }

    trackEvent("import.start", {
      properties: {
        site: selectedPlatform,
      },
    });

    setLoading(true);
    setError(null);
    setReport(null);
    setReportBlack(null);
    setImportResult(null);

    if (!currentUsername || trimmedUsername.toLowerCase() !== currentUsername.toLowerCase()) {
      setImportStatus(null);
    }

    try {
      if (!accessToken) {
        throw new Error("Not authenticated");
      }
      
      const result = await addProfile(accessToken, trimmedUsername, selectedPlatform);
      
      const updatedProfiles = (() => {
        const filtered = chessProfiles.filter(
          (p) =>
            !(p.chess_username.toLowerCase() === result.profile.chess_username.toLowerCase() &&
              p.site === result.profile.site)
        );
        return [result.profile, ...filtered];
      })();
      
      setChessProfiles(updatedProfiles);

      setUsername(result.profile.chess_username);
      setCurrentUsername(result.profile.chess_username);
      setInputUsername("");
      setImportStatus(null);
      setInsights(null);
      router.replace(`/dashboard?user=${encodeURIComponent(result.profile.chess_username)}`, { scroll: false });
      persistLastUser(result.profile.chess_username);
      
      trackEvent("profile.added", {
        properties: {
          site: selectedPlatform,
        },
      });
    } catch (err) {
      trackEvent("import.failed", {
        properties: {
          site: selectedPlatform,
          reason: err instanceof Error ? err.message : "An error occurred",
        },
      });
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  }, [
    inputUsername,
    selectedPlatform,
    accessToken,
    currentUsername,
    router,
  ]);

  // Handle syncing a profile
  const handleSyncProfile = useCallback(
    async (profile: ChessProfile) => {
      if (!accessToken) return;

      const profileKey = `${profile.site}:${profile.chess_username}`;
      setSyncingProfile(profileKey);
      setError(null);
      setImportResult(null);

      try {
        const result = await syncProfile(
          accessToken,
          profile.site as "lichess" | "chesscom",
          profile.chess_username
        );

        const updatedProfiles = chessProfiles.map((p) =>
          p.chess_username.toLowerCase() === result.profile.chess_username.toLowerCase() &&
          p.site === result.profile.site
            ? result.profile
            : p
        );
        
        setChessProfiles(updatedProfiles);

        if (
          currentUsername &&
          currentUsername.toLowerCase() === profile.chess_username.toLowerCase()
        ) {
          setImportResult(result.sync_result);
          await refreshDashboardAfterImport(profile.chess_username, profile.site as "lichess" | "chesscom", result.sync_result);
        }

        trackEvent("profile.synced", {
          properties: {
            site: profile.site,
            imported: result.sync_result.imported,
          },
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Sync failed");
      } finally {
        setSyncingProfile(null);
      }
    },
    [accessToken, currentUsername, refreshDashboardAfterImport]
  );

  // Handle importing games for a profile (first import or re-import)
  const handleImportProfile = useCallback(
    async (profile: ChessProfile) => {
      if (!accessToken) return;

      const profileKey = `${profile.site}:${profile.chess_username}`;
      setImportingProfile(profileKey);
      setError(null);
      setImportResult(null);

      try {
        const result = await importGames(
          profile.chess_username,
          profile.site as "lichess" | "chesscom",
          accessToken,
        );

        setImportResult(result);
        await refreshDashboardAfterImport(profile.chess_username, profile.site as "lichess" | "chesscom", result);

        trackEvent("profile.imported", {
          properties: {
            site: profile.site,
            imported: result.imported,
          },
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Import failed");
      } finally {
        setImportingProfile(null);
      }
    },
    [accessToken, refreshDashboardAfterImport]
  );

  // Handle deleting a profile
  const handleDeleteProfile = useCallback(
    async (profile: ChessProfile) => {
      if (!accessToken) return;

      const profileKey = `${profile.site}:${profile.chess_username}`;
      setDeletingProfile(profileKey);
      setConfirmDeleteProfile(null);

      try {
        await deleteProfile(
          accessToken,
          profile.site as "lichess" | "chesscom",
          profile.chess_username
        );

        const updatedProfiles = chessProfiles.filter(
          (p) =>
            !(p.chess_username.toLowerCase() === profile.chess_username.toLowerCase() &&
              p.site === profile.site)
        );

        setChessProfiles(updatedProfiles);

        // If this was the current user, clear the view
        if (currentUsername?.toLowerCase() === profile.chess_username.toLowerCase()) {
          setCurrentUsername(null);
          setUsername("");
          setReport(null);
          setReportBlack(null);
          setInsights(null);
          setImportStatus(null);
          router.replace("/dashboard", { scroll: false });
        }

        trackEvent("profile.deleted", {
          properties: {
            site: profile.site,
          },
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Delete failed");
      } finally {
        setDeletingProfile(null);
      }
    },
    [accessToken, currentUsername, router]
  );

  // Handle clicking on a profile card
  const handleProfileClick = useCallback(
    (profile: ChessProfile) => {
      trackEvent("feature.usage", {
        properties: {
          feature: "profile_card_select",
        },
      });
      setUsername(profile.chess_username);
      setCurrentUsername(profile.chess_username);
      router.replace(`/dashboard?user=${encodeURIComponent(profile.chess_username)}`, { scroll: false });
      persistLastUser(profile.chess_username);
      setReportRefreshNotice(null);
      setError(null);
      void loadDashboardBundle(profile.chess_username, colorFilter, timeClassFilter);
    },
    [colorFilter, timeClassFilter, router]
  );

  // Fetch import history on mount
  useEffect(() => {
    void fetchImportHistory();
  }, [authUserId]);

  useEffect(() => {
    if (!currentUsername) {
      setInsights(null);
      return;
    }
    if (isLoading) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loadInsights = async () => {
      setInsightsLoading(true);
      try {
        const data = await fetchInsights(currentUsername);
        if (cancelled) return;
        setInsights(data);
        if (shouldKeepPolling(data)) {
          timer = setTimeout(() => void loadInsights(), 8000);
        }
      } finally {
        if (!cancelled) setInsightsLoading(false);
      }
    };

    void loadInsights();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isLoading, currentUsername]);

  // Fetch problem spotter (tactical analysis) - defined before useEffect that uses it
  const fetchProblemSpotter = async (user: string) => {
    const params = new URLSearchParams();
    params.set("username", user);
    params.set("site", "all");
    const response = await fetch(
      `${API_BASE_URL}/api/v1/tactical/problem-spotter?${params}`,
      { headers: withTrackingHeaders(authHeaders) }
    );
    if (!response.ok) {
      return null;
    }
    return response.json();
  };

  // Problem spotter is now fetched inside loadDashboardBundle alongside other data

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
      return null;
    }
    
    return response.json();
  };

  const fetchImportProgress = async (user: string, site: "lichess" | "chesscom") => {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/import/progress/${site}/${encodeURIComponent(user)}`,
      { headers: withTrackingHeaders(authHeaders) }
    );
    
    if (!response.ok) {
      return null;
    }
    
    return response.json() as Promise<{
      username: string;
      site: string;
      status: string;
      total: number;
      done: number;
    }>;
  };

  const fetchImportHistory = async () => {
    if (!accessToken) {
      setAccountImportHistory([]);
      return;
    }
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/import/history`, {
        headers: withTrackingHeaders(authHeaders),
      });
      if (response.ok) {
        const data = await response.json();
        setAccountImportHistory(data.history || []);
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

  const loadDashboardBundle = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
    options?: { force?: boolean },
  ) => {
    setLoading(true);

    // Include profiles fetch on first load
    const shouldFetchProfiles = accessToken && !profilesFetchedRef.current;
    if (shouldFetchProfiles) {
      setProfilesLoading(true);
    }

    try {
      const fetchPromises: Promise<unknown>[] = [
        fetchReport(user, "white", timeClass),
        fetchReport(user, "black", timeClass),
        fetchImportStatus(user),
        fetchProblemSpotter(user),
      ];

      // Add profiles fetch if needed (batched with other requests)
      if (shouldFetchProfiles) {
        fetchPromises.push(fetchProfiles(accessToken));
      }

      const results = await Promise.all(fetchPromises);

      setReport(results[0] as OpeningStats[] | null);
      setReportBlack(results[1] as OpeningStats[] | null);
      setImportStatus(results[2] as ImportStatus | null);
      setProblemSpotter(results[3] as ProblemSpotterData | null);

      // Set profiles if fetched
      if (shouldFetchProfiles && results[4]) {
        setChessProfiles(results[4] as ChessProfile[]);
        profilesFetchedRef.current = true;
      }

      setError(null);
      setReportRefreshNotice(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
      setReportRefreshing(false);
      if (shouldFetchProfiles) {
        setProfilesLoading(false);
      }
    }
  };

  // Restore state from URL on mount or when URL user changes
  const prevUserFromUrl = useRef<string | null>(null);
  useEffect(() => {
    if (!userFromUrl) return;
    if (isLoading) return;
    
    // Skip if we already loaded this exact user
    if (prevUserFromUrl.current === userFromUrl) return;
    prevUserFromUrl.current = userFromUrl;

    setUsername(userFromUrl);
    setCurrentUsername(userFromUrl);
    persistLastUser(userFromUrl);
    setError(null);
    void loadDashboardBundle(userFromUrl, colorFilter, timeClassFilter);
  }, [userFromUrl, isLoading]);

  // Update URL and persist last selected user
  const updateUrl = (user: string | null) => {
    if (user) {
      router.replace(`/dashboard?user=${encodeURIComponent(user)}`, { scroll: false });
      persistLastUser(user);
    }
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
        trackEvent("insights.refresh.triggered", {
          properties: {
            lifecycle_status: refreshed.lifecycle_status,
          },
        });
        setInsights(refreshed);

        const pollUntilComplete = async () => {
          let attempts = 0;
          const maxAttempts = 120;
          while (attempts < maxAttempts) {
            await new Promise(r => setTimeout(r, 5000));
            attempts++;
            try {
              const data = await fetchInsights(currentUsername);
              setInsights(data);
              if (!shouldKeepPolling(data)) {
                break;
              }
            } catch {
              break;
            }
          }
        };
        pollUntilComplete().finally(() => setInsightsRefreshing(false));
        return;
      }
    } catch {
      // fall through
    }
    setInsightsRefreshing(false);
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

  const handleSignOut = async () => {
    await signout();
    router.push("/signin");
  };

  /** Sync / import toast: show inside the large profile card when it applies to the selected account */
  const showImportResultInProfileCard = useMemo(() => {
    if (!importResult || !currentUsername) return false;
    const u = importResult.username.toLowerCase();
    const c = currentUsername.toLowerCase();
    if (u !== c) return false;
    return chessProfiles.some((p) => p.chess_username.toLowerCase() === c);
  }, [importResult, currentUsername, chessProfiles]);

  // When no user in URL but we have profiles: auto-load last selected or first profile
  useEffect(() => {
    if (userFromUrl) {
      hasAutoLoadedFromProfile.current = false;
      return;
    }
    if (chessProfiles.length === 0) return;
    if (hasAutoLoadedFromProfile.current) return;

    hasAutoLoadedFromProfile.current = true;

    let lastUser: string | null = null;
    if (typeof window !== "undefined") {
      try {
        lastUser = localStorage.getItem(DASHBOARD_LAST_USER_KEY);
      } catch {
        lastUser = null;
      }
    }
    const profileToLoad =
      lastUser &&
      chessProfiles.some(
        (p) => p.chess_username.toLowerCase() === lastUser.toLowerCase()
      )
        ? chessProfiles.find(
            (p) => p.chess_username.toLowerCase() === lastUser.toLowerCase()
          )!
        : chessProfiles[0];

    if (profileToLoad) {
      setUsername(profileToLoad.chess_username);
      setCurrentUsername(profileToLoad.chess_username);
      router.replace(`/dashboard?user=${encodeURIComponent(profileToLoad.chess_username)}`, { scroll: false });
      persistLastUser(profileToLoad.chess_username);
      void loadDashboardBundle(profileToLoad.chess_username, colorFilter, timeClassFilter);
    }
  }, [
    userFromUrl,
    chessProfiles,
    colorFilter,
    timeClassFilter,
    router,
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
    const scanStatus = insights?.scan_progress?.status;
    if (!statusValue) return "Unavailable";
    if (statusValue === "queued") return "Queued";
    if (
      (statusValue === "baseline_ready" || statusValue === "complete") &&
      (scanStatus === "queued" || scanStatus === "running")
    ) {
      const done = insights?.scan_progress?.done ?? 0;
      const total = insights?.scan_progress?.total ?? 0;
      return total > 0 ? `Scanning games (${done}/${total})` : "Scanning games...";
    }
    if (statusValue === "baseline_ready") return "Baseline ready";
    if (statusValue === "complete") return "Complete";
    if (statusValue === "not_enough_data") return "Not enough data";
    if (statusValue === "stale") return "Stale";
    if (statusValue === "failed") return "Failed";
    return "Unavailable";
  }, [insights?.lifecycle_status, insights?.scan_progress]);

  const coachingSummaryReady = useMemo(() => {
    if (!insights) return false;
    const statusValue = insights.lifecycle_status;
    if (statusValue !== "complete" && statusValue !== "stale" && statusValue !== "baseline_ready") return false;
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
    const blundersTotal = Number(insights.features?.time_pressure?.blunders_total || 0);
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
      blundersTotal,
      pressureDelta,
    };
  }, [insights]);

  const playerTypeDescription = useMemo(() => {
    if (insights?.narrative?.player_type?.text) {
      return insights.narrative.player_type.text;
    }
    const styleLabel = insights?.features?.style?.label || "Developing profile";
    return `They currently profile as ${styleLabel}.`;
  }, [insights?.narrative?.player_type?.text, insights?.features?.style?.label]);

  const timePressureSummary = useMemo(() => {
    if (insights?.narrative?.time_pressure?.text) {
      return insights.narrative.time_pressure.text;
    }
    if (!coachingTemplateData) return "Time-pressure signals are not ready yet.";
    const under = coachingTemplateData.underPressurePct;
    const baseline = coachingTemplateData.overallPressureBaselinePct;
    const delta = coachingTemplateData.pressureDelta;
    const pressureGames = coachingTemplateData.pressureGames;

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
  }, [insights?.narrative?.time_pressure?.text, coachingTemplateData]);

  const strengthsBullets = useMemo(() => {
    if (insights?.narrative?.strengths?.length) {
      return insights.narrative.strengths.map((claim) => claim.text);
    }
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
  }, [insights?.narrative?.strengths, coachingTemplateData]);

  const weaknessesBullets = useMemo(() => {
    if (insights?.narrative?.weaknesses?.length) {
      return insights.narrative.weaknesses.map((claim) => claim.text);
    }
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
  }, [insights?.narrative?.weaknesses, coachingTemplateData]);

  const recurringMistakesBullets = useMemo(() => {
    if (insights?.narrative?.recurring_mistakes?.length) {
      return insights.narrative.recurring_mistakes.map((claim) => claim.text);
    }
    const themes = insights?.features?.recurring_themes || [];
    if (!themes.length) return [];
    return themes.slice(0, 3).map((item) => {
      const label = humanizeTheme(item.theme);
      return `Recurring pattern: ${label} (${item.count}).`;
    });
  }, [insights?.narrative?.recurring_mistakes, insights?.features?.recurring_themes]);

  const coachingFocusBullets = useMemo(() => {
    if (insights?.narrative?.coaching_takeaways?.length) {
      return insights.narrative.coaching_takeaways.map((claim) => claim.text);
    }
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
  }, [insights?.narrative?.coaching_takeaways, coachingTemplateData]);

  if (isLoading) {
    return (
      <div className="opening-page min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest text-[color:var(--zen-muted)]">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div role="main" className="opening-page max-w-[1550px] mx-auto px-4 sm:px-6 py-10">
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
          <button
            type="button"
            onClick={() => router.push("/profile/edit")}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[color:var(--zen-border)] bg-[color:var(--zen-surface)] hover:bg-[color:var(--zen-surface-2)] transition-colors cursor-pointer"
          >
            <div className="w-9 h-9 flex items-center justify-center shrink-0">
              {(() => {
                const IconComponent = CHESS_PIECE_ICONS[profileAvatar] || FaChessPawn;
                return <IconComponent className="text-lg text-[color:var(--zen-text)]" />;
              })()}
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
        </div>
      </div>

      <div className="min-w-0 space-y-8">
        <div className="zen-surface opening-frame p-5 sm:p-6">
        {/* Unified import input */}
        <div className="flex flex-col gap-3">
          <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
            <div className="flex-1">
              <label
                htmlFor="chess-username"
                className="block text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2"
              >
                Add chess account
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="chess-username"
                  type="text"
                  value={inputUsername}
                  onChange={(e) => setInputUsername(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddOrImport()}
                  placeholder="Enter username"
                  className="zen-input w-full px-4 py-3 outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)] transition"
                  disabled={loading}
                />
                <PlatformSelector
                  value={selectedPlatform}
                  onChange={setSelectedPlatform}
                  disabled={loading}
                />
                <button
                  onClick={handleAddOrImport}
                  disabled={loading || !inputUsername.trim()}
                  className="pixel-button shrink-0 px-5 py-3 rounded-xl font-medium text-sm border border-[color:var(--zen-border)] text-white hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  {loading ? "Loading..." : "Add"}
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Account chips */}
        <div className="mt-5 border-t border-[color:var(--zen-border)]/70 pt-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
              Your accounts
            </p>
            {profilesLoading && (
              <span className="text-[10px] text-[color:var(--zen-muted)]">Loading...</span>
            )}
          </div>

          {chessProfiles.length === 0 ? (
            <p className="text-sm text-[color:var(--zen-muted)] py-2">
              No accounts added yet. Add a Lichess or Chess.com username above.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {chessProfiles.map((profile) => {
                const profileKey = `${profile.site}:${profile.chess_username}`;
                const isSelected =
                  currentUsername &&
                  profile.chess_username.toLowerCase() === currentUsername.toLowerCase();
                return (
                  <button
                    key={profileKey}
                    type="button"
                    onClick={() => handleProfileClick(profile)}
                    className={[
                      "zen-pill px-3 py-2 text-sm transition cursor-pointer flex items-center max-w-full",
                      isSelected
                        ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-accent)] border border-[color:var(--zen-accent)]"
                        : "text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)] hover:text-[color:var(--zen-accent)]",
                    ].join(" ")}
                  >
                    <span className="truncate">{profile.chess_username}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Import / sync result — guests or when message is not tied to the selected profile card */}
        {importResult && !showImportResultInProfileCard && (
          <div className="mt-4 zen-surface-flat px-4 py-3">
            <p className="text-sm">
              <span className="text-[color:var(--zen-success)] font-semibold">
                {importResult.is_sync
                  ? `Synced ${importResult.imported} new game${importResult.imported !== 1 ? "s" : ""}`
                  : `Imported ${importResult.imported} game${importResult.imported !== 1 ? "s" : ""}`}
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

        {/* Import progress — when large profile card is not shown */}
        {importProgress &&
          currentUsername &&
          !chessProfiles.some((p) => p.chess_username.toLowerCase() === currentUsername.toLowerCase()) && (
            <div className="mt-5 zen-surface-flat px-4 py-3 rounded-lg border border-[color:var(--zen-accent)]/40 bg-[color:var(--zen-accent)]/5">
              <div className="flex items-center gap-3">
                <div className="animate-spin h-4 w-4 border-2 border-[color:var(--zen-accent)] border-t-transparent rounded-full" />
                <p className="text-sm text-[color:var(--zen-text)]">
                  {importProgress.status === "streaming" ? (
                    "Streaming games from server..."
                  ) : (
                    <>
                      Processing{" "}
                      <span className="font-medium">{importProgress.done}</span>
                      {" / "}
                      <span className="font-medium">{importProgress.total}</span>
                      {" games..."}
                    </>
                  )}
                </p>
              </div>
              {importProgress.status === "processing" && importProgress.total > 0 && (
                <div className="mt-2 h-1.5 bg-[color:var(--zen-border)] rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-[color:var(--zen-accent)] transition-all duration-300"
                    style={{ width: `${Math.round((importProgress.done / importProgress.total) * 100)}%` }}
                  />
                </div>
              )}
            </div>
          )}

        {/* Data freshness — when large profile card is not shown */}
        {!importProgress && (importStatus?.imported_at || (importStatus?.total_games ?? 0) > 0) &&
          currentUsername &&
          !loading &&
          !chessProfiles.some((p) => p.chess_username.toLowerCase() === currentUsername.toLowerCase()) && (
            <div className="mt-5 zen-surface-flat px-4 py-3">
              <p className="text-sm text-[color:var(--zen-muted)]">
                Report generated from{" "}
                <span className="text-[color:var(--zen-text)] font-medium">
                  {importStatus!.total_games}
                </span>{" "}
                games
                {importStatus!.total_games > 0 && (importStatus!.imported_at || importStatus!.last_synced_at) && (
                  <>
                    {" "}
                    ({importStatus!.last_synced_at ? "last sync" : "last import"}:{" "}
                    {new Date(importStatus!.last_synced_at || importStatus!.imported_at!).toLocaleString("en-US", {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    )
                  </>
                )}
              </p>
            </div>
          )}

        </div>

        {/* Selected Profile Card - larger display */}
        {currentUsername && (() => {
          const selectedProfile = chessProfiles.find(
            (p) => p.chess_username.toLowerCase() === currentUsername.toLowerCase()
          );
          if (!selectedProfile) return null;
          const profileKey = `${selectedProfile.site}:${selectedProfile.chess_username}`;
          const isLichess = selectedProfile.site === "lichess";
          const siteLogo = isLichess ? "/site-logos/lichess.png" : "/site-logos/chesscom.png";
          const siteLabel = isLichess ? "Lichess" : "Chess.com";

          return (
            <div className="zen-surface opening-frame p-6 sm:p-8">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                  <div
                    className={`w-14 h-14 rounded-xl flex items-center justify-center ${isLichess ? "bg-[#1a1a1e]" : ""}`}
                  >
                    <img src={siteLogo} alt={siteLabel} className="w-10 h-10 object-contain" />
                  </div>
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-1">
                      {siteLabel}
                    </p>
                    <h2 className="text-2xl sm:text-3xl font-bold text-[color:var(--zen-text)]">
                      {selectedProfile.chess_username}
                    </h2>
                  </div>
                </div>
                
                <div className="flex items-center gap-2 flex-wrap justify-end">
                  {(() => {
                    const hasGames = importStatus && importStatus.total_games > 0;
                    const isBusy = hasGames
                      ? syncingProfile === profileKey
                      : importingProfile === profileKey;
                    const action = hasGames
                      ? () => handleSyncProfile(selectedProfile)
                      : () => handleImportProfile(selectedProfile);
                    const label = hasGames
                      ? (isBusy ? "Syncing..." : "Sync")
                      : (isBusy ? "Importing..." : "Import");
                    const title = hasGames
                      ? "Sync new games and update ratings"
                      : "Import games from this account";

                    return (
                      <button
                        type="button"
                        onClick={action}
                        disabled={isBusy}
                        className={`
                          flex items-center gap-2 px-4 py-2.5 rounded-lg
                          border border-[color:var(--zen-border)] bg-[color:var(--zen-surface)]
                          text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)] 
                          hover:border-[color:var(--zen-accent)]/50 transition-all
                          ${isBusy ? "opacity-50 cursor-wait" : "cursor-pointer"}
                        `}
                        title={title}
                      >
                        {isBusy ? (
                          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="m4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                          </svg>
                        ) : hasGames ? (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                          </svg>
                        ) : (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                        )}
                        <span className="text-sm font-medium">{label}</span>
                      </button>
                    );
                  })()}
                  {SHOW_COACHING_SUMMARY && importStatus && importStatus.total_games > 0 && (
                    <button
                      type="button"
                      onClick={handleRefreshInsights}
                      disabled={insightsRefreshing || insightsLoading}
                      className="zen-pill px-4 py-2 text-sm font-medium text-[color:var(--zen-text)] hover:text-[color:var(--zen-accent)] transition disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {insightsRefreshing ? "Refreshing..." : "Refresh Insights"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteProfile(selectedProfile)}
                    disabled={deletingProfile === profileKey}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-[color:var(--zen-muted)] hover:text-red-500 hover:bg-red-500/10 transition disabled:opacity-50"
                    title="Remove profile and all data"
                  >
                    {deletingProfile === profileKey ? (
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="m4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                    ) : (
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {importResult && showImportResultInProfileCard && (
                <div className="mt-4 zen-surface-flat px-4 py-3 rounded-lg border border-[color:var(--zen-border)]/80">
                  <p className="text-sm">
                    <span className="text-[color:var(--zen-success)] font-semibold">
                      {importResult.is_sync
                        ? `Synced ${importResult.imported} new game${importResult.imported !== 1 ? "s" : ""}`
                        : `Imported ${importResult.imported} game${importResult.imported !== 1 ? "s" : ""}`}
                    </span>{" "}
                    <span className="text-[color:var(--zen-muted)]">
                      for{" "}
                      <span className="text-[color:var(--zen-text)] font-medium">{importResult.username}</span>
                      {importResult.skipped > 0 ? ` (${importResult.skipped} skipped)` : ""}
                    </span>
                  </p>
                </div>
              )}
              
              <div className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div className="zen-surface-flat p-4 rounded-lg text-center">
                  <div className="flex items-center justify-center gap-2 mb-2">
                    <img src="/time-controls/bullet.png" alt="Bullet" className="w-5 h-5" style={{ filter: "brightness(0) saturate(100%) invert(76%) sepia(65%) saturate(439%) hue-rotate(85deg) brightness(93%) contrast(92%)" }} />
                    <span className="text-base font-semibold uppercase tracking-wide text-[color:var(--zen-muted)]">Bullet</span>
                  </div>
                  <p className="text-2xl font-bold text-[color:var(--zen-text)] font-mono">
                    {selectedProfile.bullet_rating ?? "—"}
                  </p>
                </div>
                <div className="zen-surface-flat p-4 rounded-lg text-center">
                  <div className="flex items-center justify-center gap-2 mb-2">
                    <img src="/time-controls/blitz.png" alt="Blitz" className="w-5 h-5" style={{ filter: "brightness(0) saturate(100%) invert(76%) sepia(65%) saturate(439%) hue-rotate(85deg) brightness(93%) contrast(92%)" }} />
                    <span className="text-base font-semibold uppercase tracking-wide text-[color:var(--zen-muted)]">Blitz</span>
                  </div>
                  <p className="text-2xl font-bold text-[color:var(--zen-text)] font-mono">
                    {selectedProfile.blitz_rating ?? "—"}
                  </p>
                </div>
                <div className="zen-surface-flat p-4 rounded-lg text-center">
                  <div className="flex items-center justify-center gap-2 mb-2">
                    <img src="/time-controls/rapid.png" alt="Rapid" className="w-5 h-5" style={{ filter: "brightness(0) saturate(100%) invert(76%) sepia(65%) saturate(439%) hue-rotate(85deg) brightness(93%) contrast(92%)" }} />
                    <span className="text-base font-semibold uppercase tracking-wide text-[color:var(--zen-muted)]">Rapid</span>
                  </div>
                  <p className="text-2xl font-bold text-[color:var(--zen-text)] font-mono">
                    {selectedProfile.rapid_rating ?? "—"}
                  </p>
                </div>
                <div className="zen-surface-flat p-4 rounded-lg text-center">
                  <div className="flex items-center justify-center gap-2 mb-2">
                    <img src="/time-controls/classical.png" alt="Classical" className="w-5 h-5" style={{ filter: "brightness(0) saturate(100%) invert(76%) sepia(65%) saturate(439%) hue-rotate(85deg) brightness(93%) contrast(92%)" }} />
                    <span className="text-base font-semibold uppercase tracking-wide text-[color:var(--zen-muted)]">Classical</span>
                  </div>
                  <p className="text-2xl font-bold text-[color:var(--zen-text)] font-mono">
                    {selectedProfile.classical_rating ?? "—"}
                  </p>
                </div>
              </div>

              {/* Import progress indicator */}
              {importProgress && (
                <div className="mt-6 zen-surface-flat px-4 py-3 rounded-lg border border-[color:var(--zen-accent)]/40 bg-[color:var(--zen-accent)]/5">
                  <div className="flex items-center gap-3">
                    <div className="animate-spin h-4 w-4 border-2 border-[color:var(--zen-accent)] border-t-transparent rounded-full" />
                    <p className="text-sm text-[color:var(--zen-text)]">
                      {importProgress.status === "streaming" ? (
                        "Streaming games from server..."
                      ) : (
                        <>
                          Processing{" "}
                          <span className="font-medium">{importProgress.done}</span>
                          {" / "}
                          <span className="font-medium">{importProgress.total}</span>
                          {" games..."}
                        </>
                      )}
                    </p>
                  </div>
                  {importProgress.status === "processing" && importProgress.total > 0 && (
                    <div className="mt-2 h-1.5 bg-[color:var(--zen-border)] rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-[color:var(--zen-accent)] transition-all duration-300"
                        style={{ width: `${Math.round((importProgress.done / importProgress.total) * 100)}%` }}
                      />
                    </div>
                  )}
                </div>
              )}

              {/* Report info or empty state (only when not showing progress) */}
              {!importProgress && (importStatus?.imported_at || (importStatus?.total_games ?? 0) > 0) &&
                !loading &&
                importStatus!.username.toLowerCase() === selectedProfile.chess_username.toLowerCase() ? (
                  <div className="mt-6 zen-surface-flat px-4 py-3 rounded-lg border border-[color:var(--zen-border)]/80">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                      <p className="text-sm text-[color:var(--zen-muted)]">
                        Report generated from{" "}
                        <span className="text-[color:var(--zen-text)] font-medium">
                          {importStatus!.total_games}
                        </span>{" "}
                        games
                        {importStatus!.total_games > 0 && (importStatus!.imported_at || importStatus!.last_synced_at) && (
                          <>
                            {" "}
                            ({importStatus!.last_synced_at ? "last sync" : "last import"}:{" "}
                            {new Date(importStatus!.last_synced_at || importStatus!.imported_at!).toLocaleString("en-US", {
                              year: "numeric",
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                            )
                          </>
                        )}
                      </p>
                      {SHOW_COACHING_SUMMARY && (
                        <span className="zen-pill px-4 py-2 text-sm uppercase tracking-wide text-[color:var(--zen-muted)] shrink-0">
                          {insightsLoading && !insights
                            ? "Loading..."
                            : insights
                              ? insightsStatusLabel
                              : "—"}
                        </span>
                      )}
                    </div>
                  </div>
                ) : !importProgress && !loading && !importingProfile && (
                  <div className="mt-6 zen-surface-flat px-4 py-3 rounded-lg border border-[color:var(--zen-border)]/80">
                    <p className="text-sm text-[color:var(--zen-muted)]">
                      No games imported yet. Click <strong className="text-[color:var(--zen-text)]">Import</strong> to fetch games from this account.
                    </p>
                  </div>
                )}
            </div>
          );
        })()}

        {/* AI Coaching Summary skeleton — visible while insights are loading */}
        {SHOW_COACHING_SUMMARY && currentUsername && !coachingSummaryReady && (insightsLoading || (insights && shouldKeepPolling(insights))) && (
          <div className="zen-surface opening-frame p-8 sm:p-10 border border-[color:var(--zen-border)] rounded-2xl animate-pulse">
            <div className="mb-8">
              <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
                AI Insights
              </p>
              <div className="h-8 w-2/3 bg-[color:var(--zen-border)]/30 rounded mt-2" />
            </div>
            <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-8">
              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="h-4 w-24 bg-[color:var(--zen-border)]/30 rounded mb-4" />
                <div className="h-7 w-40 bg-[color:var(--zen-border)]/30 rounded mb-3" />
                <div className="space-y-2">
                  <div className="h-4 w-full bg-[color:var(--zen-border)]/20 rounded" />
                  <div className="h-4 w-3/4 bg-[color:var(--zen-border)]/20 rounded" />
                </div>
              </div>
              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="h-4 w-28 bg-[color:var(--zen-border)]/30 rounded mb-4" />
                <div className="space-y-2">
                  <div className="h-4 w-full bg-[color:var(--zen-border)]/20 rounded" />
                  <div className="h-4 w-5/6 bg-[color:var(--zen-border)]/20 rounded" />
                  <div className="h-4 w-2/3 bg-[color:var(--zen-border)]/20 rounded" />
                </div>
              </div>
              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="h-4 w-20 bg-[color:var(--zen-border)]/30 rounded mb-4" />
                <div className="space-y-2">
                  <div className="h-4 w-full bg-[color:var(--zen-border)]/20 rounded" />
                  <div className="h-4 w-3/4 bg-[color:var(--zen-border)]/20 rounded" />
                </div>
              </div>
              <div className="zen-surface p-8 rounded-xl border border-[color:var(--zen-border)] min-h-[180px]">
                <div className="h-4 w-24 bg-[color:var(--zen-border)]/30 rounded mb-4" />
                <div className="space-y-2">
                  <div className="h-4 w-full bg-[color:var(--zen-border)]/20 rounded" />
                  <div className="h-4 w-5/6 bg-[color:var(--zen-border)]/20 rounded" />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* AI Coaching Summary - render when insights are ready */}
        {SHOW_COACHING_SUMMARY && currentUsername && coachingSummaryReady && insights && (
          <div className="zen-surface opening-frame p-8 sm:p-10 border border-[color:var(--zen-border)] rounded-2xl">
            <div className="mb-8">
              <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)]">
                AI Insights
              </p>
              <h3 className="text-2xl sm:text-3xl font-semibold text-[color:var(--zen-text)] mt-1">
                Coaching summary for {currentUsername}
              </h3>
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

        {/* Problem Spotter section */}
        {currentUsername && problemSpotter && problemSpotter.total_problems > 0 && (
          <div className="zen-surface opening-frame p-8 sm:p-10 border border-[color:var(--zen-border)] rounded-2xl">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
              <div>
                <h3 className="text-2xl sm:text-3xl font-semibold text-[color:var(--zen-text)] mt-1">
                Tactical Analysis
                </h3>
              </div>
            </div>

            {/* Scan summary */}
            <div className="mb-8">
              {(() => {
                const cls = problemSpotter.by_classification;
                const gamesScanned = importStatus?.total_games || 0;
                return (
                  <p className="text-base text-[color:var(--zen-muted)]">
                    Scanned {gamesScanned} games and found {cls.blunders} tactical blunder{cls.blunders !== 1 ? "s" : ""}.
                  </p>
                );
              })()}
            </div>

            {/* Tactical fail categories grid */}
            {problemSpotter.by_theme.length > 0 && (() => {
              const themes = problemSpotter.by_theme.slice(0, 6);
              const maxCount = Math.max(...themes.map((t) => t.count), 1);
              return (
                <div className="mb-10">
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {themes.map((item) => (
                      <TacticalCategoryCard
                        key={item.theme}
                        theme={item.theme}
                        count={item.count}
                        maxCount={maxCount}
                        username={currentUsername!}
                      />
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Recent problems list - Terminal style table */}
            {problemSpotter.recent_problems.length > 0 && (
              <div className="mt-8">
                {(() => {
                  const blundersOnly = problemSpotter.recent_problems.filter(p => p.classification === "blunder" && p.tactic_type);
                  const recentBlunders = blundersOnly.slice(0, 6);

                  return (
                    <>
                      {/* Terminal-style container - matching theme */}
                      <div className="border-2 border-[#2d2d33] bg-[#1a1a1e] p-6 shadow-[0_0_20px_rgba(46,201,113,0.1)]">
                        {/* Header */}
                        <div className="flex items-center justify-between mb-6 pb-4 border-b border-[#2d2d33]">
                          <div className="flex items-center gap-3">
                            <span className="text-[#4ade80] text-lg">▌</span>
                            <h3 className="text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                              RECENT PROBLEMS
                            </h3>
                          </div>
                        </div>

                        {/* Table */}
                        <div className="overflow-x-auto">
                          <table className="w-full">
                            <thead>
                              <tr className="border-b border-[#2d2d33]">
                                <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                                  Date
                                </th>
                                <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                                  Opponent
                                </th>
                                <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                                  Time Control
                                </th>
                                <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                                  Type
                                </th>
                                <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                                  Phase
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {recentBlunders.map((problem, idx) => {
                                const gameUrl = problem.site && problem.site_game_id
                                  ? `/game/${problem.site}/${currentUsername}/${problem.site_game_id}?ply=${problem.ply}`
                                  : null;

                                return (
                                  <tr
                                    key={`problem-${idx}`}
                                    onClick={() => gameUrl && router.push(gameUrl)}
                                    className={`border-b border-[#2d2d33]/50 transition-all duration-200 ${
                                      gameUrl
                                        ? "cursor-pointer hover:bg-[#2d2d33]/40 hover:border-[#4ade80]/50 hover:shadow-[0_0_15px_rgba(74,222,128,0.15)]"
                                        : ""
                                    }`}
                                  >
                                    <td className="py-5 px-4">
                                      <span className="text-base text-[color:var(--zen-muted)]">
                                        {problem.played_at
                                          ? new Date(problem.played_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })
                                          : "—"}
                                      </span>
                                    </td>
                                    <td className="py-5 px-4">
                                      <span className="text-base font-semibold text-[color:var(--zen-text)]">
                                        {problem.opponent || "—"}
                                      </span>
                                    </td>
                                    <td className="py-5 px-4">
                                      <span className="inline-flex items-center gap-2 px-3 py-1 text-sm font-medium capitalize bg-[#2d2d33] text-[color:var(--zen-text)] border border-[#3d3d43]">
                                        {getTimeControlIcon(problem.time_class) && (
                                          <img 
                                            src={getTimeControlIcon(problem.time_class)!} 
                                            alt="" 
                                            className="w-4 h-4"
                                          />
                                        )}
                                        {problem.time_class || "—"}
                                      </span>
                                    </td>
                                    <td className="py-5 px-4">
                                      {problem.tactic_type ? (
                                        <span className="inline-block px-4 py-1.5 text-sm font-medium bg-[#6d7dcf]/20 text-[#6d7dcf] border border-[#6d7dcf]/30">
                                          {humanizeTheme(problem.tactic_type)}
                                        </span>
                                      ) : (
                                        <span className="text-[color:var(--zen-muted)]">—</span>
                                      )}
                                    </td>
                                    <td className="py-5 px-4">
                                      <span className="text-base text-[color:var(--zen-muted)] capitalize">
                                        {problem.phase}
                                      </span>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </>
                  );
                })()}
              </div>
            )}

            {/* Scan in progress indicator - shows during import */}
            {importProgress && importProgress.status === "processing" && (
              <div className="mt-6">
                <div className="w-full h-1.5 rounded-full bg-[color:var(--zen-border)] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[color:var(--zen-accent)] transition-all duration-700"
                    style={{
                      width: `${importProgress.total > 0 ? Math.max((importProgress.done / importProgress.total) * 100, 3) : 5}%`,
                    }}
                  />
                </div>
                <p className="mt-2 text-xs text-[color:var(--zen-muted)] text-center">
                  Processing games... Tactical analysis updates when complete.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Top openings - split by color (show when we have import history OR report data) */}
        {((importStatus?.total_games ?? 0) > 0 || (report && report.length > 0) || (reportBlack && reportBlack.length > 0)) && (
        <div className="zen-surface opening-frame p-5 sm:p-6 border border-[color:var(--zen-border)] rounded-2xl">
        {currentUsername && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
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
          <div className="animate-pulse">
            <div className="flex items-baseline justify-between gap-2 mb-4">
              <div className="h-6 w-48 bg-[color:var(--zen-border)]/30 rounded" />
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {[0, 1].map((side) => (
                <div key={side}>
                  <div className="h-4 w-32 bg-[color:var(--zen-border)]/30 rounded mb-3" />
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[color:var(--zen-border)]/50">
                        {["Opening", "Games", "Wins", "Draws", "Losses", "Score"].map((h) => (
                          <th key={h} className="text-left py-2 px-2">
                            <div className="h-3 w-12 bg-[color:var(--zen-border)]/20 rounded" />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Array.from({ length: 5 }).map((_, i) => (
                        <tr key={i} className="border-b border-[color:var(--zen-border)]/30">
                          {Array.from({ length: 6 }).map((_, j) => (
                            <td key={j} className="py-3 px-2">
                              <div className={`h-4 ${j === 0 ? "w-32" : "w-8"} bg-[color:var(--zen-border)]/20 rounded`} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </div>
        )}

        {report && !loading && (
          <div className="zen-surface rounded-2xl p-6 sm:p-8 border border-[color:var(--zen-border)]">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-6 h-6 rounded bg-gradient-to-br from-white to-gray-200 border border-gray-300 shadow-sm" />
              <h2 className="text-xl sm:text-2xl font-semibold text-[color:var(--zen-text)]">
                Top openings as White
              </h2>
            </div>

            <div className="overflow-hidden rounded-xl border border-[color:var(--zen-border)]">
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
                    {(processedWhiteReport || []).map((opening) => {
                      const parsed = parseOpeningName(opening.opening_label);
                      const badgeText =
                        opening.opening_key === "unknown"
                          ? "UNK"
                          : opening.opening_key.slice(0, 3).toUpperCase();
                      return (
                        <tr
                          key={`white-${opening.opening_key}`}
                          onClick={() => {
                            if (currentUsername) {
                              trackEvent("opening.view", {
                                properties: {
                                  source: "dashboard_openings_white_table",
                                },
                              });
                              const detailParams = new URLSearchParams({
                                site: "all",
                                color: "white",
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
                {(processedWhiteReport || []).length === 0 && (
                  <div className="p-8 text-center text-[color:var(--zen-muted)]">
                    No openings found as White.
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {reportBlack && !loading && (
          <div className="zen-surface rounded-2xl p-6 sm:p-8 border border-[color:var(--zen-border)]">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-6 h-6 rounded bg-gradient-to-br from-gray-800 to-black border border-gray-600 shadow-sm" />
              <h2 className="text-xl sm:text-2xl font-semibold text-[color:var(--zen-text)]">
                Top openings as Black
              </h2>
            </div>

            <div className="overflow-hidden rounded-xl border border-[color:var(--zen-border)]">
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
                    {(processedBlackReport || []).map((opening) => {
                      const parsed = parseOpeningName(opening.opening_label);
                      const badgeText =
                        opening.opening_key === "unknown"
                          ? "UNK"
                          : opening.opening_key.slice(0, 3).toUpperCase();
                      return (
                        <tr
                          key={`black-${opening.opening_key}`}
                          onClick={() => {
                            if (currentUsername) {
                              trackEvent("opening.view", {
                                properties: {
                                  source: "dashboard_openings_black_table",
                                },
                              });
                              const detailParams = new URLSearchParams({
                                site: "all",
                                color: "black",
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
                {(processedBlackReport || []).length === 0 && (
                  <div className="p-8 text-center text-[color:var(--zen-muted)]">
                    No openings found as Black.
                  </div>
                )}
              </div>
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
        )}

      {/* Delete Profile Confirmation Modal */}
      {confirmDeleteProfile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="zen-surface max-w-md w-full mx-4 p-6 rounded-2xl border border-[color:var(--zen-border)] shadow-xl">
            <h3 className="text-xl font-semibold text-[color:var(--zen-text)] mb-2">
              Remove Profile?
            </h3>
            <p className="text-[color:var(--zen-muted)] mb-4">
              This will permanently delete <span className="font-semibold text-[color:var(--zen-text)]">{confirmDeleteProfile.chess_username}</span> and all associated data:
            </p>
            <ul className="text-sm text-[color:var(--zen-muted)] mb-6 space-y-1 list-disc list-inside">
              <li>All imported games</li>
              <li>Game analysis and insights</li>
              <li>Tactical problem data</li>
              <li>Opening statistics</li>
            </ul>
            <p className="text-sm text-red-500 mb-6">
              This action cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setConfirmDeleteProfile(null)}
                className="px-4 py-2 rounded-lg text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)] transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleDeleteProfile(confirmDeleteProfile)}
                disabled={deletingProfile !== null}
                className="px-4 py-2 rounded-lg bg-red-600 text-white hover:bg-red-700 transition disabled:opacity-50"
              >
                {deletingProfile ? "Deleting..." : "Delete Profile"}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
