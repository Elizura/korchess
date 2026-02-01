import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Openingscope - Chess Opening Analysis",
  description: "Analyze your chess opening performance from Lichess games",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="bg-gray-50 min-h-screen">{children}</body>
    </html>
  );
}
