"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { withTrackingHeaders } from "@/lib/analytics/client";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

const AVATARS = [
  { id: "av-pawn", icon: "chess_pawn", piece: "pawn", label: "Pawn" },
  { id: "av-knight", icon: "chess_knight", piece: "knight", label: "Knight" },
  { id: "av-bishop", icon: "chess_bishop", piece: "bishop", label: "Bishop" },
  { id: "av-rook", icon: "chess_rook", piece: "rook", label: "Rook" },
  { id: "av-queen", icon: "chess_queen", piece: "queen", label: "Queen" },
  { id: "av-king", icon: "chess_king", piece: "king", label: "King" },
] as const;

export default function ProfileEditPage() {
  const { accessToken, isLoading, isAuthenticated } = useAuth();
  const [avatar, setAvatar] = useState<string>("pawn");
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    if (isLoading || !isAuthenticated || !accessToken) return;

    const fetchProfile = async () => {
      setFetchError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/auth/profile`, {
          headers: withTrackingHeaders({ Authorization: `Bearer ${accessToken}` }),
        });
        if (!res.ok) {
          setFetchError("Could not load profile. Please try again.");
          setProfileLoaded(true);
          return;
        }
        const profile = await res.json();
        setAvatar(profile.avatar || "pawn");
        setUsername(profile.username || "");
        setUpdatedAt(profile.updated_at || null);
      } catch {
        setFetchError("Could not load profile. Check your connection and try again.");
      } finally {
        setProfileLoaded(true);
      }
    };

    fetchProfile();
  }, [isLoading, isAuthenticated, accessToken]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    if (!username.trim()) {
      setError("Username is required");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/profile`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
          ...withTrackingHeaders(),
        } as Record<string, string>,
        body: JSON.stringify({ avatar, username: username.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to save");
      }

      // router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (isLoading || !isAuthenticated) {
    return (
      <main className="analysis-page min-h-screen py-8">
        <div className="max-w-3xl mx-auto px-4">
          <div className="py-16 flex items-center justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
          </div>
        </div>
      </main>
    );
  }

  if (!profileLoaded) {
    return (
      <main className="analysis-page min-h-screen py-8">
        <div className="max-w-3xl mx-auto px-4">
          <div className="py-16 flex items-center justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border border-[color:var(--zen-border)] border-t-[color:var(--zen-accent)]" />
          </div>
        </div>
      </main>
    );
  }

  const formatDate = (iso: string | null) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("en-US", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
      });
    } catch {
      return "—";
    }
  };

  return (
    <main className="analysis-page min-h-screen py-8">
      <div className="max-w-4xl mx-auto px-4 relative z-10">
        <header className="mb-6">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 rounded-md border border-[color:var(--zen-border)]/70 bg-[color:var(--zen-surface)]/60 px-2.5 py-1.5 text-xs font-medium text-[color:var(--zen-muted)] hover:border-[color:var(--zen-accent)]/40 hover:text-[color:var(--zen-text)] transition"
          >
            ← Dashboard
          </Link>
          <h1 className="mt-4 text-2xl sm:text-3xl font-semibold tracking-tight text-[color:var(--zen-text)]">
            Profile
          </h1>
          <p className="mt-2 text-sm text-[color:var(--zen-muted)]">
            Update your username and avatar.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="space-y-6">
          <section className="zen-surface p-4 sm:p-5">
            <h2 className="text-sm font-semibold text-[color:var(--zen-text)] mb-3">Avatar</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {AVATARS.map((av) => {
                const selected = avatar === av.piece;
                return (
                  <button
                    key={av.id}
                    type="button"
                    onClick={() => setAvatar(av.piece)}
                    className={[
                      "zen-surface-flat flex items-center gap-3 rounded-lg border px-3 py-3 text-left transition",
                      selected
                        ? "border-[color:var(--zen-accent)] bg-[color:var(--zen-accent-2)]/35"
                        : "border-[color:var(--zen-border)] hover:border-[color:var(--zen-accent)]/45",
                    ].join(" ")}
                  >
                    <span
                      className={`material-symbols-outlined text-2xl ${
                        selected ? "text-[color:var(--zen-accent)]" : "text-[color:var(--zen-muted)]"
                      }`}
                      style={{
                        fontVariationSettings: "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 40",
                      }}
                    >
                      {av.icon}
                    </span>
                    <span className="text-sm font-medium text-[color:var(--zen-text)]">
                      {av.label}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="zen-surface p-4 sm:p-5 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-[color:var(--zen-text)] mb-2">Username</h2>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="zen-input w-full px-4 py-3 outline-none focus:ring-2 focus:ring-[color:var(--zen-accent-2)] focus:border-[color:var(--zen-accent)] transition"
                maxLength={32}
                autoComplete="username"
                placeholder="Enter username"
              />
              <p className="mt-2 text-xs text-[color:var(--zen-muted)]">
                This name appears across your profile and game pages.
              </p>
            </div>

            {fetchError && (
              <p className="text-sm text-amber-300">{fetchError}</p>
            )}
            {error && (
              <p className="text-sm text-[color:var(--zen-danger)]">{error}</p>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[color:var(--zen-border)] pt-4">
              <div className="text-xs text-[color:var(--zen-muted)]">
                Last updated: {formatDate(updatedAt)}
              </div>
              <button
                type="submit"
                disabled={loading || !username.trim()}
                className="zen-pill px-5 py-2.5 text-sm font-medium bg-[color:var(--zen-accent-2)] hover:bg-[color:var(--zen-accent)] hover:text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? "Saving..." : "Save changes"}
              </button>
            </div>
          </section>
        </form>
      </div>
    </main>
  );
}
