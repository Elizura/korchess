"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

const AVATARS = [
  { id: "av-pawn", icon: "chess_pawn", piece: "pawn" },
  { id: "av-knight", icon: "chess_knight", piece: "knight" },
  { id: "av-bishop", icon: "chess_bishop", piece: "bishop" },
  { id: "av-rook", icon: "chess_rook", piece: "rook" },
  { id: "av-queen", icon: "chess_queen", piece: "queen" },
  { id: "av-king", icon: "chess_king", piece: "king" },
] as const;

export default function OnboardingPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const [avatar, setAvatar] = useState<string>("pawn");
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (status !== "authenticated") {
      return;
    }
    if (!session?.idToken) {
      router.replace("/signup");
      return;
    }

    const checkProfile = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/auth/profile`, {
          headers: { Authorization: `Bearer ${session.idToken}` },
        });
        if (!res.ok) {
          router.replace("/signup");
          return;
        }
        const profile = await res.json();
        if (profile.onboarding_complete) {
          router.replace("/dashboard");
        } else {
          setChecking(false);
        }
      } catch {
        router.replace("/signup");
      }
    };

    checkProfile();
  }, [status, session?.idToken, router]);

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
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/onboarding`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.idToken}`,
        },
        body: JSON.stringify({ avatar, username: username.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to complete setup");
      }

      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (status === "loading" || status === "unauthenticated" || checking) {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest opacity-60">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-primary selection:text-white min-h-screen relative overflow-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-40 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 blue-glow rounded-full pointer-events-none" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md px-6 z-10">
        <form
          onSubmit={handleSubmit}
          className="bg-background-dark signup-pixel-border p-8 md:p-12 mt-20"
        >
          <div className="flex items-center justify-center gap-2 mb-8">
            <span className="w-2 h-2 bg-accent-blue rounded-full shadow-[0_0_8px_#00f2ff]" />
            <span className="text-[8px] font-display uppercase tracking-widest text-accent-blue opacity-80">
              Identity Module: Active
            </span>
          </div>
          <div className="mb-10 text-center">
            <h1 className="font-display text-xl md:text-2xl signup-glitch-text mb-2 text-white leading-tight">
              IDENTITY INITIALIZATION
            </h1>
            <p className="text-[9px] font-display opacity-40 tracking-tighter uppercase">
              Subject Registration Protocol
            </p>
          </div>
          <div className="mb-10">
            <p className="font-display text-[9px] mb-6 tracking-widest uppercase text-center text-white/60">
              Select Your Avatar
            </p>
            <div className="grid grid-cols-3 gap-4 justify-items-center max-w-[240px] mx-auto">
              {AVATARS.map((av) => (
                <div key={av.id} className="relative">
                  <input
                    type="radio"
                    id={av.id}
                    name="avatar"
                    value={av.piece}
                    checked={avatar === av.piece}
                    onChange={() => setAvatar(av.piece)}
                    className="hidden avatar-radio-onboarding"
                  />
                  <label
                    htmlFor={av.id}
                    className="avatar-option-onboarding cursor-pointer"
                  >
                    <span
                      className="material-symbols-outlined text-white text-[32px]"
                      style={{ fontVariationSettings: "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 48" }}
                    >
                      {av.icon}
                    </span>
                  </label>
                </div>
              ))}
            </div>
          </div>
          <div className="mb-10">
            <label
              htmlFor="username"
              className="font-display text-[9px] mb-4 tracking-widest uppercase block text-white/60"
            >
              Username
            </label>
            <div className="relative flex items-center">
              <span className="text-primary mr-2 font-mono text-lg">&gt;</span>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="TYPE_NAME..."
                className="terminal-input font-mono text-lg tracking-wider flex-1 pr-4"
                maxLength={32}
                autoComplete="username"
              />
              <span className="cursor-block absolute right-0 pointer-events-none" />
            </div>
          </div>
          {error && (
            <p className="mb-4 text-sm text-red-400 font-display text-[8px] uppercase">
              {error}
            </p>
          )}
          <div className="space-y-6">
            <button
              type="submit"
              disabled={loading || !username.trim()}
              className="w-full bg-primary text-white font-display text-[10px] py-5 px-4 arcade-button flex items-center justify-center gap-4 uppercase tracking-widest disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Initializing..." : "Complete Initialization"}
            </button>
            <p className="text-[8px] font-display opacity-30 leading-loose uppercase text-center">
              System authority check in progress...
              <br />
              <span className="text-accent-blue">Encrypted session established</span>
            </p>
          </div>
        </form>
        <div className="mt-12 text-center">
          <p className="font-display text-[8px] opacity-20 tracking-[0.2em]">
            © 2024 KORCHESS SYSTEM // NEURAL_LINK_V1
          </p>
        </div>
      </div>
      <div className="fixed top-8 left-8 border-t-2 border-l-2 border-primary w-8 h-8 opacity-40 pointer-events-none" />
      <div className="fixed top-8 right-8 border-t-2 border-r-2 border-primary w-8 h-8 opacity-40 pointer-events-none" />
      <div className="fixed bottom-8 left-8 border-b-2 border-l-2 border-primary w-8 h-8 opacity-40 pointer-events-none" />
      <div className="fixed bottom-8 right-8 border-b-2 border-r-2 border-primary w-8 h-8 opacity-40 pointer-events-none" />
    </div>
  );
}
