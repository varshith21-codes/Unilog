import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EnrichForm } from "@/components/enrich-form";
import type { EnrichResult } from "@/lib/enrich";
import { enrichLimits, enrichResponse, enrichSummary } from "@/test/factories";

/**
 * The server action is replaced, not the network.
 *
 * `runEnrichment` carries `"use server"` and spends money when it runs for real. Mocking at that
 * boundary keeps these tests about the form's behaviour — what it sends, what it refuses, and how it
 * explains a refusal — rather than about `fetch`, which `lib/enrich.test.ts` already covers.
 */
const runEnrichment = vi.fn<(input: unknown) => Promise<EnrichResult>>();

vi.mock("@/lib/actions", () => ({
  runEnrichment: (input: unknown) => runEnrichment(input),
}));

beforeEach(() => {
  runEnrichment.mockReset();
  runEnrichment.mockResolvedValue({ ok: true, data: enrichResponse() });
});

afterEach(cleanup);

const LIMITS = enrichLimits();

// `fireEvent` rather than `user-event`, matching the rest of this suite. These are controlled inputs,
// so one change event per field is exactly what a keystroke sequence would end at.
function type(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const MPN = /Manufacturer part number/i;
const MAKER = /^Manufacturer$/i;
const DESC = /Product description/i;
const URL_FIELD = /Manufacturer or datasheet URL/i;

/**
 * Located by type, not by label.
 *
 * Its label deliberately changes — it carries the call count when idle and says it is running while
 * in flight — so querying by accessible name would make the test that checks the running state fail
 * for the reason it is testing.
 */
function submitButton(): HTMLButtonElement {
  const button = document.querySelector<HTMLButtonElement>("form button[type=submit]");
  if (button === null) throw new Error("no submit button rendered");
  return button;
}

/** `jest-dom` is not a dependency here, so the property is read directly. */
const locked = () => submitButton().disabled;

function form() {
  return render(<EnrichForm limits={LIMITS} />);
}

function identify() {
  type(MPN, "PDSH4816AF");
  type(MAKER, "Frigidaire");
}

async function submitWith(result: EnrichResult, fields: () => void = identify) {
  runEnrichment.mockResolvedValue(result);
  form();
  fields();
  type(DESC, "24IN BUILT IN DISHWASHER");
  fireEvent.click(submitButton());
  await waitFor(() => expect(runEnrichment).toHaveBeenCalled());
}

/**
 * Wait for the result by something only the result has.
 *
 * Not by the heading or by "Run complete": the SKU appears in both the result heading and the stage
 * view's, and "Run complete" appears in both the eyebrow and the status line. The download link exists
 * only once a run has produced a file, which makes it the unambiguous readiness signal.
 */
const resultRendered = () => screen.findByRole("link", { name: /Excel workbook/i });

/** The gate paragraph, read as an element because its copy is deliberately echoed in the guidance. */
function gateText(): string {
  const button = submitButton();
  const gate = document.getElementById(button.getAttribute("aria-describedby") ?? "");
  return gate?.textContent ?? "";
}

/**
 * The gate is the whole point of this component's validation.
 *
 * A part number and a manufacturer are the two required fields, and they are *still* not enough to
 * run: classification has nothing to read and extraction has nothing to cite, so the run would cost
 * two model calls and return an identity-only row. A disabled button alone does not communicate
 * that — somebody who filled in both required fields and cannot proceed needs to be told which third
 * field is missing, and why.
 */
describe("the submit gate", () => {
  it("unlocks on a part number and a manufacturer alone", () => {
    // The submission this feature exists for. Retrieval is on by default, so those two fields are
    // the whole input and the gate says what will happen with them.
    form();
    identify();

    expect(locked()).toBe(false);
    // Read off the gate the button points at, rather than from anywhere on the page: the section
    // guidance says something similar, and matching that would not prove the gate says anything.
    expect(gateText()).toMatch(/look for the manufacturer's own document/i);
    expect(gateText()).toMatch(/library first/i);
  });

  it("re-locks with retrieval off, and says why", () => {
    form();
    identify();
    fireEvent.click(screen.getByLabelText(/Find the manufacturer's document/i));

    expect(locked()).toBe(true);
    expect(gateText()).toMatch(/Retrieval is off/i);
    expect(gateText()).toMatch(/two model calls to return an identity-only row/i);
  });

  it("names the missing required field rather than restating the general rule", () => {
    form();
    type(MPN, "PDSH4816AF");

    expect(screen.getByText("A manufacturer is required.")).toBeTruthy();
  });

  it("names a missing part number first, since nothing else identifies the record", () => {
    form();
    type(MAKER, "Frigidaire");

    expect(screen.getByText("A manufacturer part number is required.")).toBeTruthy();
  });

  it("says a description will be read as well as the document being looked for", () => {
    form();
    identify();
    type(DESC, "24IN BUILT IN DISHWASHER");

    expect(locked()).toBe(false);
    expect(gateText()).toMatch(/read your description either way/i);
  });

  it("with retrieval off, a description makes the submission its own source", () => {
    form();
    identify();
    type(DESC, "24IN BUILT IN DISHWASHER");
    fireEvent.click(screen.getByLabelText(/Find the manufacturer's document/i));

    expect(locked()).toBe(false);
    // Stated before the run rather than discovered in the result.
    expect(gateText()).toMatch(/the submission itself is the source/i);
    expect(gateText()).toMatch(/weaker claim than a datasheet/i);
  });

  it("unlocks on a URL alone, and says the document will be the source", () => {
    form();
    identify();
    type(URL_FIELD, "https://example.com/ds.pdf");

    expect(locked()).toBe(false);
    expect(screen.getByText(/cite the page and line/i)).toBeTruthy();
  });

  it("explains the ordering when both are supplied", () => {
    form();
    identify();
    type(DESC, "24IN BUILT IN DISHWASHER");
    type(URL_FIELD, "https://example.com/ds.pdf");

    expect(screen.getByText(/the document supersedes it/i)).toBeTruthy();
  });

  it("re-locks when the only content is removed and retrieval is off", () => {
    form();
    identify();
    fireEvent.click(screen.getByLabelText(/Find the manufacturer's document/i));
    type(DESC, "24IN BUILT IN DISHWASHER");
    expect(locked()).toBe(false);

    type(DESC, "   ");

    expect(locked()).toBe(true);
  });

  it("stays unlocked when content is removed but retrieval is on", () => {
    form();
    identify();
    type(DESC, "24IN BUILT IN DISHWASHER");
    type(DESC, "   ");

    expect(locked()).toBe(false);
  });
});

describe("how the document was found", () => {
  it("reports a library hit as needing no request", async () => {
    // The economic claim, on the screen: retrieval is a one-time cost per document, not per part.
    const summary = enrichSummary({
      retrieval: {
        attempted: true,
        found: true,
        from_library: true,
        manufacturer: { id: "kichler", name: "Kichler Lighting", domain: "kichler.com" },
        documents: [
          {
            document_id: "avery-pendant-43911bk@01d4f6dc",
            sha256: "01d4f6dc" + "0".repeat(56),
            uri: "https://www.kichler.com/products/indoor-lighting/pendants/avery-pendant-43911bk",
            doc_type: "web_page",
            pages: 3,
            tables: 0,
          },
        ],
        requests_made: 0,
        notes: ["a stored document already covers 43911BK, so no request was made."],
      },
      source: { ...enrichSummary().source, kind: "document" },
    });
    await submitWith({ ok: true, data: enrichResponse({ summary }) });
    await resultRendered();

    expect(screen.getByText(/Already in the document library/i)).toBeTruthy();
    expect(screen.getByText(/0 requests · no model call/i)).toBeTruthy();
    expect(
      screen.getByText(/kichler\.com\/products\/indoor-lighting\/pendants/i),
    ).toBeTruthy();
  });

  it("says plainly when nothing was found, rather than letting a thin row imply a bug", async () => {
    await submitWith({ ok: true, data: enrichResponse() });
    await resultRendered();

    expect(screen.getByText(/No manufacturer document was found/i)).toBeTruthy();
    expect(screen.getByText(/what would change it/i)).toBeTruthy();
  });
});

describe("the cost, before it is spent", () => {
  it("is on the submit control", () => {
    form();
    expect(screen.getByRole("button", { name: /2 model calls/i })).toBeTruthy();
  });

  it("goes up when copy generation is enabled", () => {
    form();
    fireEvent.click(screen.getByLabelText(/Generate and claim-check copy/i));

    expect(screen.getByRole("button", { name: /3 model calls/i })).toBeTruthy();
  });

  it("says the deterministic alternative costs nothing", () => {
    form();
    expect(screen.getByText(/Publish page does the deterministic version/i)).toBeTruthy();
  });
});

describe("submitting", () => {
  it("sends the typed fields", async () => {
    await submitWith({ ok: true, data: enrichResponse() });

    expect(runEnrichment).toHaveBeenCalledTimes(1);
    expect(runEnrichment.mock.calls[0]![0]).toMatchObject({
      mpn: "PDSH4816AF",
      manufacturer: "Frigidaire",
      description: "24IN BUILT IN DISHWASHER",
      replace: false,
    });
  });

  it("keeps a part number with a separator intact", async () => {
    await submitWith({ ok: true, data: enrichResponse() }, () => {
      type(MPN, "52C3-5/8-UPC");
      type(MAKER, "Nibco");
    });

    // The slug addresses the result. Slugging here would enrich a product that does not exist.
    expect(runEnrichment.mock.calls[0]![0]).toMatchObject({ mpn: "52C3-5/8-UPC" });
  });

  it("does not submit while a run is in flight", async () => {
    let release: ((result: EnrichResult) => void) | null = null;
    runEnrichment.mockReturnValue(
      new Promise<EnrichResult>((resolve) => {
        release = resolve;
      }),
    );

    form();
    identify();
    type(DESC, "24IN BUILT IN DISHWASHER");
    fireEvent.click(submitButton());

    await waitFor(() => expect(locked()).toBe(true));
    expect(screen.getByText(/Classifying and extracting/i)).toBeTruthy();

    fireEvent.click(submitButton());
    expect(runEnrichment).toHaveBeenCalledTimes(1);

    release!({ ok: true, data: enrichResponse() });
    await resultRendered();
  });
});

/**
 * Every branch renders its own message, because every one has a different remedy.
 *
 * Collapsing these into a shared "it failed" panel would be the substantive regression: a conflict
 * needs a button, a credential problem is not something the person filling in the form can fix, and
 * a busy endpoint is worth retrying in ten seconds. One message cannot carry all three.
 */
describe("refusals", () => {
  it("lists the fields when there was nothing to read", async () => {
    await submitWith({
      ok: false,
      failure: {
        kind: "insufficient_input",
        message: "a description or a manufacturer URL is required",
        missing: ["description", "source_url"],
      },
    });

    expect(await screen.findByText(/There is nothing here to read/i)).toBeTruthy();
    expect(screen.getByText("source_url")).toBeTruthy();
    expect(screen.getByText(/Refused · nothing run/i)).toBeTruthy();
  });

  it("offers to replace an existing run rather than silently overwriting it", async () => {
    await submitWith({
      ok: false,
      failure: {
        kind: "conflict",
        message: "PDSH4816AF has already been enriched",
        sku: "PDSH4816AF",
        slug: "PDSH4816AF",
        enrichedAt: "2026-08-23T07:11:29+00:00",
      },
    });

    expect(
      await screen.findByRole("heading", { name: /already been enriched/i }),
    ).toBeTruthy();
    // Offered, never assumed: re-running discards the session a reviewer may already have worked.
    const replace = screen.getByRole("button", { name: /Re-run and replace/i });
    expect(screen.getByText(/discards the existing review session/i)).toBeTruthy();
    // And the alternative is a link to the run that already exists.
    expect(screen.getByRole("link", { name: /Open the existing run/i })).toBeTruthy();

    runEnrichment.mockResolvedValue({ ok: true, data: enrichResponse() });
    fireEvent.click(replace);

    await waitFor(() => expect(runEnrichment).toHaveBeenCalledTimes(2));
    expect(runEnrichment.mock.calls[1]![0]).toMatchObject({ replace: true });
  });

  it("reports a credential problem as an operator problem, with what the API said", async () => {
    await submitWith({
      ok: false,
      failure: {
        kind: "unavailable",
        message: "the model call could not be made",
        detail: "Unable to locate credentials",
        region: "us-east-2",
      },
    });

    expect(await screen.findByText(/The model is not available/i)).toBeTruthy();
    expect(screen.getByText("Unable to locate credentials")).toBeTruthy();
    expect(screen.getByText(/Region us-east-2/i)).toBeTruthy();
    // Not offered as something to fix in the form.
    expect(screen.queryByRole("button", { name: /Re-run and replace/i })).toBeNull();
  });

  it("keeps the fetch error and the URL when the document could not be retrieved", async () => {
    await submitWith({
      ok: false,
      failure: {
        kind: "source_unreachable",
        message: "https://example.com/ds.pdf returned HTTP 403 Forbidden",
        url: "https://example.com/ds.pdf",
      },
    });

    expect(await screen.findByText(/could not be fetched/i)).toBeTruthy();
    expect(screen.getByText(/403 Forbidden/)).toBeTruthy();
  });

  it("distinguishes a run already in flight", async () => {
    await submitWith({
      ok: false,
      failure: { kind: "busy", message: "an enrichment run is already in flight" },
    });

    expect(await screen.findByText(/Another run is in flight/i)).toBeTruthy();
  });

  it("says the API is unreachable rather than blaming the submission", async () => {
    await submitWith({
      ok: false,
      failure: { kind: "unreachable", message: "Could not reach the AXIOM API (fetch failed)" },
    });

    expect(await screen.findByText(/API is not reachable/i)).toBeTruthy();
  });

  it("says nothing was run", async () => {
    await submitWith({
      ok: false,
      failure: { kind: "busy", message: "an enrichment run is already in flight" },
    });

    expect(await screen.findByText("Nothing was run.")).toBeTruthy();
  });
});

describe("the result", () => {
  async function succeed(data = enrichResponse()) {
    await submitWith({ ok: true, data });
    await resultRendered();
  }

  it("reports the certificate, the values and the cost", async () => {
    await succeed();

    expect(screen.getByText("VERIFIED")).toBeTruthy();
    expect(screen.getByText("ec_d5f5a99e2e9a")).toBeTruthy();
    expect(screen.getByText(/0 publishable, 2 queued/)).toBeTruthy();
  });

  it("renders the stages as an execution, not a replay", async () => {
    await succeed();

    expect(screen.getByText(/Pipeline executed/i)).toBeTruthy();
    expect(screen.queryByText(/Nothing is executing now/i)).toBeNull();
  });

  it("explains a sparse delivery row rather than leaving it to look broken", async () => {
    // 29 of 252 columns is the correct answer, not a gap: the client's own ground truth leaves 173
    // blank. A caller who is not told that will read it as a failure.
    await succeed();

    expect(screen.getByText(/29 of 252 columns populated/)).toBeTruthy();
    expect(screen.getByText(/because nothing evidenced them/)).toBeTruthy();
  });

  it("offers both downloads, and says they re-run nothing", async () => {
    await succeed();

    expect(screen.getByRole("link", { name: /Excel workbook/i }).getAttribute("href")).toBe(
      "/api/enrich/delivery?sku=PDSH4816AF&output=xlsx",
    );
    expect(screen.getByRole("link", { name: /^CSV$/i }).getAttribute("href")).toBe(
      "/api/enrich/delivery?sku=PDSH4816AF&output=csv",
    );
    expect(screen.getByText(/downloading costs nothing/i)).toBeTruthy();
  });

  it("links the SKU into the Resolve queue and the Audit list", async () => {
    await succeed();

    expect(screen.getByRole("link", { name: /Open in Resolve/i }).getAttribute("href")).toBe(
      "/review/PDSH4816AF",
    );
    expect(screen.getByRole("link", { name: /View the certificate/i }).getAttribute("href")).toBe(
      "/certificates/PDSH4816AF",
    );
  });

  it("slugs the cross-links for a part number that needs it", async () => {
    await succeed(
      enrichResponse({
        sku: "52C3-5/8-UPC",
        slug: "52C3-5~2F8-UPC",
        summary: enrichSummary({ sku: "52C3-5/8-UPC" }),
      }),
    );

    expect(screen.getByRole("link", { name: /Open in Resolve/i }).getAttribute("href")).toBe(
      "/review/52C3-5~2F8-UPC",
    );
    expect(screen.getByRole("link", { name: /Excel workbook/i }).getAttribute("href")).toContain(
      "sku=52C3-5~2F8-UPC",
    );
  });

  it("says the submission was the source, and that this is the weaker claim", async () => {
    await succeed();

    expect(
      screen.getByText(/records what was supplied rather than what a manufacturer published/i),
    ).toBeTruthy();
  });

  it("flags a distributor in the manufacturer field rather than publishing it", async () => {
    await succeed(
      enrichResponse({
        summary: enrichSummary({
          manufacturer: {
            name: "Appliance Dealers Cooperative",
            supplier_code: "APPDE",
            looks_like_a_distributor: true,
            publishable_as_manufacturer: false,
          },
        }),
      }),
    );

    expect(screen.getByText(/Looks like a distributor or buying co-op/i)).toBeTruthy();
  });

  it("distinguishes a fallback class from a classified one", async () => {
    await succeed(enrichResponse({ summary: enrichSummary({ class_from_fallback: true }) }));

    expect(screen.getByText(/Classification abstained/i)).toBeTruthy();
    expect(screen.getByText(/a different claim from having classified it/i)).toBeTruthy();
  });

  it("refuses to report a quality score for an unclassified run", async () => {
    // The certificate genuinely carries `completeness: 1.0` here, because `fill_rate` over an empty
    // required set is vacuously perfect — no required attribute is missing when none is required. On
    // a record with zero values that is the most misleading number the page could show, so it is not
    // shown. Same treatment the Resolve queue already gives an unclassified SKU.
    const summary = enrichSummary({
      class_code: null,
      values: { total: 0, publishable: 0, needing_review: 0 },
      certificate: {
        ...enrichSummary().certificate,
        quality_index: {
          ...enrichSummary().certificate.quality_index,
          completeness: 1,
          composite: 0.6,
        },
      },
    });
    await succeed(enrichResponse({ summary }));

    expect(screen.getByText("unmeasured")).toBeTruthy();
    expect(screen.getByText(/no denominator to score completeness against/i)).toBeTruthy();
    // The vacuous figure must not appear anywhere on the page.
    expect(screen.queryByText(/completeness 100%/i)).toBeNull();
    expect(screen.queryByText("60%")).toBeNull();
  });

  it("says the queue is the correct opening state rather than a failure", async () => {
    await succeed();

    expect(screen.getByText(/2 of 2 values need a decision/)).toBeTruthy();
    expect(screen.getByText(/correct opening state/i)).toBeTruthy();
  });

  it("offers a clean slate rather than leaving the last run on screen", async () => {
    await succeed();

    fireEvent.click(screen.getByRole("button", { name: /Start another/i }));

    expect(screen.queryByText(/Pipeline executed/i)).toBeNull();
    expect(locked()).toBe(true);
  });
});
