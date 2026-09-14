import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  ADVERSARIAL,
  BACKTEST,
  CLASSIFICATION,
  COHORT,
  CORPUS,
  DELIVERY_SCORE,
  ECONOMICS,
  HEADLINE_FIGURES,
  OFFLINE_EXTRACTION,
} from "@/data/landing";

/**
 * The landing page's figures, held to the artifacts they were transcribed from.
 *
 * `data/landing.ts` is a hand-maintained mirror of `evals/*.json`, in the same way `types.ts` is a
 * hand-maintained mirror of the Python models — and it carries the same silent failure mode.
 * `scripts/check_console_types.py` exists because adding an enum member in Python leaves the console
 * compiling while quietly unable to represent the new case. This file exists for the mirror image of
 * that: re-running a scorer rewrites `evals/baseline.json`, and the public page keeps rendering the
 * previous run's numbers. Nothing breaks, nothing warns, and the page quietly starts lying.
 *
 * That failure costs more here than a normal staleness bug, because the page's entire argument is
 * that a published value should be checkable against its source. A marketing claim drifting from the
 * measurement behind it is the exact defect the product refuses to ship, printed on its own
 * storefront.
 *
 * This is live and pointed at right now. The extraction prompt was bumped to `extract.v3` on
 * 2026-08-25 while `evals/baseline.json` still records `extract.v2`, which is already failing
 * `tests/test_evaluation.py::test_the_committed_baseline_matches_the_current_prompt_version`. When
 * that rebaseline happens, precision, recall, F1 and the exact-match rate will all move, and this
 * suite is what turns "the landing page is now wrong" from something nobody notices into a red
 * build naming the constant to edit.
 *
 * Deliberately a drift test rather than a direct import of the JSON. Reading `evals/` from a
 * statically-generated page would put build-time file reads and an out-of-project import path into
 * the render graph to save a transcription that changes a few times a year. The repo's own answer to
 * a hand-maintained mirror is a check that fails on drift, so that is what this is.
 */

/**
 * Repo root, four levels up from `<root>/apps/console/src/data/`.
 *
 * Resolved with `node:path` rather than `new URL(..., import.meta.url)`. Vite statically analyses
 * that second form as an asset reference, and with a non-literal path it globs the repository and
 * trips `server.fs.deny` on the first match it is not allowed to read. `join` is opaque to the
 * transform, which is what we want: these files are test inputs, not bundled assets.
 */
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

function repoFile(relative: string): unknown {
  return JSON.parse(readFileSync(join(REPO_ROOT, relative), "utf8"));
}

/**
 * A displayed figure, back to a number.
 *
 * The page writes figures the way a reader wants them — `"1,000"`, `"98%"`, `"115 / 227"` — so a
 * comparison against the source has to undo that. Percentages are returned as fractions, and a
 * `a / b` pair as its numerator, because those are the forms the JSON holds.
 */
function figure(display: string): number {
  const cleaned = display.replace(/,/g, "").trim();
  if (cleaned.endsWith("%")) return Number(cleaned.slice(0, -1)) / 100;
  const ratio = /^(-?[\d.]+)\s*\/\s*[\d.]+$/.exec(cleaned);
  return Number(ratio ? ratio[1] : cleaned);
}

/** Figures are looked up by label rather than by index, so reordering the band is not a failure. */
function byLabel(items: readonly { label: string; value: string }[]): Map<string, number> {
  return new Map(items.map((item) => [item.label, figure(item.value)]));
}

describe("the extraction backtest figures match evals/baseline.json", () => {
  const baseline = repoFile("evals/baseline.json") as Record<string, number | string>;

  it("reports the provenance of the measurement, not just its results", () => {
    // The four fields that say *what was measured*. A page quoting a precision figure without the
    // golden set and prompt version behind it is quoting a number, not a measurement.
    expect(BACKTEST.goldenSet).toBe(baseline.golden_set);
    expect(BACKTEST.measuredAt).toBe(baseline.measured_at);
    expect(BACKTEST.promptVersion).toBe(baseline.prompt_version);
    expect(BACKTEST.products).toBe(baseline.products);
  });

  it("reports the denominators", () => {
    expect(BACKTEST.comparisons).toBe(baseline.comparisons);
    expect(BACKTEST.calibrationSamples).toBe(baseline.calibration_samples);
  });

  it("reports the raw outcome counts the rates are derived from", () => {
    expect(BACKTEST.correct).toBe(baseline.correct);
    expect(BACKTEST.wrongValue).toBe(baseline.wrong_value);
    expect(BACKTEST.missed).toBe(baseline.missed);
    expect(BACKTEST.correctlyAbstained).toBe(baseline.correctly_abstained);
    expect(BACKTEST.hallucinated).toBe(baseline.hallucinated);
  });

  it("reports the measured rates at the precision they were measured to", () => {
    // Compared as strings after normalising, not with a tolerance. `toBeCloseTo` would let 0.978
        // pass against a baseline of 0.98, and rounding a measurement toward a friendlier number is
    // the specific thing this file is here to prevent.
    const shown = byLabel(BACKTEST.figures);

    expect(shown.get("Precision")).toBe(baseline.precision);
    expect(shown.get("Recall")).toBe(baseline.recall);
    expect(shown.get("F1")).toBe(baseline.f1);
    expect(shown.get("Hallucination rate")).toBe(baseline.hallucination_rate);
    expect(shown.get("Abstention correctness")).toBe(baseline.abstention_correctness);
    expect(shown.get("Exact match")).toBe(baseline.exact_match_rate);
  });

  it("keeps the counts quoted in the hint text true", () => {
    // The hints carry the raw numbers in prose — "224 correct, 2 wrong values". Prose rots more
    // quietly than a figure does, because nobody rereads a caption, so it is checked as data.
    const hint = (label: string) =>
      String(BACKTEST.figures.find((f) => f.label === label)?.hint ?? "");

    expect(hint("Precision")).toContain(`${baseline.correct} correct`);
    expect(hint("Precision")).toContain(`${baseline.wrong_value} wrong`);
    expect(hint("Recall")).toContain(`${baseline.missed} missed`);
    expect(hint("Recall")).toContain(`${baseline.calibration_samples} available`);
    expect(hint("Hallucination rate")).toContain(`${baseline.hallucinated} fabricated`);
    expect(hint("Hallucination rate")).toContain(`${baseline.comparisons} comparisons`);
    expect(hint("Abstention correctness")).toContain(
      `${baseline.correctly_abstained} of ${baseline.correctly_abstained}`,
    );
  });
});

describe("the recorded corpus figures match the bundles on disk", () => {
  /**
   * Aggregate `data/console/*.bundle.json` the way the page claims to have aggregated it.
   *
   * Added after this exact drift shipped unnoticed: a pipeline change rewrote three bundles, and
   * every corpus figure on the page — values, gaps, tokens, cost — moved without a single test
   * objecting. The eval guards above only cover `evals/*.json`, so the largest set of numbers on
   * the page was the least protected.
   *
   * Note the nesting: per-run figures live under `bundle`, while `policy` sits at the top level.
   */
  const dir = join(REPO_ROOT, "data", "console");
  const totals = {
    records: 0,
    values: 0,
    publishable: 0,
    needingReview: 0,
    gaps: 0,
    gapsRequired: 0,
    verified: 0,
    calls: 0,
    escalations: 0,
    inputTokens: 0,
    outputTokens: 0,
    cost: 0,
  };

  for (const name of readdirSync(dir).filter((f) => f.endsWith(".bundle.json"))) {
    const bundle = (JSON.parse(readFileSync(join(dir, name), "utf8")) as { bundle?: Record<string, any> })
      .bundle;
    if (!bundle) continue;
    const metrics = bundle.metrics ?? {};
    const cost = bundle.cost ?? {};
    totals.records += 1;
    totals.values += metrics.values_total ?? 0;
    totals.publishable += metrics.values_publishable ?? 0;
    totals.needingReview += metrics.values_needing_review ?? 0;
    totals.gaps += metrics.gaps_total ?? 0;
    totals.gapsRequired += metrics.gaps_required ?? 0;
    totals.calls += cost.calls ?? 0;
    totals.escalations += cost.escalations ?? 0;
    totals.inputTokens += cost.input_tokens ?? 0;
    totals.outputTokens += cost.output_tokens ?? 0;
    totals.cost += cost.cost_usd ?? 0;
    if (bundle.certificate?.signature_verified) totals.verified += 1;
  }

  it("counts every recorded run", () => {
    expect(figure(CORPUS.records)).toBe(totals.records);
    expect(figure(CORPUS.documents)).toBe(totals.records);
    expect(CORPUS.certificatesVerified).toBe(
      `${totals.verified.toLocaleString("en-US")} / ${totals.records.toLocaleString("en-US")}`,
    );
  });

  it("reports values and gaps as recorded", () => {
    expect(figure(CORPUS.values)).toBe(totals.values);
    expect(figure(CORPUS.publishable)).toBe(totals.publishable);
    expect(figure(CORPUS.needingReview)).toBe(totals.needingReview);
    expect(figure(CORPUS.gaps)).toBe(totals.gaps);
    expect(figure(CORPUS.gapsRequired)).toBe(totals.gapsRequired);
  });

  it("still records more gaps than values, which is the claim being made", () => {
    // The section's argument, not decoration: a system filing more of what it could not establish
    // than what it did is either broken or honest. If this ever inverts, the copy needs rewriting
    // rather than the number nudging.
    expect(totals.gaps).toBeGreaterThan(totals.values);
  });

  it("reports model usage and cost as metered", () => {
    expect(figure(CORPUS.modelCalls)).toBe(totals.calls);
    expect(figure(CORPUS.escalations)).toBe(totals.escalations);
    expect(figure(CORPUS.inputTokens)).toBe(totals.inputTokens);
    expect(figure(CORPUS.outputTokens)).toBe(totals.outputTokens);
    expect(CORPUS.totalCost).toBe(`$${totals.cost.toFixed(6)}`);
  });

  it("agrees with the economics section on the same corpus", () => {
    expect(ECONOMICS.corpusTotal).toBe(CORPUS.totalCost);
    expect(ECONOMICS.corpusCalls).toBe(CORPUS.modelCalls);
    expect(ECONOMICS.corpusRecords).toBe(CORPUS.records);
  });

  it("keeps the hero band consistent with the corpus", () => {
    const gaps = HEADLINE_FIGURES.find((f) => f.label === "Recorded gaps");

    expect(figure(gaps!.value)).toBe(totals.gaps);
    expect(gaps!.unit).toBe(`against ${totals.values.toLocaleString("en-US")} values`);
  });
});

describe("the adversarial figures match evals/adversarial.json", () => {
  type Case = {
    target_sku: string;
    document: string;
    requested: number;
    abstained: number;
    fabricated: number;
    fabricated_with_verified_quote: number;
  };
  const cases = repoFile("evals/adversarial.json") as Case[];

  it("totals every case rather than quoting the best one", () => {
    const sum = (pick: (c: Case) => number) => cases.reduce((total, c) => total + pick(c), 0);

    expect(ADVERSARIAL.requested).toBe(sum((c) => c.requested));
    expect(ADVERSARIAL.abstained).toBe(sum((c) => c.abstained));
    expect(ADVERSARIAL.fabricated).toBe(sum((c) => c.fabricated));
    expect(ADVERSARIAL.fabricatedWithVerifiedQuote).toBe(
      sum((c) => c.fabricated_with_verified_quote),
    );
  });

  it("shows every case that was run", () => {
    // A suite of three where the page lists two is the cheapest possible way to overstate a
    // refusal record, and it would look identical to an honest page.
    expect(ADVERSARIAL.cases).toHaveLength(cases.length);

    for (const shown of ADVERSARIAL.cases) {
      const source = cases.find(
        (c) => c.target_sku === shown.sku && c.document === shown.document,
      );
      expect(source, `no case in evals/adversarial.json for ${shown.sku} × ${shown.document}`)
        .toBeDefined();
      expect(shown.requested).toBe(source!.requested);
      expect(shown.abstained).toBe(source!.abstained);
    }
  });
});

describe("the classification figures match evals/classification_score.json", () => {
  const score = repoFile("evals/classification_score.json") as {
    rows: number;
    classes: number;
    classified: number;
    coverage: number;
    fabricated: number;
    by_method: Record<string, number>;
    by_class: Record<string, number>;
  };

  it("reports the run's totals", () => {
    expect(figure(CLASSIFICATION.rows)).toBe(score.rows);
    expect(CLASSIFICATION.classes).toBe(score.classes);
    expect(CLASSIFICATION.classified).toBe(score.classified);
    expect(figure(CLASSIFICATION.coverage)).toBeCloseTo(score.coverage, 4);
    expect(CLASSIFICATION.fabricated).toBe(score.fabricated);
  });

  it("splits by method with counts that sum to the rows attempted", () => {
    const shown = new Map(CLASSIFICATION.byMethod.map((m) => [m.method, m.count]));

    expect(shown.get("Retrieval only")).toBe(score.by_method.retrieval_only);
    expect(shown.get("Model adjudicated")).toBe(score.by_method.retrieval_decisive);
    expect(shown.get("No viable candidate")).toBe(score.by_method.no_viable_candidate);
    expect(shown.get("Ambiguous, no model")).toBe(score.by_method.ambiguous_no_model);

    const total = CLASSIFICATION.byMethod.reduce((sum, m) => sum + m.count, 0);
    expect(total).toBe(score.rows);
  });

  it("names the busiest classes with their real counts", () => {
    for (const shown of CLASSIFICATION.topClasses) {
      expect(shown.count, `count for ${shown.code}`).toBe(score.by_class[shown.code]);
    }

    // Presented as the top classes, so they have to actually be in descending order.
    const counts = CLASSIFICATION.topClasses.map((c) => c.count);
    expect([...counts].sort((a, b) => b - a)).toEqual(counts);
  });
});

describe("the offline extraction figures match evals/extraction_score.json", () => {
  const score = repoFile("evals/extraction_score.json") as {
    documents: string[];
    arms: {
      agreed: number;
      disagreed: number;
      not_extracted: number;
      absent_respected: number;
      absent_violated: number;
      fabrications: unknown[];
      quote_failures: unknown[];
    }[];
  };
  const arm = score.arms[0]!;

  it("reports the agreement counts and the documents behind them", () => {
    expect(OFFLINE_EXTRACTION.documents).toBe(score.documents.length);
    expect(OFFLINE_EXTRACTION.agreed).toBe(arm.agreed);
    expect(OFFLINE_EXTRACTION.disagreed).toBe(arm.disagreed);
    expect(OFFLINE_EXTRACTION.notExtracted).toBe(arm.not_extracted);
    expect(OFFLINE_EXTRACTION.absentRespected).toBe(arm.absent_respected);
    expect(OFFLINE_EXTRACTION.absentViolated).toBe(arm.absent_violated);
    expect(OFFLINE_EXTRACTION.fabrications).toBe(arm.fabrications.length);
    expect(OFFLINE_EXTRACTION.quoteFailures).toBe(arm.quote_failures.length);

    for (const name of score.documents) {
      expect(OFFLINE_EXTRACTION.documentNames).toContain(name);
    }
  });

  it("keeps the unflattering coverage figure honest", () => {
    // The one number on this section that does not flatter the system: half the available values
    // were not extracted. Its denominator is agreed + not_extracted, so a change to either has to
    // move it — otherwise coverage could stay at a comfortable 50.7% while the arithmetic beneath
    // it stopped supporting the claim.
    const available = arm.agreed + arm.not_extracted;

    expect(figure(OFFLINE_EXTRACTION.coverage)).toBe(arm.agreed);
    expect(OFFLINE_EXTRACTION.coverage).toBe(`${arm.agreed} / ${available}`);
    expect(figure(OFFLINE_EXTRACTION.coveragePercent) * 100).toBeCloseTo(
      (arm.agreed / available) * 100,
      1,
    );
    expect(OFFLINE_EXTRACTION.precision).toBe(`${arm.agreed} / ${arm.agreed}`);
  });
});

describe("the delivery grading matches evals/delivery_score*.json", () => {
  type Score = {
    rows_scored: number;
    cells_compared: number;
    exact_of_expected: [number, number];
    fill_discipline: [number, number];
    verdicts: { missed: number; overfilled: number; wrong: number };
  };
  const unseeded = repoFile("evals/delivery_score.json") as Score;
  const supplied = repoFile("evals/delivery_score_supplied.json") as Score;

  it("compares the same number of cells in both arms", () => {
    expect(DELIVERY_SCORE.cellsCompared).toBe(unseeded.cells_compared);
    expect(DELIVERY_SCORE.cellsCompared).toBe(supplied.cells_compared);
    expect(DELIVERY_SCORE.rowsScored).toBe(unseeded.rows_scored);
  });

  it("reports each arm as it was scored", () => {
    const arms = [unseeded, supplied];

    expect(DELIVERY_SCORE.arms).toHaveLength(arms.length);

    DELIVERY_SCORE.arms.forEach((shown, index) => {
      const source = arms[index]!;
      expect(shown.exact, `exact for ${shown.arm}`).toBe(
        `${source.exact_of_expected[0]} / ${source.exact_of_expected[1]}`,
      );
      expect(shown.fillDiscipline, `fill discipline for ${shown.arm}`).toBe(
        `${source.fill_discipline[0]} / ${source.fill_discipline[1]}`,
      );
      expect(shown.missed, `missed for ${shown.arm}`).toBe(source.verdicts.missed);
      expect(shown.overfilled, `overfilled for ${shown.arm}`).toBe(source.verdicts.overfilled);
      expect(shown.wrong, `wrong for ${shown.arm}`).toBe(source.verdicts.wrong);
    });
  });

  it("only claims overfilled zero while both arms actually scored zero", () => {
    // The section's headline invariant is that the gate never guessed a cell to improve its own
    // score. 173 of the client's 252 columns are blank by design, so a single overfill would make
    // the sentence false while every other figure on the page stayed correct.
    expect(unseeded.verdicts.overfilled).toBe(0);
    expect(supplied.verdicts.overfilled).toBe(0);
    expect(DELIVERY_SCORE.invariant).toContain("overfilled 0 in both arms");
  });

  it("does not round the supplied arm's exact-match percentage in its favour", () => {
    const [exact, expected] = supplied.exact_of_expected;

    expect(figure(DELIVERY_SCORE.arms[1]!.exactPercent!) * 100).toBeCloseTo(
      (exact / expected) * 100,
      1,
    );
  });
});

describe("the cohort figures match evals/cohort.json", () => {
  const study = repoFile("evals/cohort.json") as {
    lift: Record<string, number>;
    control_drift: Record<string, number>;
    field_presence: { before: number; after: number };
    treatment_before: Record<string, number>;
    treatment_after: Record<string, number>;
    members: { sku: string; arm: string; before: Record<string, number> }[];
  };

  it("reports the composite before and after, and the lift between them", () => {
    expect(figure(COHORT.treatment.before.composite)).toBe(study.treatment_before.composite);
    expect(figure(COHORT.treatment.after.composite)).toBe(study.treatment_after.composite);
    expect(figure(COHORT.treatment.lift)).toBe(study.lift.composite);
    expect(figure(COHORT.treatment.before.verifiability)).toBe(
      study.treatment_before.verifiability,
    );
    expect(figure(COHORT.treatment.after.verifiability)).toBe(study.treatment_after.verifiability);
  });

  it("reports field presence separately from completeness", () => {
    expect(figure(COHORT.fieldPresence.before)).toBe(study.field_presence.before);
    expect(figure(COHORT.fieldPresence.after)).toBe(study.field_presence.after);
  });

  it("claims the control held still only while every drift dimension is zero", () => {
    // The whole attributability claim rests on this. A control SKU was never enriched, so any
    // movement means the scorer changed between readings and the lift above is an artefact.
    for (const [dimension, drift] of Object.entries(study.control_drift)) {
      expect(drift, `control drifted on ${dimension}`).toBe(0);
    }
    expect(figure(COHORT.control.drift)).toBe(0);

    for (const dimension of Object.keys(study.control_drift)) {
      expect(COHORT.control.dimensions).toContain(dimension);
    }
  });

  it("names a control SKU that is actually in the control arm", () => {
    const control = study.members.find((member) => member.arm === "control");

    expect(control?.sku).toBe(COHORT.control.sku);
  });

  it("reports the treatment SKU's own before-state, not the study average", () => {
    const treatment = study.members.find((member) => member.arm === "treatment");

    expect(treatment?.sku).toBe(COHORT.treatment.sku);
    expect(COHORT.treatment.before.valuesPresent).toBe(treatment?.before.values_present);
    expect(COHORT.treatment.before.publishable).toBe(treatment?.before.values_publishable);
    expect(COHORT.treatment.before.withEvidence).toBe(treatment?.before.values_with_evidence);
    expect(COHORT.checksRun.before).toBe(treatment?.before.checks_run);
  });
});
