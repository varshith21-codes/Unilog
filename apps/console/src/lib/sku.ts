/**
 * One SKU, one safe URL path segment. The TypeScript half of `axiom.core.naming`.
 *
 * Industrial part numbers are not identifiers. The client's own item master carries
 * `52C3-5/8-UPC`, `SHOP/4X2/840/V1` and `MAG:2044-230-1`, and a fractional size written with a
 * solidus is completely ordinary in this domain. Interpolating one straight into `/review/${sku}`
 * produces `/review/52C3-5/8-UPC` — two path segments, no matching route, a 404 on a product that
 * exists. `encodeURIComponent` does not fix it either: `%2F` inside a path is re-decoded by
 * browsers and proxies, which puts the separator back.
 *
 * So a SKU is addressed by its slug, which escapes anything outside the unreserved set as `~XX`.
 * The same slug names the artifact on disk, which is what lets the API resolve a session for a
 * part number it could previously only reject.
 *
 * **This must agree with `sku_slug` in `packages/axiom/core/naming.py` exactly.** Two independent
 * implementations of one identifier scheme is a real risk, and the honest mitigation is that
 * `sku.test.ts` pins the same cases both sides claim. A disagreement means a link that 404s or a
 * decision written to the wrong file, so it is worth the duplication being visible rather than
 * hidden behind a generated constant.
 */

/** Unreserved in RFC 3986 minus `~`, which is the escape introducer. */
const SAFE = new Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.");

/** Reserved device names on Windows, where `CON.json` is not a creatable file. */
const WINDOWS_RESERVED = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);

const encoder = new TextEncoder();

function escapeChar(char: string): string {
  // The UTF-8 bytes, not the code point, so a non-ASCII part number slugs identically in Python.
  return Array.from(encoder.encode(char))
    .map((byte) => `~${byte.toString(16).toUpperCase().padStart(2, "0")}`)
    .join("");
}

/**
 * A URL- and filename-safe single path segment for `sku`.
 *
 * A part number needing no escaping is returned unchanged, so every existing link and artifact
 * (`BA-100-075`) keeps working.
 */
export function skuSlug(sku: string): string {
  if (!sku || sku.trim() === "") {
    throw new Error("cannot derive a slug from an empty SKU");
  }

  let slug = "";
  // Iterating the string yields whole code points, so a surrogate pair is escaped once rather
  // than twice into two invalid halves.
  for (const char of sku) {
    slug += SAFE.has(char) ? char : escapeChar(char);
  }

  if (slug.startsWith(".")) slug = `~2E${slug.slice(1)}`;

  if (WINDOWS_RESERVED.has(slug.toUpperCase())) {
    slug = `~${slug.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0")}${slug.slice(1)}`;
  }

  return slug;
}

/** The resolve workspace URL for a SKU. */
export function reviewHref(sku: string): string {
  return `/review/${skuSlug(sku)}`;
}

/** The audit artifact URL for a SKU. */
export function certificateHref(sku: string): string {
  return `/certificates/${skuSlug(sku)}`;
}
