import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Manrope } from "next/font/google";
import Link from "next/link";

import { DataSourceBanner } from "@/components/data-source-banner";
import { CurrentSection, Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";

import "./globals.css";

const sans = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-ibm-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Operations · AXIOM",
    template: "%s · AXIOM",
  },
  description:
    "Catalog operations for evidence-backed enrichment, review, process replay, delivery, and audit.",
  applicationName: "AXIOM",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f5ef" },
    { media: "(prefers-color-scheme: dark)", color: "#050506" },
  ],
};

const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem('axiom-theme');
  var dark = stored ? stored === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (dark) document.documentElement.classList.add('dark');
} catch (e) {}
`;

function ProductMark() {
  return (
    <span className="product-mark" aria-hidden>
      <span>A</span>
      <i />
    </span>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh antialiased">
        <a href="#main" className="skip-link">
          Skip to content
        </a>

        <div className="app-shell">
          <aside className="app-sidebar" aria-label="AXIOM workspace">
            <div className="sidebar-brand">
              <Link href="/" className="brand-link" aria-label="AXIOM Operations">
                <ProductMark />
                <span>
                  <strong>AXIOM</strong>
                  <small>Catalog operations</small>
                </span>
              </Link>
            </div>

            <div className="workspace-context" aria-label="Current workspace">
              <span className="workspace-indicator" aria-hidden />
              <span>
                <small>Workspace</small>
                <strong>Production catalog</strong>
              </span>
            </div>

            <Nav variant="desktop" />

            <div className="sidebar-footer">
              <div>
                <span className="overline">Environment</span>
                <p>Catalog intelligence</p>
              </div>
              <ThemeToggle />
            </div>
          </aside>

          <div className="app-content">
            <header className="mobile-header app-bar">
              <Link href="/" className="brand-link" aria-label="AXIOM Operations">
                <ProductMark />
                <strong>AXIOM</strong>
              </Link>
              <div className="mobile-section">
                <span>Workspace</span>
                <CurrentSection />
              </div>
              <ThemeToggle />
            </header>
            <Nav variant="mobile" />
            <DataSourceBanner />
            <main id="main">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
