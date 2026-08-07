import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import Link from "next/link";

import { DataSourceBanner } from "@/components/data-source-banner";
import { Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "AXIOM Console",
    template: "%s · AXIOM",
  },
  description:
    "Review workspace, quality dashboards and catalog explorer for AXIOM product intelligence.",
};

/**
 * Browser chrome colour, matching `--canvas` in each theme.
 *
 * These are the sRGB renderings of `--color-ink-150` and `--color-ink-1000`. They must be
 * kept in step with `globals.css` by hand, because a `meta` tag cannot read a custom
 * property. Note this keys off the OS preference while the app itself keys off
 * `localStorage`, so a user who has overridden the theme in-app will briefly see the other
 * chrome colour. That is a platform limitation, not a bug worth working around.
 */
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f1f1f4" },
    { media: "(prefers-color-scheme: dark)", color: "#040507" },
  ],
};

/**
 * Resolve the theme before first paint.
 *
 * Without this the document renders in the light theme and then swaps, which is the most
 * visible quality defect a themed app can ship. Kept inline and tiny; it runs before the
 * body exists so there is nothing to repaint.
 */
const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem('axiom-theme');
  var dark = stored ? stored === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (dark) document.documentElement.classList.add('dark');
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    /*
     * `suppressHydrationWarning` is required here, and only here.
     *
     * `THEME_SCRIPT` below adds `dark` to this element before React hydrates, so the client's
     * className is deliberately not the one the server sent. React cannot tell an intentional
     * pre-paint mutation from a bug and warns on every page load without this.
     *
     * It is safe rather than a blanket silencer: the flag applies to this element's own attributes
     * and one level of children, so a genuine mismatch anywhere inside the app still reports. The
     * alternative — resolving the theme in React state — reintroduces the light-mode flash the
     * script exists to prevent.
     */
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50
                     focus:rounded-md focus:bg-[var(--surface-raised)] focus:px-3 focus:py-2
                     focus:text-sm focus:shadow-md"
        >
          Skip to content
        </a>

        <header className="app-bar sticky top-0 z-40">
          {/* Gap tightens and the tagline drops below `sm` so the bar fits 360px without
              the nav overflowing the viewport. */}
          <div className="mx-auto flex h-14 max-w-[var(--container-shell)] items-center gap-3 px-[var(--spacing-gutter)] sm:gap-6">
            <Link
              href="/"
              className="flex shrink-0 items-baseline gap-2 rounded-xs transition-opacity duration-[var(--duration-fast)] hover:opacity-70"
            >
              <span className="text-lg font-medium tracking-[-0.03em]">AXIOM</span>
              <span className="hidden text-meta text-[var(--fg-quiet)] lg:inline">
                Product Intelligence
              </span>
            </Link>

            <div className="hairline-l hidden h-5 self-center sm:block" aria-hidden />

            <Nav />

            <div className="ml-auto flex shrink-0 items-center gap-2">
              <ThemeToggle />
            </div>
          </div>
        </header>

        <DataSourceBanner />

        <main id="main">{children}</main>
      </body>
    </html>
  );
}
