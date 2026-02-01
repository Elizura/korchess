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
    if (result === "win") return "text-green-600 font-semibold";
    if (result === "loss") return "text-red-600 font-semibold";
    return "text-gray-600 font-medium";
  };

  const getResultText = (result: string) => {
    return result.charAt(0).toUpperCase() + result.slice(1);
  };

  return (
    <main className="max-w-4xl mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <Link
          href={`/?user=${encodeURIComponent(username)}`}
          className="text-blue-600 hover:text-blue-800 mb-4 flex items-center gap-2"
        >
          ← Back to openings
        </Link>
        <h1 className="text-3xl font-bold text-gray-900">
          {eco} {openingName && `– ${openingName}`}
        </h1>
        <p className="text-gray-600 mt-1">Recent games for {username}</p>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-md">
          <p className="text-red-800">{error}</p>
        </div>
      )}

      {/* Games List */}
      {games && !loading && (
        <div className="space-y-4">
          {games.map((game, idx) => (
            <div
              key={`${game.site_game_id}-${idx}`}
              className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow"
            >
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm text-gray-500">
                      {formatDate(game.played_at)}
                    </span>
                    <span className="px-2 py-1 text-xs font-medium rounded bg-gray-100 text-gray-700">
                      {game.color === "white" ? "White" : "Black"}
                    </span>
                    <span className={`text-sm ${getResultColor(game.result)}`}>
                      {getResultText(game.result)}
                    </span>
                  </div>
                  <p className="text-gray-700">
                    vs{" "}
                    <span className="font-medium">
                      {game.opponent || "Unknown"}
                    </span>
                  </p>
                </div>
                <a
                  href={game.lichess_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 transition-colors"
                >
                  View on Lichess →
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty State */}
      {games && games.length === 0 && !loading && (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <p className="text-gray-500">No games found for this opening.</p>
        </div>
      )}
    </main>
  );
}
