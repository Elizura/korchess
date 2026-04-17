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

const SITE_LOGOS: Record<string, string> = {
  lichess: "/site-logos/lichess.png",
  chesscom: "/site-logos/chesscom.png",
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
    <div className="flex items-center gap-1" title={label}>
      <img 
        src={icon} 
        alt={label} 
        className="w-3.5 h-3.5 opacity-70" 
        style={{ 
          filter: "brightness(0) saturate(100%) invert(84%) sepia(13%) saturate(800%) hue-rotate(78deg) brightness(85%) contrast(70%)"
        }} 
      />
      <span className="text-[11px] font-mono text-[color:var(--zen-text)]">
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

  const isLichess = profile.site === "lichess";
  const siteLogo = SITE_LOGOS[profile.site];
  const siteLabel = isLichess ? "Lichess" : "Chess.com";

  return (
    <div
      onClick={onClick}
      className={`
        relative flex flex-col gap-2.5 p-3 rounded-lg border cursor-pointer transition-all
        ${
          isSelected
            ? "bg-[color:var(--zen-accent-2)] border-[color:var(--zen-accent)] ring-1 ring-[color:var(--zen-accent)]"
            : "bg-[color:var(--zen-surface)] border-[color:var(--zen-border)] hover:bg-[color:var(--zen-surface-2)] hover:border-[color:var(--zen-border-hover)]"
        }
      `}
    >
      <div className="flex items-center gap-2 justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <div className="shrink-0 w-6 h-6 rounded flex items-center justify-center">
            <img src={siteLogo} alt={siteLabel} className="w-5 h-5 object-contain" />
          </div>
          <span className="text-sm font-semibold text-[color:var(--zen-text)] truncate leading-tight">
            {profile.chess_username}
          </span>
        </div>
        
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={handleSync}
            disabled={isSyncing}
            className={`
              w-7 h-7 flex items-center justify-center rounded-md
              border border-[color:var(--zen-border)] bg-[color:var(--zen-surface)]
              text-[color:var(--zen-muted)] hover:text-[color:var(--zen-text)]
              hover:bg-[color:var(--zen-surface-2)] hover:border-[color:var(--zen-accent)]/50
              transition-all duration-200
              ${isSyncing ? "opacity-50 cursor-wait animate-pulse" : "cursor-pointer hover:scale-105"}
            `}
            title="Sync new games and update ratings"
          >
            {isSyncing ? (
              <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="m4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            )}
          </button>

          <button
            type="button"
            onClick={handleDelete}
            className={`
              w-6 h-6 flex items-center justify-center rounded-md
              border border-[color:var(--zen-border)] bg-transparent
              text-[color:var(--zen-muted)] hover:text-[color:var(--zen-danger)]
              hover:border-[color:var(--zen-danger)]/50 hover:bg-[color:var(--zen-danger)]/10
              transition-colors cursor-pointer hover:scale-105
            `}
            title={showDeleteConfirm ? "Click again to confirm" : "Remove profile"}
          >
            {showDeleteConfirm ? (
              <span className="text-[10px] font-bold text-[color:var(--zen-danger)]">?</span>
            ) : (
              <span className="text-sm leading-none">×</span>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5">
        <RatingBadge label="Bullet" rating={profile.bullet_rating} icon={TIME_CONTROL_ICONS.bullet} />
        <RatingBadge label="Blitz" rating={profile.blitz_rating} icon={TIME_CONTROL_ICONS.blitz} />
        <RatingBadge label="Rapid" rating={profile.rapid_rating} icon={TIME_CONTROL_ICONS.rapid} />
        <RatingBadge label="Classical" rating={profile.classical_rating} icon={TIME_CONTROL_ICONS.classical} />
      </div>
    </div>
  );
}
