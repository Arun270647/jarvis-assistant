import type { Metadata, Viewport } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { THEME_SCRIPT } from "@/components/theme-toggle";
import { SITE_URL } from "@/lib/site";
import "./globals.css";

// The app's typefaces (src/gui/web/fonts, SIL Open Font License).
const barlow = Barlow({ variable: "--font-barlow", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const barlowCondensed = Barlow_Condensed({ variable: "--font-barlow-condensed", subsets: ["latin"], weight: ["500", "600", "700"] });

const description =
  "Jarvis is a free, open-source voice assistant for Windows and Mac. Talk to it, and it opens apps, finds files, " +
  "remembers things and works on your computer, with every action checked and the risky ones asking you first.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Jarvis: a voice assistant that does things on your computer, safely", template: "%s · Jarvis" },
  description,
  applicationName: "Jarvis",
  openGraph: { type: "website", siteName: "Jarvis", url: "/", title: "Jarvis", description },
  twitter: { card: "summary_large_image", title: "Jarvis", description },
};

export const viewport: Viewport = {
  themeColor: "#03111e",
  colorScheme: "dark light",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning className={`${barlow.variable} ${barlowCondensed.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-dvh flex-col">
        <SiteHeader />
        <main id="main" className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
