"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";

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
    return `M${Math.abs(evalData.mate)}`;
  }

  if (evalData.cp !== undefined && evalData.cp !== null) {
    const value = Math.abs(evalData.cp / 100);
    if (value === 0) return "0.0";
    return `${value.toFixed(1)}`;
  }

  return "0.0";
}

export default function EvalBar({
  eval: evalData,
  orientation = "white",
  height = 400,
  width = 30,
}: EvalBarProps) {
  const { targetWhitePercent, displayValue, isWhiteAdvantage } = useMemo(() => {
    if (!evalData) {
      return { targetWhitePercent: 50, displayValue: "0.0", isWhiteAdvantage: null };
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
      targetWhitePercent: percent,
      displayValue: formatEval(evalData),
      isWhiteAdvantage: isAdvantage,
    };
  }, [evalData]);

  const [animatedWhitePercent, setAnimatedWhitePercent] = useState(targetWhitePercent);
  const animatedRef = useRef(targetWhitePercent);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    const animate = () => {
      const current = animatedRef.current;
      const delta = targetWhitePercent - current;

      if (Math.abs(delta) < 0.08) {
        animatedRef.current = targetWhitePercent;
        setAnimatedWhitePercent(targetWhitePercent);
        rafRef.current = null;
        return;
      }

      // Dampened interpolation for smoother bar movement during frequent eval updates.
      const next = current + delta * 0.2;
      animatedRef.current = next;
      setAnimatedWhitePercent(next);
      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = null;
    };
  }, [targetWhitePercent]);

  // If board is flipped, we still show white on bottom
  const whiteHeight = (height * animatedWhitePercent) / 100;
  const blackHeight = height - whiteHeight;
  const evalLabelPositionClass =
    isWhiteAdvantage === null
      ? "top-1/2 -translate-y-1/2"
      : isWhiteAdvantage
      ? "bottom-2"
      : "top-2";
  const evalLabelStyle = {
    color:
      isWhiteAdvantage === null
        ? "#e5e7eb"
        : isWhiteAdvantage
        ? "rgba(17,24,39,0.92)"
        : "rgba(255,255,255,0.95)",
  };

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
      {/* Evaluation display (fixed at bar end, not on divider) */}
      <div
        className={`pointer-events-none absolute left-1/2 -translate-x-1/2 text-xs font-mono font-semibold ${evalLabelPositionClass}`}
        style={evalLabelStyle}
      >
        {displayValue}
      </div>
    </div>
  );
}
