"use client";

import React from "react";

interface BoardControlsProps {
  // Navigation
  onFirst: () => void;
  onPrev: () => void;
  onNext: () => void;
  onLast: () => void;
  
  // Board settings
  orientation: "white" | "black";
  onFlip: () => void;
  
  showCoordinates: boolean;
  onToggleCoordinates: () => void;
  
  showArrows: boolean;
  onToggleArrows: () => void;
  
  // External link (URL and site for label: "Lichess" vs "Chess.com")
  lichessUrl?: string;
  site?: string;
}

export default function BoardControls({
  onFirst,
  onPrev,
  onNext,
  onLast,
  orientation,
  onFlip,
  showCoordinates,
  onToggleCoordinates,
  showArrows,
  onToggleArrows,
  lichessUrl,
  site,
}: BoardControlsProps) {
  const externalLinkLabel = site === "chesscom" ? "Chess.com" : "Lichess";
  return (
    <div className="flex flex-wrap items-center gap-2 p-3 zen-surface-flat analysis-controls-bar">
      {/* Navigation buttons */}
      <div className="flex items-center gap-1 border-r border-[color:var(--zen-border)] pr-3">
        <button
          onClick={onFirst}
          className="zen-pill p-2 hover:bg-[color:var(--zen-accent-2)] transition-colors text-[color:var(--zen-text)]"
          title="Go to start (Home)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
        <button
          onClick={onPrev}
          className="zen-pill p-2 hover:bg-[color:var(--zen-accent-2)] transition-colors text-[color:var(--zen-text)]"
          title="Previous move (←)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <button
          onClick={onNext}
          className="zen-pill p-2 hover:bg-[color:var(--zen-accent-2)] transition-colors text-[color:var(--zen-text)]"
          title="Next move (→)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>
        <button
          onClick={onLast}
          className="zen-pill p-2 hover:bg-[color:var(--zen-accent-2)] transition-colors text-[color:var(--zen-text)]"
          title="Go to end (End)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      {/* Board controls */}
      <div className="flex items-center gap-2 border-r border-[color:var(--zen-border)] pr-3">
        <button
          onClick={onFlip}
          className={`zen-pill p-2 transition-colors text-[color:var(--zen-text)] ${
            orientation === "black"
              ? "bg-[color:var(--zen-accent-2)]"
              : "hover:bg-[color:var(--zen-accent-2)]"
          }`}
          title="Flip board (F)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
          </svg>
        </button>
        <button
          onClick={onToggleCoordinates}
          className={`zen-pill p-2 transition-colors text-xs font-mono text-[color:var(--zen-text)] ${
            showCoordinates
              ? "bg-[color:var(--zen-accent-2)]"
              : "hover:bg-[color:var(--zen-accent-2)]"
          }`}
          title="Toggle coordinates"
        >
          a1
        </button>
        <button
          onClick={onToggleArrows}
          className={`zen-pill p-2 transition-colors text-[color:var(--zen-text)] ${
            showArrows
              ? "bg-[color:var(--zen-accent-2)]"
              : "hover:bg-[color:var(--zen-accent-2)]"
          }`}
          title="Toggle arrows"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
          </svg>
        </button>
      </div>

      {/* External game link (Lichess or Chess.com) */}
      {lichessUrl && (
        <a
          href={lichessUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto zen-pill px-3 py-1.5 text-sm font-medium hover:bg-[color:var(--zen-accent-2)] transition-colors flex items-center gap-1 text-[color:var(--zen-text)]"
        >
          {externalLinkLabel}
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      )}
    </div>
  );
}
