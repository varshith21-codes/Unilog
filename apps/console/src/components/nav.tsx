"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

type NavVariant = "desktop" | "mobile";
type NavItem = {
  href: string;
  label: string;
  descriptor: string;
  glyph: "operations" | "resolve" | "process" | "publish" | "audit" | "intelligence";
};

const PRIMARY: NavItem[] = [
  { href: "/", label: "Operations", descriptor: "Portfolio command", glyph: "operations" },
  { href: "/review", label: "Resolve", descriptor: "Decision queue", glyph: "resolve" },
  { href: "/pipeline", label: "Process", descriptor: "Recorded runs", glyph: "process" },
  { href: "/delivery", label: "Publish", descriptor: "Delivery studio", glyph: "publish" },
  { href: "/certificates", label: "Audit", descriptor: "Certified records", glyph: "audit" },
];

const SECONDARY: NavItem[] = [
  {
    href: "/quality",
    label: "Intelligence",
    descriptor: "Impact, policy & cost",
    glyph: "intelligence",
  },
];

const ALL = [...PRIMARY, ...SECONDARY];

function activeFor(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavGlyph({ name }: { name: NavItem["glyph"] }) {
  const paths = {
    operations: <path d="M2.5 3.5h11v3h-11zm0 6h5v3h-5zm8 0h3v3h-3z" />,
    resolve: <path d="M3 3.5h6m-6 4h10m-10 4h7m2.5-9v3m-4 4v3" />,
    process: <path d="M2.5 4h4l1.5 2.5L9.5 4h4M2.5 12h4L8 9.5 9.5 12h4" />,
    publish: <path d="M8 2.5v7m0-7L5.5 5M8 2.5 10.5 5M3 9.5v3h10v-3" />,
    audit: <path d="M4 2.5h8v11H4zM2.5 5H4m-1.5 3H4m-1.5 3H4m2.5-5h3m-3 3h3" />,
    intelligence: <path d="M2.5 12.5V9m3.5 3.5V5.5m3.5 7V7.5m3.5 5v-10" />,
  }[name];

  return (
    <svg viewBox="0 0 16 16" aria-hidden className="nav-glyph" fill="none">
      <g stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round">
        {paths}
      </g>
    </svg>
  );
}

function NavGroup({
  items,
  pathname,
  variant,
  label,
}: {
  items: NavItem[];
  pathname: string;
  variant: NavVariant;
  label: string;
}) {
  return (
    <div className="nav-group">
      {variant === "desktop" ? <p className="nav-group-label">{label}</p> : null}
      <ul className={clsx("nav-list", variant === "mobile" && "nav-list-mobile")}>
        {items.map((item) => {
          const active = activeFor(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={clsx("nav-link", variant === "mobile" && "nav-link-mobile")}
              >
                <NavGlyph name={item.glyph} />
                <span className="nav-copy">
                  <strong>{item.label}</strong>
                  {variant === "desktop" ? <small>{item.descriptor}</small> : null}
                </span>
                {variant === "desktop" ? <span className="nav-active-mark" aria-hidden /> : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function CurrentSection() {
  const pathname = usePathname();
  return <strong>{ALL.find((item) => activeFor(pathname, item.href))?.label ?? "Workspace"}</strong>;
}

export function Nav({ variant = "desktop" }: { variant?: NavVariant }) {
  const pathname = usePathname();

  useEffect(() => {
    if (variant !== "mobile") return;
    const active = document.querySelector<HTMLElement>(
      '.workflow-nav-mobile [aria-current="page"]',
    );
    active?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname, variant]);

  return (
    <nav
      aria-label={variant === "desktop" ? "AXIOM workflows" : "Workflow navigation"}
      className={clsx("workflow-nav", variant === "mobile" && "workflow-nav-mobile")}
    >
      <NavGroup items={PRIMARY} pathname={pathname} variant={variant} label="Workflows" />
      <NavGroup items={SECONDARY} pathname={pathname} variant={variant} label="Analysis" />
    </nav>
  );
}
