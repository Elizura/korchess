import type { Metadata } from "next";
import Link from "next/link";
import { FaChessKing } from "react-icons/fa";

export const metadata: Metadata = {
  title: "Page not found - Korchess",
};

export default function NotFound() {
  return (
    <div className="bg-charcoal font-mono text-white selection:bg-electric-blue selection:text-white min-h-screen flex items-center justify-center py-12 overflow-x-hidden">
      <div className="crt-overlay pointer-events-none" />
      <div className="fixed inset-0 signup-grid-bg opacity-30 pointer-events-none" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg h-[400px] blue-glow rounded-full opacity-60 pointer-events-none" />
      <main className="relative z-10 w-full max-w-sm px-6">
        <div className="bg-background-dark signup-pixel-border p-10 md:p-12 text-center">
          <FaChessKing aria-hidden className="mx-auto mb-6 text-4xl text-electric-blue rotate-90 opacity-80" />
          <p className="mb-4 text-[9px] font-display uppercase tracking-widest text-red-400">Error 404</p>
          <h1 className="font-display text-xl md:text-2xl signup-glitch-text mb-4 tracking-tighter">
            PAGE NOT FOUND
          </h1>
          <p className="mb-8 text-[10px] font-display opacity-50 leading-relaxed">
            That square is empty. The page may have moved, or the link is wrong.
          </p>
          <Link
            href="/dashboard"
            className="block w-full bg-electric-blue text-white font-display text-[10px] py-4 px-4 arcade-button hover:bg-[#5a86ff] transition-colors uppercase"
          >
            Go to dashboard
          </Link>
          <Link
            href="/"
            className="mt-6 inline-block text-[9px] font-display uppercase tracking-widest text-electric-blue hover:underline"
          >
            Back to home
          </Link>
        </div>
      </main>
    </div>
  );
}
