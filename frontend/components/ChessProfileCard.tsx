"use client";

import { useState } from "react";

export interface ChessProfile {
  chess_username: string;
  site: "lichess" | "chesscom";
  bullet_rating: number | null;
  blitz_rating: number | null;
  rapid_rating: number | null;
  classical_rating: number | null;
  created_at: string | null;
  updated_at: string | null;
}

interface ChessProfileCardProps {
  profile: ChessProfile;
  isSelected: boolean;
  isSyncing: boolean;
  onClick: () => void;
  onSync: () => void;
  onDelete: () => void;
}

const TIME_CONTROL_ICONS: Record<string, string> = {
  bullet: "/time-controls/bullet.png",
  blitz: "/time-controls/blitz.png",
  rapid: "/time-controls/rapid.png",
  classical: "/time-controls/classical.png",
};

function RatingBadge({
  label,
  rating,
  icon,
}: {
  label: string;
  rating: number | null;
  icon: string;
}) {
  return (
    <div className="flex items-center gap-1.5" title={label}>
      <img src={icon} alt={label} className="w-4 h-4 opacity-70" />
      <span className="text-xs font-mono text-[color:var(--zen-text)]">
        {rating !== null ? rating : "—"}
      </span>
    </div>
  );
}

export function ChessProfileCard({
  profile,
  isSelected,
  isSyncing,
  onClick,
  onSync,
  onDelete,
}: ChessProfileCardProps) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (showDeleteConfirm) {
      onDelete();
      setShowDeleteConfirm(false);
    } else {
      setShowDeleteConfirm(true);
      setTimeout(() => setShowDeleteConfirm(false), 3000);
    }
  };

  const handleSync = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSync();
  };

  const platformLabel = profile.site === "lichess" ? "Lichess" : "Chess.com";
  const platformColor =
    profile.site === "lichess"
      ? "bg-[#629924]/20 text-[#629924] border-[#629924]/30"
      : "bg-[#81b64c]/20 text-[#81b64c] border-[#81b64c]/30";

  return (
    <div
      onClick={onClick}
      className={`
        relative flex flex-col gap-3 p-4 rounded-xl border cursor-pointer transition-all
        ${
          isSelected
            ? "bg-[color:var(--zen-accent-2)] border-[color:var(--zen-accent)] ring-1 ring-[color:var(--zen-accent)]"
            : "bg-[color:var(--zen-surface)] border-[color:var(--zen-border)] hover:bg-[color:var(--zen-surface-2)] hover:border-[color:var(--zen-border-hover)]"
        }
      `}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold text-[color:var(--zen-text)] truncate max-w-[140px]">
            {profile.chess_username}
          </span>
          <span
            className={`inline-flex items-center px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider rounded border ${platformColor}`}
          >
            {platformLabel}
          </span>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleSync}
            disabled={isSyncing}
            className={`
              px-2.5 py-1.5 text-[10px] font-medium uppercase tracking-wider rounded-md
              border border-[color:var(--zen-border)] bg-transparent
              text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]
              hover:bg-[color:var(--zen-surface-2)] transition-colors
              ${isSyncing ? "opacity-50 cursor-wait" : "cursor-pointer"}
            `}
            title="Sync new games and update ratings"
          >
            {isSyncing ? "..." : "Sync"}
          </button>

          <button
            type="button"
            onClick={handleDelete}
            className={`
              w-7 h-7 flex items-center justify-center rounded-md
              border border-[color:var(--zen-border)] bg-transparent
              text-[color:var(--zen-muted)] hover:text-[color:var(--zen-danger)]
              hover:border-[color:var(--zen-danger)]/50 hover:bg-[color:var(--zen-danger)]/10
              transition-colors cursor-pointer
            `}
            title={showDeleteConfirm ? "Click again to confirm" : "Remove profile"}
          >
            {showDeleteConfirm ? (
              <span className="text-[10px] font-bold text-[color:var(--zen-danger)]">?</span>
            ) : (
              <span className="text-xs">×</span>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <RatingBadge
          label="Bullet"
          rating={profile.bullet_rating}
          icon={TIME_CONTROL_ICONS.bullet}
        />
        <RatingBadge
          label="Blitz"
          rating={profile.blitz_rating}
          icon={TIME_CONTROL_ICONS.blitz}
        />
        <RatingBadge
          label="Rapid"
          rating={profile.rapid_rating}
          icon={TIME_CONTROL_ICONS.rapid}
        />
        <RatingBadge
          label="Classical"
          rating={profile.classical_rating}
          icon={TIME_CONTROL_ICONS.classical}
        />
      </div>
    </div>
  );
}
