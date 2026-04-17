"use client";

export type Platform = "lichess" | "chesscom";

interface PlatformSelectorProps {
  value: Platform;
  onChange: (platform: Platform) => void;
  className?: string;
  disabled?: boolean;
}

export function PlatformSelector({
  value,
  onChange,
  className = "",
  disabled = false,
}: PlatformSelectorProps) {
  return (
    <div className={`flex gap-1 ${className}`}>
      <button
        type="button"
        disabled={disabled}
        className={`zen-pill px-3 py-2 text-xs font-medium uppercase tracking-wider transition ${
          value === "lichess"
            ? "bg-[color:var(--zen-accent)] text-white border border-[color:var(--zen-accent)]"
            : "bg-transparent text-[color:var(--zen-muted)] border border-[color:var(--zen-border)] hover:bg-[color:var(--zen-surface-2)] hover:text-[color:var(--zen-text)]"
        } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
        onClick={() => !disabled && onChange("lichess")}
      >
        Lichess
      </button>
      <button
        type="button"
        disabled={disabled}
        className={`zen-pill px-3 py-2 text-xs font-medium uppercase tracking-wider transition ${
          value === "chesscom"
            ? "bg-[color:var(--zen-accent)] text-white border border-[color:var(--zen-accent)]"
            : "bg-transparent text-[color:var(--zen-muted)] border border-[color:var(--zen-border)] hover:bg-[color:var(--zen-surface-2)] hover:text-[color:var(--zen-text)]"
        } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
        onClick={() => !disabled && onChange("chesscom")}
      >
        Chess.com
      </button>
    </div>
  );
}
