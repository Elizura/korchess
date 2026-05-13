"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { trackEvent } from "@/lib/analytics/client";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          prompt: (cb?: (notification: { isNotDisplayed: () => boolean }) => void) => void;
        };
      };
    };
  }
}

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

export default function SignupPage() {
  const router = useRouter();
  const { signup, googleSignin, isAuthenticated } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (isAuthenticated) {
    router.replace("/dashboard");
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    trackEvent("auth.signup.clicked", {
      properties: { source: "signup_page" },
    });

    const result = await signup(email, password);
    setLoading(false);

    if (result.ok) {
      router.push(`/verify?email=${encodeURIComponent(email)}`);
    } else {
      setError(result.error || "Signup failed.");
    }
  };

  const handleGoogle = () => {
    if (!window.google || !GOOGLE_CLIENT_ID) {
      setError("Google sign-in is not available.");
      return;
    }
    setError(null);
    setLoading(true);

    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: async (response: { credential?: string }) => {
        if (!response.credential) {
          setError("Google sign-in was cancelled.");
          setLoading(false);
          return;
        }
        trackEvent("auth.signup.clicked", {
          properties: { source: "signup_page", provider: "google" },
        });
        const result = await googleSignin(response.credential);
        setLoading(false);
        if (result.ok) {
          router.push("/dashboard");
        } else {
          setError(result.error || "Google sign in failed.");
        }
      },
    });

    window.google.accounts.id.prompt((notification) => {
      if (notification.isNotDisplayed()) {
        setError("Google popup was blocked. Please allow popups and try again.");
        setLoading(false);
      }
    });
  };

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-electric-blue selection:text-white min-h-screen flex items-center justify-center overflow-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-30 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg h-[400px] blue-glow rounded-full opacity-60 pointer-events-none" />
      <div className="relative z-10 w-full max-w-sm px-6">
        <div className="bg-background-dark signup-pixel-border p-10 md:p-12">
          <div className="flex items-center justify-center gap-3 mb-8">
          </div>
          <div className="mb-8 text-center">
            <h1 className="font-display text-2xl md:text-3xl signup-glitch-text mb-4 tracking-tighter">
              KORCHESS
            </h1>
            <p className="text-[8px] font-display opacity-40 tracking-[0.25em] uppercase">
              Create your account
            </p>
          </div>

          {error && (
            <p className="mb-4 text-sm text-red-400 font-display uppercase tracking-wider text-center">
              {error}
            </p>
          )}

          {GOOGLE_CLIENT_ID && (
            <>
              <button
                type="button"
                onClick={handleGoogle}
                disabled={loading}
                className="w-full bg-white text-gray-800 font-display text-[10px] py-3.5 px-4 flex items-center justify-center gap-3 hover:bg-gray-100 transition-colors uppercase disabled:opacity-50 rounded-sm mb-5"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" />
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                </svg>
                Continue with Google
              </button>
              <div className="flex items-center gap-3 mb-5">
                <div className="flex-1 h-px bg-gray-700" />
                <span className="text-[8px] font-display uppercase tracking-widest opacity-30">or</span>
                <div className="flex-1 h-px bg-gray-700" />
              </div>
            </>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-[9px] font-display uppercase tracking-widest opacity-60 mb-2">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full bg-black/50 border border-gray-700 text-white px-3 py-2.5 font-mono text-sm focus:border-electric-blue focus:outline-none transition-colors"
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className="block text-[9px] font-display uppercase tracking-widest opacity-60 mb-2">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                className="w-full bg-black/50 border border-gray-700 text-white px-3 py-2.5 font-mono text-sm focus:border-electric-blue focus:outline-none transition-colors"
                placeholder="Min 8 characters"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase disabled:opacity-50"
            >
              {loading ? "Creating account..." : "Sign Up"}
            </button>
          </form>

          <p className="mt-6 text-center text-[9px] font-display opacity-50">
            Already have an account?{" "}
            <Link href="/signin" className="text-electric-blue hover:underline">
              Sign in
            </Link>
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
