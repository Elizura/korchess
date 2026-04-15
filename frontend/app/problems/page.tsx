"use client";

export const dynamic = "force-dynamic";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { withTrackingHeaders } from "@/lib/analytics/client";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "https://korchess.com";

const PAGE_SIZE = 8;

const TACTIC_CARD_LABEL: Record<string, string> = {
  hanging_piece: "Hanging Pieces",
  fork: "Missed Forks",
  double_attack: "Double Attacks",
  skewer: "Missed Skewers",
  pin: "Pins and Skewers",
  forced_mate: "Forced Mates",
  missed_forced_mate: "Forced Mates Missed",
  discovered_attack: "Discovery Attacks",
  missed_tactic: "Missed Tactics",
  tactical_oversight: "Tactical Oversight",
  critical_inaccuracy: "Critical Inaccuracy",
  conversion_miss: "Conversion Miss",
  defensive_slip: "Defensive Slips",
};

const TACTIC_BLURB: Record<string, string> = {
  hanging_piece: "You left a piece undefended, giving your opponent a free capture.",
  fork: "You missed a move that attacks two pieces at once.",
  double_attack: "You overlooked a move threatening two targets simultaneously.",
  skewer: "You fell for an attack through a high-value piece to one behind it.",
  pin: "You missed a pin holding a piece to your king or a more valuable target.",
  forced_mate: "You had a forced checkmate sequence on the board.",
  missed_forced_mate: "You missed a checkmate sequence that was available.",
  discovered_attack: "You overlooked an attack revealed by moving a blocking piece.",
  missed_tactic: "You had a tactical shot available but played something else.",
  tactical_oversight: "You missed a concrete tactical detail in the position.",
  critical_inaccuracy: "You played an imprecise move in a critical moment.",
  conversion_miss: "You failed to convert a winning or clearly better position.",
  defensive_slip: "You missed a defensive resource that could have held the position.",
};

interface Problem {
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
}

interface ProblemsByThemeResponse {
  items: Problem[];
  total_count: number;
  filtered_count: number;
  page: number;
  page_size: number;
  total_pages: number;
  available_time_controls: string[];
  available_phases: string[];
}

export default function ProblemsPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session } = useSession();

  const username = searchParams.get("user") || "";
  const theme = searchParams.get("theme") || "";

  const authHeaders = useMemo((): Record<string, string> => {
    if (!session?.idToken) return {};
    return { Authorization: `Bearer ${session.idToken}` };
  }, [session?.idToken]);

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<ProblemsByThemeResponse | null>(null);
  const [page, setPage] = useState(0);
  const [timeControlFilter, setTimeControlFilter] = useState<string>("");
  const [phaseFilter, setPhaseFilter] = useState<string>("");

  const themeLabel = TACTIC_CARD_LABEL[theme] || theme.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  const themeBlurb = TACTIC_BLURB[theme] || "";

  const fetchProblems = useCallback(async (
    currentPage: number,
    timeControl: string,
    phase: string
  ) => {
    if (!username || !theme) return;

    setLoading(true);
    try {
      const params = new URLSearchParams({
        username,
        theme,
        site: "all",
        page: currentPage.toString(),
        page_size: PAGE_SIZE.toString(),
      });
      if (timeControl) params.set("time_control", timeControl);
      if (phase) params.set("phase", phase);

      const res = await fetch(
        `${API_BASE_URL}/api/v1/insights/problems-by-theme?${params}`,
        { headers: withTrackingHeaders(authHeaders) }
      );
      if (!res.ok) {
        setData(null);
        return;
      }
      const responseData: ProblemsByThemeResponse = await res.json();
      setData(responseData);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [username, theme, authHeaders]);

  useEffect(() => {
    fetchProblems(page, timeControlFilter, phaseFilter);
  }, [fetchProblems, page, timeControlFilter, phaseFilter]);

  const handleTimeControlChange = (value: string) => {
    setTimeControlFilter(value);
    setPage(0);
  };

  const handlePhaseChange = (value: string) => {
    setPhaseFilter(value);
    setPage(0);
  };

  const handleClearFilters = () => {
    setTimeControlFilter("");
    setPhaseFilter("");
    setPage(0);
  };

  if (!username || !theme) {
    return (
      <div className="opening-page min-h-screen flex items-center justify-center">
        <p className="text-[color:var(--zen-muted)]">Missing user or theme parameter.</p>
      </div>
    );
  }

  const totalPages = data?.total_pages || 0;
  const problems = data?.items || [];
  const totalCount = data?.total_count || 0;
  const filteredCount = data?.filtered_count || 0;
  const availableTimeControls = data?.available_time_controls || [];
  const availablePhases = data?.available_phases || [];

  return (
    <div role="main" className="opening-page max-w-[1200px] mx-auto px-4 sm:px-6 py-10">
      {/* Back button */}
      <button
        onClick={() => router.push(`/dashboard?user=${encodeURIComponent(username)}`)}
        className="mb-6 px-4 py-2 text-sm font-semibold text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] transition-colors"
      >
        ← Back to Dashboard
      </button>

      {/* Header */}
      <div className="mb-8">
        <p className="text-sm font-medium uppercase tracking-wider text-[color:var(--zen-muted)] mb-1">
          Problem Category
        </p>
        <h1 className="text-3xl sm:text-4xl font-bold text-[color:var(--zen-text)] mb-3">
          {themeLabel}
        </h1>
        {themeBlurb && (
          <p className="text-base text-[color:var(--zen-muted)] max-w-2xl">
            {themeBlurb}
          </p>
        )}
        <div className="flex items-center gap-4 mt-4">
          <span className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold bg-[#f87171]/20 text-[#f87171] border border-[#f87171]/40">
            {totalCount} total occurrences
          </span>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="py-20 flex justify-center">
          <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
        </div>
      )}

      {/* Empty state - no problems at all */}
      {!loading && totalCount === 0 && (
        <div className="border-2 border-[#2d2d33] bg-[#1a1a1e] p-12 text-center">
          <p className="text-lg text-[color:var(--zen-muted)]">
            No problems found for this category.
          </p>
        </div>
      )}

      {/* Table */}
      {!loading && totalCount > 0 && (
        <div className="border-2 border-[#2d2d33] bg-[#1a1a1e] p-6 shadow-[0_0_20px_rgba(46,201,113,0.1)]">
          <div className="flex items-center justify-between mb-6 pb-4 border-b border-[#2d2d33]">
            <div className="flex items-center gap-3">
              <span className="text-[#4ade80] text-lg">▌</span>
              <h3 className="text-lg font-bold uppercase tracking-wider text-[color:var(--zen-text)]">
                Games with {themeLabel}
              </h3>
            </div>
            <span className="text-sm text-[color:var(--zen-muted)] uppercase tracking-wide">
              Showing {filteredCount} games
            </span>
          </div>

          {/* Filters */}
          <div className="flex items-center gap-4 mb-6 pb-4 border-b border-[#2d2d33]">
            <div className="flex items-center gap-2">
              <label className="text-sm font-semibold uppercase tracking-wider text-[color:var(--zen-muted)]">
                Time Control:
              </label>
              <select
                value={timeControlFilter}
                onChange={(e) => handleTimeControlChange(e.target.value)}
                className="px-3 py-1.5 text-sm font-medium capitalize bg-[#2d2d33] text-[color:var(--zen-text)] border border-[#3d3d43] focus:outline-none focus:border-[#4ade80]/50 transition-colors"
              >
                <option value="">All</option>
                {availableTimeControls.map(tc => (
                  <option key={tc} value={tc}>{tc}</option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-2">
              <label className="text-sm font-semibold uppercase tracking-wider text-[color:var(--zen-muted)]">
                Phase:
              </label>
              <select
                value={phaseFilter}
                onChange={(e) => handlePhaseChange(e.target.value)}
                className="px-3 py-1.5 text-sm font-medium capitalize bg-[#2d2d33] text-[color:var(--zen-text)] border border-[#3d3d43] focus:outline-none focus:border-[#4ade80]/50 transition-colors"
              >
                <option value="">All</option>
                {availablePhases.map(phase => (
                  <option key={phase} value={phase}>{phase}</option>
                ))}
              </select>
            </div>
          </div>

          {filteredCount === 0 ? (
            <div className="py-12 text-center">
              <p className="text-lg text-[color:var(--zen-muted)]">
                No games match the selected filters.
              </p>
              <button
                onClick={handleClearFilters}
                className="mt-4 px-4 py-2 text-sm font-semibold text-[color:var(--zen-accent)] hover:text-[color:var(--zen-text)] transition-colors"
              >
                Clear filters
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-[#2d2d33]">
                    <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                      Opponent
                    </th>
                    <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                      Time Control
                    </th>
                    <th className="text-left py-4 px-4 text-sm font-bold uppercase tracking-wider text-[color:var(--zen-muted)]">
                      Phase
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {problems.map((problem, idx) => {
                    const gameUrl = problem.site && problem.site_game_id
                      ? `/game/${problem.site}/${username}/${problem.site_game_id}?ply=${problem.ply}`
                      : null;

                    return (
                      <tr
                        key={`problem-${page}-${idx}`}
                        onClick={() => gameUrl && router.push(gameUrl)}
                        className={`border-b border-[#2d2d33]/50 transition-all duration-200 ${
                          gameUrl
                            ? "cursor-pointer hover:bg-[#2d2d33]/40 hover:border-[#4ade80]/50 hover:scale-[1.01] hover:shadow-[0_0_15px_rgba(74,222,128,0.15)]"
                            : ""
                        }`}
                      >
                        <td className="py-5 px-4">
                          <span className="text-base font-semibold text-[color:var(--zen-text)]">
                            {problem.opponent || "—"}
                          </span>
                        </td>
                        <td className="py-5 px-4">
                          <span className="inline-block px-3 py-1 text-sm font-medium capitalize bg-[#2d2d33] text-[color:var(--zen-text)] border border-[#3d3d43]">
                            {problem.time_class || "—"}
                          </span>
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

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-6 pt-4 border-t border-[#2d2d33]">
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="px-4 py-2 text-sm font-semibold uppercase tracking-wide text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    ← Previous
                  </button>

                  <div className="flex items-center gap-1">
                    {Array.from({ length: Math.min(totalPages, 10) }, (_, i) => {
                      let pageNum = i;
                      if (totalPages > 10) {
                        if (page < 5) {
                          pageNum = i;
                        } else if (page > totalPages - 6) {
                          pageNum = totalPages - 10 + i;
                        } else {
                          pageNum = page - 4 + i;
                        }
                      }
                      return (
                        <button
                          key={pageNum}
                          onClick={() => setPage(pageNum)}
                          className={`w-9 h-9 text-sm font-semibold transition-colors ${
                            page === pageNum
                              ? "bg-[#6d7dcf] text-white"
                              : "text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] hover:bg-[#2d2d33]"
                          }`}
                        >
                          {pageNum + 1}
                        </button>
                      );
                    })}
                  </div>

                  <button
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                    className="px-4 py-2 text-sm font-semibold uppercase tracking-wide text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    Next →
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
