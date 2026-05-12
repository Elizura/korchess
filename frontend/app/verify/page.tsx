"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function VerifyPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const emailParam = searchParams.get("email") || "";
  const { verify, isAuthenticated } = useAuth();

  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, router]);

  useEffect(() => {
    inputRefs.current[0]?.focus();
  }, []);

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
    setError(null);
    setLoading(true);

    const result = await verify(emailParam, fullCode);
    setLoading(false);

    if (result.ok) {
      router.push("/dashboard");
    } else {
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

          <p className="mt-6 text-center text-[8px] font-display opacity-30">
            Code expires in 10 minutes
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
