import { describe, expect, it } from "vitest";

import { reviewOrder, unresolvedConflicts } from "@/lib/data";
import { composite } from "@/lib/types";
import { qualityIndex, triageBundle } from "@/test/factories";

/**
 * Triage order decides what a reviewer sees first, and getting it wrong is expensive in a way that
 * is invisible: nothing errors, the queue is simply in the wrong order and the important finding sits
 * on page two.
 */
describe("reviewOrder", () => {
  it("puts an unresolved cross-source conflict above a blocking validation failure", () => {
    // L4 is the only finding where the system holds two contradictory answers and deliberately
    // refused to pick. Everything below it is one answer it is merely unsure about.
    const ordered = reviewOrder([
      triageBundle({ sku: "HAS-FAILURES", failures: 5 }),
      triageBundle({ sku: "HAS-CONFLICT", unresolved: 1 }),
    ]);

    expect(ordered.map((bundle) => bundle.sku)).toEqual(["HAS-CONFLICT", "HAS-FAILURES"]);
  });

  it("falls back to blocking failures, then required gaps, then queued values", () => {
    const ordered = reviewOrder([
      triageBundle({ sku: "QUEUED", needingReview: 9 }),
      triageBundle({ sku: "GAPS", gapsRequired: 2 }),
      triageBundle({ sku: "FAILURES", failures: 1 }),
    ]);

    expect(ordered.map((bundle) => bundle.sku)).toEqual(["FAILURES", "GAPS", "QUEUED"]);
  });

  it("orders more conflicts before fewer", () => {
    const ordered = reviewOrder([
      triageBundle({ sku: "ONE", unresolved: 1 }),
      triageBundle({ sku: "THREE", unresolved: 3 }),
    ]);

    expect(ordered.map((bundle) => bundle.sku)).toEqual(["THREE", "ONE"]);
  });

  it("is stable and alphabetical when nothing distinguishes two SKUs", () => {
    const ordered = reviewOrder([triageBundle({ sku: "B-2" }), triageBundle({ sku: "A-1" })]);

    expect(ordered.map((bundle) => bundle.sku)).toEqual(["A-1", "B-2"]);
  });

  it("does not mutate the array it was given", () => {
    const input = [triageBundle({ sku: "SECOND" }), triageBundle({ sku: "FIRST", failures: 1 })];
    reviewOrder(input);

    expect(input[0]!.sku).toBe("SECOND");
  });

  it("treats a SKU with no L4 run as having no conflicts, not as unknown", () => {
    expect(unresolvedConflicts(triageBundle({ sku: "NO-L4" }))).toBe(0);
    expect(unresolvedConflicts(triageBundle({ sku: "CLEAN-L4", unresolved: 0 }))).toBe(0);
    expect(unresolvedConflicts(triageBundle({ sku: "CONFLICTED", unresolved: 2 }))).toBe(2);
  });
});

/**
 * `composite` used to reimplement the Python weighting in TypeScript, so the formula lived in two
 * languages and could disagree with itself. It is now a reader.
 */
describe("composite", () => {
  it("returns the value the pipeline computed rather than recomputing it", () => {
    // A composite that could not be derived from these dimensions and weights. If this function
    // ever starts calculating again, this test fails — which is the point.
    const index = qualityIndex({ composite: 0.1234 });

    expect(composite(index)).toBe(0.1234);
  });

  it("does not depend on weights being present", () => {
    const index = qualityIndex({ composite: 0.9, weights: undefined });

    expect(composite(index)).toBe(0.9);
  });
});
