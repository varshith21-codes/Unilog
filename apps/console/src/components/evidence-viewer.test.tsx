import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvidenceTextFallback, EvidenceViewer } from "@/components/evidence-viewer";
import type { ParsedPage } from "@/lib/types";

/*
 * The PDF layer is mocked rather than exercised. What needs guarding is the *viewer's* branching —
 * which base layer it chooses, what it suppresses, and what it tells the reader it is showing.
 * Whether pdfjs can paint a canvas is pdfjs's problem, and asserting it in jsdom would test the
 * mock more than the component.
 *
 * `onFailure` is captured so a test can drive the failure path deliberately, which is the branch
 * that actually matters: the artifact store is gitignored, so a fresh clone has bundles but no bytes.
 */
let failLayer: (() => void) | null = null;

vi.mock("@/components/pdf-page-layer", () => ({
  PdfPageLayer: ({ url, pageNumber, onFailure }: {
    url: string;
    pageNumber: number;
    onFailure: () => void;
  }) => {
    failLayer = onFailure;
    return <div data-testid="pdf-layer" data-url={url} data-page={pageNumber} />;
  },
}));

afterEach(() => {
  cleanup();
  failLayer = null;
});

/**
 * The layer arrives a tick late, on purpose.
 *
 * `next/dynamic` resolves its loader as a promise, so the layer is absent from the first commit even
 * when the module is already mocked. Waiting for it here is not a workaround for the test — it is the
 * behaviour the viewer actually has in a browser, and the reason the caption and the suppression gate
 * are driven by `pdfSha256` rather than by whether the layer has mounted.
 */
async function pdfLayer(): Promise<HTMLElement> {
  return screen.findByTestId("pdf-layer");
}

const PAGE: ParsedPage = {
  number: 1,
  width: 612,
  height: 792,
  lines: [
    { line_index: 0, text: "APOLLO VALVE - 77C SERIES", bbox: [54, 54, 300, 66] },
    { line_index: 1, text: "77C-105  DN25  1\"  Full  49.0  12", bbox: [54, 300, 400, 312] },
  ],
  tables: [],
};

// The cited region, in the object form BoundingBox takes on the wire.
const HIGHLIGHT = { x0: 54, y0: 300, x1: 400, y1: 312 };

describe("choosing a base layer", () => {
  it("reconstructs from the text layer when the source is not a PDF", () => {
    render(<EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={null} />);

    expect(screen.queryByTestId("pdf-layer")).toBeNull();
    expect(screen.getByText("APOLLO VALVE - 77C SERIES")).toBeTruthy();
    // The caption is the honest part: "the datasheet" and "our reconstruction of it" are different
    // claims, and only one is proof.
    expect(screen.getByText(/typography is synthesised/i)).toBeTruthy();
  });

  it("renders the stored PDF when the source is one, and says so", async () => {
    render(<EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={"a".repeat(64)} />);

    expect(await pdfLayer()).toBeTruthy();
    expect(screen.getByText(/Rendered from the stored PDF/i)).toBeTruthy();
  });

  it("suppresses the reconstructed glyphs while the real page is showing", () => {
    // Drawing both would double every character and make the highlight look misaligned against
    // text that is now a bitmap rather than a grid.
    render(<EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={"b".repeat(64)} />);

    expect(screen.queryByText("APOLLO VALVE - 77C SERIES")).toBeNull();
  });

  it("addresses the artifact by content hash on the requested page", async () => {
    const sha = "c".repeat(64);
    render(<EvidenceViewer page={{ ...PAGE, number: 4 }} pdfSha256={sha} />);

    const layer = await pdfLayer();
    expect(layer.getAttribute("data-url")).toContain(`/api/artifact/${sha}`);
    expect(layer.getAttribute("data-page")).toBe("4");
  });
});

describe("when the PDF cannot be loaded", () => {
  it("falls back to the reconstruction rather than an empty frame", async () => {
    render(<EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={"d".repeat(64)} />);
    await pdfLayer();

    // The store is gitignored, so this is the state a fresh clone is in.
    expect(failLayer).not.toBeNull();
    await act(async () => failLayer!());

    expect(screen.queryByTestId("pdf-layer")).toBeNull();
    expect(screen.getByText("APOLLO VALVE - 77C SERIES")).toBeTruthy();
    expect(screen.getByText(/could not be loaded/i)).toBeTruthy();
  });

  it("does not claim the PDF was rendered after it failed", async () => {
    render(<EvidenceViewer page={PAGE} pdfSha256={"e".repeat(64)} />);
    await pdfLayer();
    await act(async () => failLayer!());

    expect(screen.queryByText(/Rendered from the stored PDF/i)).toBeNull();
  });
});

describe("the overlay", () => {
  it("positions the highlight as a percentage of page extent, not in pixels", () => {
    // This is what lets one highlight implementation sit over both a bitmap and a synthesised grid.
    const { container } = render(
      <EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={"f".repeat(64)} />,
    );
    const box = container.querySelector<HTMLElement>(".page-highlight");

    expect(box).not.toBeNull();
    // y0 300 of 792 -> 37.878...%
    expect(box!.style.top.endsWith("%")).toBe(true);
    expect(Number.parseFloat(box!.style.top)).toBeCloseTo((300 / 792) * 100, 3);
    expect(Number.parseFloat(box!.style.left)).toBeCloseTo((54 / 612) * 100, 3);
  });

  it("draws the highlight over a PDF just as it does over text", () => {
    for (const sha of [null, "0".repeat(64)]) {
      const { container, unmount } = render(
        <EvidenceViewer page={PAGE} highlight={HIGHLIGHT} pdfSha256={sha} />,
      );
      expect(container.querySelector(".page-highlight")).not.toBeNull();
      unmount();
    }
  });

  it("keeps the scroll region reachable by keyboard", () => {
    render(<EvidenceViewer page={PAGE} highlight={HIGHLIGHT} />);
    const region = screen.getByRole("group");

    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.getAttribute("aria-label")).toMatch(/page 1/i);
  });
});

describe("no geometry at all", () => {
  it("warns rather than showing an empty box", () => {
    render(<EvidenceTextFallback quote="600 PSI WOG @ 73 degF" />);

    expect(screen.getByText("600 PSI WOG @ 73 degF")).toBeTruthy();
    expect(screen.getByText(/cannot be located on the page/i)).toBeTruthy();
  });
});
