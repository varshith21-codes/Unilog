import { describe, expect, it } from "vitest";

import {
  classificationAbstention,
  reviewOrder,
  unresolvedConflicts,
  variantGroupFor,
  variantGroups,
} from "@/lib/data";
import { composite, type SkuBundle } from "@/lib/types";
import {
  qualityIndex,
  stageBundle,
  triageBundle,
  variantBundle,
  variantValue,
} from "@/test/factories";

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

/**
 * Variant grouping, where the shape of the data is genuinely counter-intuitive.
 *
 * `explode` emits the reference SKU as one of the children with `parent_sku: null`, and points every
 * sibling at it. So a series is not "a parent plus its children" — the parent *is* a child, and the
 * grouping key is `parent_sku ?? sku`. Getting that wrong splits every series in two: the reference
 * in one group of one, its siblings in another.
 */
describe("variantGroups", () => {
  const series = () => [
    variantBundle({ sku: "BA-100-050", parentSku: null }),
    variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" }),
    variantBundle({ sku: "BA-100-100", parentSku: "BA-100-050" }),
  ];

  it("groups the reference together with its siblings rather than beside them", () => {
    const groups = variantGroups(series());

    expect(groups).toHaveLength(1);
    expect(groups[0]!.seriesSku).toBe("BA-100-050");
    expect(groups[0]!.members.map((member) => member.bundle.sku)).toEqual([
      "BA-100-050",
      "BA-100-075",
      "BA-100-100",
    ]);
  });

  it("marks the reference without promoting it to the top of the list", () => {
    // It is special in how extraction happened, not in the catalogue. Ordering it first would imply
    // a hierarchy the data does not have — every row here is an equally orderable part number.
    const groups = variantGroups([
      variantBundle({ sku: "BA-100-125", parentSku: "BA-100-100" }),
      variantBundle({ sku: "BA-100-100", parentSku: null }),
    ]);

    const members = groups[0]!.members;
    expect(members.map((member) => member.bundle.sku)).toEqual(["BA-100-100", "BA-100-125"]);
    expect(members.map((member) => member.isReference)).toEqual([true, false]);
  });

  it("returns nothing for a catalogue of standalone products", () => {
    // The state both committed bundles are in. An empty result must mean "no series", not "one
    // group per SKU" — otherwise every ordinary catalogue renders a wall of one-member series.
    expect(
      variantGroups([
        variantBundle({ sku: "BA-100-075", parentSku: null }),
        variantBundle({ sku: "T-113-100", parentSku: null }),
      ]),
    ).toEqual([]);
  });

  it("keeps a lone surviving child whose reference was never persisted", () => {
    // Grouping on member count would drop this. The `parent_sku` pointer is the positive signal
    // that explosion happened, and one child of an absent reference is still a real series.
    const groups = variantGroups([variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" })]);

    expect(groups).toHaveLength(1);
    expect(groups[0]!.referencePresent).toBe(false);
    expect(groups[0]!.members).toHaveLength(1);
  });

  it("reports the reference as present when its own bundle is in the dataset", () => {
    expect(variantGroups(series())[0]!.referencePresent).toBe(true);
  });

  it("keeps two unrelated series apart", () => {
    const groups = variantGroups([
      variantBundle({ sku: "BA-100-050", parentSku: null }),
      variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" }),
      variantBundle({ sku: "77C-103", parentSku: null }),
      variantBundle({ sku: "77C-104", parentSku: "77C-103" }),
    ]);

    expect(groups.map((group) => group.seriesSku)).toEqual(["77C-103", "BA-100-050"]);
    expect(groups.map((group) => group.members.length)).toEqual([2, 2]);
  });

  it("counts values read from a variant's own table row separately from inherited ones", () => {
    // The claim the whole feature rests on. If these collapsed into one number, "five products" and
    // "one product copied five times" would look identical on screen.
    const groups = variantGroups([
      variantBundle({
        sku: "BA-100-075",
        parentSku: "BA-100-050",
        values: [
          variantValue({ code: "nominal_size", display: "DN20", fromCell: "t1:r2:c1" }),
          variantValue({ code: "cv_rating", display: "38", fromCell: "t1:r2:c4" }),
          variantValue({ code: "body_material", display: "Bronze", inheritedBy: "BA-100-075" }),
        ],
        inapplicable: 1,
      }),
    ]);

    const member = groups[0]!.members[0]!;
    expect(member.fromOwnRow).toBe(2);
    expect(member.inherited).toBe(1);
    expect(member.inapplicable).toBe(1);
  });

  it("counts only gaps the source withheld by size, not every gap", () => {
    // `accept_as_not_applicable` is produced by exactly one branch of `explode`. Counting all gaps
    // would report a missing carton weight as a size-scoped exclusion.
    const bundle = variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050", inapplicable: 2 });
    (bundle.gaps as { recommended_action: string }[]).push({
      recommended_action: "request_from_supplier",
    });

    expect(variantGroups([bundle])[0]!.members[0]!.inapplicable).toBe(2);
  });

  it("treats a bundle with no parent_sku field as standalone, not as its own parent", () => {
    /*
     * The shape of the checked-in offline fixture, which was generated before `parent_sku` was
     * projected. `undefined !== null` is true, so a naive check classifies every SKU in that fixture
     * as a variant and renders five one-member series where there is no series at all.
     */
    const legacy = variantBundle({ sku: "BA-100-025" });
    delete (legacy.record as { parent_sku?: string | null }).parent_sku;

    expect(variantGroups([legacy])).toEqual([]);
    expect(variantGroupFor(legacy, [legacy])).toBeNull();
  });

  it("does not mutate the array it was given", () => {
    const input = series();
    variantGroups(input);

    expect(input.map((bundle) => bundle.sku)).toEqual([
      "BA-100-050",
      "BA-100-075",
      "BA-100-100",
    ]);
  });
});

describe("variantGroupFor", () => {
  const catalogue = [
    variantBundle({ sku: "BA-100-050", parentSku: null }),
    variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" }),
    variantBundle({ sku: "T-113-100", parentSku: null }),
  ];

  it("finds the series from a child", () => {
    expect(variantGroupFor(catalogue[1]!, catalogue)?.seriesSku).toBe("BA-100-050");
  });

  it("finds the same series from the reference itself", () => {
    // The reference carries no `parent_sku`, so a naive lookup returns nothing for the one SKU whose
    // spec block the whole series was read from.
    expect(variantGroupFor(catalogue[0]!, catalogue)?.seriesSku).toBe("BA-100-050");
  });

  it("returns null for a standalone product rather than a series of one", () => {
    expect(variantGroupFor(catalogue[2]!, catalogue)).toBeNull();
  });
});

/**
 * A SKU can end up with no class for two reasons that need opposite work, and the console showed one
 * message for both.
 *
 * The message said retrieval matched no class in the schema and the remedy is to add a class
 * definition under `schema/classes/`. For a heater kit or an insulated water bottle that is exactly
 * right. For a "Satco Tape Light", a "Voltage Detector w/ LED" or a "Coil Roofing - Nailer Kit" it
 * was wrong twice: retrieval DID match classes — two of them, named and scored, already carried in
 * the bundle — and a thirty-third class definition would not resolve a tie between two that exist.
 *
 * These tests exist because the failure is invisible. Nothing errors; a reviewer is simply sent to
 * write a schema file when what was needed was ten seconds of judgement.
 */
describe("classificationAbstention", () => {
  const unclassified = (
    method: string,
    candidates: { code: string; score: number; path_text: string }[],
  ) =>
    ({
      sku: "SKU",
      class_code: null,
      classification_summary: { method, abstained: true },
      classification_candidates: candidates,
    }) as unknown as SkuBundle;

  it("returns null for a SKU that has a class", () => {
    expect(classificationAbstention(stageBundle())).toBeNull();
  });

  it("reports a genuine coverage gap as no-candidate", () => {
    const result = classificationAbstention(unclassified("no_viable_candidate", []));
    expect(result?.kind).toBe("no-candidate");
    expect(result?.candidates).toEqual([]);
  });

  it("reports a near-tie as ambiguous, carrying what was in contention", () => {
    const result = classificationAbstention(
      unclassified("ambiguous_no_model", [
        { code: "LGT.LMP.GEN", score: 0.2158, path_text: "Electrical > Lighting > Lamps" },
        { code: "BLD.TAPE.GEN", score: 0.1751, path_text: "Building Materials > Tapes" },
      ]),
    );

    expect(result?.kind).toBe("ambiguous");
    // The leader first, because it is the reviewer's answer in the great majority of these.
    expect(result?.candidates.map((candidate) => candidate.code)).toEqual([
      "LGT.LMP.GEN",
      "BLD.TAPE.GEN",
    ]);
  });

  it("reads more than one contender as ambiguous even without the method string", () => {
    // Keyed on the candidates as well as the method so a bundle written by an older pipeline
    // version still renders the more useful of the two messages rather than the misleading one.
    const result = classificationAbstention(
      unclassified("something_else", [
        { code: "A", score: 0.2, path_text: "A" },
        { code: "B", score: 0.19, path_text: "B" },
      ]),
    );
    expect(result?.kind).toBe("ambiguous");
  });
});
