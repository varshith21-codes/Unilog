"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useState } from "react";

import { Nav } from "@/components/nav";

type DesktopSidebarProps = {
  brandMark: ReactNode;
};

function CollapseIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden
      fill="none"
      className="sidebar-collapse-icon"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <path
        d="m9.5 3.5-4.5 4.5 4.5 4.5"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function DesktopSidebar({ brandMark }: DesktopSidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const controlLabel = collapsed ? "Expand sidebar" : "Collapse sidebar";

  return (
    <aside
      id="axiom-desktop-sidebar"
      className="app-sidebar"
      aria-label="AXIOM workspace"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <div className="sidebar-header">
        <Link href="/operations" className="brand-link" aria-label="AXIOM Operations">
          {brandMark}
          {!collapsed ? (
            <span className="sidebar-brand-copy">
              <strong>AXIOM</strong>
              <small>Catalog operations</small>
            </span>
          ) : null}
        </Link>

        <button
          type="button"
          className="btn btn-bare icon-target size-8 px-0 sidebar-collapse-button"
          aria-controls="axiom-desktop-sidebar"
          aria-expanded={!collapsed}
          aria-label={controlLabel}
          title={controlLabel}
          onClick={() => setCollapsed((current) => !current)}
        >
          <CollapseIcon collapsed={collapsed} />
        </button>
      </div>

      <div
        className="workspace-context"
        aria-label="Current workspace: Production catalog"
      >
        <span className="workspace-indicator" aria-hidden />
        <span className="workspace-copy">
          <small>Workspace</small>
          <strong>Production catalog</strong>
        </span>
      </div>

      <Nav variant="desktop" collapsed={collapsed} />
    </aside>
  );
}
