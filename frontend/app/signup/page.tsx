"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { signIn, useSession } from "next-auth/react";
import { trackEvent, withTrackingHeaders } from "@/lib/analytics/client";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function SignupPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const [profileError, setProfileError] = useState<string | null>(null);

  const ensureAndRedirect = useCallback(async () => {
    if (!session?.idToken || !session?.userId) return;

    setProfileError(null);

    try {
      await fetch(`${API_BASE_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: withTrackingHeaders({ Authorization: `Bearer ${session.idToken}` }),
      });

      const profileRes = await fetch(`${API_BASE_URL}/api/v1/auth/profile`, {
        headers: withTrackingHeaders({ Authorization: `Bearer ${session.idToken}` }),
      });

      if (!profileRes.ok) {
        setProfileError("Couldn't load your profile. Please try again.");
        return;
      }

      const profile = await profileRes.json();
      if (profile.onboarding_complete) {
        router.replace("/dashboard");
      } else {
        router.replace("/onboarding");
      }
    } catch {
      setProfileError("Something went wrong. Please try again.");
    }
  }, [session?.idToken, session?.userId, router]);

  useEffect(() => {
    if (status !== "authenticated" || !session?.idToken || !session?.userId) {
      return;
    }
    if (!profileError) {
      ensureAndRedirect();
    }
  }, [status, session?.idToken, session?.userId, ensureAndRedirect]);

  if (status === "authenticated" && !profileError) {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest opacity-60">
          Authenticating...
        </div>
      </div>
    );
  }

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-electric-blue selection:text-white min-h-screen flex items-center justify-center overflow-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-30 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg h-[400px] blue-glow rounded-full opacity-60 pointer-events-none" />
      <div className="relative z-10 w-full max-w-sm px-6">
        <div className="bg-background-dark signup-pixel-border p-10 md:p-12 text-center">
          <div className="flex items-center justify-center gap-3 mb-8">
            <span className="w-2 h-2 bg-accent-green rounded-full shadow-[0_0_10px_#4ade80]" />
            <span className="text-[9px] font-display uppercase tracking-widest text-accent-green opacity-90">
              Server: Online
            </span>
          </div>
          <div className="mb-10">
            <h1 className="font-display text-2xl md:text-3xl signup-glitch-text mb-4 tracking-tighter">
              KORCHESS
            </h1>
            <p className="text-[8px] font-display opacity-40 tracking-[0.25em] uppercase">
              Noir Protocol v1.0.4
            </p>
          </div>
          {profileError && (
            <p className="mb-6 text-sm text-red-400 font-display uppercase tracking-wider">
              {profileError}
            </p>
          )}
          <div className="space-y-10">
            <button
              type="button"
              className="w-full bg-electric-blue text-white font-display text-[10px] py-5 px-4 arcade-button flex items-center justify-center gap-4 group hover:bg-[#5a86ff] transition-colors uppercase"
              onClick={() => {
                trackEvent("auth.signin.clicked", {
                  properties: {
                    source: "signup_page",
                  },
                });
                signIn("google", { callbackUrl: "/signup" });
              }}
            >
              <svg className="w-4 h-4 fill-current" viewBox="0 0 24 24">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" />
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 12-4.53z" />
              </svg>
              Continue with Google
            </button>
            <p className="text-[7px] font-display opacity-30 leading-relaxed uppercase tracking-[0.15em]">
              By entering you agree to the <br />
              <a
                className="underline hover:text-electric-blue transition-colors decoration-dotted"
                href="#"
              >
                terms of engagement
              </a>
            </p>
          </div>
        </div>
        <div className="mt-12 text-center">
          <p className="font-display text-[7px] opacity-20 tracking-[0.5em]">
            © 2024 KORCHESS SYSTEM // ACCESS RESTRICTED
          </p>
        </div>
      </div>
      <div className="fixed top-8 left-8 border-t-2 border-l-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed top-8 right-8 border-t-2 border-r-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed bottom-8 left-8 border-b-2 border-l-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed bottom-8 right-8 border-b-2 border-r-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
    </div>
  );
}
