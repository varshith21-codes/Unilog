"use client";

/**
 * Evidence viewer.
 *
 * Renders a source page and draws the cited region on top of it. Coordinates come from
 * `axiom.core.evidence.BoundingBox` — PDF points, origin at top-left — and every element is
 * positioned as a percentage of page width and height. That is what lets the same highlight code
 * sit over a real PDF bitmap and over a synthesised text layout without knowing which it is on.
 *
 * **Two base layers, one overlay.** When the source is a PDF, `PdfPageLayer` renders the actual page
 * underneath and the highlight boxes the real cell. When it is text or HTML — which is most of this
 * corpus — there is no bitmap to render, so the parser's synthesised monospace layout stands in. The
 * distinction is visible to the reader rather than hidden: a caption says which one they are looking
 * at, because "the datasheet" and "our reconstruction of the datasheet" are different claims and
 * only one of them is proof.
 *
 * A PDF that fails to load falls back to the text layer rather than to an empty box.
 */

import clsx from "clsx";
import dynamic from "next/dynamic";
import { useState } from "react";

import type { BoundingBox, ParsedPage } from "@/lib/types";

/*
 * pdfjs is the largest dependency in this app and only this component needs it. `ssr: false` keeps
 * it out of the server render — it spawns a worker and touches the DOM at import — and the dynamic
 * boundary keeps it out of the initial bundle for every screen that never opens a PDF.
 */
const PdfPageLayer = dynamic(
  () => import("@/components/pdf-page-layer").then((module) => module.PdfPageLayer),
  { ssr: false },
);

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
  /**
   * SHA-256 of the source document, when it is a PDF the store can serve.
   *
   * Null for text and HTML sources — there is no bitmap to render, and the synthesised layout is
   * the honest thing to show. The hash rather than a URL because the artifact endpoint is addressed
   * by content, which is what makes it traversal-proof.
   */
  pdfSha256?: string | null;
  className?: string;
}

export function EvidenceViewer({
  page,
  highlight = null,
  context = [],
  pdfSha256 = null,
  className,
}: EvidenceViewerProps) {
  const glyph = `${(GLYPH_POINTS / page.width) * 100}cqw`;
  const leading = `${(LINE_POINTS / page.width) * 100}cqw`;

  // A PDF that will not load is a real state: the artifact store is gitignored, so a fresh clone
  // has the bundle but not the bytes. Falling back to the reconstruction beats an empty frame.
  const [pdfFailed, setPdfFailed] = useState(false);
  const showPdf = pdfSha256 !== null && !pdfFailed;

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
        className="page-canvas relative min-w-[34rem]"
        style={{ aspectRatio: `${page.width} / ${page.height}` }}
      >
      {showPdf ? (
        <PdfPageLayer
          url={`${apiBase()}/api/artifact/${pdfSha256}`}
          pageNumber={page.number}
          onFailure={() => setPdfFailed(true)}
        />
      ) : null}

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

      {/*
        Suppressed when the real page is showing. Drawing reconstructed glyphs on top of the
        actual document would double every character and make the highlight look misaligned
        against text that is now a bitmap rather than a grid.
      */}
      {showPdf
        ? null
        : page.lines.map((line) => {
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

      {/*
        Which artifact is on screen. "The datasheet" and "a reconstruction of the datasheet" are
        different claims, and a provenance tool that blurs them is undermining its own argument —
        so the caption states it rather than leaving a reader to infer it from how the page looks.
      */}
      <p className="mt-2 text-meta text-[var(--fg-quiet)]">
        {showPdf ? (
          <>Rendered from the stored PDF, page {page.number}. The box is the cited region.</>
        ) : pdfFailed ? (
          <>
            The stored PDF could not be loaded, so this is the parser&rsquo;s reconstruction of page{" "}
            {page.number}. Coordinates are the document&rsquo;s; the glyphs are not.
          </>
        ) : (
          <>
            Reconstructed from the parsed text layer, page {page.number}. Coordinates are the
            document&rsquo;s; the typography is synthesised.
          </>
        )}
      </p>
    </div>
  );
}

/**
 * Where the artifact endpoint lives.
 *
 * Read from the browser-visible env var rather than `API_BASE` in `lib/data.ts`: that module is
 * server-only and imports `node:fs`. This URL is fetched by the browser, so it has to be a value the
 * client bundle can see.
 */
function apiBase(): string {
  return process.env.NEXT_PUBLIC_AXIOM_API_URL ?? "http://127.0.0.1:8000";
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
