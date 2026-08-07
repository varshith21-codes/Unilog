import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CrossSourcePanel } from "@/components/cross-source-panel";
import { conflict, crossSourceView } from "@/test/factories";

afterEach(cleanup);

/**
 * Validation layer L4 in the review workspace.
 *
 * The panel's job is to keep a *currency* problem apart from a *contradiction*. An older catalogue
 * printing last year's pressure rating is behaving correctly and should read as stale; two sources
 * that disagree with nothing to order them is a blocking review task. Collapsing the two into one
 * "disagreement" indicator would either cry wolf on every stale catalogue or bury a real conflict.
 */
describe("headline verdict", () => {
  it("reads as agreement when nothing disagreed", () => {
    render(<CrossSourcePanel view={crossSourceView({ conflicts: [], disagreements: 0 })} />);

    expect(screen.getByText("Sources agree")).toBeTruthy();
  });

  it("reads as superseded, not as a failure, when a revision ordered the disagreement", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    expect(screen.getByText("1 superseded")).toBeTruthy();
    expect(screen.queryByText(/unresolved/i)).toBeNull();
  });

  it("leads with the unresolved count when nothing could order the sources", () => {
    render(
      <CrossSourcePanel
        view={crossSourceView({
          unresolved: 1,
          passed: false,
          conflicts: [conflict({ resolved: false, winner: null, reason: "no revision marker on catalog" })],
        })}
      />,
    );

    expect(screen.getByText("1 unresolved")).toBeTruthy();
    expect(screen.getByText("Unresolved conflict")).toBeTruthy();
    expect(screen.getByText(/Left for a human deliberately/i)).toBeTruthy();
  });
});

describe("conflict detail", () => {
  it("shows both competing values so a reviewer can judge them", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    expect(screen.getByText("600 psi")).toBeTruthy();
    expect(screen.getByText("400 psi")).toBeTruthy();
  });

  it("marks which source won, and why", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    // The reason carries the precedence rule. Without it "600 wins" is an assertion, not a finding.
    expect(screen.getByText(/is the newer revision/i)).toBeTruthy();
    expect(screen.getByLabelText("Preferred")).toBeTruthy();
  });

  it("marks nothing as preferred when the conflict is unresolved", () => {
    render(
      <CrossSourcePanel
        view={crossSourceView({
          unresolved: 1,
          conflicts: [conflict({ resolved: false, winner: null, reason: "equivalent revisions" })],
        })}
      />,
    );

    // Showing a winner here would be the console inventing a precedence the pipeline refused to.
    expect(screen.queryByLabelText("Preferred")).toBeNull();
  });
});

describe("source provenance", () => {
  it("flags a source with no revision marker, because that is why a conflict cannot be ordered", () => {
    const view = crossSourceView();
    render(
      <CrossSourcePanel
        view={{
          ...view,
          sources: [
            view.sources[0]!,
            { ...view.sources[1]!, revision_label: null, revision_method: null },
          ],
        }}
      />,
    );

    expect(screen.getByText("no revision marker")).toBeTruthy();
  });

  it("shows each revision label, since it is what decides precedence", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    expect(screen.getAllByText("Rev C 2024-08").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rev A 2022-03").length).toBeGreaterThan(0);
  });
});

describe("corroboration and single-source", () => {
  it("presents corroboration as a positive finding rather than an absence of problems", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    expect(screen.getByText("Corroborated")).toBeTruthy();
    expect(screen.getByText("Body material")).toBeTruthy();
    expect(screen.getByText(/two prove it was not a typo/i)).toBeTruthy();
  });

  it("names single-source attributes as skipped rather than passed", () => {
    render(<CrossSourcePanel view={crossSourceView()} />);

    expect(screen.getByText("Country of origin")).toBeTruthy();
    expect(screen.getByText(/skipped rather than passed/i)).toBeTruthy();
  });
});

describe("scripted runs", () => {
  it("warns when the findings came from a dry run", () => {
    render(<CrossSourcePanel view={crossSourceView({ dry_run: true })} />);

    expect(screen.getByText("Scripted run.")).toBeTruthy();
    expect(screen.getByText(/not a measurement/i)).toBeTruthy();
  });

  it("says nothing about scripting on a real run", () => {
    render(<CrossSourcePanel view={crossSourceView({ dry_run: false })} />);

    expect(screen.queryByText("Scripted run.")).toBeNull();
  });
});
