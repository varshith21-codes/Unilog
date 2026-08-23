/**
 * Shaping an enrichment submission. Pure, and importable from the browser.
 *
 * **This is a separate module for a build reason, not a taste one. Do not merge it back into
 * `enrich.ts`.**
 *
 * `enrich.ts` imports `API_BASE` from `data.ts`, and `data.ts` imports `node:fs/promises` for the
 * offline fixture fallback. So any *value* imported from `enrich.ts` drags Node's filesystem module
 * into whatever bundle imports it — which fails the client build outright:
 *
 *     the chunking context (unknown) does not support external modules (request: node:fs/promises)
 *
 * Types are fine to import from there, because `import type` is erased before bundling; that is how
 * `delivery-upload.tsx` gets away with it. Functions are not. `EnrichForm` needs `isSubmittable` at
 * runtime to decide whether the submit button is live, so it lives here with nothing behind it.
 *
 * The same check runs in three places on purpose: here for the button, again in the server action
 * (which is a public endpoint of its own, so "the button was disabled" is not a validation), and
 * finally in `EnrichmentRequest.__post_init__`, which is the only one that can actually stop a run.
 * They agree because the rule is stated once per layer that can be reached independently, not
 * because one of them is trusted.
 */

/** Mirrors `apps.api.main.EnrichRequest`. */
export interface EnrichInput {
  mpn: string;
  manufacturer: string;
  description?: string;
  sourceUrl?: string;
  brand?: string;
  classCode?: string;
  includeOptional?: boolean;
  generateCopy?: boolean;
  riskBudget?: number;
  /** Overwrite an existing run for this part number, and the session behind it. */
  replace?: boolean;
  /**
   * Look for the manufacturer's own document when no URL is given. Defaults to on.
   *
   * This is what makes a part number and a manufacturer name sufficient, so `undefined` and `true`
   * mean the same thing here and only an explicit `false` re-imposes the description-or-URL rule.
   */
  retrieve?: boolean;
}

/**
 * Build the request body, trimming and dropping what was left blank.
 *
 * Empty strings are omitted rather than sent, because `""` and "not supplied" are different claims
 * and the API's own check is `description or source_url`. Sending `description: ""` would satisfy
 * neither and read as a field somebody filled in.
 *
 * The part number is **not** slugged. The slug addresses a result; slugging here would submit a part
 * number that does not exist.
 */
export function enrichBody(input: EnrichInput): Record<string, unknown> {
  const trim = (value: string | undefined) => {
    const text = (value ?? "").trim();
    return text.length > 0 ? text : undefined;
  };

  const body: Record<string, unknown> = {
    mpn: input.mpn.trim(),
    manufacturer: input.manufacturer.trim(),
  };

  const description = trim(input.description);
  const sourceUrl = trim(input.sourceUrl);
  const brand = trim(input.brand);
  const classCode = trim(input.classCode);

  if (description !== undefined) body.description = description;
  if (sourceUrl !== undefined) body.source_url = sourceUrl;
  if (brand !== undefined) body.brand = brand;
  if (classCode !== undefined) body.class_code = classCode;
  // Omitted when off rather than sent as false, so the request states choices rather than restating
  // every default.
  if (input.includeOptional) body.include_optional = true;
  if (input.generateCopy) body.generate_copy = true;
  if (input.replace) body.replace = true;
  // Sent only to turn it off, since on is the API's default too.
  if (input.retrieve === false) body.retrieve = false;
  if (typeof input.riskBudget === "number") body.risk_budget = input.riskBudget;

  return body;
}

/**
 * Whether this submission can produce anything, checked before it is sent.
 *
 * The interesting half is the last clause. A part number and a manufacturer are the two required
 * fields and they are still not enough: a part number identifies a product but does not describe
 * one, so with neither a description nor a URL there is nothing to classify and nothing to cite, and
 * the run would spend two model calls to return an identity-only row.
 */
export function isSubmittable(input: EnrichInput): boolean {
  const body = enrichBody(input);
  const identified =
    typeof body.mpn === "string" &&
    body.mpn.length > 0 &&
    typeof body.manufacturer === "string" &&
    body.manufacturer.length > 0;

  // With retrieval on, those two fields are the whole submission: the library is consulted and then
  // the manufacturer's own site is searched using the search form it published. Demanding a
  // description as well would be demanding the thing retrieval exists to find.
  //
  // With it off there is nowhere to look, so something to read is required again.
  if (input.retrieve === false) {
    return identified && (body.description !== undefined || body.source_url !== undefined);
  }
  return identified;
}
