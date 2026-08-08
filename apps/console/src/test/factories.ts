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
  AttributeValue,
  CohortMember,
  CohortScore,
  CohortStudy,
  CrossSourceConflict,
  CrossSourceView,
  EquivalenceCandidate,
  EquivalenceComparison,
  EquivalenceView,
  EvidenceSpan,
  FormalCheck,
  GeneratedCopy,
  QualityIndex,
  RiskPolicySummary,
  SkuBundle,
  SourceDocument,
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

export function equivalenceComparison(
  overrides: Partial<EquivalenceComparison> = {},
): EquivalenceComparison {
  return {
    attribute_code: "pressure_rating_wog",
    name: "Pressure Rating (WOG)",
    interchange: "functional",
    substitution: "at_least",
    compatibility: "differs",
    decides: true,
    reference_value: 600,
    candidate_value: 400,
    reference_display: "600 psi",
    candidate_display: "400 psi",
    match_kind: null,
    detail: "does not satisfy substitution: at_least",
    ...overrides,
  };
}

export function equivalenceCandidate(
  overrides: Partial<EquivalenceCandidate> = {},
): EquivalenceCandidate {
  return {
    reference_sku: "BA-100-100",
    candidate_sku: "77C-105",
    verdict: "drop_in",
    substitutable: true,
    needs_enrichment: false,
    reason: "form and fit match, and the candidate exceeds the reference on flow coefficient (cv)",
    reference_class: "PLB.VLV.BALL.2PC",
    candidate_class: "PLB.VLV.BALL.2PC",
    same_class: true,
    reference_brand: "Milwaukee Valve",
    candidate_brand: "Apollo Valves",
    cross_brand: true,
    deciding: 12,
    compared: 11,
    agreed: 10,
    satisfied: 1,
    blocking: 0,
    unknown: 1,
    cosmetic_differences: 0,
    inapplicable: [],
    unclassified: [],
    coverage_note: "11 of 12 interchange-relevant attributes were established on both records",
    blocking_detail: [],
    unknown_detail: [],
    satisfied_detail: [],
    cosmetic_detail: [],
    agreed_attributes: ["body_material", "nominal_size"],
    comparisons: [],
    ...overrides,
  };
}

export function equivalenceView(overrides: Partial<EquivalenceView> = {}): EquivalenceView {
  const candidates = overrides.candidates_detail ?? [equivalenceCandidate()];
  return {
    generated_at: "2026-08-07T00:00:00+00:00",
    reference_sku: "BA-100-100",
    source: "pipeline",
    measured: true,
    source_note:
      "Records are real pipeline output: every value was extracted from a source document.",
    candidates: candidates.length,
    substitutable: candidates.filter((candidate) => candidate.substitutable).length,
    indeterminate: candidates.filter((candidate) => candidate.verdict === "indeterminate").length,
    by_verdict: {
      identical: 0,
      drop_in: candidates.filter((candidate) => candidate.verdict === "drop_in").length,
      functional_equivalent: candidates.filter(
        (candidate) => candidate.verdict === "functional_equivalent",
      ).length,
      not_equivalent: candidates.filter((candidate) => candidate.verdict === "not_equivalent")
        .length,
      indeterminate: candidates.filter((candidate) => candidate.verdict === "indeterminate").length,
    },
    best_substitute: candidates.find((candidate) => candidate.substitutable)?.candidate_sku ?? null,
    best_verdict: candidates.find((candidate) => candidate.substitutable)?.verdict ?? null,
    cross_brand_substitutes: candidates.filter(
      (candidate) => candidate.substitutable && candidate.cross_brand,
    ).length,
    records: 15,
    skus: ["BA-100-100", "77C-105"],
    failures: [],
    ...overrides,
    candidates_detail: candidates,
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

/**
 * One value on a variant, in whichever of the three provenance shapes matters.
 *
 * `table_extraction` plus a `table_ref` is a value read from this variant's own row.
 * A non-null `derived_from` is one inherited from the series specification. The distinction is not
 * cosmetic — it is what `variantGroups` counts — so it is expressed here rather than assumed.
 */
export function variantValue(overrides: {
  code: string;
  display?: string;
  fromCell?: string | null;
  inheritedBy?: string | null;
}): AttributeValue {
  const fromCell = overrides.fromCell ?? null;
  return {
    attribute_code: overrides.code,
    value_raw: overrides.display ?? "DN20",
    value_canonical: overrides.display ?? "DN20",
    value_display: overrides.display ?? "DN20",
    method: fromCell !== null ? "table_extraction" : "document_extraction",
    confidence: 0.95,
    status: "auto_accepted",
    evidence:
      fromCell !== null
        ? [{ table_ref: fromCell, page: 1, quote_verified: true } as unknown as EvidenceSpan]
        : [],
    validations: [],
    model_id: null,
    model_tier: null,
    prompt_version: null,
    schema_version: "PLB.VLV.BALL.2PC@v1",
    version: 1,
    superseded_by: null,
    derived_from: overrides.inheritedBy
      ? `series specification inherited by ${overrides.inheritedBy}`
      : null,
    reviewed_by: null,
    reviewed_at: null,
    created_at: "2026-08-06T00:00:00+00:00",
    score: 0.9,
    decision: null,
    features: {},
    is_publishable: true,
    has_verified_evidence: true,
    citation_summary: [],
  };
}

/**
 * A bundle carrying only what `variantGroups` reads: the parent pointer, values and gaps.
 *
 * Same reasoning as `triageBundle` — the grouping touches three fields, and completing the other
 * forty would imply the assertions depend on them.
 */
export function variantBundle(overrides: {
  sku: string;
  parentSku?: string | null;
  values?: AttributeValue[];
  inapplicable?: number;
}): SkuBundle {
  return {
    sku: overrides.sku,
    record: { sku: overrides.sku, parent_sku: overrides.parentSku ?? null },
    values: overrides.values ?? [],
    gaps: Array.from({ length: overrides.inapplicable ?? 0 }, (_, index) => ({
      attribute_code: `withheld_${index}`,
      recommended_action: "accept_as_not_applicable",
      is_required: false,
    })),
  } as unknown as SkuBundle;
}

// ------------------------------------------------------------------ pipeline stages
//
// The numbers below are copied from the real `data/console/BA-100-075.bundle.json`, not invented.
// The stage view's whole purpose is to report a run faithfully, so a fixture describing a shape the
// pipeline never produces would test the wrong thing — and these particular values carry the awkward
// cases worth keeping: one channel held rather than published, three validation rules skipped, and a
// `quality_index` whose `composite` was absent until the API reinterpreted it on read.

export function sourceDocument(overrides: Partial<SourceDocument> = {}): SourceDocument {
  return {
    document_id: "ba100@f7500023",
    uri: "local://f7/50/f7500023aad5093f9f77c12af5186aa8d4c5a67db25550c5c49a589959bf1da0.txt",
    sha256: "f7500023aad5093f9f77c12af5186aa8d4c5a67db25550c5c49a589959bf1da0",
    doc_type: "spec_sheet",
    fetched_at: "2026-08-05T03:15:49.608515Z",
    page_count: 1,
    revision_label: null,
    supplier_id: null,
    license_note: null,
    parser: "text",
    line_count: 26,
    table_count: 1,
    warnings: [],
    size_bytes: 1175,
    storage_uri: "local://f7/50/f7500023aad5093f9f77c12af5186aa8d4c5a67db25550c5c49a589959bf1da0.txt",
    ...overrides,
  };
}

export function policySummary(overrides: Partial<RiskPolicySummary> = {}): RiskPolicySummary {
  return {
    epsilon: 0.05,
    confidence_level: 0.95,
    threshold: 0.62,
    coverage: 0.87,
    accepted: 15,
    accepted_errors: 0,
    observed_error_rate: 0,
    error_upper_bound: 0.04,
    calibration_size: 120,
    achievable: true,
    reason: "",
    ...overrides,
  };
}

/**
 * A bundle carrying exactly the fields `pipelineStages` reads.
 *
 * Cast at the boundary like `triageBundle`, for the same reason: the derivation touches nine of the
 * bundle's keys and filling the rest would imply the assertions depend on them.
 */
export function stageBundle(): SkuBundle {
  return {
    sku: "BA-100-075",
    class_code: "PLB.VLV.BALL.2PC",
    classification_summary: {
      class_code: "PLB.VLV.BALL.2PC",
      confidence: 0.92,
      confident_depth: 4,
      schemes: ["internal", "ETIM", "UNSPSC"],
      candidates_considered: 2,
      method: "model_adjudicated",
      abstained: false,
    },
    extraction: {
      requested: 23,
      values: 15,
      gaps: 8,
      rejected_unverifiable: 0,
      citation_coverage: 1,
      input_tokens: 3225,
      output_tokens: 1649,
      escalations: 0,
      latency_ms: 7022,
    },
    normalization_issues: [],
    validation: { checks: 15, failures: 0, warnings: 1, skipped_rules: 3, consistency: 0.9167 },
    metrics: {
      fill_rate: 0.75,
      verifiability: 1,
      values_total: 15,
      values_publishable: 15,
      values_needing_review: 0,
      gaps_total: 8,
      gaps_required: 3,
      conflicts: [],
    },
    certificate: {
      generated_at: "2026-08-05T03:16:03.271000+00:00",
      pipeline_version: "axiom-0.1.0",
      signature_verified: true,
      summary: {
        attributes_required: 12,
        attributes_populated: 15,
        quality_index: {
          completeness: 0.75,
          verifiability: 1,
          consistency: 1,
          richness: 0,
          composite: 0.83,
        },
        wall_clock_seconds: 10.65,
      },
    },
    // One held, one published — the state that proves pre-flight is per channel.
    channels: [
      { name: "cx1_pim", published: false, value_count: 0, withheld: [] },
      { name: "schema_org", published: true, value_count: 15, withheld: [] },
    ],
    cost: { calls: 3, escalations: 0, latency_ms: 13319 },
    copy: null,
  } as unknown as SkuBundle;
}
