"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS = [
  { href: "/", label: "Overview" },
  { href: "/review", label: "Review" },
  { href: "/certificates", label: "Certificates" },
] as const;

export function Nav() {
  const pathname = usePathname();

  function isActive(href: string): boolean {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <nav aria-label="Sections" className="scroll-x -mx-1 min-w-0">
      <ul className="flex items-center gap-0.5 px-1">
        {SECTIONS.map((section) => {
          const active = isActive(section.href);
          return (
            <li key={section.href}>
              <Link
                href={section.href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "relative block rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap",
                  "transition-colors duration-[var(--duration-fast)]",
                  /*
                   * The active state is carried by an accent underline rather than a tinted
                   * background. A background step subtle enough to suit this palette lands
                   * near 1.02:1 against the bar, which is not a state anyone can see; a 2px
                   * accent rule clears 3:1 and reads instantly.
                   */
                  active
                    ? "font-medium text-[var(--fg)] after:absolute after:inset-x-2.5 after:-bottom-px after:h-0.5 after:rounded-full after:bg-[var(--accent)]"
                    : "text-[var(--fg-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--fg)]",
                )}
              >
                {section.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
