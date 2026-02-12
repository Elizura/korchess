"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useSession } from "next-auth/react";

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
  const router = useRouter();
  const { data: session, status } = useSession();
  const [avatar, setAvatar] = useState<string>("pawn");
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "authenticated" || !session?.idToken) {
      return;
    }

    const fetchProfile = async () => {
      setFetchError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/auth/profile`, {
          headers: { Authorization: `Bearer ${session.idToken}` },
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
  }, [status, session?.idToken]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!session?.idToken) return;
    if (!username.trim()) {
      setError("Username is required");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/profile`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.idToken}`,
        },
        body: JSON.stringify({ avatar, username: username.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to save");
      }

      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (status === "loading" || status === "unauthenticated") {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest opacity-60">
          Loading...
        </div>
      </div>
    );
  }

  if (!profileLoaded) {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest opacity-60">
          Loading profile...
        </div>
      </div>
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
    <div className="profile-edit-page min-h-screen text-white font-mono">
      <div className="max-w-4xl mx-auto px-6 py-12">
        <header className="mb-12">
          <div className="flex items-center gap-4 mb-4">
            <Link
              href="/dashboard"
              className="group flex items-center text-primary hover:text-accent-blue transition-colors"
            >
              <span className="material-symbols-outlined text-2xl mr-2 group-hover:-translate-x-1 transition-transform">
                west
              </span>
              <span className="font-display text-[10px] tracking-tight">
                BACK TO DASHBOARD
              </span>
            </Link>
          </div>
          <h1 className="font-display text-2xl md:text-3xl lg:text-4xl text-white tracking-tighter mb-2">
            USER PROFILE / CHARACTER SHEET
          </h1>
          <div className="h-1 w-32 bg-primary profile-glow" />
        </header>

        <form onSubmit={handleSubmit}>
          <main className="grid grid-cols-1 lg:grid-cols-12 gap-12">
            <section className="lg:col-span-7">
              <div className="flex items-center gap-3 mb-8">
                <span className="material-symbols-outlined text-primary">
                  face
                </span>
                <h2 className="font-display text-sm text-slate-400">
                  CHANGE AVATAR
                </h2>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-6">
                {AVATARS.map((av) => (
                  <button
                    key={av.id}
                    type="button"
                    onClick={() => setAvatar(av.piece)}
                    className={`profile-avatar-card group relative flex flex-col items-center justify-center aspect-square bg-slate-900 border-4 transition-all ${
                      avatar === av.piece
                        ? "border-primary profile-glow"
                        : "border-slate-800 hover:bg-primary/10"
                    }`}
                  >
                    {avatar === av.piece && (
                      <div className="absolute -top-3 -right-3 bg-primary text-white p-1 flex">
                        <span className="material-symbols-outlined text-sm">
                          check
                        </span>
                      </div>
                    )}
                    <span
                      className={`material-symbols-outlined text-4xl ${
                        avatar === av.piece
                          ? "text-primary"
                          : "text-white opacity-60 group-hover:opacity-100"
                      }`}
                      style={{
                        fontVariationSettings: "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 48",
                      }}
                    >
                      {av.icon}
                    </span>
                    <span
                      className={`mt-2 text-[10px] font-display uppercase ${
                        avatar === av.piece
                          ? "text-primary"
                          : "text-slate-500 group-hover:text-primary"
                      }`}
                    >
                      {av.label}
                    </span>
                  </button>
                ))}
              </div>
            </section>

            <section className="lg:col-span-5 space-y-12">
              <div>
                <div className="flex items-center gap-3 mb-8">
                  <span className="material-symbols-outlined text-primary">
                    terminal
                  </span>
                  <h2 className="font-display text-sm text-slate-400">
                    CHANGE USERNAME
                  </h2>
                </div>
                <div className="relative">
                  <div className="bg-black/40 border-2 border-slate-800 p-6 profile-glow focus-within:border-primary transition-all">
                    <label className="block text-[10px] font-display text-slate-500 mb-2">
                      SYSTEM_USER_ID
                    </label>
                    <div className="flex items-center">
                      <span className="text-primary mr-2 font-bold font-mono">
                        $
                      </span>
                      <input
                        type="text"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        className="bg-transparent border-none p-0 text-xl font-mono text-white focus:ring-0 w-full flex-1 pr-4"
                        maxLength={32}
                        autoComplete="username"
                      />
                      <span className="profile-cursor" />
                    </div>
                  </div>
                  <p className="mt-3 text-[10px] font-display text-slate-600">
                    WARNING: USERNAME CHANGES ARE RESTRICTED TO ONCE PER SEASON.
                  </p>
                </div>
              </div>

              {fetchError && (
                <p className="text-amber-400 font-display text-[10px] uppercase">
                  {fetchError}
                </p>
              )}
              {error && (
                <p className="text-red-400 font-display text-[10px] uppercase">
                  {error}
                </p>
              )}

              <div className="pt-8 border-t border-slate-800">
                <button
                  type="submit"
                  disabled={loading || !username.trim()}
                  className="w-full bg-primary hover:bg-blue-600 text-white font-display py-6 px-8 text-lg transition-all active:translate-y-1 mb-6 profile-glow relative group overflow-hidden disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="relative z-10">
                    {loading ? "SAVING..." : "SAVE CHANGES"}
                  </span>
                  <div className="absolute inset-0 bg-white/10 translate-x-full group-hover:translate-x-0 transition-transform duration-300" />
                </button>
                <div className="flex flex-col gap-4">
                  <div className="flex justify-between items-center text-[10px] font-display text-slate-500">
                    <span>LAST MODIFIED</span>
                    <span className="text-slate-300">
                      {formatDate(updatedAt)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[10px] font-display text-slate-500">
                    <span>ACCOUNT STATUS</span>
                    <span className="text-accent-green">[ ACTIVE ]</span>
                  </div>
                </div>
              </div>
            </section>
          </main>
        </form>

        <footer className="mt-24 border-t border-slate-900 pt-8 flex justify-between items-end opacity-20">
          <div className="space-y-1">
            <div className="h-1 w-24 bg-slate-700" />
            <div className="h-1 w-16 bg-slate-700" />
            <div className="h-1 w-32 bg-slate-700" />
          </div>
          <div className="font-display text-[8px] text-slate-700">
            KORCHESS_ENGINE_V2.0.4 // NOIR_EDITION
          </div>
        </footer>
      </div>
    </div>
  );
}
