"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Script from "next/script";
import { useAuth } from "@/lib/auth";
import { trackEvent } from "@/lib/analytics/client";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
          prompt: () => void;
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
  const googleBtnRef = useRef<HTMLDivElement>(null);
  const googleCallbackRef = useRef<(credential: string) => void>();

  googleCallbackRef.current = async (credential: string) => {
    setLoading(true);
    setError(null);
    trackEvent("auth.signup.clicked", {
      properties: { source: "signup_page", provider: "google" },
    });
    const result = await googleSignin(credential);
    setLoading(false);
    if (result.ok) {
      window.location.href = "/dashboard";
    } else {
      setError(result.error || "Google sign in failed.");
    }
  };

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const renderButton = () => {
      if (!window.google || !googleBtnRef.current) return;

      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: (response: { credential?: string }) => {
          if (response.credential) {
            googleCallbackRef.current?.(response.credential);
          }
        },
      });

      window.google.accounts.id.renderButton(googleBtnRef.current, {
        theme: "filled_black",
        size: "large",
        text: "continue_with",
        shape: "rectangular",
        width: googleBtnRef.current.offsetWidth || 320,
      });
    };

    if (window.google) {
      renderButton();
    } else {
      const interval = setInterval(() => {
        if (window.google) {
          clearInterval(interval);
          renderButton();
        }
      }, 100);
      return () => clearInterval(interval);
    }
  }, []);

  if (isAuthenticated) {
    window.location.href = "/dashboard";
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
              <div
                ref={googleBtnRef}
                className="w-full flex justify-center mb-5 [&_iframe]:!rounded-sm"
              />
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
      {GOOGLE_CLIENT_ID && (
        <Script
          src="https://accounts.google.com/gsi/client"
          strategy="lazyOnload"
        />
      )}
    </div>
  );
}
