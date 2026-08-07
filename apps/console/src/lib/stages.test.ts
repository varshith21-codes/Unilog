import { describe, expect, it } from "vitest";

import { modelStageShare, pipelineStages, stageSeconds } from "@/lib/stages";
import { policySummary, sourceDocument, stageBundle, variantBundle } from "@/test/factories";
import { variantGroups } from "@/lib/data";

/**
 * The stage list is a claim about a run that already happened, so the tests are mostly about refusing
 * to overstate it: no stage may appear that did not run, no figure may be invented, and the one
 * genuinely absent measurement — per-stage timing — has to stay absent rather than default to zero.
 */
describe("pipelineStages", () => {
  it("runs in pipeline order, from the document to the channels", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    expect(stages.map((stage) => stage.id)).toEqual([
      "ingest",
      "parse",
      "classify",
      "extract",
      "normalize",
      "validate",
      "decide",
      "certify",
      "syndicate",
    ]);
  });

  it("omits the ingest and parse stages when no document is joined", () => {
    // Documents are stored once and referenced by key. A bundle pointing at a missing one is a
    // degraded dataset, and inventing a page count for it would be worse than showing fewer cards.
    const stages = pipelineStages(stageBundle(), null, policySummary(), null);

    expect(stages.map((stage) => stage.id)).not.toContain("ingest");
    expect(stages.map((stage) => stage.id)).not.toContain("parse");
    expect(stages[0]!.id).toBe("classify");
  });

  it("adds a variant stage only for a SKU that belongs to an exploded series", () => {
    const withoutSeries = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);
    expect(withoutSeries.map((stage) => stage.id)).not.toContain("variants");

    const series = variantGroups([
      variantBundle({ sku: "BA-100-050", parentSku: null }),
      variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" }),
    ])[0]!;
    const withSeries = pipelineStages(stageBundle(), sourceDocument(), policySummary(), series);

    const variants = withSeries.find((stage) => stage.id === "variants")!;
    expect(variants.headline).toContain("2 part numbers from one ordering table");
  });

  it("marks the variant stage as deterministic, because no model picks the row", () => {
    // The cheapness claim and the safety claim are the same claim. Reading a neighbouring row
    // produces a value that is in the document, correctly cited, and wrong for the part.
    const series = variantGroups([
      variantBundle({ sku: "BA-100-050", parentSku: null }),
      variantBundle({ sku: "BA-100-075", parentSku: "BA-100-050" }),
    ])[0]!;
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), series);

    expect(stages.find((stage) => stage.id === "variants")!.model).toBe(false);
  });

  it("attributes a model call to exactly the stages that made one", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);
    const modelled = stages.filter((stage) => stage.model).map((stage) => stage.id);

    expect(modelled).toEqual(["classify", "extract"]);
  });

  it("keeps every publication decision on the deterministic side", () => {
    // The architectural claim the whole view exists to make legible. If a model ever appears on one
    // of these, the claim is false and this test is the place that says so.
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    for (const id of ["validate", "decide", "certify", "syndicate"]) {
      expect(stages.find((stage) => stage.id === id)!.model).toBe(false);
    }
  });

  it("reports the recorded extraction latency and leaves untimed stages null", () => {
    // Only the model calls were ever instrumented. A zero here would read as "instant" rather than
    // "never measured", which is a different and false statement.
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    expect(stages.find((stage) => stage.id === "extract")!.latencyMs).toBe(7022);
    expect(stages.find((stage) => stage.id === "validate")!.latencyMs).toBeNull();
    expect(stages.find((stage) => stage.id === "parse")!.latencyMs).toBeNull();
  });

  it("reads the extraction headline off the run rather than counting published values", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    expect(stages.find((stage) => stage.id === "extract")!.headline).toBe(
      "15 of 23 attributes",
    );
  });

  it("names the schemes classification actually produced", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    expect(stages.find((stage) => stage.id === "classify")!.headline).toContain("3 schemes");
    expect(stages.find((stage) => stage.id === "classify")!.headline).toContain("ETIM");
  });

  it("says a classifier abstained rather than reporting zero schemes", () => {
    // Abstention is a decision, not a failure to produce output, and it reads completely differently.
    const bundle = stageBundle();
    bundle.classification_summary = { schemes: [], abstained: true, method: "abstained" };
    const stages = pipelineStages(bundle, sourceDocument(), policySummary(), null);

    const classify = stages.find((stage) => stage.id === "classify")!;
    expect(classify.headline).toMatch(/abstained/i);
    expect(classify.tone).toBe("warn");
  });

  it("calls an unachievable threshold unachievable, not zero", () => {
    /*
     * The failure this guards is a rendering that reads as its own opposite. `threshold: null` means
     * no cutoff could hold the requested error budget, so nothing may be auto-accepted on a validated
     * policy; `0.000` would read as "accept every value regardless of score".
     */
    const stages = pipelineStages(
      stageBundle(),
      sourceDocument(),
      policySummary({ threshold: null }),
      null,
    );

    const decide = stages.find((stage) => stage.id === "decide")!;
    expect(decide.facts.find((fact) => fact.label === "Threshold")!.value).toBe("none achievable");
    expect(decide.facts.find((fact) => fact.label === "Threshold")!.value).not.toContain("0.00");
    expect(decide.tone).toBe("fail");
  });

  it("turns a failed validation into a failing stage rather than a warning", () => {
    const bundle = stageBundle();
    bundle.validation = { ...bundle.validation, failures: 2 };

    const stages = pipelineStages(bundle, sourceDocument(), policySummary(), null);
    expect(stages.find((stage) => stage.id === "validate")!.tone).toBe("fail");
  });

  it("flags an unverified certificate signature as a failure", () => {
    // A certificate whose HMAC does not check out is worse than no certificate, so it cannot render
    // in the same tone as a signed one.
    const bundle = stageBundle();
    bundle.certificate = { ...bundle.certificate, signature_verified: false };

    const stages = pipelineStages(bundle, sourceDocument(), policySummary(), null);
    const certify = stages.find((stage) => stage.id === "certify")!;
    expect(certify.tone).toBe("fail");
    expect(certify.headline).toMatch(/did not verify/i);
  });

  it("adds a copy stage only when the run generated copy", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);
    expect(stages.map((stage) => stage.id)).not.toContain("copy");
  });

  it("counts held channels rather than reporting them as published", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);
    const syndicate = stages.find((stage) => stage.id === "syndicate")!;

    expect(syndicate.headline).toBe("1 of 2 channels ready");
    expect(syndicate.tone).toBe("warn");
  });
});

describe("modelStageShare", () => {
  it("splits the run into model and deterministic stages", () => {
    const stages = pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

    expect(modelStageShare(stages)).toEqual({ model: 2, deterministic: 7 });
  });
});

describe("stageSeconds", () => {
  it("reads milliseconds as seconds to one decimal", () => {
    expect(stageSeconds(7022)).toBe("7.0s");
    expect(stageSeconds(13319)).toBe("13.3s");
  });
});
