"use client";

export type Site = "lichess" | "chesscom" | "all";

interface SourceSelectorProps {
  value: Site;
  onChange: (site: Site) => void;
  className?: string;
}

export function SourceSelector({ value, onChange, className = "" }: SourceSelectorProps) {
  return (
    <div className={`flex gap-2 ${className}`}>
      <button
        className={`zen-pill px-4 py-2 text-sm font-medium transition ${
          value === "lichess"
            ? "bg-[color:var(--zen-accent)] text-white"
            : "bg-[color:var(--zen-surface)] text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)]"
        }`}
        onClick={() => onChange("lichess")}
      >
        Lichess
      </button>
      <button
        className={`zen-pill px-4 py-2 text-sm font-medium transition ${
          value === "chesscom"
            ? "bg-[color:var(--zen-accent)] text-white"
            : "bg-[color:var(--zen-surface)] text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)]"
        }`}
        onClick={() => onChange("chesscom")}
      >
        Chess.com
      </button>
      <button
        className={`zen-pill px-4 py-2 text-sm font-medium transition ${
          value === "all"
            ? "bg-[color:var(--zen-accent)] text-white"
            : "bg-[color:var(--zen-surface)] text-[color:var(--zen-text)] hover:bg-[color:var(--zen-surface-2)]"
        }`}
        onClick={() => onChange("all")}
      >
        All
      </button>
    </div>
  );
}
