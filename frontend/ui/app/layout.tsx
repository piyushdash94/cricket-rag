import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// Scoreboard numerals and headlines on the front page.
const display = Barlow_Condensed({ variable: "--font-display", weight: ["600", "700", "800"], subsets: ["latin"] });

const title = "cricket-rag — IPL Match Centre & Pandit";
const description = "IPL match centre over 19 seasons of ball-by-ball data, with Pandit: answers whose every number comes from SQL.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  // A local address is plain http (127.0.0.1 too, or the icon and preview image resolve to https and fail).
  const local = /^(localhost|127\.0\.0\.1|\[::1\])(:|$)/.test(host);
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (local ? "http" : "https");
  const origin = `${protocol}://${host}`;

  return {
    metadataBase: new URL(origin),
    title,
    description,
    icons: { icon: "/favicon.svg" },
    openGraph: { title, description, type: "website" },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${geistSans.variable} ${geistMono.variable} ${display.variable}`}>{children}</body></html>;
}
