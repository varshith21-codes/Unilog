/**
 * Tests for the cross-reference panel.
 *
 * These target the branches where being wrong would mislead a merchandiser about their own data,
 * not the layout. Three distinctions carry the screen:
 *
 * - **indeterminate must not read as a rejection.** Nothing is known to differ; something could not
 *   be established. A UI that rendered it as "not equivalent" would send someone hunting for a
 *   different valve when the real problem was a missing attribute.
 * - **a verdict must never appear without its basis.** "Drop-in on eleven attributes" and "drop-in
 *   on three" are different strengths of the same word.
 * - **hand-authored records must not read as a measurement**, the same way the offline fixture and
 *   the L4 dry-run marker must not.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EquivalencePanel } from "@/components/equivalence-panel";
import {
  equivalenceCandidate,
  equivalenceComparison,
  equivalenceView,
} from "@/test/factories";

afterEach(cleanup);

describe("headline verdict", () => {
  it("counts substitutes when any exist", () => {
    render(<EquivalencePanel view={equivalenceView()} />);
    expect(screen.getByText("1 substitute")).toBeTruthy();
  });

  it("pluralises more than one substitute", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({ candidate_sku: "A-1" }),
        equivalenceCandidate({ candidate_sku: "B-2" }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("2 substitutes")).toBeTruthy();
  });

  it("reports undetermined candidates rather than calling them substitutes", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({
          verdict: "indeterminate",
          substitutable: false,
          needs_enrichment: true,
        }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("1 undetermined")).toBeTruthy();
    expect(screen.queryByText(/1 substitute$/)).toBeNull();
  });

  it("says no substitute rather than showing zero when nothing matches", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({ verdict: "not_equivalent", substitutable: false }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("No substitute")).toBeTruthy();
  });
});

describe("indeterminate is a data gap, not a rejection", () => {
  const view = equivalenceView({
    candidates_detail: [
      equivalenceCandidate({
        candidate_sku: "77C-104",
        verdict: "indeterminate",
        substitutable: false,
        needs_enrichment: true,
        reason:
          "end connection could not be established on both records, so compatibility cannot be confirmed",
        unknown: 1,
        unknown_detail: [
          equivalenceComparison({
            attribute_code: "end_connection",
            name: "End Connection",
            interchange: "critical",
            substitution: "equal",
            compatibility: "unknown_candidate",
            candidate_display: null,
          }),
        ],
      }),
    ],
  });

  it("labels it as undetermined rather than as a failure", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("Cannot be determined")).toBeTruthy();
    expect(screen.queryByText("Not equivalent")).toBeNull();
  });

  it("tells the reader to enrich rather than to rule the part out", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText(/enrich these records rather than rule the parts out/i)).toBeTruthy();
  });

  it("states that nothing is known to differ", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText(/Nothing here is known to differ/i)).toBeTruthy();
  });

  it("names the attribute that could not be established", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("Not established")).toBeTruthy();
    expect(screen.getByText("End Connection")).toBeTruthy();
    expect(screen.getAllByText("not established").length).toBeGreaterThan(0);
  });
});

describe("the basis travels with the verdict", () => {
  it("shows the coverage note on every candidate", () => {
    render(<EquivalencePanel view={equivalenceView()} />);
    expect(
      screen.getByText("11 of 12 interchange-relevant attributes were established on both records"),
    ).toBeTruthy();
  });

  it("explains that an unestablished attribute is never a match", () => {
    render(<EquivalencePanel view={equivalenceView()} />);
    expect(screen.getByText(/never counted as a match/i)).toBeTruthy();
  });
});

describe("functional equivalence is a qualified yes", () => {
  const view = equivalenceView({
    candidates_detail: [
      equivalenceCandidate({
        verdict: "functional_equivalent",
        substitutable: true,
        reason:
          "performs the same function, but differs on end connection, so it is not a drop-in and installation changes",
        blocking: 1,
        blocking_detail: [
          equivalenceComparison({
            attribute_code: "end_connection",
            name: "End Connection",
            interchange: "critical",
            substitution: "equal",
            reference_display: "NPT Threaded",
            candidate_display: "Solder",
          }),
        ],
      }),
    ],
  });

  it("counts as a substitute", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("1 substitute")).toBeTruthy();
    expect(screen.getByText("Functional equivalent")).toBeTruthy();
  });

  it("shows what differs so the right fittings get ordered", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("Differs")).toBeTruthy();
    expect(screen.getByText(/NPT Threaded/)).toBeTruthy();
    expect(screen.getByText(/Solder/)).toBeTruthy();
  });
});

describe("an upgrade is not a match", () => {
  it("reports exceeding the reference separately from agreeing with it", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({
          satisfied: 1,
          satisfied_detail: [
            equivalenceComparison({
              attribute_code: "pressure_rating_wog",
              name: "Pressure Rating (WOG)",
              compatibility: "satisfies",
              reference_display: "600 psi",
              candidate_display: "1000 psi",
            }),
          ],
        }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText("Candidate exceeds the reference")).toBeTruthy();
    expect(screen.getByText(/must be at least/i)).toBeTruthy();
  });
});

describe("cosmetic differences are shown without blocking", () => {
  it("separates them from the differences that refuse a substitution", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({
          cosmetic_differences: 1,
          cosmetic_detail: [
            equivalenceComparison({
              attribute_code: "handle_type",
              name: "Handle Type",
              interchange: "cosmetic",
              substitution: "equal",
              decides: false,
              reference_display: "Lever",
              candidate_display: "Tee",
            }),
          ],
        }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(
      screen.getByText("Differs, does not affect interchangeability"),
    ).toBeTruthy();
    expect(screen.getByText("Drop-in replacement")).toBeTruthy();
  });
});

describe("class differences withhold the drop-in claim", () => {
  it("explains that the behaviour is unmodelled rather than merely different", () => {
    const view = equivalenceView({
      candidates_detail: [
        equivalenceCandidate({
          candidate_sku: "T-113-100",
          candidate_class: "PLB.VLV.GATE.BRZ",
          same_class: false,
          verdict: "functional_equivalent",
        }),
      ],
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText(/not modelled by any attribute here/i)).toBeTruthy();
  });

  it("says nothing about classes when both sides share one", () => {
    render(<EquivalencePanel view={equivalenceView()} />);
    expect(screen.queryByText(/not modelled by any attribute here/i)).toBeNull();
  });
});

describe("corpus provenance", () => {
  it("warns when the compared records were hand-authored", () => {
    const view = equivalenceView({
      measured: false,
      source: "golden",
      source_note: "Records are read from the hand-authored golden set.",
    });
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText(/Corpus records\./)).toBeTruthy();
    expect(screen.getByText(/were not extracted by this run/i)).toBeTruthy();
  });

  it("says nothing about provenance on a real pipeline run", () => {
    render(<EquivalencePanel view={equivalenceView({ measured: true })} />);
    expect(screen.queryByText(/Corpus records\./)).toBeNull();
  });
});

describe("no substitutes at all", () => {
  const view = equivalenceView({
    candidates_detail: [
      equivalenceCandidate({
        candidate_sku: "77C-105",
        verdict: "not_equivalent",
        substitutable: false,
        blocking: 1,
        blocking_detail: [equivalenceComparison()],
      }),
    ],
  });

  it("attributes the absence to the corpus rather than to the comparison", () => {
    render(<EquivalencePanel view={view} />);
    expect(screen.getByText(/Nothing in this catalogue/i)).toBeTruthy();
    expect(screen.getByText(/genuinely do not interchange/i)).toBeTruthy();
  });

  it("names the attribute that refused, so it is actionable", () => {
    render(<EquivalencePanel view={view} />);
    const table = screen.getByRole("table", { name: /cannot replace BA-100-100/i });
    expect(table).toBeTruthy();
    expect(screen.getByText("Pressure rating wog")).toBeTruthy();
  });
});
