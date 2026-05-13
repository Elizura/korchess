import type { Metadata } from "next";
import {
  Inter,
  JetBrains_Mono,
  Press_Start_2P,
  Silkscreen,
  Space_Mono,
  VT323,
} from "next/font/google";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "Korchess - Chess Opening Analysis",
  description: "Analyze your chess opening performance from Lichess games",
  icons: {
    icon: "/logo.png",
    apple: "/logo.png",
  },
};

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
  weight: ["400", "500", "600"],
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
  weight: ["400", "600", "700"],
});

const spaceMono = Space_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-space-mono",
  weight: ["400", "700"],
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
      className={`dark ${inter.variable} ${jetbrainsMono.variable} ${spaceMono.variable} ${pressStart.variable} ${silkscreen.variable} ${vt323.variable}`}
    >
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200"
          rel="stylesheet"
        />
        <script src="https://accounts.google.com/gsi/client" async defer />
      </head>
      <body className="min-h-screen antialiased bg-[color:var(--zen-bg)] text-[color:var(--zen-text)]">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
