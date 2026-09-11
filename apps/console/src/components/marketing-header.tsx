import Link from "next/link";

import { ProductMark } from "@/components/product-mark";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Public masthead.
 *
 * Sticky and translucent rather than static. The landing page is now long enough that a
 * masthead which scrolls away strands the reader with no route back to the workspace, and a
 * long technical page is exactly where an in-page index earns its keep.
 *
 * The glass treatment is `backdrop-filter` paired with a hairline, never alone — a blur with
 * no boundary reads as a rendering artefact rather than as a surface. It also has something
 * worth blurring behind it, which is the other half of the rule: the page beneath is dense
 * type and rules, so the blur registers.
 *
 * No JavaScript. `position: sticky` and a translucent fill get the whole effect, so the
 * masthead paints with the document instead of waiting on hydration. A scroll listener that
 * swapped a class here would buy a slightly crisper transition at the cost of the first paint.
 */
export function MarketingHeader({
  /**
   * In-page section index, rendered on wide viewports only.
   *
   * Optional because `not-found` reuses this masthead and has no sections to point at. An
   * index whose links all 404 is worse than no index.
   */
  sections,
}: {
  sections?: readonly { id: string; label: string }[];
}) {
  return (
    <header className="marketing-header">
      <div className="marketing-container marketing-header-inner">
        <Link href="/" className="marketing-brand" aria-label="AXIOM home">
          <ProductMark />
          <span className="marketing-brand-copy">
            <strong>AXIOM</strong>
            <small>Product intelligence</small>
          </span>
        </Link>

        {sections && sections.length > 0 ? (
          <nav className="marketing-index" aria-label="Sections of this page">
            <ul>
              {sections.map((section) => (
                <li key={section.id}>
                  <a href={`#${section.id}`}>{section.label}</a>
                </li>
              ))}
            </ul>
          </nav>
        ) : null}

        <div className="marketing-header-actions">
          <div className="marketing-theme-control">
            <ThemeToggle />
          </div>
          <Link href="/operations" className="btn btn-quiet">
            Open operations
          </Link>
        </div>
      </div>
    </header>
  );
}
