"use client";

import React from "react";

interface EngineLine {
  cp?: number;
  mate?: number;
  depth?: number;
  pv?: string[];
  pvSan?: string[];
  pv_uci?: string[];
  pv_san?: string[];
}

interface EngineLinesProps {
  lines: EngineLine[] | null;
  depth?: number;
  isLoading?: boolean;
  onLineClick?: (line: EngineLine, index: number) => void;
}

/**
 * Format evaluation for display
 */
function formatEval(line: EngineLine): string {
  if (line.mate !== undefined && line.mate !== null) {
    const sign = line.mate > 0 ? "+" : "";
    return `M${sign}${line.mate}`;
  }

  if (line.cp !== undefined && line.cp !== null) {
    const value = line.cp / 100;
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(2)}`;
  }

  return "0.00";
}

/**
 * Get color class based on evaluation
 */
function getEvalColor(line: EngineLine): string {
  if (line.mate !== undefined && line.mate !== null) {
    return line.mate > 0
      ? "text-[color:var(--zen-success)]"
      : "text-[color:var(--zen-danger)]";
  }

  if (line.cp !== undefined && line.cp !== null) {
    if (line.cp > 100) return "text-[color:var(--zen-success)]";
    if (line.cp > 30) return "text-[color:var(--zen-success)]";
    if (line.cp < -100) return "text-[color:var(--zen-danger)]";
    if (line.cp < -30) return "text-[color:var(--zen-danger)]";
    return "text-[color:var(--zen-muted)]";
  }

  return "text-[color:var(--zen-muted)]";
}

/**
 * Get background color for eval badge
 */
function getEvalBg(line: EngineLine): string {
  if (line.mate !== undefined && line.mate !== null) {
    return line.mate > 0 ? "bg-emerald-500/15" : "bg-red-500/15";
  }

  if (line.cp !== undefined && line.cp !== null) {
    if (line.cp > 100) return "bg-emerald-500/15";
    if (line.cp > 30) return "bg-emerald-500/10";
    if (line.cp < -100) return "bg-red-500/15";
    if (line.cp < -30) return "bg-red-500/10";
    return "bg-white/5";
  }

  return "bg-white/5";
}

export default function EngineLines({
  lines,
  depth,
  isLoading,
  onLineClick,
}: EngineLinesProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex items-center gap-2 animate-pulse">
            <div className="w-16 h-6 rounded bg-[color:var(--zen-surface-2)] border border-[color:var(--zen-border)]" />
            <div className="flex-1 h-6 rounded bg-[color:var(--zen-surface)] border border-[color:var(--zen-border)]" />
          </div>
        ))}
      </div>
    );
  }

  if (!lines || lines.length === 0) {
    return (
      <div className="text-[color:var(--zen-muted)] text-sm text-center py-4">
        No engine analysis available
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {/* Header */}
      {depth && (
        <div className="text-xs text-[color:var(--zen-muted)] mb-2">
          Depth {depth}
        </div>
      )}

      {/* Lines */}
      {lines.map((line, index) => {
        // Get PV moves (handle different property names)
        const pvMoves = line.pv_san || line.pvSan || line.pv || line.pv_uci || [];
        const displayMoves = pvMoves.slice(0, 8);

        return (
          <div
            key={index}
            className={`
              flex items-start gap-2 p-1.5 rounded
              ${onLineClick ? "cursor-pointer hover:bg-[color:var(--zen-accent-2)]" : ""}
              ${index === 0 ? "bg-[color:var(--zen-accent-2)]" : ""}
            `}
            onClick={() => onLineClick?.(line, index)}
          >
            {/* Line number */}
            <span className="text-xs text-[color:var(--zen-muted)] w-4 flex-shrink-0 pt-0.5">
              {index + 1}.
            </span>

            {/* Evaluation badge */}
            <span
              className={`
                font-mono text-sm font-semibold px-2 py-0.5 rounded
                min-w-[60px] text-center flex-shrink-0
                ${getEvalBg(line)} ${getEvalColor(line)}
              `}
            >
              {formatEval(line)}
            </span>

            {/* PV moves */}
            <div className="flex-1 text-sm font-mono text-gray-700 leading-relaxed">
              {displayMoves.map((move, moveIdx) => (
                <span key={moveIdx}>
                  <span className="hover:text-[color:var(--zen-accent)] hover:underline cursor-pointer text-[color:var(--zen-text)]">
                    {move}
                  </span>
                  {moveIdx < displayMoves.length - 1 && (
                    <span className="text-[color:var(--zen-muted)] mx-1"></span>
                  )}
                </span>
              ))}
              {pvMoves.length > 8 && (
                <span className="text-[color:var(--zen-muted)] ml-1">...</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
