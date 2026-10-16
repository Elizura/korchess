"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { FIRST_RUN_PATH, needsOnboarding, useAuth } from "@/lib/auth";

export default function VerifyPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const emailParam = searchParams.get("email") || "";
  const autoResend = searchParams.get("resend") === "1";
  const { verify, resendCode, hasPendingSignup, isAuthenticated } = useAuth();
  // After a reload the sign-up password is gone from memory; ask for it again
  const [needsPassword] = useState(() => !hasPendingSignup(emailParam));
  const [password, setPassword] = useState("");

  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendWait, setResendWait] = useState(0);
  const autoResendDone = useRef(false);
  // Set while the code is being checked so the "already signed in" redirect
  // doesn't override where a successful verification sends the user
  const verifyingRef = useRef(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    if (isAuthenticated && !verifyingRef.current) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, router]);

  useEffect(() => {
    if (!needsPassword) inputRefs.current[0]?.focus();
  }, [needsPassword]);

  useEffect(() => {
    if (resendWait <= 0) return;
    const timer = setTimeout(() => setResendWait((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendWait]);

  const handleResend = async () => {
    if (!emailParam || resending || resendWait > 0) return;
    setResending(true);
    setError(null);
    setNotice(null);

    const result = await resendCode(emailParam);
    setResending(false);

    if (result.ok) {
      setNotice("A new code is on its way.");
      setResendWait(60);
      setCode(["", "", "", "", "", ""]);
      inputRefs.current[0]?.focus();
    } else {
      setError(result.error || "Couldn't send a new code.");
    }
  };

  // Arriving from sign-in with an unverified account: the old code has likely expired
  useEffect(() => {
    if (!autoResend || !emailParam || autoResendDone.current) return;
    autoResendDone.current = true;
    void handleResend();
  }, [autoResend, emailParam]);

  const handleChange = (index: number, value: string) => {
    if (!/^\d*$/.test(value)) return;
    const newCode = [...code];
    newCode[index] = value.slice(-1);
    setCode(newCode);

    if (value && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }

    const fullCode = newCode.join("");
    if (fullCode.length === 6) {
      handleVerify(fullCode);
    }
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent) => {
    if (e.key === "Backspace" && !code[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    if (!pasted) return;
    const newCode = [...code];
    for (let i = 0; i < 6; i++) {
      newCode[i] = pasted[i] || "";
    }
    setCode(newCode);
    if (pasted.length === 6) {
      handleVerify(pasted);
    } else {
      inputRefs.current[Math.min(pasted.length, 5)]?.focus();
    }
  };

  const handleVerify = async (fullCode: string) => {
    if (!emailParam) {
      setError("Missing email parameter.");
      return;
    }
    if (needsPassword && !password) {
      setError("Enter your password to finish verifying.");
      return;
    }
    setError(null);
    setNotice(null);
    setLoading(true);
    verifyingRef.current = true;

    const result = await verify(emailParam, fullCode, needsPassword ? password : undefined);
    setLoading(false);

    if (result.ok) {
      // Verifying the email completes sign-up: pick a username and avatar first
      router.push(needsOnboarding(result) ? FIRST_RUN_PATH : "/dashboard");
    } else {
      verifyingRef.current = false;
      setError(result.error || "Verification failed.");
      setCode(["", "", "", "", "", ""]);
      inputRefs.current[0]?.focus();
    }
  };

  if (!emailParam) {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <p className="text-red-400">No email provided. Please sign up first.</p>
      </div>
    );
  }

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
              Verification Required
            </span>
          </div>
          <div className="mb-8 text-center">
            <h1 className="font-display text-xl md:text-2xl mb-4 tracking-tighter">
              CHECK YOUR EMAIL
            </h1>
            <p className="text-[10px] font-display opacity-50 leading-relaxed">
              We sent a 6-digit code to
              <br />
              <span className="text-electric-blue">{emailParam}</span>
            </p>
          </div>

          {error && (
            <p className="mb-4 text-sm text-red-400 font-display uppercase tracking-wider text-center">
              {error}
            </p>
          )}
          {notice && !error && (
            <p className="mb-4 text-sm text-accent-green font-display uppercase tracking-wider text-center">
              {notice}
            </p>
          )}

          {needsPassword && (
            <div className="mb-6">
              <label
                htmlFor="verify-password"
                className="block text-[9px] font-display uppercase tracking-widest opacity-60 mb-2"
              >
                Your password
              </label>
              <input
                id="verify-password"
                autoFocus
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                className="w-full bg-black/50 border border-gray-700 text-white px-4 py-3 text-sm focus:border-electric-blue focus:outline-none transition-colors disabled:opacity-50"
                placeholder="The password you signed up with"
              />
            </div>
          )}

          <div className="flex justify-center gap-2 mb-8" onPaste={handlePaste}>
            {code.map((digit, i) => (
              <input
                key={i}
                ref={(el) => { inputRefs.current[i] = el; }}
                type="text"
                inputMode="numeric"
                maxLength={1}
                value={digit}
                onChange={(e) => handleChange(i, e.target.value)}
                onKeyDown={(e) => handleKeyDown(i, e)}
                disabled={loading}
                className="w-11 h-14 bg-black/50 border border-gray-700 text-white text-center font-mono text-xl focus:border-electric-blue focus:outline-none transition-colors disabled:opacity-50"
              />
            ))}
          </div>

          {loading && (
            <p className="text-center text-[9px] font-display uppercase tracking-widest opacity-60">
              Verifying...
            </p>
          )}

          {needsPassword && !loading && (
            <button
              type="button"
              onClick={() => handleVerify(code.join(""))}
              disabled={code.join("").length !== 6}
              className="w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase disabled:opacity-50"
            >
              Verify
            </button>
          )}

          <p className="mt-6 text-center text-[8px] font-display opacity-30">
            Code expires in 10 minutes
          </p>

          <div className="mt-4 text-center">
            <button
              type="button"
              onClick={handleResend}
              disabled={resending || resendWait > 0}
              className="text-[9px] font-display uppercase tracking-widest text-electric-blue hover:underline disabled:opacity-40 disabled:no-underline disabled:cursor-default"
            >
              {resending
                ? "Sending..."
                : resendWait > 0
                  ? `Resend code in ${resendWait}s`
                  : "Didn't get it? Resend code"}
            </button>
          </div>
        </div>
      </div>
      <div className="fixed top-8 left-8 border-t-2 border-l-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed top-8 right-8 border-t-2 border-r-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed bottom-8 left-8 border-b-2 border-l-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
      <div className="fixed bottom-8 right-8 border-b-2 border-r-2 border-primary w-8 h-8 opacity-20 pointer-events-none" />
    </div>
  );
}
