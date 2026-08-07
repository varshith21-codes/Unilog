import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GeneratedCopyPanel } from "@/components/generated-copy";
import { formalCheck, generatedCopy } from "@/test/factories";

afterEach(cleanup);

/**
 * The formal-verification panel exists to keep three states apart that look almost identical in a
 * summary and mean opposite things:
 *
 *   not checked      — no reasoning policy was consulted
 *   checked, clean   — a solver reached a verdict and found no contradiction
 *   checked, blind   — the solver could not translate anything, so nothing was established
 *
 * Rendering any of them as any other would put a verified badge on an unverified claim, which is the
 * single most damaging thing this console could do. `tsc` cannot catch a swapped branch.
 */
describe("formal verification panel", () => {
  it("says not-run rather than showing a clean result when no policy was consulted", () => {
    render(<GeneratedCopyPanel copy={generatedCopy({ formal_check: null })} />);

    expect(screen.getByText(/Not run for this SKU/i)).toBeTruthy();
    // The crucial negative: absence must not read as a pass.
    expect(screen.queryByText(/No contradiction/i)).toBeNull();
  });

  it("reports no contradiction only when the solver actually reached a verdict", () => {
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({ formal_check: formalCheck({ passed: true, conclusive: true }) })}
      />,
    );

    expect(screen.getByText("No contradiction")).toBeTruthy();
  });

  it("does not claim verification when every sentence was untranslatable", () => {
    // passed, because nothing was disproven — but conclusive is false, because nothing was proven
    // either. The pill must reflect the second fact, not the first.
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({
          formal_check: formalCheck({ passed: true, conclusive: false, indeterminate: 4 }),
        })}
      />,
    );

    expect(screen.getByText("No verdict")).toBeTruthy();
    expect(screen.queryByText("No contradiction")).toBeNull();
    expect(screen.getByText(/formed no opinion on any sentence/i)).toBeTruthy();
  });

  it("withholds copy and says why when the policy could not be reached", () => {
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({
          published: false,
          formal_check: formalCheck({
            passed: false,
            conclusive: false,
            error: "the reasoning policy could not be reached: ExpiredTokenException",
          }),
        })}
      />,
    );

    // "Not verified" rather than "Blocked": a reader needs to know which gate stopped it.
    expect(screen.getAllByText("Not verified").length).toBeGreaterThan(0);
    expect(screen.getByText(/withheld rather than published unverified/i)).toBeTruthy();
  });

  it("names the violated rule on a proven contradiction", () => {
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({
          published: false,
          formal_check: formalCheck({
            passed: false,
            contradictions: 1,
            violated_rules: ["RLEADEDALLOY"],
            claims: [
              {
                claim: "This valve is certified lead-free.",
                verdict: "invalid",
                rules: ["RLEADEDALLOY"],
                contradiction: true,
                indeterminate: false,
                confidence: 1,
                detail: "",
              },
            ],
          }),
        })}
      />,
    );

    // The rule id is the proof. Summarising it away would leave a reviewer with a score.
    expect(screen.getByText("RLEADEDALLOY")).toBeTruthy();
    expect(screen.getByText("This valve is certified lead-free.")).toBeTruthy();
    expect(screen.getByText("Contradiction")).toBeTruthy();
  });

  it("distinguishes a claim-check block from a formal-verification block", () => {
    // Copy that failed the *claim* check, with L6 never run. The pill must not blame L6.
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({
          published: false,
          formal_check: null,
          claim_check: { claims: 2, supported: 1, unsupported: 1, banned: 0, passed: false },
          claims: [
            {
              kind: "quantity",
              text: "800 psi",
              verdict: "unsupported",
              reason: "no verified attribute states this figure",
              field: "bullets",
              supported_by: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("Blocked")).toBeTruthy();
    expect(screen.queryByText("Contradiction")).toBeNull();
    expect(screen.queryByText("Not verified")).toBeNull();
  });
});

describe("copy that failed to generate", () => {
  it("shows the error instead of an empty layout", () => {
    render(
      <GeneratedCopyPanel
        copy={generatedCopy({
          published: false,
          headline: "",
          error: "no publishable attributes: copy would have no verified facts to draw on",
        })}
      />,
    );

    expect(screen.getByText(/no verified facts to draw on/i)).toBeTruthy();
  });
});
