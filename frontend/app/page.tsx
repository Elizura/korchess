"use client";

import Link from "next/link";
import { signIn, signOut, useSession } from "next-auth/react";

export default function LandingPage() {
  const { data: session } = useSession();

  return (
    <div className="bg-background-dark font-mono text-gray-100 selection:bg-accent-green selection:text-black min-h-screen">
      <div className="crt-overlay" />
      <nav className="border-b-2 border-gray-800 bg-background-dark p-4 sticky top-0 z-40">
        <div className="max-w-5xl mx-auto flex justify-between items-center">
          <Link href="/" className="flex items-center gap-3">
            <span className="text-lg font-display text-primary glitch-text">
              KORCHESS
            </span>
          </Link>
          {/* <div className="flex items-center gap-4">
            {session?.user ? (
              <>
                <span className="hidden md:block text-[9px] font-display uppercase opacity-50">
                  USER: {(session.user.name ?? session.user.email ?? "USER")
                    .toUpperCase()
                    .slice(0, 12)}
                </span>
                <button
                  onClick={() => signOut()}
                  className="bg-primary hover:bg-primary/90 text-white px-3 py-1.5 font-display text-[9px] pixel-border-primary transition-all active:translate-y-1"
                >
                  SIGN OUT
                </button>
              </>
            ) : (
              <button
                onClick={() => signIn("google")}
                className="bg-primary hover:bg-primary/90 text-white px-3 py-1.5 font-display text-[9px] pixel-border-primary transition-all active:translate-y-1"
              >
                SIGN IN
              </button>
            )}
          </div> */}
        </div>
      </nav>

      <main className="relative min-h-screen grid-bg overflow-hidden">
        {/* Floating decorations */}
        <div className="absolute top-40 left-[10%] opacity-15 floating pointer-events-none">
          <img
            alt="Pixel Knight"
            className="w-16 h-16 grayscale invert"
            src="https://lh3.googleusercontent.com/aida-public/AB6AXuA9tGTx-59N6y89hFnCSQd-VvguPlCqGRKPmkCvj1xXVRKZq_ah0AFxPWWSrnPqdLeOPc_OSO4CZ4f6G0xczmz4QVWGS-wQUh0jhU8lluhhSj3Uxinu_rU25WZT4ff1jSvBh-KxdXxV-XqKe_rfwKkgYcDqpvRc7hqVW4WNnESoxR0AV4WE2JQwtL59moXqO7O_06RhntKSZUKygdtMYVo7drUiXaJmHExJEYKk13n5IhMawJHYGWfeUG42qTwSUr6HnW6hzEayqEfs"
          />
        </div>
        <div
          className="absolute top-60 right-[10%] opacity-15 floating pointer-events-none"
          style={{ animationDelay: "2s" }}
        >
          <img
            alt="Pixel Pawn"
            className="w-20 h-20 grayscale invert"
            src="https://lh3.googleusercontent.com/aida-public/AB6AXuBMHIUaQcqKasjLtL8t8UI599RnKcHc-FORpYSbct2KIB--FIrl8l2-WJ69L3ultFqYFGWOHYO-9EJ2c7KzfnoiOWxrDhtP79tvYxDlhVlo6hmoU1NAJukmRnqUyNUX8ymm0Oq_WWX3Kghyh2NnZ-VD5R3Rz20HJ_kDgU6Rj0DPexSHBP7fV3iVIAIBtae1TZLC-i772zdT3ZNIH6QoVDyUzMUgNj0Vy7IPIQKDlJ3XqbxIDZjq-KJ3KRpbEbB74Li5QRNhNCCFYlld"
          />
        </div>

        {/* Hero */}
        <section className="max-w-4xl mx-auto px-6 pt-24 pb-16 text-center relative z-10">
          <h1 className="font-display text-4xl md:text-6xl mb-6 tracking-tight glitch-text text-white">
            KORCHESS
          </h1>
          <p className="text-accent-green font-display text-[10px] md:text-xs mb-10 tracking-[0.2em] uppercase max-w-2xl mx-auto leading-relaxed">
            Anal your performance and dominate the board
          </p>
          <div className="flex flex-col items-center">
            <Link
              href="/dashboard"
              className="bg-primary hover:bg-primary/90 text-white px-8 py-4 font-display text-xs pixel-border-primary flex items-center gap-3 group transition-all transform hover:-translate-y-1 active:translate-y-0"
            >
              <span className="material-symbols-outlined text-base">
                sports_esports
              </span>
              START YOUR QUEST
            </Link>
          </div>
        </section>

        {/* Preview section */}
        <section className="max-w-4xl mx-auto px-6 pb-20 relative z-10">
          <div className="border-2 border-accent-green bg-background-dark p-6 md:p-10">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-10">
              <div className="space-y-3">
                <label className="block text-[9px] font-display opacity-60 uppercase">
                  Lichess Username
                </label>
                <div className="flex">
                  <input
                    className="flex-1 bg-gray-900 border-2 border-r-0 border-gray-700 p-2.5 font-mono text-xs focus:outline-none focus:border-accent-green"
                    placeholder="e.g. hikaru"
                    type="text"
                    readOnly
                  />
                  <button className="bg-primary text-white px-4 font-display text-[9px] border-2 border-primary cursor-default">
                    IMPORT
                  </button>
                </div>
              </div>
              <div className="space-y-3">
                <label className="block text-[9px] font-display opacity-60 uppercase">
                  Chess.com Username
                </label>
                <div className="flex">
                  <input
                    className="flex-1 bg-gray-900 border-2 border-r-0 border-gray-700 p-2.5 font-mono text-xs focus:outline-none focus:border-accent-green"
                    placeholder="e.g. hikaru"
                    type="text"
                    readOnly
                  />
                  <button className="bg-primary text-white px-4 font-display text-[9px] border-2 border-primary cursor-default">
                    IMPORT
                  </button>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-4 mb-8 pb-6 border-b-2 border-gray-800">
              <div className="flex gap-2">
                <button className="bg-primary text-white px-4 py-2 font-display text-[9px] pixel-border-primary">
                  AS WHITE
                </button>
                <button className="border-2 border-gray-700 px-4 py-2 font-display text-[9px] text-gray-400 hover:bg-gray-800">
                  AS BLACK
                </button>
              </div>
              <div className="flex gap-2 items-center">
                <select className="bg-gray-900 border-2 border-gray-700 py-2 px-3 font-mono focus:outline-none text-[10px] uppercase">
                  <option>All time controls</option>
                  <option>Blitz</option>
                  <option>Rapid</option>
                  <option>Bullet</option>
                </select>
                <button className="border-2 border-gray-700 px-3 py-2 font-display text-[9px] text-gray-400 flex items-center gap-2 hover:bg-gray-800">
                  <span className="material-symbols-outlined text-xs">
                    refresh
                  </span>
                </button>
              </div>
            </div>
            <div className="space-y-6">
              <h3 className="font-display text-xs mb-4 text-white uppercase tracking-wider">
                Top 10 Openings
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="text-[9px] font-display opacity-40 uppercase border-b border-gray-800">
                      <th className="py-3 font-normal">Opening</th>
                      <th className="py-3 px-4 font-normal text-right">Games</th>
                      <th className="py-3 px-4 font-normal text-right">Wins</th>
                      <th className="py-3 px-4 font-normal text-right">Draws</th>
                      <th className="py-3 px-4 font-normal text-right">Losses</th>
                      <th className="py-3 font-normal text-right">Score %</th>
                    </tr>
                  </thead>
                  <tbody className="font-display text-[9px] leading-relaxed">
                    <tr className="border-b border-gray-800/30 hover:bg-gray-800/20 transition-colors">
                      <td className="py-4 flex items-center gap-3">
                        <span className="bg-accent-green/10 text-accent-green border border-accent-green px-1.5 py-0.5 text-[7px]">
                          QUE
                        </span>
                        <span className="text-white">Queen&apos;s Pawn Game</span>
                      </td>
                      <td className="py-4 px-4 text-right">45</td>
                      <td className="py-4 px-4 text-right text-accent-green">24</td>
                      <td className="py-4 px-4 text-right">2</td>
                      <td className="py-4 px-4 text-right text-red-500">19</td>
                      <td className="py-4 text-right text-accent-green">55.6%</td>
                    </tr>
                    <tr className="border-b border-gray-800/30 hover:bg-gray-800/20 transition-colors">
                      <td className="py-4 flex items-center gap-3">
                        <span className="bg-red-500/10 text-red-500 border border-red-500 px-1.5 py-0.5 text-[7px]">
                          IND
                        </span>
                        <span className="text-white">Indian Defense</span>
                      </td>
                      <td className="py-4 px-4 text-right">10</td>
                      <td className="py-4 px-4 text-right text-accent-green">2</td>
                      <td className="py-4 px-4 text-right">0</td>
                      <td className="py-4 px-4 text-right text-red-500">8</td>
                      <td className="py-4 text-right text-red-500">20.0%</td>
                    </tr>
                    <tr className="border-b border-gray-800/30 hover:bg-gray-800/20 transition-colors">
                      <td className="py-4 flex items-center gap-3">
                        <span className="bg-red-500/10 text-red-500 border border-red-500 px-1.5 py-0.5 text-[7px]">
                          ITA
                        </span>
                        <span className="text-white">Italian Game</span>
                      </td>
                      <td className="py-4 px-4 text-right">6</td>
                      <td className="py-4 px-4 text-right text-accent-green">1</td>
                      <td className="py-4 px-4 text-right">0</td>
                      <td className="py-4 px-4 text-right text-red-500">5</td>
                      <td className="py-4 text-right text-red-500">16.7%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        {/* Feature cards */}
        <section className="max-w-5xl mx-auto px-6 pb-24">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="bg-card-dark border-2 border-gray-800 p-6 group hover:border-accent-green transition-colors">
              <div className="mb-4">
                <span className="material-symbols-outlined text-3xl text-accent-green">
                  psychology
                </span>
              </div>
              <h3 className="font-display text-[10px] mb-3 text-white uppercase">
                Master Lines
              </h3>
              <p className="text-[11px] opacity-70 leading-relaxed font-mono">
                In-depth statistical breakdown of every line you play. Find your
                blind spots and sharpen your prep.
              </p>
            </div>
            <div className="bg-card-dark border-2 border-gray-800 p-6 group hover:border-primary transition-colors">
              <div className="mb-4">
                <span className="material-symbols-outlined text-3xl text-primary">
                  sync
                </span>
              </div>
              <h3 className="font-display text-[10px] mb-3 text-white uppercase">
                Global Sync
              </h3>
              <p className="text-[11px] opacity-70 leading-relaxed font-mono">
                Seamlessly import your entire game history from leading chess
                platforms instantly.
              </p>
            </div>
            <div className="bg-card-dark border-2 border-gray-800 p-6 group hover:border-accent-green transition-colors">
              <div className="mb-4">
                <span className="material-symbols-outlined text-3xl text-accent-green">
                  bar_chart
                </span>
              </div>
              <h3 className="font-display text-[10px] mb-3 text-white uppercase">
                Retro Analytics
              </h3>
              <p className="text-[11px] opacity-70 leading-relaxed font-mono">
                Gamified insights with an 8-bit noir aesthetic. Progress tracking
                that feels like an RPG quest.
              </p>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t-2 border-gray-800 bg-background-dark py-8 px-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="font-display text-[8px] opacity-40">
            © 2024 KORCHESS // ALL SYSTEMS NOMINAL
          </div>
          <div className="flex gap-6">
            <a
              className="font-display text-[8px] hover:text-accent-green transition-colors uppercase"
              href="#"
            >
              Twitter
            </a>
            <a
              className="font-display text-[8px] hover:text-accent-green transition-colors uppercase"
              href="#"
            >
              Discord
            </a>
            <a
              className="font-display text-[8px] hover:text-accent-green transition-colors uppercase"
              href="#"
            >
              GitHub
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
