"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

const INPUT_CLASS =
  "w-full bg-black/50 border border-gray-700 text-white px-4 py-3 text-sm focus:border-electric-blue focus:outline-none transition-colors placeholder:text-gray-500 placeholder:text-xs placeholder:tracking-wide";
const LABEL_CLASS = "block text-[9px] font-display uppercase tracking-widest opacity-60 mb-2";
const BUTTON_CLASS =
  "w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase disabled:opacity-50";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { requestPasswordReset, resetPassword } = useAuth();

  const [step, setStep] = useState<"email" | "reset">("email");
  const [email, setEmail] = useState(searchParams.get("email") || "");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const sendCode = async () => {
    setError(null);
    setNotice(null);
    setLoading(true);
    const result = await requestPasswordReset(email);
    setLoading(false);

    if (result.ok) {
      setNotice(`If an account exists for ${email}, we sent it a 6-digit code.`);
      setStep("reset");
    } else {
      setError(result.error || "Couldn't send a reset code.");
    }
  };

  const handleRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    await sendCode();
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setLoading(true);
    const result = await resetPassword(email, code, password);
    setLoading(false);

    if (result.ok) {
      router.push("/dashboard");
    } else {
      setError(result.error || "Password reset failed.");
    }
  };

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-electric-blue selection:text-white min-h-screen flex items-center justify-center overflow-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-30 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-electric-blue/20 rounded-full blur-[120px] pointer-events-none" />
      <div className="relative z-10 w-full max-w-lg px-6">
        <div className="bg-background-dark signup-pixel-border p-10 md:p-14">
          <div className="mb-8 text-center">
            <h1 className="font-display text-2xl md:text-3xl signup-glitch-text mb-4 tracking-tighter">
              KORCHESS
            </h1>
            <p className="text-[8px] font-display opacity-40 tracking-[0.25em] uppercase">
              Reset your password
            </p>
          </div>

          {error && (
            <p className="mb-4 text-sm text-red-400 font-display uppercase tracking-wider text-center">
              {error}
            </p>
          )}
          {notice && !error && (
            <p className="mb-4 text-[10px] text-accent-green font-display leading-relaxed text-center">
              {notice}
            </p>
          )}

          {step === "email" ? (
            <form onSubmit={handleRequest} className="space-y-5">
              <div>
                <label htmlFor="reset-email" className={LABEL_CLASS}>
                  Email
                </label>
                <input
                  id="reset-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className={INPUT_CLASS}
                  placeholder="you@example.com"
                />
              </div>
              <button type="submit" disabled={loading} className={BUTTON_CLASS}>
                {loading ? "Sending..." : "Send reset code"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleReset} className="space-y-5">
              <div>
                <label htmlFor="reset-code" className={LABEL_CLASS}>
                  6-digit code
                </label>
                <input
                  id="reset-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  required
                  className={`${INPUT_CLASS} tracking-[0.5em] text-center`}
                  placeholder="000000"
                />
              </div>
              <div>
                <label htmlFor="reset-password" className={LABEL_CLASS}>
                  New password
                </label>
                <input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className={INPUT_CLASS}
                  placeholder="At least 8 characters"
                />
              </div>
              <button
                type="submit"
                disabled={loading || code.length !== 6}
                className={BUTTON_CLASS}
              >
                {loading ? "Saving..." : "Set new password"}
              </button>
              <div className="text-center">
                <button
                  type="button"
                  onClick={sendCode}
                  disabled={loading}
                  className="text-[9px] font-display uppercase tracking-widest text-electric-blue hover:underline disabled:opacity-40"
                >
                  Didn&apos;t get it? Send a new code
                </button>
              </div>
            </form>
          )}

          <p className="mt-6 text-center text-[9px] font-display opacity-50">
            Remembered it?{" "}
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
