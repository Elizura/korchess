"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signIn, useSession } from "next-auth/react";

const AVATARS = [
  { id: "av-pawn", icon: "chess_pawn", label: "Pawn", piece: "pawn" },
  { id: "av-knight", icon: "chess_knight", label: "Knight", piece: "knight" },
  { id: "av-bishop", icon: "chess_bishop", label: "Bishop", piece: "bishop" },
  { id: "av-rook", icon: "chess_rook", label: "Rook", piece: "rook" },
  { id: "av-queen", icon: "chess_queen", label: "Queen", piece: "queen" },
  { id: "av-king", icon: "chess_king", label: "King", piece: "king" },
] as const;

export default function SignupPage() {
  const router = useRouter();
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status === "authenticated") {
      router.replace("/dashboard");
    }
  }, [status, router]);

  if (status === "loading" || status === "authenticated") {
    return (
      <div className="bg-charcoal font-mono text-white min-h-screen flex items-center justify-center">
        <div className="font-display text-xs uppercase tracking-widest opacity-60">
          Authenticating...
        </div>
      </div>
    );
  }

  return (
    <div className="bg-charcoal font-mono text-white selection:bg-primary selection:text-white min-h-screen flex items-center justify-center overflow-hidden">
      <div className="crt-overlay" />
      <div className="fixed inset-0 signup-grid-bg opacity-40" />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 blue-glow rounded-full" />
      <div className="relative z-10 w-full max-w-md px-6">
        <div className="bg-background-dark signup-pixel-border p-8 md:p-12 text-center">
          <div className="flex items-center justify-center gap-2 mb-8">
            <span className="w-2 h-2 bg-accent-green rounded-full shadow-[0_0_8px_#4ade80]" />
            <span className="text-[8px] font-display uppercase tracking-widest text-accent-green opacity-80">
              Server: Online
            </span>
          </div>
          <div className="mb-12">
            <h1 className="font-display text-3xl md:text-4xl signup-glitch-text mb-2">
              KORCHESS
            </h1>
            <p className="text-[9px] font-display opacity-40 tracking-tighter uppercase">
              Noir Protocol v1.0.4
            </p>
          </div>
          <div className="mb-10">
            <p className="font-display text-[10px] mb-6 tracking-widest uppercase text-white/60">
              Select Your Avatar
            </p>
            <div className="grid grid-cols-3 gap-3 justify-items-center max-w-[200px] mx-auto">
              {AVATARS.map((avatar, index) => (
                <div key={avatar.id} className="relative">
                  <input
                    defaultChecked={index === 0}
                    className="hidden avatar-radio"
                    id={avatar.id}
                    name="avatar"
                    type="radio"
                  />
                  <label
                    className={`avatar-option avatar-${avatar.piece} flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40`}
                    htmlFor={avatar.id}
                  >
                    <span
                      className="material-symbols-outlined avatar-icon text-4xl"
                      aria-hidden
                    >
                      {avatar.icon}
                    </span>
                  </label>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-6">
            <button
              className="w-full bg-primary text-white font-display text-xs py-5 px-4 arcade-button flex items-center justify-center gap-4 group"
              onClick={() => signIn("google")}
              type="button"
            >
              <svg className="w-5 h-5 fill-current" viewBox="0 0 24 24">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" />
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 12-4.53z" />
              </svg>
              CONTINUE WITH GOOGLE
            </button>
            <p className="text-[8px] font-display opacity-30 leading-loose uppercase">
              By entering you agree to the <br />
              <a
                className="underline hover:text-primary transition-colors"
                href="#"
              >
                terms of engagement
              </a>
            </p>
          </div>
        </div>
        <div className="mt-12 text-center">
          <p className="font-display text-[8px] opacity-20 tracking-[0.2em]">
            © 2024 KORCHESS SYSTEM // ACCESS RESTRICTED
          </p>
        </div>
      </div>
      <div className="fixed top-8 left-8 border-t-2 border-l-2 border-primary w-8 h-8 opacity-40" />
      <div className="fixed top-8 right-8 border-t-2 border-r-2 border-primary w-8 h-8 opacity-40" />
      <div className="fixed bottom-8 left-8 border-b-2 border-l-2 border-primary w-8 h-8 opacity-40" />
      <div className="fixed bottom-8 right-8 border-b-2 border-r-2 border-primary w-8 h-8 opacity-40" />
    </div>
  );
}
