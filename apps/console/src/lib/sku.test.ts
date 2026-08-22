/**
 * The slug contract, pinned on both sides.
 *
 * Every case here is also asserted against `axiom.core.naming.sku_slug` in
 * `tests/test_sku_naming.py`. The two implementations exist because one names files in Python and
 * the other builds URLs in TypeScript; keeping the same table in front of both is what stops them
 * drifting into a link that 404s or a review decision written to the wrong file.
 */

import { describe, expect, it } from "vitest";

import { certificateHref, reviewHref, skuSlug } from "./sku";

// [sku, slug] — mirrored verbatim in tests/test_sku_naming.py.
const CASES: ReadonlyArray<readonly [string, string]> = [
  // Unchanged, and that is load-bearing: every artifact already on disk must keep resolving.
  ["BA-100-075", "BA-100-075"],
  ["T-113-100", "T-113-100"],
  ["3MABR-7100075678", "3MABR-7100075678"],
  ["a.b_c-1", "a.b_c-1"],
  // Real part numbers from the client's item master.
  ["52C3-5/8-UPC", "52C3-5~2F8-UPC"],
  ["72171-3/4-1W-UPC", "72171-3~2F4-1W-UPC"],
  ["SHOP/4X2/840/V1", "SHOP~2F4X2~2F840~2FV1"],
  ["MAG:2044-230-1", "MAG~3A2044-230-1"],
  ["2/2/4 UD ALUM", "2~2F2~2F4~20UD~20ALUM"],
  ["FS C01 2004S", "FS~20C01~202004S"],
  // The escape character itself, so the mapping stays injective.
  ["A~B", "A~7EB"],
  // A leading dot would make a hidden file; `.` and `..` would name a directory.
  [".hidden", "~2Ehidden"],
  ["..", "~2E."],
  // Windows device names are not creatable whatever the extension.
  ["CON", "~43ON"],
  ["com1", "~63om1"],
  // Non-ASCII escapes as UTF-8 bytes, not as a code point.
  ["\u00c5-1", "~C3~85-1"],
];

describe("skuSlug", () => {
  it.each(CASES)("maps %j to %j", (sku, slug) => {
    expect(skuSlug(sku)).toBe(slug);
  });

  it("is injective across the fixture table", () => {
    // The property that matters more than any single mapping: two products cannot collide on one
    // file, because a collision would silently merge their review history.
    const slugs = CASES.map(([sku]) => skuSlug(sku));
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("emits only characters that are safe unencoded in a URL path", () => {
    for (const [sku] of CASES) {
      expect(skuSlug(sku)).toMatch(/^[A-Za-z0-9\-_.~]+$/);
    }
  });

  it("is not idempotent, deliberately", () => {
    // Re-slugging escapes the escape character. Callers holding a slug must not slug it again —
    // which is why the API validates rather than re-derives. Asserted so nobody "fixes" it.
    expect(skuSlug(skuSlug("52C3-5/8-UPC"))).toBe("52C3-5~7E2F8-UPC");
  });

  it("refuses an empty SKU rather than inventing a name", () => {
    expect(() => skuSlug("")).toThrow(/empty SKU/);
    expect(() => skuSlug("   ")).toThrow(/empty SKU/);
  });
});

describe("hrefs", () => {
  it("keeps a slash-bearing part number inside one path segment", () => {
    expect(reviewHref("52C3-5/8-UPC")).toBe("/review/52C3-5~2F8-UPC");
    expect(certificateHref("52C3-5/8-UPC")).toBe("/certificates/52C3-5~2F8-UPC");
    // One segment after the route prefix. Two would be the bug.
    expect(reviewHref("SHOP/4X2/840/V1").split("/")).toHaveLength(3);
  });
});
