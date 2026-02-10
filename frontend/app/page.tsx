"use client";

// This page uses client-side routing/searchParams; force dynamic rendering to
// avoid Next.js "useSearchParams() should be wrapped in a suspense boundary" build errors.
export const dynamic = "force-dynamic";

import { useState, useMemo, useEffect, Fragment } from "react";
import { useSearchParams, useRouter } from "next/navigation";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

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

export default function Home() {
  const searchParams = useSearchParams();
  const router = useRouter();
  
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
  const [sortConfig, setSortConfig] = useState<{
    key: keyof OpeningStats;
    direction: "asc" | "desc";
  }>({ key: "games", direction: "desc" });
  const [initialized, setInitialized] = useState(false);
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
      `${API_BASE_URL}/api/openings/all/${encodeURIComponent(user)}?${params}`
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to fetch report: ${response.status}`);
    }

    return response.json();
  };

  const fetchImportStatus = async (user: string) => {
    const response = await fetch(
      `${API_BASE_URL}/api/import-status/all/${encodeURIComponent(user)}`
    );
    
    if (!response.ok) {
      return null; // Silently handle - not critical
    }
    
    return response.json();
  };

  const fetchVariations = async (user: string, openingKey: string) => {
    const params = new URLSearchParams();
    params.set("opening_key", openingKey);
    params.set("color", colorFilter);
    params.set("time_class", timeClassFilter);

    const response = await fetch(
      `${API_BASE_URL}/api/openings/all/${encodeURIComponent(user)}/variations?${params}`
    );

    if (!response.ok) {
      return [];
    }

    return response.json();
  };

  // Restore state from URL on mount
  useEffect(() => {
    const userFromUrl = searchParams.get("user");

    if (!initialized) {
      setInitialized(true);

      if (userFromUrl) {
        setUsername(userFromUrl);
        setCurrentUsername(userFromUrl);

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
      }
    }
  }, [searchParams, initialized, colorFilter, timeClassFilter, router]);

  // Update URL when currentUsername changes
  const updateUrl = (user: string | null) => {
    if (user) {
      router.replace(`/?user=${encodeURIComponent(user)}`, { scroll: false });
    }
  };

  const handleImportLichess = async () => {
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
      const importResponse = await fetch(`${API_BASE_URL}/api/import/lichess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    } catch (err) {
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

    setLoading(true);
    setError(null);
    setReport(null);
    setImportResult(null);

    const trimmedUsername = chesscomUsername.trim();

    try {
      const importResponse = await fetch(`${API_BASE_URL}/api/import/chesscom`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

  return (
    <div role="main" className="opening-page max-w-[1152px] mx-auto px-4 sm:px-6 py-10">
      <div className="mb-6">
        <h1 className="opening-title text-3xl sm:text-4xl font-semibold tracking-tight">
          Korchess
        </h1>
        <p className="opening-subtitle mt-2 text-sm sm:text-base text-[color:var(--zen-muted)]">
          Analyze your chess opening performance from your games
        </p>
      </div>

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
                placeholder="e.g. elizura"
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
                placeholder="e.g. elizura"
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

        {/* Toolbar */}
        {currentUsername && (
          <div className="mt-6 flex flex-col lg:flex-row gap-3 lg:items-center lg:justify-between">
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

              {/* <label className="zen-pill px-3 py-2.5 flex items-center gap-2 text-sm text-[color:var(--zen-muted)] cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={hideUnknown}
                  onChange={(e) => setHideUnknown(e.target.checked)}
                  className="accent-[color:var(--zen-accent)]"
                />
                Hide UNKNOWN
              </label> */}
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
          <div className="mt-6">
            <h2 className="text-lg sm:text-xl font-semibold text-[color:var(--zen-text)] mb-4">
              Your top 10 openings as {colorFilter === "white" ? "White" : "Black"}
            </h2>
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
          <div className="mt-6 zen-surface-flat p-10 text-center">
            <p className="text-[color:var(--zen-muted)]">
              Select a source, enter a username, and click Import Games to see opening
              statistics.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
