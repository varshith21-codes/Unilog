import Link from "next/link";

import { DataSourceBanner } from "@/components/data-source-banner";
import { DesktopSidebar } from "@/components/desktop-sidebar";
import { CurrentSection, Nav } from "@/components/nav";
import { ProductMark } from "@/components/product-mark";
import { ThemeToggle } from "@/components/theme-toggle";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <DesktopSidebar brandMark={<ProductMark />} />

      <div className="app-content">
        <div className="desktop-theme-control">
          <ThemeToggle />
        </div>
        <header className="mobile-header app-bar">
          <Link href="/operations" className="brand-link" aria-label="AXIOM Operations">
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
  );
}
