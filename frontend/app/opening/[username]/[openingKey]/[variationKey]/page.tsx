"use client";

// This page reads initial filter state from URL search params.
export const dynamic = "force-dynamic";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { Site } from "@/components/SourceSelector";
import { useCountUp } from "@/hooks/useCountUp";
import { useSession } from "next-auth/react";
import { trackEvent, withTrackingHeaders } from "@/lib/analytics/client";
import {
  PAGE_DATA_CACHE_TTL_MS,
  buildOpeningCacheKey,
  getCached,
  isFresh,
  setCached,
} from "@/lib/pageDataCache";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface GameDetail {
  site: string;
  site_game_id: string;
  played_at: string | null;
  color: string;
  result: string;
  opponent: string | null;
  opening_name: string;
}

interface OpeningSummary {
  total_games: number;
  wins: number;
  draws: number;
  losses: number;
  score_pct: number;
  opening_key: string;
  opening_label: string;
  variation_label?: string | null;
}

interface OpeningGamesResponse {
  summary: OpeningSummary;
  games: GameDetail[];
}

interface OpeningPageCacheData {
  games: GameDetail[];
  summary: OpeningSummary | null;
  openingName: string;
  offset: number;
  hasMore: boolean;
}

type ColorFilter = "all" | "white" | "black";
type TimeClassFilter = "all" | "blitz" | "rapid" | "classical";
type ResultFilter = "all" | "win" | "draw" | "loss";

const sanitizeColorFilter = (raw: string | null): ColorFilter => {
  if (raw === "all" || raw === "white" || raw === "black") {
    return raw;
  }
  return "all";
};

const sanitizeTimeClassFilter = (raw: string | null): TimeClassFilter => {
  if (raw === "all" || raw === "blitz" || raw === "rapid" || raw === "classical") {
    return raw;
  }
  return "all";
};

// Helper to parse opening name
const parseOpeningName = (fullName: string) => {
  // Common pattern: "Main Opening: Variation" or "Main Opening, Variation"
  const separators = [': ', ' – ', ', '];
  
  for (const sep of separators) {
    if (fullName.includes(sep)) {
      const parts = fullName.split(sep);
      return {
        main: parts[0].trim(),
        variation: parts.slice(1).join(sep).trim()
      };
    }
  }
  
  // No separator found, entire name is the main opening
  return { main: fullName, variation: null };
};

export default function OpeningDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session } = useSession();
  const username = params.username as string;
  const openingKey = params.openingKey as string;
  const variationKey = params.variationKey as string;
  const siteParam = "all";
  const initialColorFilter = sanitizeColorFilter(searchParams.get("color"));
  const initialTimeClassFilter = sanitizeTimeClassFilter(searchParams.get("time_class"));
  const [site] = useState<Site>(siteParam as Site);
  const [games, setGames] = useState<GameDetail[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openingName, setOpeningName] = useState<string>("");
  const [summary, setSummary] = useState<OpeningSummary | null>(null);
  const [colorFilter, setColorFilter] = useState<ColorFilter>(() => initialColorFilter);
  const [timeClassFilter, setTimeClassFilter] = useState<TimeClassFilter>(() => initialTimeClassFilter);
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null);
  const authUserId = useMemo(
    () => (session?.user?.email || session?.user?.name || "anonymous").toLowerCase(),
    [session?.user?.email, session?.user?.name],
  );

  const statsVisible = !!summary && !loading;
  const countScore = useCountUp(summary?.score_pct ?? 0, { enabled: statsVisible, decimals: 1 });

  const authHeaders = useMemo((): Record<string, string> => {
    if (!session?.idToken) {
      return {};
    }
    return { Authorization: `Bearer ${session.idToken}` };
  }, [session?.idToken]);

  const openingCacheKey = (targetOffset: number): string =>
    buildOpeningCacheKey(
      username,
      openingKey,
      variationKey || "_",
      colorFilter,
      timeClassFilter,
      resultFilter,
      targetOffset,
      authUserId,
    );

  const fetchGames = async (resetOffset: boolean = false, options?: { force?: boolean }) => {
    const force = Boolean(options?.force);
    const currentOffset = resetOffset ? 0 : offset;
    let hasCached = false;

    if (resetOffset) {
      const cached = force ? null : getCached<OpeningPageCacheData>(openingCacheKey(0));
      if (cached) {
        hasCached = true;
        setGames(cached.data.games || []);
        setSummary(cached.data.summary || null);
        setOpeningName(cached.data.openingName || openingKey);
        setOffset(cached.data.offset || 0);
        setHasMore(Boolean(cached.data.hasMore));
        setLoading(false);
        setError(null);
        setRefreshNotice(null);
        if (isFresh(cached, PAGE_DATA_CACHE_TTL_MS)) {
          return;
        }
        setRefreshing(true);
      } else {
        // Keep existing content visible during filter switches; only full-load on first visit.
        if (games && games.length > 0) {
          setRefreshing(true);
          setLoading(false);
        } else {
          setLoading(true);
        }
      }
    } else {
      setLoadingMore(true);
    }

    setError(null);
    setRefreshNotice(null);

    try {
      const params = new URLSearchParams({
        opening_key: openingKey,
        variation_key: variationKey,
        color: colorFilter,
        time_class: timeClassFilter,
        result: resultFilter,
        offset: currentOffset.toString(),
        limit: "15"
      });

      const response = await fetch(
        `${API_BASE_URL}/api/v1/games/${site}/${encodeURIComponent(username)}?${params}`,
        { headers: withTrackingHeaders(authHeaders) }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Failed to fetch games: ${response.status}`);
      }

      const data: OpeningGamesResponse = await response.json();
      const baseGames = resetOffset ? [] : games || [];
      const nextGames = resetOffset ? data.games : [...baseGames, ...data.games];
      const nextOffset = nextGames.length;

      setGames(nextGames);
      setOffset(nextOffset);
      setSummary(data.summary);
      setHasMore(data.games.length === 15);

      let nextOpeningName = openingKey;
      if (data.summary?.opening_label) {
        nextOpeningName = data.summary.opening_label;
      } else if (data.games.length > 0) {
        nextOpeningName = data.games[0].opening_name;
      }
      setOpeningName(nextOpeningName);

      const cachePayload: OpeningPageCacheData = {
        games: nextGames,
        summary: data.summary,
        openingName: nextOpeningName,
        offset: nextOffset,
        hasMore: data.games.length === 15,
      };
      setCached<OpeningPageCacheData>(openingCacheKey(nextOffset), cachePayload);
      setCached<OpeningPageCacheData>(openingCacheKey(0), cachePayload);
    } catch (err) {
      if (hasCached) {
        setRefreshNotice("Showing cached data; background refresh failed.");
      } else {
        setError(err instanceof Error ? err.message : "An error occurred");
      }
    } finally {
      setLoading(false);
      setLoadingMore(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    if (username && openingKey) {
      void fetchGames(true);
    }
  }, [username, openingKey, variationKey, colorFilter, timeClassFilter, resultFilter, site, authUserId]);

  useEffect(() => {
    if (!username || !openingKey || !variationKey) return;
    trackEvent("opening.view", {
      properties: {
        source: "variation_detail_page",
      },
    });
  }, [username, openingKey, variationKey]);

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "Unknown date";
    
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return "Unknown date";
    }
  };

  const getResultColor = (result: string) => {
    if (result === "win") return "text-[color:var(--zen-success)]";
    if (result === "loss") return "text-[color:var(--zen-danger)]";
    return "text-[color:var(--zen-muted)]";
  };

  const getResultText = (result: string) => {
    return result.charAt(0).toUpperCase() + result.slice(1);
  };

  const parsedOpening = openingName ? parseOpeningName(openingName) : null;
  // On variation page: show "Main opening: Variation" when we have variation_label from the API
  const titleMain = summary?.variation_label
    ? summary.opening_label
    : parsedOpening?.main ?? openingKey;
  const titleVariation = summary?.variation_label ?? parsedOpening?.variation ?? null;

  return (
    <main className="opening-detail-page opening-detail-frame max-w-5xl mx-auto px-4 sm:px-6 py-10">
      <div className="mb-6">
        <h1 className="mt-0 text-2xl sm:text-3xl font-semibold tracking-tight text-[color:var(--zen-text)]">
          {titleVariation != null ? (
            <>
              {titleMain}
              <span className="font-normal text-[color:var(--zen-muted)]">
                {" : "}
                {titleVariation}
              </span>
            </>
          ) : (
            <>{titleMain}</>
          )}
        </h1>
        {/* <p className="text-sm text-[color:var(--zen-muted)] mt-2 flex items-center gap-2">
          <span className="zen-pill px-2 py-0.5 text-xs font-mono">{openingKey}</span>
          <span>Recent games for {username}</span>
        </p> */}
      </div>

      {/* Summary Stats - Now Clickable Filter Tiles */}
      {summary && !loading && (
        <div className="zen-surface-flat p-4 mb-6">
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 text-center">
            {/* Games tile - neutral (total) */}
            <button
              onClick={() => setResultFilter("all")}
              className={`summary-tile summary-tile-games p-3 rounded-lg transition cursor-pointer ${
                resultFilter === "all"
                  ? "summary-tile-active bg-[color:var(--zen-accent-2)] ring-2 ring-[color:var(--zen-accent)]"
                  : "hover:bg-[color:var(--zen-surface)]"
              }`}
            >
              <div className="text-2xl font-semibold summary-tile-num-games">{summary.total_games}</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Games</div>
            </button>

            {/* Wins tile - green */}
            <button
              onClick={() => setResultFilter("win")}
              className={`summary-tile summary-tile-wins p-3 rounded-lg transition cursor-pointer ${
                resultFilter === "win"
                  ? "summary-tile-active bg-[color:var(--zen-accent-2)] ring-2 ring-[color:var(--zen-success)]"
                  : "hover:bg-[color:var(--zen-surface)]"
              }`}
            >
              <div className="text-2xl font-semibold summary-tile-num-wins">{summary.wins}</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Wins</div>
            </button>

            {/* Draws tile - muted/neutral */}
            <button
              onClick={() => setResultFilter("draw")}
              className={`summary-tile summary-tile-draws p-3 rounded-lg transition cursor-pointer ${
                resultFilter === "draw"
                  ? "summary-tile-active bg-[color:var(--zen-accent-2)] ring-2 ring-[color:var(--zen-muted)]"
                  : "hover:bg-[color:var(--zen-surface)]"
              }`}
            >
              <div className="text-2xl font-semibold summary-tile-num-draws">{summary.draws}</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Draws</div>
            </button>

            {/* Losses tile - red */}
            <button
              onClick={() => setResultFilter("loss")}
              className={`summary-tile summary-tile-losses p-3 rounded-lg transition cursor-pointer ${
                resultFilter === "loss"
                  ? "summary-tile-active bg-[color:var(--zen-accent-2)] ring-2 ring-[color:var(--zen-danger)]"
                  : "hover:bg-[color:var(--zen-surface)]"
              }`}
            >
              <div className="text-2xl font-semibold summary-tile-num-losses">{summary.losses}</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Losses</div>
            </button>

            {/* Score tile - neutral */}
            <div className="summary-tile summary-tile-static summary-tile-score p-3 rounded-lg">
              <div className="text-2xl font-semibold summary-tile-num-score">{countScore.toFixed(1)}%</div>
              <div className="text-xs text-[color:var(--zen-muted)] uppercase tracking-wide">Score</div>
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      {!loading && (
        <div className="mb-6 flex flex-wrap gap-3 items-center">
          {/* Color tabs */}
          <div className="opening-detail-tabs zen-pill p-1 flex gap-1">
            {[
              { value: "all" as const, label: "All" },
              { value: "white" as const, label: "As White" },
              { value: "black" as const, label: "As Black" },
            ].map((tab) => (
              <button
                key={tab.value}
                onClick={() => setColorFilter(tab.value)}
                className={`opening-detail-tab px-3 py-1.5 rounded-full text-sm font-medium transition ${
                  colorFilter === tab.value
                    ? "bg-[color:var(--zen-accent-2)] text-[color:var(--zen-text)]"
                    : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Time control */}
          <select
            value={timeClassFilter}
            onChange={(e) => setTimeClassFilter(e.target.value as TimeClassFilter)}
            className="opening-detail-select zen-input px-3 py-1.5 text-sm outline-none"
          >
            <option value="all">All time controls</option>
            <option value="blitz">Blitz</option>
            <option value="rapid">Rapid</option>
            <option value="classical">Classical</option>
          </select>
        </div>
      )}

      {refreshing && games && (
        <p className="mb-3 text-xs text-[color:var(--zen-muted)]">Refreshing...</p>
      )}

      {refreshNotice && games && (
        <p className="mb-3 text-xs text-[color:var(--zen-muted)]">{refreshNotice}</p>
      )}

      {/* Loading */}
      {loading && (
        <div className="py-10 flex justify-center">
          <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div className="zen-surface-flat p-4 border-[color:var(--zen-danger)]/30">
          <p className="text-sm text-[color:var(--zen-danger)]">{error}</p>
        </div>
      )}

      {/* Games List */}
      {games && !loading && (
        <div className="opening-detail-panel zen-surface p-5 sm:p-6">
          <div className="space-y-3">
          {games.map((game, idx) => (
            <div
              key={`${game.site_game_id}-${idx}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                trackEvent("game.view", {
                  properties: {
                    source: "variation_games_list",
                  },
                });
                router.push(`/game/${game.site}/${encodeURIComponent(username)}/${game.site_game_id}`);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  trackEvent("game.view", {
                    properties: {
                      source: "variation_games_list_keyboard",
                    },
                  });
                  router.push(`/game/${game.site}/${encodeURIComponent(username)}/${game.site_game_id}`);
                }
              }}
              className="opening-detail-card group zen-surface-flat px-4 py-4 sm:px-5 sm:py-4 rounded-lg border border-transparent cursor-pointer transition-all duration-[180ms] ease-out hover:scale-[1.02] hover:-translate-y-0.5 hover:bg-[color:var(--zen-surface-2)] hover:border-[color:var(--zen-accent)] hover:shadow-[var(--zen-shadow-sm)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--zen-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--zen-bg)]"
            >
              <div className="flex flex-wrap items-center gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="text-base sm:text-lg font-semibold truncate">
                      vs{" "}
                      <span className="text-[color:var(--zen-text)]">
                        {game.opponent || "Unknown"}
                      </span>
                    </span>

                    <span className={`text-sm font-semibold ${getResultColor(game.result)}`}>
                      <span
                        className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
                        style={{
                          background:
                            game.result === "win"
                              ? "var(--zen-success)"
                              : game.result === "loss"
                                ? "var(--zen-danger)"
                                : "var(--zen-border)",
                        }}
                      />
                      {getResultText(game.result)}
                    </span>
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[color:var(--zen-muted)]">
                    <span className="opacity-80 group-hover:opacity-100 transition">
                      {formatDate(game.played_at)}
                    </span>
                    <span className="opacity-80 group-hover:opacity-100 transition">
                      {game.color === "white" ? "White" : "Black"}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
          </div>
        </div>
      )}

      {/* Load More */}
      {hasMore && games && games.length > 0 && !loading && (
        <div className="mt-6 text-center">
          <button
            onClick={() => fetchGames(false)}
            disabled={loadingMore}
            className="detail-action detail-load zen-pill px-6 py-2.5 text-sm font-medium text-[color:var(--zen-text)] hover:bg-[color:var(--zen-accent-2)] transition disabled:opacity-50"
          >
            {loadingMore ? "Loading..." : "Load more games"}
          </button>
        </div>
      )}

      {/* Empty State */}
      {games && games.length === 0 && !loading && (
        <div className="opening-detail-panel zen-surface-flat p-12 text-center">
          <p className="text-[color:var(--zen-muted)]">No games found for this opening.</p>
        </div>
      )}
    </main>
  );
}
