"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { trackEvent } from "@/lib/analytics/client";

export default function SigninPage() {
  const router = useRouter();
  const { signin, isAuthenticated } = useAuth();
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

    trackEvent("auth.signin.clicked", {
      properties: { source: "signin_page" },
    });

    const result = await signin(email, password);
    setLoading(false);

    if (result.ok) {
      router.push("/dashboard");
    } else {
      if (result.error?.includes("verify your email")) {
        router.push(`/verify?email=${encodeURIComponent(email)}`);
        return;
      }
      setError(result.error || "Sign in failed.");
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
            <span className="w-2 h-2 bg-accent-green rounded-full shadow-[0_0_10px_#4ade80]" />
            <span className="text-[9px] font-display uppercase tracking-widest text-accent-green opacity-90">
              Server: Online
            </span>
          </div>
          <div className="mb-8 text-center">
            <h1 className="font-display text-2xl md:text-3xl signup-glitch-text mb-4 tracking-tighter">
              KORCHESS
            </h1>
            <p className="text-[8px] font-display opacity-40 tracking-[0.25em] uppercase">
              Welcome back
            </p>
          </div>

          {error && (
            <p className="mb-4 text-sm text-red-400 font-display uppercase tracking-wider text-center">
              {error}
            </p>
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
                className="w-full bg-black/50 border border-gray-700 text-white px-3 py-2.5 font-mono text-sm focus:border-electric-blue focus:outline-none transition-colors"
                placeholder="Your password"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase disabled:opacity-50"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>

          <p className="mt-6 text-center text-[9px] font-display opacity-50">
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="text-electric-blue hover:underline">
              Sign up
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
