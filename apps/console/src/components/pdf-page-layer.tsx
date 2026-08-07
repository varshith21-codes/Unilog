"use client";

/**
 * The PDF bitmap that sits underneath the evidence overlay.
 *
 * This is the beat the blueprint says judges remember: click a value, and the source document opens
 * to the page with the cited cell outlined. Until now the viewer drew a reconstructed monospace
 * layout — correct geometry, right highlight, wrong artifact. A reader could reasonably ask whether
 * they were looking at the datasheet or at the system's idea of it.
 *
 * Isolated in its own client-only module for three reasons:
 *
 * 1. **react-pdf and pdfjs touch the DOM and spawn a worker at import time.** Next prerenders client
 *    components on the server, so importing this eagerly would run that during SSR.
 * 2. **It is large.** pdfjs is the heaviest thing in this bundle by a wide margin, and only one
 *    screen ever needs it. Loading it behind `next/dynamic` keeps it off every other page.
 * 3. **It can fail.** A 404 from the artifact store, a corrupt file, a worker that will not start —
 *    all real, and all must degrade to the text layer rather than to a blank rectangle.
 *
 * The overlay is not defined here. Highlights stay in `EvidenceViewer` positioned as percentages of
 * page width and height, which is why they land correctly over a bitmap they know nothing about.
 */

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

// Bundled rather than fetched from a CDN. A demo that needs the network to render its own evidence
// is a demo that fails on conference wifi, and the blueprint is explicit about pre-warming.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export interface PdfPageLayerProps {
  /** `/api/artifact/{sha256}` — content-addressed, so it is safe to cache forever. */
  url: string;
  pageNumber: number;
  /** Called when the PDF cannot be rendered, so the caller can fall back to the text layer. */
  onFailure: () => void;
}

export function PdfPageLayer({ url, pageNumber, onFailure }: PdfPageLayerProps) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);

  /*
   * The canvas needs a pixel width; the overlay above it needs none. Measuring here rather than
   * hard-coding one keeps the bitmap sharp at whatever size the container settles on, and keeps it
   * in step when the pane is resized — a stale canvas width would leave the highlight visibly
   * offset from the text it is meant to be boxing.
   */
  useEffect(() => {
    const element = host.current;
    if (element === null) return;

    const measure = () => setWidth(element.clientWidth || null);
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={host} className="absolute inset-0">
      {width !== null ? (
        <Document
          file={url}
          onLoadError={onFailure}
          onSourceError={onFailure}
          // The parent already renders the quote as text, so a spinner here would be the only thing
          // announced during load. Silence is better.
          loading=""
          error=""
          noData=""
        >
          <Page
            pageNumber={pageNumber}
            width={width}
            onRenderError={onFailure}
            // The bitmap is decoration: the quote above it carries the text, and pdfjs's own text
            // layer would be a second, differently-positioned copy of the same words for a screen
            // reader to read out. The annotation layer is off for the same reason plus one more —
            // it renders supplier-authored links, and nothing in a cited datasheet should be
            // clickable inside this console.
            renderTextLayer={false}
            renderAnnotationLayer={false}
            loading=""
            error=""
            noData=""
          />
        </Document>
      ) : null}
    </div>
  );
}
