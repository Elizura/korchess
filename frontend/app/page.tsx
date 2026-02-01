"use client";

import { useState } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface OpeningStats {
  eco: string;
  opening_name: string;
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

type ColorFilter = "all" | "white" | "black";
type TimeClassFilter = "all" | "blitz" | "rapid" | "classical";

export default function Home() {
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<OpeningStats[] | null>(null);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [colorFilter, setColorFilter] = useState<ColorFilter>("all");
  const [timeClassFilter, setTimeClassFilter] =
    useState<TimeClassFilter>("all");
  const [currentUsername, setCurrentUsername] = useState<string | null>(null);

  const fetchReport = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter
  ) => {
    const params = new URLSearchParams();
    params.set("color", color);
    params.set("time_class", timeClass);

    const response = await fetch(
      `${API_BASE_URL}/api/openings/lichess/${encodeURIComponent(user)}?${params}`
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to fetch report: ${response.status}`);
    }

    return response.json();
  };

  const handleImport = async () => {
    if (!username.trim()) {
      setError("Please enter a username");
      return;
    }

    setLoading(true);
    setError(null);
    setReport(null);
    setImportResult(null);

    try {
      // Import games
      const importResponse = await fetch(`${API_BASE_URL}/api/import/lichess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), max_games: 200 }),
      });

      if (!importResponse.ok) {
        const data = await importResponse.json().catch(() => ({}));
        throw new Error(
          data.detail || `Import failed: ${importResponse.status}`
        );
      }

      const importData: ImportResponse = await importResponse.json();
      setImportResult(importData);
      setCurrentUsername(username.trim());

      // Auto-fetch report after successful import
      const reportData = await fetchReport(
        username.trim(),
        colorFilter,
        timeClassFilter
      );
      setReport(reportData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = async () => {
    if (!currentUsername) return;

    setLoading(true);
    setError(null);

    try {
      const reportData = await fetchReport(
        currentUsername,
        colorFilter,
        timeClassFilter
      );
      setReport(reportData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
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
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <main className="max-w-6xl mx-auto p-6">
      <h1 className="text-3xl font-bold text-gray-900 mb-2">Openingscope</h1>
      <p className="text-gray-600 mb-8">
        Analyze your chess opening performance from Lichess games
      </p>

      {/* Input Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1">
            <label
              htmlFor="username"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Lichess Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleImport()}
              placeholder="Enter username..."
              className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
              disabled={loading}
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleImport}
              disabled={loading || !username.trim()}
              className="w-full sm:w-auto px-6 py-2 bg-blue-600 text-white font-medium rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Loading..." : "Import Games"}
            </button>
          </div>
        </div>

        {/* Import Result */}
        {importResult && (
          <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-md">
            <p className="text-green-800">
              Imported <strong>{importResult.imported}</strong> games for{" "}
              <strong>{importResult.username}</strong>
              {importResult.skipped > 0 && (
                <span className="text-green-600">
                  {" "}
                  ({importResult.skipped} skipped)
                </span>
              )}
            </p>
          </div>
        )}
      </div>

      {/* Error Display */}
      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-md">
          <p className="text-red-800">{error}</p>
        </div>
      )}

      {/* Filters */}
      {currentUsername && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="flex flex-wrap gap-4 items-center">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Color
              </label>
              <select
                value={colorFilter}
                onChange={(e) =>
                  handleFilterChange(
                    e.target.value as ColorFilter,
                    timeClassFilter
                  )
                }
                className="px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                disabled={loading}
              >
                <option value="all">All</option>
                <option value="white">White</option>
                <option value="black">Black</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Time Control
              </label>
              <select
                value={timeClassFilter}
                onChange={(e) =>
                  handleFilterChange(
                    colorFilter,
                    e.target.value as TimeClassFilter
                  )
                }
                className="px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                disabled={loading}
              >
                <option value="all">All</option>
                <option value="blitz">Blitz</option>
                <option value="rapid">Rapid</option>
                <option value="classical">Classical</option>
              </select>
            </div>
            <div className="flex items-end">
              <button
                onClick={handleRefresh}
                disabled={loading}
                className="px-4 py-2 bg-gray-100 text-gray-700 font-medium rounded-md hover:bg-gray-200 disabled:bg-gray-50 disabled:cursor-not-allowed transition-colors"
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading && (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
        </div>
      )}

      {/* Results Table */}
      {report && !loading && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Opening (ECO)
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Games
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Wins
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Draws
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Losses
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Score %
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {report.map((opening, idx) => (
                  <tr key={`${opening.eco}-${idx}`} className="hover:bg-gray-50">
                    <td className="px-6 py-4">
                      <div className="font-medium text-gray-900">
                        {opening.eco}
                      </div>
                      <div className="text-sm text-gray-500">
                        {opening.opening_name}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right text-gray-900">
                      {opening.games}
                    </td>
                    <td className="px-6 py-4 text-right text-green-600 font-medium">
                      {opening.wins}
                    </td>
                    <td className="px-6 py-4 text-right text-gray-500">
                      {opening.draws}
                    </td>
                    <td className="px-6 py-4 text-right text-red-600 font-medium">
                      {opening.losses}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span
                        className={`font-semibold ${
                          opening.score_pct >= 55
                            ? "text-green-600"
                            : opening.score_pct <= 45
                              ? "text-red-600"
                              : "text-gray-900"
                        }`}
                      >
                        {opening.score_pct.toFixed(1)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.length === 0 && (
            <div className="p-8 text-center text-gray-500">
              No games found with the selected filters.
            </div>
          )}
        </div>
      )}

      {/* Empty State */}
      {!report && !loading && !error && (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <p className="text-gray-500">
            Enter a Lichess username and click Import Games to see opening
            statistics.
          </p>
        </div>
      )}
    </main>
  );
}
