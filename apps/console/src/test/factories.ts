/**
 * Fixture builders for the console tests.
 *
 * Each returns a *valid, boring* object and takes overrides, so a test varies exactly one thing and
 * its name describes that thing. Assembling these inline per test would bury the one field that
 * mattered under thirty that did not.
 *
 * These are shaped by hand against `types.ts`. That is safe here in a way it would not be in
 * production code, because `scripts/check_console_types.py` holds `types.ts` itself to the Python
 * models on every push — so a drift shows up there rather than as a fixture that silently describes
 * a shape the API stopped emitting.
 */

import type {
  CohortMember,
  CohortScore,
  CohortStudy,
  CrossSourceConflict,
  CrossSourceView,
  FormalCheck,
  GeneratedCopy,
  QualityIndex,
  SkuBundle,
} from "@/lib/types";

export function cohortScore(overrides: Partial<CohortScore> = {}): CohortScore {
  return {
    completeness: 0.75,
    verifiability: 1,
    consistency: 1,
    richness: null,
    composite: 0.9,
    field_presence: 0.75,
    values_present: 15,
    values_publishable: 15,
    values_with_evidence: 15,
    validation_failures: 0,
    required_total: 12,
    checks_run: 12,
    failed_rules: [],
    ...overrides,
  };
}

export function cohortMember(overrides: Partial<CohortMember> = {}): CohortMember {
  const before = overrides.before ?? cohortScore({ completeness: 0, verifiability: 0, composite: 0.28, field_presence: 0.33 });
  const after = overrides.after ?? cohortScore();
  return {
    sku: "BA-100-075",
    arm: "treatment",
    before,
    after,
    delta: {
      completeness: after.completeness - before.completeness,
      verifiability: after.verifiability - before.verifiability,
      consistency: after.consistency - before.consistency,
      composite: after.composite - before.composite,
      field_presence: after.field_presence - before.field_presence,
    },
    ...overrides,
  };
}

export function cohortStudy(overrides: Partial<CohortStudy> = {}): CohortStudy {
  return {
    available: true,
    schema_version: "PLB.VLV.BALL.2PC@v1",
    treatment_skus: 1,
    control_skus: 1,
    trustworthy: true,
    control_drift: { completeness: 0, verifiability: 0, consistency: 0, composite: 0 },
    drifted_dimensions: [],
    lift: { completeness: 0.75, verifiability: 1, consistency: 0, composite: 0.62 },
    field_presence: { before: 0.333, after: 0.75 },
    treatment_before: { completeness: 0, verifiability: 0, consistency: 1, composite: 0.278 },
    treatment_after: { completeness: 0.75, verifiability: 1, consistency: 1, composite: 0.903 },
    notes: [],
    members: [
      cohortMember(),
      cohortMember({ sku: "T-113-100", arm: "control", before: cohortScore(), after: cohortScore() }),
    ],
    ...overrides,
  };
}

export function conflict(overrides: Partial<CrossSourceConflict> = {}): CrossSourceConflict {
  return {
    attribute_code: "pressure_rating_wog",
    resolved: true,
    reason: "ba100 is the newer revision (Rev C 2024-08)",
    winner: "'600 psi' (ba100 Rev C 2024-08)",
    observations: [
      {
        document_id: "ba100",
        revision_label: "Rev C 2024-08",
        supplier_id: "milwaukee",
        value_display: "600 psi",
      },
      {
        document_id: "catalog",
        revision_label: "Rev A 2022-03",
        supplier_id: "acme",
        value_display: "400 psi",
      },
    ],
    ...overrides,
  };
}

export function crossSourceView(overrides: Partial<CrossSourceView> = {}): CrossSourceView {
  return {
    generated_at: "2026-08-06T00:00:00+00:00",
    dry_run: false,
    sources: [
      {
        source: "data/samples/ba100.txt",
        document_id: "ba100",
        revision_label: "Rev C 2024-08",
        revision_method: "letter_and_date",
        supplier_id: "milwaukee",
        values: 5,
        parser: "text",
      },
      {
        source: "data/samples/ba100-catalog.txt",
        document_id: "catalog",
        revision_label: "Rev A 2022-03",
        revision_method: "letter_and_date",
        supplier_id: "acme",
        values: 6,
        parser: "text",
      },
    ],
    applicable: true,
    documents: 2,
    corroborated: 3,
    disagreements: 1,
    unresolved: 0,
    single_source: 1,
    passed: true,
    corroborated_attributes: ["body_material", "end_connection", "seat_material"],
    single_source_attributes: ["country_of_origin"],
    conflicts: [conflict()],
    ...overrides,
  };
}

export function formalCheck(overrides: Partial<FormalCheck> = {}): FormalCheck {
  return {
    checked: 4,
    contradictions: 0,
    indeterminate: 0,
    violated_rules: [],
    passed: true,
    conclusive: true,
    error: null,
    premises: "The valve body alloy is Bronze C84400. The end connection is NPT threaded.",
    claims: [
      {
        claim: "Bronze ball valve for industrial service.",
        verdict: "satisfiable",
        rules: [],
        contradiction: false,
        indeterminate: false,
        confidence: 1,
        detail: "",
      },
    ],
    ...overrides,
  };
}

export function generatedCopy(overrides: Partial<GeneratedCopy> = {}): GeneratedCopy {
  return {
    sku: "BA-100-075",
    published: true,
    headline: "Bronze ball valve, 3/4 in NPT",
    short_description: "Two-piece full port bronze ball valve.",
    long_description: "Rated to 600 PSI WOG.",
    bullets: ["600 PSI WOG", "Bronze C84400 body"],
    attempts: 1,
    prompt_version: "copy@v1",
    model_id: "zai.glm-4.7",
    model_tier: "mid",
    error: null,
    claim_check: { claims: 3, supported: 3, unsupported: 0, banned: 0, passed: true },
    claims: [
      {
        kind: "quantity",
        text: "600 PSI",
        verdict: "supported",
        reason: "matches verified 'pressure_rating_wog'",
        field: "bullets",
        supported_by: "pressure_rating_wog",
      },
    ],
    formal_check: null,
    ...overrides,
  };
}

export function qualityIndex(overrides: Partial<QualityIndex> = {}): QualityIndex {
  return {
    completeness: 0.75,
    verifiability: 1,
    consistency: 1,
    richness: 0.5,
    composite: 0.83,
    measured_dimensions: ["completeness", "consistency", "richness", "verifiability"],
    weights: { completeness: 0.35, verifiability: 0.3, consistency: 0.25, richness: 0.1 },
    ...overrides,
  };
}

/**
 * A bundle carrying only what `reviewOrder` and `unresolvedConflicts` read.
 *
 * Cast at the boundary rather than filled out to 40 fields. The triage functions touch four of them,
 * and a complete bundle here would imply the test depends on the rest.
 */
export function triageBundle(overrides: {
  sku: string;
  failures?: number;
  gapsRequired?: number;
  needingReview?: number;
  unresolved?: number;
}): SkuBundle {
  return {
    sku: overrides.sku,
    validation: { failures: overrides.failures ?? 0, warnings: 0 },
    metrics: {
      gaps_required: overrides.gapsRequired ?? 0,
      values_needing_review: overrides.needingReview ?? 0,
    },
    cross_source:
      overrides.unresolved === undefined ? null : { unresolved: overrides.unresolved },
  } as unknown as SkuBundle;
}
