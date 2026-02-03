"use client";

import { useState, useMemo, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SourceSelector, Site } from "@/components/SourceSelector";

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

export default function Home() {
  const searchParams = useSearchParams();
  const router = useRouter();
  
  const [username, setUsername] = useState("");
  const [site, setSite] = useState<Site>("lichess");
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
  const [initialized, setInitialized] = useState(false);

  const fetchReport = async (
    user: string,
    color: ColorFilter,
    timeClass: TimeClassFilter,
    sourceSite: Site
  ) => {
    const params = new URLSearchParams();
    params.set("color", color);
    params.set("time_class", timeClass);

    const response = await fetch(
      `${API_BASE_URL}/api/openings/${sourceSite}/${encodeURIComponent(user)}?${params}`
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to fetch report: ${response.status}`);
    }

    return response.json();
  };

  const fetchImportStatus = async (user: string, sourceSite: Site) => {
    const response = await fetch(
      `${API_BASE_URL}/api/import-status/${sourceSite}/${encodeURIComponent(user)}`
    );
    
    if (!response.ok) {
      return null; // Silently handle - not critical
    }
    
    return response.json();
  };

  // Restore state from URL on mount or localStorage
  useEffect(() => {
    const userFromUrl = searchParams.get("user");
    
    if (!initialized) {
      setInitialized(true);
      
      // Determine which user to load: URL param or localStorage
      let userToLoad = userFromUrl;
      if (!userToLoad && typeof window !== "undefined") {
        userToLoad = localStorage.getItem("lastUsername");
      }
      
      if (userToLoad) {
        setUsername(userToLoad);
        setCurrentUsername(userToLoad);
        
        // Fetch data for the user
        const loadData = async () => {
          setLoading(true);
          try {
            const [reportData, statusData] = await Promise.all([
              fetchReport(userToLoad, colorFilter, timeClassFilter, site),
              fetchImportStatus(userToLoad, site)
            ]);
            setReport(reportData);
            if (statusData) {
              setImportStatus(statusData);
            }
            // Update URL if we loaded from localStorage
            if (!userFromUrl && userToLoad) {
              router.replace(`/?user=${encodeURIComponent(userToLoad)}`, { scroll: false });
            }
          } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load data");
          } finally {
            setLoading(false);
          }
        };
        
        loadData();
      }
    }
  }, [searchParams, initialized, colorFilter, timeClassFilter, site, router]);

  // Reload report when source site changes (Lichess / Chess.com / All)
  useEffect(() => {
    if (!currentUsername) return;

    const user = currentUsername;

    const loadForSiteChange = async () => {
      setLoading(true);
      setError(null);
      try {
        const [reportData, statusData] = await Promise.all([
          fetchReport(user, colorFilter, timeClassFilter, site),
          fetchImportStatus(user, site),
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

    loadForSiteChange();
  }, [site, currentUsername, colorFilter, timeClassFilter]);

  // Update URL when currentUsername changes
  const updateUrl = (user: string | null) => {
    if (user) {
      router.replace(`/?user=${encodeURIComponent(user)}`, { scroll: false });
    }
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
      let totalImported = 0;
      let totalSkipped = 0;

      // Import from selected sites
      if (site === "all") {
        // Import from both sites sequentially
        for (const sourceSite of ["lichess", "chesscom"] as const) {
          try {
            const importResponse = await fetch(`${API_BASE_URL}/api/import/${sourceSite}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ username: username.trim(), max_games: 200 }),
            });

            if (importResponse.ok) {
              const importData: ImportResponse = await importResponse.json();
              totalImported += importData.imported;
              totalSkipped += importData.skipped;
            }
          } catch (err) {
            console.warn(`Failed to import from ${sourceSite}:`, err);
          }
        }

        setImportResult({
          username: username.trim(),
          imported: totalImported,
          skipped: totalSkipped
        });
      } else {
        // Import from single site
        const importResponse = await fetch(`${API_BASE_URL}/api/import/${site}`, {
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
      }

      const trimmedUsername = username.trim();
      setCurrentUsername(trimmedUsername);
      updateUrl(trimmedUsername);
      
      // Save to localStorage for future visits
      if (typeof window !== "undefined") {
        localStorage.setItem("lastUsername", trimmedUsername);
      }

      // Auto-fetch report after successful import
      const reportData = await fetchReport(
        trimmedUsername,
        colorFilter,
        timeClassFilter,
        site
      );
      setReport(reportData);

      // Fetch import status
      const status = await fetchImportStatus(trimmedUsername, site);
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
        timeClassFilter,
        site
      );
      setReport(reportData);
      
      // Also fetch status
      const status = await fetchImportStatus(currentUsername, site);
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
          newTimeClass,
          site
        );
        setReport(reportData);
        
        // Also fetch status
        const status = await fetchImportStatus(currentUsername, site);
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
    <main className="max-w-6xl mx-auto px-4 sm:px-6 py-10">
      <div className="mb-6">
        <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight">
          Openingscope
        </h1>
        <p className="mt-2 text-sm sm:text-base text-[color:var(--zen-muted)]">
          Analyze your chess opening performance from Lichess games
        </p>
      </div>

      <div className="zen-surface p-5 sm:p-6">
        {/* Input row */}
        <div className="flex flex-col sm:flex-row gap-3 sm:gap-4">
          <div className="flex-1">
            <div className="mb-4">
              <label className="block text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2">
                Source
              </label>
              <SourceSelector value={site} onChange={setSite} />
            </div>

            <label
              htmlFor="username"
              className="block text-xs font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-2"
            >
              Username
            </label>
            <div className="flex items-center gap-3">
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleImport()}
                placeholder="e.g. elizura"
                className="zen-input w-full px-4 py-3 outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)] transition"
                disabled={loading}
              />
              <button
                onClick={handleImport}
                disabled={loading || !username.trim()}
                className="shrink-0 px-5 py-3 rounded-xl font-medium text-sm border border-[color:var(--zen-border)] bg-[color:var(--zen-accent)] text-white hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                {loading ? "Loading..." : "Import Games"}
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

        {/* Toolbar */}
        {currentUsername && (
          <div className="mt-6 flex flex-col lg:flex-row gap-3 lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <div className="zen-pill p-1 flex gap-1">
                {[
                  { value: "all", label: "All" },
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
                        "px-4 py-2 rounded-full text-sm font-medium transition",
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

              <label className="zen-pill px-3 py-2.5 flex items-center gap-2 text-sm text-[color:var(--zen-muted)] cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={hideUnknown}
                  onChange={(e) => setHideUnknown(e.target.checked)}
                  className="accent-[color:var(--zen-accent)]"
                />
                Hide UNKNOWN
              </label>
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

        {/* Loading State */}
        {loading && (
          <div className="py-10 flex justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
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

        {/* Results */}
        {report && !loading && (
          <div className="mt-6 overflow-hidden rounded-2xl border border-[color:var(--zen-border)]">
            <div className="overflow-x-auto">
              <table className="min-w-full">
                <thead className="bg-[color:var(--zen-surface-2)]">
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
                        className={`px-6 py-3 text-${col.align} text-[11px] font-medium uppercase tracking-wider text-[color:var(--zen-muted)] cursor-pointer hover:bg-[color:var(--zen-surface)] transition`}
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
                      <tr
                        key={`${opening.eco}-${idx}`}
                        onClick={() => {
                          if (currentUsername) {
                            router.push(
                              `/opening/${encodeURIComponent(
                                currentUsername
                              )}/${encodeURIComponent(opening.eco)}?site=${site}`
                            );
                          }
                        }}
                        className="cursor-pointer hover:bg-[color:var(--zen-surface)] transition"
                      >
                        <td className="px-6 py-4">
                          {(() => {
                            const parsed = parseOpeningName(opening.opening_name);
                            return (
                              <>
                                <div className="font-medium text-sm flex items-center gap-2">
                                  {parsed.main}
                                  <span className="zen-pill px-1.5 py-0.5 text-[10px] font-mono text-[color:var(--zen-muted)]">
                                    {opening.eco}
                                  </span>
                                </div>
                                {parsed.variation && (
                                  <div className="text-sm text-[color:var(--zen-muted)] mt-0.5">
                                    {parsed.variation}
                                  </div>
                                )}
                              </>
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
        )}

        {!report && !loading && !error && (
          <div className="mt-6 zen-surface-flat p-10 text-center">
            <p className="text-[color:var(--zen-muted)]">
              Select a source, enter a username, and click Import Games to see opening
              statistics.
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
