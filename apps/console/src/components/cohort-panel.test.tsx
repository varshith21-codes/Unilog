import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CohortPanel } from "@/components/cohort-panel";
import { cohortMember, cohortScore, cohortStudy } from "@/test/factories";

afterEach(cleanup);

/**
 * The Quality Index cohort is the one measurement in this console whose output is a *sales* claim,
 * which makes it the one most worth attacking. These tests are about the guards on that claim rather
 * than the layout: whether the panel refuses to vouch for numbers it cannot support, and whether it
 * reports the before-state honestly instead of flatteringly.
 */
describe("trust banner", () => {
  it("vouches for the deltas when the control arm held still", () => {
    render(<CohortPanel study={cohortStudy()} />);

    expect(screen.getByText("Control held")).toBeTruthy();
    expect(screen.getByText(/attributable to enrichment/i)).toBeTruthy();
  });

  it("declines to vouch when there is no control arm", () => {
    render(<CohortPanel study={cohortStudy({ control_skus: 0, trustworthy: false })} />);

    expect(screen.getByText("No control arm")).toBeTruthy();
    expect(screen.getByText(/reported but not vouched for/i)).toBeTruthy();
    expect(screen.queryByText("Control held")).toBeNull();
  });

  it("declares the study invalid when the untouched arm appears to have moved", () => {
    // A control SKU was never enriched, so its score cannot legitimately change. If it did, the
    // *scorer* changed between readings and every number below is an artefact.
    render(
      <CohortPanel
        study={cohortStudy({
          trustworthy: false,
          drifted_dimensions: ["completeness"],
          control_drift: { completeness: 0.2, verifiability: 0, consistency: 0, composite: 0.07 },
        })}
      />,
    );

    expect(screen.getByText("Study not valid")).toBeTruthy();
    expect(screen.getByText(/measurement itself changed/i)).toBeTruthy();
    // Louder than the lift it invalidates.
    expect(screen.queryByText("Control held")).toBeNull();
  });
});

describe("the two completeness numbers", () => {
  it("reports field presence alongside publishable completeness", () => {
    render(<CohortPanel study={cohortStudy()} />);

    // Publishing "0% → 75%" without the presence figure beside it would be a strawman: the item
    // master's fields *are* populated, they are just unsourced.
    //
    // getAllBy, not getBy: both labels appear again as column headers in the per-SKU table below,
    // which is correct — the point is that the pair is shown together, not that it is shown once.
    expect(screen.getAllByText("Field presence").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Publishable").length).toBeGreaterThan(0);

    // The two figures must differ, and both must be on the page. Presence starts at 33% because the
    // item master's fields hold values; publishable starts at 0% because none of them can be
    // sourced. Showing only the second is the strawman this guards against.
    expect(screen.getAllByText(/33% → 75%/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0% → 75%/).length).toBeGreaterThan(0);
  });

  it("states the gap in the before state as the finding", () => {
    render(<CohortPanel study={cohortStudy()} />);

    expect(screen.getByText(/what legacy catalogue data actually looks like/i)).toBeTruthy();
  });
});

describe("consistency", () => {
  it("explains a consistency drop instead of leaving it looking like a regression", () => {
    const before = cohortScore({ consistency: 1, checks_run: 11, values_present: 6 });
    const after = cohortScore({
      consistency: 0.857,
      checks_run: 9,
      values_present: 14,
      failed_rules: ["R_POTABLE_REQUIRES_NSF61"],
    });

    render(
      <CohortPanel
        study={cohortStudy({
          lift: { completeness: 0.75, verifiability: 1, consistency: -0.143, composite: 0.6 },
          treatment_before: { completeness: 0, verifiability: 0, consistency: 1, composite: 0.278 },
          treatment_after: {
            completeness: 0.75,
            verifiability: 1,
            consistency: 0.857,
            composite: 0.88,
          },
          members: [cohortMember({ before, after })],
        })}
      />,
    );

    expect(screen.getByText("Why consistency fell")).toBeTruthy();
    // The denominators, and the rule — not a causal story the numbers do not support.
    expect(screen.getByText("11 → 9")).toBeTruthy();
    // Named twice on purpose: once in the explanation, once in the per-SKU table's remaining
    // failures. Both matter, so this asserts it is named rather than named exactly once.
    expect(screen.getAllByText("R_POTABLE_REQUIRES_NSF61").length).toBeGreaterThan(0);
  });

  it("says nothing about consistency when it did not fall", () => {
    render(<CohortPanel study={cohortStudy()} />);

    expect(screen.queryByText("Why consistency fell")).toBeNull();
  });
});

describe("per-SKU table", () => {
  it("orders least-improved first, because that is where the work is", () => {
    render(
      <CohortPanel
        study={cohortStudy({
          members: [
            cohortMember({ sku: "BIG-WIN", after: cohortScore({ composite: 0.95 }) }),
            cohortMember({ sku: "BARELY-MOVED", after: cohortScore({ composite: 0.3 }) }),
          ],
        })}
      />,
    );

    const rows = screen.getAllByRole("row").map((row) => row.textContent ?? "");
    const barely = rows.findIndex((text) => text.includes("BARELY-MOVED"));
    const big = rows.findIndex((text) => text.includes("BIG-WIN"));

    expect(barely).toBeGreaterThan(0);
    expect(barely).toBeLessThan(big);
  });

  it("distinguishes a control row from a treatment row", () => {
    render(<CohortPanel study={cohortStudy()} />);

    expect(screen.getByText("Control")).toBeTruthy();
    expect(screen.getByText("Treatment")).toBeTruthy();
  });
});
