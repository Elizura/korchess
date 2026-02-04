import type { Metadata } from "next";
import {
  Inter,
  JetBrains_Mono,
  Press_Start_2P,
  Silkscreen,
  VT323,
} from "next/font/google";
import "./globals.css";

export const metadata: Metadata = {
  title: "Openingscope - Chess Opening Analysis",
  description: "Analyze your chess opening performance from Lichess games",
};

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
});

const pressStart = Press_Start_2P({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-pixel",
  weight: "400",
});

const silkscreen = Silkscreen({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-pixel-bold",
  weight: ["400", "700"],
});

const vt323 = VT323({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-pixel-body",
  weight: "400",
});

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} ${pressStart.variable} ${silkscreen.variable} ${vt323.variable}`}
    >
      <body className="min-h-screen antialiased bg-[color:var(--zen-bg)] text-[color:var(--zen-text)]">
        {children}
      </body>
    </html>
  );
}
