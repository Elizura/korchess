"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signIn, useSession } from "next-auth/react";

const PAWN_IMG =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuBMHIUaQcqKasjLtL8t8UI599RnKcHc-FORpYSbct2KIB--FIrl8l2-WJ69L3ultFqYFGWOHYO-9EJ2c7KzfnoiOWxrDhtP79tvYxDlhVlo6hmoU1NAJukmRnqUyNUX8ymm0Oq_WWX3Kghyh2NnZ-VD5R3Rz20HJ_kDgU6Rj0DPexSHBP7fV3iVIAIBtae1TZLC-i772zdT3ZNIH6QoVDyUzMUgNj0Vy7IPIQKDlJ3XqbxIDZjq-KJ3KRpbEbB74Li5QRNhNCCFYlld";
const KNIGHT_IMG =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuA9tGTx-59N6y89hFnCSQd-VvguPlCqGRKPmkCvj1xXVRKZq_ah0AFxPWWSrnPqdLeOPc_OSO4CZ4f6G0xczmz4QVWGS-wQUh0jhU8lluhhSj3Uxinu_rU25WZT4ff1jSvBh-KxdXxV-XqKe_rfwKkgYcDqpvRc7hqVW4WNnESoxR0AV4WE2JQwtL59moXqO7O_06RhntKSZUKygdtMYVo7drUiXaJmHExJEYKk13n5IhMawJHYGWfeUG42qTwSUr6HnW6hzEayqEfs";

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
            <p className="font-display text-[10px] mb-6 tracking-widest uppercase">
              Select Your Avatar
            </p>
            <div className="flex justify-between items-center gap-2">
              <div className="relative">
                <input
                  defaultChecked
                  className="hidden avatar-radio"
                  id="av1"
                  name="avatar"
                  type="radio"
                />
                <label
                  className="avatar-option flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40"
                  htmlFor="av1"
                >
                  <img
                    alt="Pawn"
                    className="w-10 h-10 invert"
                    src={PAWN_IMG}
                  />
                </label>
              </div>
              <div className="relative">
                <input
                  className="hidden avatar-radio"
                  id="av2"
                  name="avatar"
                  type="radio"
                />
                <label
                  className="avatar-option flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40"
                  htmlFor="av2"
                >
                  <img
                    alt="Knight"
                    className="w-10 h-10 invert"
                    src={KNIGHT_IMG}
                  />
                </label>
              </div>
              <div className="relative">
                <input
                  className="hidden avatar-radio"
                  id="av3"
                  name="avatar"
                  type="radio"
                />
                <label
                  className="avatar-option flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40"
                  htmlFor="av3"
                >
                  <span className="material-symbols-outlined text-4xl">fort</span>
                </label>
              </div>
              <div className="relative">
                <input
                  className="hidden avatar-radio"
                  id="av4"
                  name="avatar"
                  type="radio"
                />
                <label
                  className="avatar-option flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40"
                  htmlFor="av4"
                >
                  <span className="material-symbols-outlined text-4xl">
                    person_2
                  </span>
                </label>
              </div>
              <div className="relative">
                <input
                  className="hidden avatar-radio"
                  id="av5"
                  name="avatar"
                  type="radio"
                />
                <label
                  className="avatar-option flex items-center justify-center p-2 cursor-pointer w-14 h-14 bg-black/40"
                  htmlFor="av5"
                >
                  <span className="material-symbols-outlined text-4xl">
                    crown
                  </span>
                </label>
              </div>
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
