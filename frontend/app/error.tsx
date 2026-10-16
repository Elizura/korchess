"use client";

import { useEffect } from "react";
import Link from "next/link";
import { FaChessKnight } from "react-icons/fa";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-electric-blue selection:text-white min-h-screen flex items-center justify-center py-12 overflow-x-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-30 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg h-[400px] blue-glow rounded-full opacity-60 pointer-events-none" />
      <main className="relative z-10 w-full max-w-sm px-6">
        <div className="bg-background-dark signup-pixel-border p-10 md:p-12 text-center">
          <FaChessKnight aria-hidden className="mx-auto mb-6 text-4xl text-electric-blue opacity-80" />
          <p className="mb-4 text-[9px] font-display uppercase tracking-widest text-red-400">System error</p>
          <h1 className="font-display text-xl md:text-2xl signup-glitch-text mb-4 tracking-tighter">
            SOMETHING WENT WRONG
          </h1>
          <p className="mb-8 text-[10px] font-display opacity-50 leading-relaxed">
            This page hit an unexpected error. Try again, or head back to your dashboard.
          </p>
          <button
            type="button"
            onClick={reset}
            className="w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase"
          >
            Try again
          </button>
          <div className="mt-6 flex justify-center gap-6">
            <Link
              href="/dashboard"
              className="text-[9px] font-display uppercase tracking-widest text-electric-blue hover:underline"
            >
              Dashboard
            </Link>
            <Link
              href="/"
              className="text-[9px] font-display uppercase tracking-widest text-electric-blue hover:underline"
            >
              Home
            </Link>
          </div>
          {error.digest && (
            <p className="mt-6 text-[8px] font-display opacity-30 break-all">Ref: {error.digest}</p>
          )}
        </div>
      </main>
    </div>
  );
}
