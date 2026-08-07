/**
 * The pipeline run, reconstructed from what it left behind.
 *
 * The demo script's second beat asks for stage cards that light up — "parsed 12 pages, classified
 * into four schemes, exploded 412 variants from one ordering table, extracted 34 attributes". The
 * obvious way to build that is to run the pipeline while somebody watches. This does not do that,
 * deliberately: the blueprint's own demo hygiene notes say to keep everything local or pre-warmed
 * because conference WiFi will fail, and a single frontier escalation on stage is thirty seconds of
 * silence in a seven-minute slot.
 *
 * So every number here is read off a run that already happened and was persisted to
 * `data/console/{sku}.bundle.json`. Nothing is recomputed, nothing is simulated, and nothing is
 * estimated. The consequence worth stating plainly: **a stage card lighting up is a replay, not an
 * execution.** The view that renders these says so, because an animated progress bar that implies
 * live work while reading from disk would be the same category of dishonesty as showing hand-seeded
 * fixture numbers without labelling them.
 *
 * The one thing genuinely absent is a per-stage clock. Only the model calls were timed
 * (`extraction.latency_ms`, and the run total in `cost.latency_ms`); the deterministic stages were
 * never instrumented because they are microseconds of local work. `latencyMs` is therefore null for
 * most stages, and null renders as "not separately timed" rather than as a zero.
 */

import { bytes, count, percent, score, shortHash } from "./format";
import type { VariantGroup } from "./data";
import type { RiskPolicySummary, SkuBundle, SourceDocument } from "./types";

export interface StageFact {
  label: string;
  value: string;
}

export interface PipelineStage {
  id: string;
  name: string;
  /** The one number this stage is worth reading aloud, phrased as the demo script phrases it. */
  headline: string;
  facts: StageFact[];
  /**
   * Whether a model was called.
   *
   * Tracked per stage because the ratio is the architectural claim: the parts that decide whether a
   * value may be published are deterministic, and the model is confined to reading and classifying.
   */
  model: boolean;
  /** Recorded model latency, or null where the stage was never separately timed. */
  latencyMs: number | null;
  tone: "pass" | "warn" | "fail" | "quiet";
  /** Why the stage exists, for the card's second line. */
  note: string;
}

/** Model time in seconds, to one decimal. */
export function stageSeconds(latencyMs: number): string {
  return `${(latencyMs / 1000).toFixed(1)}s`;
}

/**
 * The stages of one recorded run, in execution order.
 *
 * `series` is optional and only produces a card when the SKU actually belongs to an exploded series,
 * because a card reading "1 part number from one table" would dress up a standalone product as a
 * variant family. Same for generated copy: absent unless the run passed `--generate-copy`.
 */
export function pipelineStages(
  bundle: SkuBundle,
  document: SourceDocument | null,
  policy: RiskPolicySummary,
  series: VariantGroup | null,
): PipelineStage[] {
  const stages: PipelineStage[] = [];

  // ---------------------------------------------------------------- ingest
  if (document) {
    stages.push({
      id: "ingest",
      name: "Ingest",
      headline: `${bytes(document.size_bytes ?? 0)} stored under its own hash`,
      facts: [
        { label: "SHA-256", value: shortHash(document.sha256) },
        { label: "Type", value: document.doc_type },
        { label: "Parser", value: document.parser ?? "unknown" },
      ],
      model: false,
      latencyMs: null,
      tone: "quiet",
      note:
        "Content-addressed on the way in, so a citation written today still resolves to the same " +
        "bytes after the supplier silently replaces the file at that URL.",
    });

    // ---------------------------------------------------------------- parse
    const pages = document.page_count ?? 0;
    stages.push({
      id: "parse",
      name: "Parse",
      headline: `${pages} ${pages === 1 ? "page" : "pages"}, ${document.table_count} ${
        document.table_count === 1 ? "table" : "tables"
      }`,
      facts: [
        { label: "Lines", value: count(document.line_count) },
        { label: "Tables", value: count(document.table_count) },
        { label: "Warnings", value: count(document.warnings.length) },
      ],
      model: false,
      latencyMs: null,
      tone: document.warnings.length > 0 ? "warn" : "quiet",
      note:
        "Tables are addressable down to the cell, which is what lets a value cite `t1:r5:c1` " +
        "instead of a page number.",
    });
  }

  // ---------------------------------------------------------------- classify
  const classification = bundle.classification_summary as {
    schemes?: string[];
    confidence?: number;
    confident_depth?: number;
    candidates_considered?: number;
    method?: string;
    abstained?: boolean;
  };
  const schemes = classification.schemes ?? [];
  stages.push({
    id: "classify",
    name: "Classify",
    headline: classification.abstained
      ? "abstained rather than guess a class"
      : `${schemes.length} ${schemes.length === 1 ? "scheme" : "schemes"}: ${schemes.join(", ")}`,
    facts: [
      { label: "Class", value: bundle.class_code ?? "none" },
      {
        label: "Confidence",
        value: classification.confidence === undefined ? "—" : score(classification.confidence),
      },
      { label: "Candidates", value: count(classification.candidates_considered ?? 0) },
      { label: "Method", value: classification.method ?? "unknown" },
    ],
    model: true,
    latencyMs: null,
    tone: classification.abstained ? "warn" : "pass",
    note:
      "One internal class drives the schema; the external schemes are derived from it, so ETIM " +
      "and UNSPSC cannot drift apart from what was actually extracted.",
  });

  // ---------------------------------------------------------------- variants
  if (series) {
    const n = series.members.length;
    stages.push({
      id: "variants",
      name: "Explode variants",
      headline: `${n} ${n === 1 ? "part number" : "part numbers"} from one ordering table`,
      facts: [
        { label: "Series", value: series.seriesSku },
        {
          label: "From own row",
          value: count(series.members.reduce((total, m) => total + m.fromOwnRow, 0)),
        },
        {
          label: "Inherited",
          value: count(series.members.reduce((total, m) => total + m.inherited, 0)),
        },
      ],
      // The claim that makes this stage cheap and safe at once.
      model: false,
      latencyMs: null,
      tone: "pass",
      note:
        "No model chooses the row. Per-variant values come from deterministic cell lookup, so a " +
        "neighbouring row cannot be read as this part's size.",
    });
  }

  // ---------------------------------------------------------------- extract
  const extraction = bundle.extraction;
  stages.push({
    id: "extract",
    name: "Extract",
    headline: `${extraction.values} of ${extraction.requested} attributes`,
    facts: [
      { label: "Cited", value: percent(extraction.citation_coverage) },
      { label: "Gaps", value: count(extraction.gaps) },
      { label: "Escalations", value: count(extraction.escalations) },
      { label: "Rejected", value: count(extraction.rejected_unverifiable) },
    ],
    model: true,
    latencyMs: extraction.latency_ms,
    tone: extraction.rejected_unverifiable > 0 ? "warn" : "pass",
    note:
      "A value whose quote could not be found in the source is discarded rather than published " +
      "with a weaker citation. `Rejected` counts exactly that.",
  });

  // ---------------------------------------------------------------- normalize
  const issues = bundle.normalization_issues.length;
  stages.push({
    id: "normalize",
    name: "Normalize",
    headline: issues === 0 ? "every value parsed to a canonical unit" : `${issues} unparsed`,
    facts: [{ label: "Issues", value: count(issues) }],
    model: false,
    latencyMs: null,
    tone: issues > 0 ? "warn" : "quiet",
    note:
      "Canonical units are what make two suppliers' figures comparable at all, and what a " +
      "cross-field rule needs before it can do arithmetic.",
  });

  // ---------------------------------------------------------------- validate
  const validation = bundle.validation;
  stages.push({
    id: "validate",
    name: "Validate",
    headline: `${validation.checks} checks, ${validation.failures} failed`,
    facts: [
      { label: "Warnings", value: count(validation.warnings) },
      { label: "Skipped", value: count(validation.skipped_rules) },
      { label: "Consistency", value: percent(bundle.certificate.summary.quality_index.consistency) },
    ],
    model: false,
    latencyMs: null,
    tone: validation.failures > 0 ? "fail" : validation.warnings > 0 ? "warn" : "pass",
    note:
      "Layers L0 to L3, all deterministic. A rule that could not run is counted as skipped rather " +
      "than passed, because a check that did not execute has established nothing.",
  });

  // ---------------------------------------------------------------- decide
  //
  // A null threshold is not a threshold of zero. It means no cutoff on the calibration set could
  // hold the requested error budget at the requested confidence, so nothing was auto-accepted on a
  // validated policy at all. Rendering it as 0.000 would read as "accept everything", which is the
  // precise opposite, so it gets its own words.
  stages.push({
    id: "decide",
    name: "Score and decide",
    headline: `${bundle.metrics.values_publishable} auto-accepted, ${bundle.metrics.values_needing_review} queued`,
    facts: [
      {
        label: "Threshold",
        value: policy.threshold === null ? "none achievable" : score(policy.threshold),
      },
      { label: "Error budget", value: percent(policy.epsilon) },
      { label: "Verifiability", value: percent(bundle.metrics.verifiability) },
    ],
    model: false,
    latencyMs: null,
    tone:
      policy.threshold === null
        ? "fail"
        : bundle.metrics.values_needing_review > 0
          ? "warn"
          : "pass",
    note:
      policy.threshold === null
        ? "No cutoff on the calibration set could hold the requested error budget at the " +
          "requested confidence, so there is no validated policy to auto-accept against."
        : "The threshold is the one the calibration set produced for the chosen error budget, not " +
          "a number somebody picked. Everything below it becomes a review task, not a publication.",
  });

  // ---------------------------------------------------------------- copy
  if (bundle.copy) {
    const check = bundle.copy.claim_check;
    stages.push({
      id: "copy",
      name: "Generate copy",
      headline: bundle.copy.published
        ? `${check.supported} of ${check.claims} claims supported`
        : "withheld: a claim could not be supported",
      facts: [
        { label: "Unsupported", value: count(check.unsupported) },
        { label: "Banned", value: count(check.banned) },
        { label: "Attempts", value: count(bundle.copy.attempts) },
      ],
      model: true,
      latencyMs: null,
      tone: check.passed ? "pass" : "fail",
      note:
        "Generation is checked against the extracted facts before publication, so a fluent " +
        "sentence containing an unsupported number is discarded rather than shipped.",
    });
  }

  // ---------------------------------------------------------------- certify
  stages.push({
    id: "certify",
    name: "Certify",
    headline: bundle.certificate.signature_verified
      ? "signed and verified"
      : "signature did not verify",
    facts: [
      {
        label: "Composite",
        value: score(bundle.certificate.summary.quality_index.composite ?? 0),
      },
      { label: "Pipeline", value: bundle.certificate.pipeline_version },
      { label: "Recorded", value: `${bundle.certificate.summary.wall_clock_seconds ?? 0}s` },
    ],
    model: false,
    latencyMs: null,
    tone: bundle.certificate.signature_verified ? "pass" : "fail",
    note:
      "The certificate is the artifact a buyer can audit without trusting this console. It is " +
      "verified server-side because a browser has no key to check an HMAC with.",
  });

  // ---------------------------------------------------------------- syndicate
  const ready = bundle.channels.filter((channel) => channel.published).length;
  stages.push({
    id: "syndicate",
    name: "Syndicate",
    headline: `${ready} of ${bundle.channels.length} channels ready`,
    facts: bundle.channels.map((channel) => ({
      label: channel.name,
      value: channel.published ? `${channel.value_count} values` : "held",
    })),
    model: false,
    latencyMs: null,
    tone: ready === bundle.channels.length ? "pass" : "warn",
    note:
      "A channel is held when a field it requires is missing, rather than published with a gap. " +
      "Pre-flight is per channel because each one demands different fields.",
  });

  return stages;
}

/** How much of the run was a model call, which is the point of counting. */
export function modelStageShare(stages: PipelineStage[]): {
  model: number;
  deterministic: number;
} {
  const model = stages.filter((stage) => stage.model).length;
  return { model, deterministic: stages.length - model };
}
