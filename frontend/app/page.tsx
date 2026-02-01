"use client";

import { useState, useMemo } from "react";

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

interface ImportStatus {
  username: string;
  imported_at: string | null;
  last_imported: number | null;
  last_skipped: number | null;
  total_games: number;
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
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [hideUnknown, setHideUnknown] = useState(false);
  const [sortConfig, setSortConfig] = useState<{
    key: keyof OpeningStats;
    direction: "asc" | "desc";
  }>({ key: "games", direction: "desc" });

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

  const fetchImportStatus = async (user: string) => {
    const response = await fetch(
      `${API_BASE_URL}/api/import-status/lichess/${encodeURIComponent(user)}`
    );
    
    if (!response.ok) {
      return null; // Silently handle - not critical
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

      // Fetch import status
      const status = await fetchImportStatus(username.trim());
      if (status) {
        setImportStatus(status);
      }
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

  const handleSort = (key: keyof OpeningStats) => {
    let direction: "asc" | "desc" = "desc";
    if (sortConfig.key === key && sortConfig.direction === "desc") {
      direction = "asc";
    }
    setSortConfig({ key, direction });
  };

  // Process report: filter -> sort
  const processedReport = useMemo(() => {
    if (!report) return null;
    
    // Step 1: Filter (hide unknown)
    let filtered = hideUnknown
      ? report.filter(
          (row) => row.eco !== "UNKNOWN" && row.opening_name !== "Unknown"
        )
      : report;
    
    // Step 2: Sort
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
            {/* Color Tabs */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Color
              </label>
              <div className="flex gap-1 bg-gray-100 rounded-md p-1">
                {[
                  { value: "all", label: "All" },
                  { value: "white", label: "As White" },
                  { value: "black", label: "As Black" },
                ].map((tab) => (
                  <button
                    key={tab.value}
                    onClick={() =>
                      handleFilterChange(tab.value as ColorFilter, timeClassFilter)
                    }
                    disabled={loading}
                    className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                      colorFilter === tab.value
                        ? "bg-white text-blue-600 shadow-sm"
                        : "text-gray-600 hover:text-gray-900"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
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
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Options
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={hideUnknown}
                  onChange={(e) => setHideUnknown(e.target.checked)}
                  className="w-4 h-4 text-blue-600 rounded focus:ring-2 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700">Hide UNKNOWN</span>
              </label>
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

      {/* Data Freshness Line */}
      {importStatus && currentUsername && !loading && (
        <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-md">
          {importStatus.imported_at ? (
            <p className="text-sm text-blue-900">
              Report generated from{" "}
              <strong>{importStatus.total_games}</strong> games
              {importStatus.total_games > 0 && (
                <>
                  {" "}(last import:{" "}
                  {new Date(importStatus.imported_at).toLocaleString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  {importStatus.last_imported === 0 && 
                    `, imported: 0, skipped: ${importStatus.last_skipped}`
                  })
                </>
              )}
            </p>
          ) : (
            <p className="text-sm text-blue-900">
              No imports yet for <strong>{currentUsername}</strong>
            </p>
          )}
        </div>
      )}

      {/* Results Table */}
      {report && !loading && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {[
                    { key: "eco" as const, label: "Opening (ECO)", align: "left" },
                    { key: "games" as const, label: "Games", align: "right" },
                    { key: "wins" as const, label: "Wins", align: "right" },
                    { key: "draws" as const, label: "Draws", align: "right" },
                    { key: "losses" as const, label: "Losses", align: "right" },
                    { key: "score_pct" as const, label: "Score %", align: "right" },
                  ].map((col) => (
                    <th
                      key={col.key}
                      onClick={() => handleSort(col.key)}
                      className={`px-6 py-3 text-${col.align} text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors`}
                    >
                      <div className={`flex items-center gap-1 ${col.align === "right" ? "justify-end" : ""}`}>
                        <span>{col.label}</span>
                        {sortConfig.key === col.key && (
                          <span className="text-blue-600">
                            {sortConfig.direction === "asc" ? "▲" : "▼"}
                          </span>
                        )}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {processedReport && processedReport.map((opening, idx) => (
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
          {processedReport && processedReport.length === 0 && (
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
