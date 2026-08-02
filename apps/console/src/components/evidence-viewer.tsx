/**
 * Evidence viewer.
 *
 * Renders a parsed page and draws the cited region on top of it. Coordinates come from
 * `axiom.core.evidence.BoundingBox` — PDF points, origin at top-left — and every element is
 * positioned as a percentage of page width and height. That keeps the overlay correct at
 * any rendered size without measuring anything, and it is the same geometry a real PDF
 * canvas will use once `react-pdf` renders the page bitmap underneath.
 *
 * The text layer here stands in for that bitmap. The sample corpus is a text datasheet, so
 * the parser synthesises a monospace layout at a 6pt advance; when a genuine PDF arrives,
 * this layer becomes the selectable text overlay and the bitmap sits behind it. The
 * highlight code does not change.
 */

import clsx from "clsx";

import type { BoundingBox, ParsedPage } from "@/lib/types";

/** Point size the text parser lays out at. Advance is 0.6em, giving the 6pt grid. */
const GLYPH_POINTS = 10;
const LINE_POINTS = 12;

function pct(value: number, extent: number): string {
  return `${(value / extent) * 100}%`;
}

export interface EvidenceViewerProps {
  page: ParsedPage;
  /** The cited region, drawn as the primary highlight. */
  highlight?: BoundingBox | null;
  /** Other spans on the same page, drawn faintly for orientation. */
  context?: BoundingBox[];
  className?: string;
}

export function EvidenceViewer({
  page,
  highlight = null,
  context = [],
  className,
}: EvidenceViewerProps) {
  const glyph = `${(GLYPH_POINTS / page.width) * 100}cqw`;
  const leading = `${(LINE_POINTS / page.width) * 100}cqw`;

  return (
    /*
     * The floor width keeps the 10pt document text legible on a phone; below it the region
     * scrolls horizontally rather than shrinking the glyphs into illegibility.
     *
     * The scroll container is focusable so a keyboard user can actually reach and pan it
     * (WCAG 2.1.1). Its accessible name lives here rather than on the canvas, because the
     * canvas is `aria-hidden` — the quote above it already carries the text, and having a
     * screen reader read a synthesised page layout would be noise.
     */
    <div
      className={clsx("scroll-x rounded-md", className)}
      tabIndex={0}
      role="group"
      aria-label={`Source page ${page.number}, scrollable. The cited region is highlighted.`}
    >
      <div
        aria-hidden
        className="page-canvas min-w-[34rem]"
        style={{ aspectRatio: `${page.width} / ${page.height}` }}
      >
      {context.map((box, index) => (
        <div
          key={`context-${index}`}
          className="page-highlight-secondary"
          style={{
            left: pct(box.x0, page.width),
            top: pct(box.y0, page.height),
            width: pct(box.x1 - box.x0, page.width),
            height: pct(box.y1 - box.y0, page.height),
          }}
        />
      ))}

      {highlight ? (
        <div
          className="page-highlight"
          style={{
            left: pct(highlight.x0, page.width),
            top: pct(highlight.y0, page.height),
            width: pct(highlight.x1 - highlight.x0, page.width),
            height: pct(highlight.y1 - highlight.y0, page.height),
          }}
        />
      ) : null}

      {page.lines.map((line) => {
        const [x0, y0] = line.bbox;
        const cited =
          highlight !== null && y0 >= highlight.y0 - 0.5 && y0 <= highlight.y1 - 0.5;
        return (
          <span
            key={line.line_index}
            className="page-line"
            style={{
              left: pct(x0, page.width),
              top: pct(y0, page.height),
              fontSize: glyph,
              lineHeight: leading,
              color: cited ? "var(--fg)" : undefined,
            }}
          >
            {line.text}
          </span>
        );
      })}
      </div>
    </div>
  );
}

/**
 * Text fallback for when no geometry resolved.
 *
 * An unlocatable quote is a real state — `build_evidence_span` still returns a span with
 * `quote_verified: false` — and it must read as a warning rather than an empty box.
 */
export function EvidenceTextFallback({ quote }: { quote: string }) {
  return (
    <div className="rounded-md border border-dashed border-[var(--hairline-strong)] bg-[var(--surface-sunken)] p-4">
      <p className="mono whitespace-pre-wrap text-[var(--fg-secondary)]">{quote}</p>
      <p className="mt-3 text-meta text-[var(--warn)]">
        No coordinates resolved for this quote, so it cannot be located on the page.
      </p>
    </div>
  );
}
