"use client";

import { useParams } from "next/navigation";
import { useState, useEffect } from "react";
import Link from "next/link";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface GameDetail {
  site_game_id: string;
  played_at: string | null;
  color: string;
  result: string;
  opponent: string | null;
  opening_name: string;
  lichess_url: string;
}

export default function OpeningDetailPage() {
  const params = useParams();
  const username = params.username as string;
  const eco = params.eco as string;

  const [games, setGames] = useState<GameDetail[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openingName, setOpeningName] = useState<string>("");

  useEffect(() => {
    const fetchGames = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${API_BASE_URL}/api/games/lichess/${encodeURIComponent(username)}?eco=${encodeURIComponent(eco)}&limit=10`
        );

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || `Failed to fetch games: ${response.status}`);
        }

        const data: GameDetail[] = await response.json();
        setGames(data);
        
        // Set opening name from first game
        if (data.length > 0) {
          setOpeningName(data[0].opening_name);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    };

    if (username && eco) {
      fetchGames();
    }
  }, [username, eco]);

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

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10">
      <div className="mb-6">
        <Link
          href={`/?user=${encodeURIComponent(username)}`}
          className="inline-flex items-center gap-2 text-sm zen-pill px-3 py-2 text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition"
        >
          ← Back to openings
        </Link>

        <h1 className="mt-5 text-2xl sm:text-3xl font-semibold tracking-tight">
          <span className="font-mono">{eco}</span>{" "}
          {openingName && (
            <span className="text-[color:var(--zen-text)]">– {openingName}</span>
          )}
        </h1>
        <p className="mt-2 text-sm text-[color:var(--zen-muted)]">
          Recent games for{" "}
          <span className="text-[color:var(--zen-text)] font-medium">
            {username}
          </span>
        </p>
      </div>

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
        <div className="zen-surface p-5 sm:p-6">
          <div className="space-y-3">
          {games.map((game, idx) => (
            <div
              key={`${game.site_game_id}-${idx}`}
              className="group zen-surface-flat px-4 py-4 sm:px-5 sm:py-4 hover:bg-[color:var(--zen-surface-2)] transition"
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

                <a
                  href={game.lichess_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="zen-pill px-4 py-2 text-sm font-medium text-[color:var(--zen-text)] hover:bg-[color:var(--zen-accent-2)] transition"
                >
                  View on Lichess →
                </a>
              </div>
            </div>
          ))}
          </div>
        </div>
      )}

      {/* Empty State */}
      {games && games.length === 0 && !loading && (
        <div className="zen-surface-flat p-12 text-center">
          <p className="text-[color:var(--zen-muted)]">No games found for this opening.</p>
        </div>
      )}
    </main>
  );
}
