"use client";

import React, { useMemo } from "react";

interface EvalBarProps {
  eval: { cp?: number; mate?: number } | null;
  orientation?: "white" | "black";
  height?: number;
  width?: number;
}

/**
 * Convert evaluation to percentage (0-100)
 * 50 = equal, 100 = white winning, 0 = black winning
 */
function evalToPercent(cp: number): number {
  // Sigmoid-like curve, capped at ±1000cp visual range
  // This gives a smooth curve where:
  // ±0cp = 50%
  // ±200cp ≈ 65%/35%
  // ±500cp ≈ 85%/15%
  // ±1000cp+ ≈ 98%/2%
  const clamped = Math.max(-1000, Math.min(1000, cp));
  return 50 + 50 * (2 / (1 + Math.exp(-0.004 * clamped)) - 1);
}

/**
 * Format evaluation for display
 */
function formatEval(evalData: { cp?: number; mate?: number } | null): string {
  if (!evalData) return "0.0";

  if (evalData.mate !== undefined && evalData.mate !== null) {
    const sign = evalData.mate > 0 ? "" : "-";
    return `M${sign}${Math.abs(evalData.mate)}`;
  }

  if (evalData.cp !== undefined && evalData.cp !== null) {
    const value = evalData.cp / 100;
    if (value === 0) return "0.0";
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(1)}`;
  }

  return "0.0";
}

export default function EvalBar({
  eval: evalData,
  orientation = "white",
  height = 400,
  width = 30,
}: EvalBarProps) {
  const { whitePercent, displayValue, isWhiteAdvantage } = useMemo(() => {
    if (!evalData) {
      return { whitePercent: 50, displayValue: "0.0", isWhiteAdvantage: null };
    }

    let percent: number;
    if (evalData.mate !== undefined && evalData.mate !== null) {
      // Mate: show almost full bar
      percent = evalData.mate > 0 ? 98 : 2;
    } else if (evalData.cp !== undefined && evalData.cp !== null) {
      percent = evalToPercent(evalData.cp);
    } else {
      percent = 50;
    }

    const cp = evalData.cp ?? 0;
    const isAdvantage =
      evalData.mate !== undefined && evalData.mate !== null
        ? evalData.mate > 0
        : cp > 0
        ? true
        : cp < 0
        ? false
        : null;

    return {
      whitePercent: percent,
      displayValue: formatEval(evalData),
      isWhiteAdvantage: isAdvantage,
    };
  }, [evalData]);

  // If board is flipped, we still show white on bottom
  const whiteHeight = (height * whitePercent) / 100;
  const blackHeight = height - whiteHeight;

  return (
    <div
      className="relative flex flex-col rounded overflow-hidden shadow-md"
      style={{ width, height }}
    >
      {/* Black section (top) */}
      <div
        className="w-full transition-all duration-300 ease-out"
        style={{
          height: blackHeight,
          backgroundColor: "#1a1a1a",
        }}
      />
      {/* White section (bottom) */}
      <div
        className="w-full transition-all duration-300 ease-out"
        style={{
          height: whiteHeight,
          backgroundColor: "#f0f0f0",
        }}
      />
      {/* Evaluation display */}
      <div
        className="absolute left-1/2 -translate-x-1/2 text-xs font-mono font-bold px-1 py-0.5 rounded shadow-sm"
        style={{
          // Position near the dividing line
          top: blackHeight - 12,
          backgroundColor: isWhiteAdvantage === null 
            ? "#888" 
            : isWhiteAdvantage 
            ? "#f0f0f0" 
            : "#1a1a1a",
          color: isWhiteAdvantage === null 
            ? "#fff" 
            : isWhiteAdvantage 
            ? "#1a1a1a" 
            : "#f0f0f0",
          minWidth: 28,
          textAlign: "center",
        }}
      >
        {displayValue}
      </div>
    </div>
  );
}
