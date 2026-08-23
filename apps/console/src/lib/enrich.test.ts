/**
 * The request builder and the failure mapping for `POST /api/enrich`.
 *
 * These two functions are where the enrichment screen can go wrong quietly. Everything else on that
 * page is rendering; these decide what gets sent to an endpoint that spends money, and how a refusal
 * is explained to the person who caused it.
 *
 * The failure mapping gets most of the attention because the status code alone is not the answer. A
 * 422 is both "you filled in neither field" and "that URL is not https", and those belong in
 * different places on the form with different remedies. Collapsing them into one message would leave
 * somebody re-reading a paragraph to work out which field to touch.
 */

import { describe, expect, it } from "vitest";

import { enrichBody, isSubmittable, readEnrichFailure } from "./enrich";

const MPN = "PDSH4816AF";
const MANUFACTURER = "Frigidaire";
const DESCRIPTION = "24IN BUILT IN DISHWASHER STAINLESS STEEL 47DBA";

/** A `Response` carrying a FastAPI-shaped error body. */
function failure(status: number, detail: unknown): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("enrichBody", () => {
  it("sends the two required fields trimmed", () => {
    const body = enrichBody({ mpn: "  PDSH4816AF ", manufacturer: " Frigidaire  ", description: DESCRIPTION });
    expect(body.mpn).toBe(MPN);
    expect(body.manufacturer).toBe(MANUFACTURER);
  });

  it("omits a blank optional field rather than sending an empty string", () => {
    // `""` and "not supplied" are different claims, and the API's own check is
    // `description or source_url`. An empty string satisfies neither and reads as a filled-in field.
    const body = enrichBody({
      mpn: MPN,
      manufacturer: MANUFACTURER,
      description: DESCRIPTION,
      sourceUrl: "   ",
      brand: "",
    });
    expect(body).not.toHaveProperty("source_url");
    expect(body).not.toHaveProperty("brand");
    expect(body.description).toBe(DESCRIPTION);
  });

  it("omits the booleans when they are off, so a default is not restated as a choice", () => {
    const body = enrichBody({ mpn: MPN, manufacturer: MANUFACTURER, description: DESCRIPTION });
    expect(body).not.toHaveProperty("include_optional");
    expect(body).not.toHaveProperty("generate_copy");
    expect(body).not.toHaveProperty("replace");
  });

  it("maps the camelCase input onto the API's snake_case fields", () => {
    const body = enrichBody({
      mpn: MPN,
      manufacturer: MANUFACTURER,
      sourceUrl: "https://example.com/ds.pdf",
      classCode: "APP.KIT.DISHWASHER.BUILTIN",
      includeOptional: true,
      generateCopy: true,
      replace: true,
      riskBudget: 0.1,
    });
    expect(body).toMatchObject({
      source_url: "https://example.com/ds.pdf",
      class_code: "APP.KIT.DISHWASHER.BUILTIN",
      include_optional: true,
      generate_copy: true,
      replace: true,
      risk_budget: 0.1,
    });
  });

  it("keeps a part number with a separator intact", () => {
    // The slug is for addressing the result, not for submitting it. Slugging here would enrich a
    // product that does not exist.
    const body = enrichBody({ mpn: "52C3-5/8-UPC", manufacturer: "Nibco", description: "5/8IN" });
    expect(body.mpn).toBe("52C3-5/8-UPC");
  });
});

describe("isSubmittable", () => {
  it("accepts a part number and a manufacturer on their own", () => {
    // The change retrieval bought. Those two fields are the whole submission: the library is
    // consulted, then the manufacturer's own site is searched using the search form it published.
    // Demanding a description as well would demand the thing retrieval exists to find.
    expect(isSubmittable({ mpn: MPN, manufacturer: MANUFACTURER })).toBe(true);
  });

  it("refuses them with retrieval off, when there is nowhere to look", () => {
    expect(isSubmittable({ mpn: MPN, manufacturer: MANUFACTURER, retrieve: false })).toBe(false);
  });

  it("accepts them with retrieval off once there is something to read", () => {
    expect(
      isSubmittable({
        mpn: MPN,
        manufacturer: MANUFACTURER,
        description: DESCRIPTION,
        retrieve: false,
      }),
    ).toBe(true);
  });

  it("accepts a description on its own", () => {
    expect(isSubmittable({ mpn: MPN, manufacturer: MANUFACTURER, description: DESCRIPTION })).toBe(
      true,
    );
  });

  it("accepts a URL on its own", () => {
    expect(
      isSubmittable({
        mpn: MPN,
        manufacturer: MANUFACTURER,
        sourceUrl: "https://example.com/ds.pdf",
      }),
    ).toBe(true);
  });

  it("refuses when a required field is only whitespace", () => {
    expect(isSubmittable({ mpn: "  ", manufacturer: MANUFACTURER, description: DESCRIPTION })).toBe(
      false,
    );
    expect(isSubmittable({ mpn: MPN, manufacturer: " ", description: DESCRIPTION })).toBe(false);
  });

  it("refuses whitespace-only content with retrieval off", () => {
    expect(
      isSubmittable({
        mpn: MPN,
        manufacturer: MANUFACTURER,
        description: "   ",
        sourceUrl: "  ",
        retrieve: false,
      }),
    ).toBe(false);
  });
});

describe("the retrieve flag on the wire", () => {
  it("is omitted when on, since that is the API's default too", () => {
    expect(enrichBody({ mpn: MPN, manufacturer: MANUFACTURER })).not.toHaveProperty("retrieve");
    expect(
      enrichBody({ mpn: MPN, manufacturer: MANUFACTURER, retrieve: true }),
    ).not.toHaveProperty("retrieve");
  });

  it("is sent only to turn it off", () => {
    expect(enrichBody({ mpn: MPN, manufacturer: MANUFACTURER, retrieve: false })).toMatchObject({
      retrieve: false,
    });
  });
});

describe("readEnrichFailure", () => {
  it("names the fields when there was nothing to read", async () => {
    const result = await readEnrichFailure(
      failure(422, {
        error: "insufficient_input",
        message: "a description or a manufacturer URL is required",
        missing: ["description", "source_url"],
      }),
    );

    expect(result.kind).toBe("insufficient_input");
    if (result.kind !== "insufficient_input") throw new Error("wrong branch");
    expect(result.missing).toEqual(["description", "source_url"]);
  });

  it("separates an insecure URL from a missing field, though both are 422", async () => {
    const result = await readEnrichFailure(
      failure(422, {
        error: "insecure_url",
        message: "the manufacturer URL must start with https://",
        field: "source_url",
      }),
    );

    expect(result.kind).toBe("invalid");
    if (result.kind !== "invalid") throw new Error("wrong branch");
    expect(result.field).toBe("source_url");
  });

  it("carries the existing run's timestamp on a conflict, so the offer to replace is informed", async () => {
    const result = await readEnrichFailure(
      failure(409, {
        error: "already_enriched",
        message: "PDSH4816AF has already been enriched",
        sku: MPN,
        slug: MPN,
        enriched_at: "2026-08-23T07:11:29Z",
      }),
    );

    expect(result.kind).toBe("conflict");
    if (result.kind !== "conflict") throw new Error("wrong branch");
    expect(result.slug).toBe(MPN);
    expect(result.enrichedAt).toBe("2026-08-23T07:11:29Z");
  });

  it("tolerates a conflict with no readable timestamp rather than rendering undefined", async () => {
    const result = await readEnrichFailure(
      failure(409, { error: "already_enriched", message: "x", sku: MPN, slug: MPN }),
    );
    expect(result.kind).toBe("conflict");
    if (result.kind !== "conflict") throw new Error("wrong branch");
    expect(result.enrichedAt).toBeNull();
  });

  it("keeps the upstream fetch error verbatim when the source could not be retrieved", async () => {
    // A caller looking at a link that works in their browser needs to know what the server saw. A 403
    // from a portal reads very differently from a DNS failure.
    const result = await readEnrichFailure(
      failure(502, {
        error: "source_unreachable",
        message: "https://example.com/ds.pdf returned HTTP 403 Forbidden",
        url: "https://example.com/ds.pdf",
      }),
    );

    expect(result.kind).toBe("source_unreachable");
    if (result.kind !== "source_unreachable") throw new Error("wrong branch");
    expect(result.message).toContain("403");
    expect(result.url).toBe("https://example.com/ds.pdf");
  });

  it("reports a credential problem as an operator problem, not a form error", async () => {
    const result = await readEnrichFailure(
      failure(503, {
        error: "model_unavailable",
        message: "the model call could not be made",
        detail: "Unable to locate credentials",
        region: "us-east-2",
      }),
    );

    expect(result.kind).toBe("unavailable");
    if (result.kind !== "unavailable") throw new Error("wrong branch");
    expect(result.detail).toBe("Unable to locate credentials");
    expect(result.region).toBe("us-east-2");
  });

  it("distinguishes a run already in flight, which is worth retrying", async () => {
    const result = await readEnrichFailure(
      failure(429, "an enrichment run is already in flight"),
    );
    expect(result.kind).toBe("busy");
  });

  it("flattens FastAPI's own validation errors into one sentence", async () => {
    // The form already checks the fields, so a per-field render here would duplicate it.
    const result = await readEnrichFailure(
      failure(422, [
        { loc: ["body", "manufacturer"], msg: "Field required" },
        { loc: ["body", "description"], msg: "String should have at most 4000 characters" },
      ]),
    );

    expect(result.kind).toBe("invalid");
    expect(result.message).toContain("Field required");
    expect(result.message).toContain("4000");
  });

  it("falls back to the status when the body is not JSON at all", async () => {
    const response = new Response("<html>502 Bad Gateway</html>", {
      status: 500,
      statusText: "Internal Server Error",
    });
    const result = await readEnrichFailure(response);

    expect(result.kind).toBe("error");
    if (result.kind !== "error") throw new Error("wrong branch");
    expect(result.status).toBe(500);
    expect(result.message).toContain("500");
  });

  it("does not mistake an unrecognised structured error for a form problem", async () => {
    // A future error key must not be silently rendered as an invalid field, because the remedy would
    // be wrong. Falling through on the status is the honest default.
    const result = await readEnrichFailure(
      failure(500, { error: "something_new", message: "the disk is full" }),
    );
    expect(result.kind).toBe("error");
  });
});
