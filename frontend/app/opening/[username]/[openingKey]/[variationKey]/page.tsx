"use client";

import { useParams } from "next/navigation";
import { useState, useEffect } from "react";
import Link from "next/link";
import { Site } from "@/components/SourceSelector";
import { useCountUp } from "@/hooks/useCountUp";

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

type ColorFilter = "all" | "white" | "black";
type TimeClassFilter = "all" | "blitz" | "rapid" | "classical";
type ResultFilter = "all" | "win" | "draw" | "loss";

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
    const username = params.username as string;
  const openingKey = params.openingKey as string;
  const variationKey = params.variationKey as string;
  const siteParam = "all";
  const [site] = useState<Site>(siteParam as Site);
  const [games, setGames] = useState<GameDetail[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openingName, setOpeningName] = useState<string>("");
  const [summary, setSummary] = useState<OpeningSummary | null>(null);
  const [colorFilter, setColorFilter] = useState<ColorFilter>("all");
  const [timeClassFilter, setTimeClassFilter] = useState<TimeClassFilter>("all");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const statsVisible = !!summary && !loading;
  const countScore = useCountUp(summary?.score_pct ?? 0, { enabled: statsVisible, decimals: 1 });

  const fetchGames = async (resetOffset: boolean = false) => {
    const currentOffset = resetOffset ? 0 : offset;
    setLoading(resetOffset);
    setLoadingMore(!resetOffset);
    setError(null);

    try {
      const params = new URLSearchParams({
        opening_key: openingKey,
        variation_key: variationKey,
        color: colorFilter,
        time_class: timeClassFilter,
        result: resultFilter,
        offset: currentOffset.toString(),
        limit: "10"
      });

      const response = await fetch(
        `${API_BASE_URL}/api/games/${site}/${encodeURIComponent(username)}?${params}`
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Failed to fetch games: ${response.status}`);
      }

      const data: OpeningGamesResponse = await response.json();
      
      if (resetOffset) {
        setGames(data.games);
        setOffset(data.games.length);
      } else {
        setGames((prev) => [...(prev || []), ...data.games]);
        setOffset((prev) => prev + data.games.length);
      }
      
      setSummary(data.summary);
      setHasMore(data.games.length === 10);
      
      if (data.summary?.opening_label) {
        setOpeningName(data.summary.opening_label);
      } else if (data.games.length > 0) {
        setOpeningName(data.games[0].opening_name);
      } else {
        setOpeningName(openingKey);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    if (username && openingKey) {
      fetchGames(true);
    }
  }, [username, openingKey, variationKey, colorFilter, timeClassFilter, resultFilter, site]);

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
        <Link
          href={`/?user=${encodeURIComponent(username)}`}
          className="detail-back inline-flex items-center gap-2 text-sm zen-pill px-3 py-2 text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition"
        >
          ← Back to openings
        </Link>

        <h1 className="mt-5 text-2xl sm:text-3xl font-semibold tracking-tight text-[color:var(--zen-text)]">
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
              className="opening-detail-card group zen-surface-flat px-4 py-4 sm:px-5 sm:py-4 hover:bg-[color:var(--zen-surface-2)] transition"
            >
              <div className="flex flex-wrap items-center justify-between gap-4">
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

                <div className="flex gap-2">
                  <Link
                    href={`/game/${game.site}/${encodeURIComponent(username)}/${game.site_game_id}`}
                    className="detail-action zen-pill px-4 py-2 text-sm font-medium text-[color:var(--zen-text)] hover:bg-[color:var(--zen-accent-2)] transition"
                  >
                    Analyze
                  </Link>
                  <a
                    href={
                      game.site === "lichess"
                        ? `https://lichess.org/${game.site_game_id}`
                        : `https://www.chess.com/game/live/${game.site_game_id}`
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    className="detail-action zen-pill px-4 py-2 text-sm font-medium text-[color:var(--zen-text)] hover:bg-[color:var(--zen-accent-2)] transition"
                  >
                    {game.site === "lichess" ? "Lichess" : "Chess.com"} →
                  </a>
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
