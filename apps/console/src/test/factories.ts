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

import type { EnrichLimits, EnrichResponse, EnrichSummary } from "@/lib/enrich";
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
    // Provenance diagnostics, reported beside completeness and never inside it. Non-null here
    // because the interesting fixture is one where the input suggested values and something
    // confirmed some of them — `null` is the "nothing observable" case, which is not the default
    // worth having.
    self_declared: 0.5,
    corroborated: 0.25,
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

// ------------------------------------------------------------------ enrichment
//
// Shaped against the real `POST /api/enrich` response, which is asserted field by field in
// `tests/test_enrich.py`. The default is the *awkward* case rather than the flattering one: a
// description-only submission on a cold start, so nothing auto-publishes, the source is the
// submission rather than a datasheet, and the delivery row is sparse. That is what a first run
// actually looks like, and a fixture showing a fully-populated row would let the screen's honesty
// about sparseness go untested.

export function enrichLimits(overrides: Partial<EnrichLimits> = {}): EnrichLimits {
  return {
    required: ["mpn", "manufacturer"],
    optional: ["description", "source_url", "brand", "class_code"],
    one_of: ["description", "source_url"],
    max_description_chars: 4000,
    max_url_chars: 2048,
    max_document_bytes: 8 * 1024 * 1024,
    fetch_timeout_seconds: 20,
    url_schemes: ["https"],
    concurrent_runs: 1,
    outputs: ["csv", "xlsx"],
    model_calls_per_run: { without_copy: 2, with_copy: 3 },
    // A trimmed stand-in for `axiom.pipeline.progress.STAGE_PLAN`, which the API serves so the run
    // screen can draw its checklist before the first progress poll returns. Four rows rather than
    // thirteen: the form's behaviour depends on the *shape* — that a plan exists, and that `retrieve`
    // and `copy` are the two rows dropped conditionally — not on the full list. `retrieve` and `copy`
    // are both here precisely because they are the conditional ones.
    stages: [
      {
        id: "retrieve",
        name: "Find the document",
        narration: "Looking for the manufacturer's own document.",
        model: false,
      },
      {
        id: "classify",
        name: "Classify",
        narration: "Asking the model which product class this is.",
        model: true,
      },
      {
        id: "extract",
        name: "Extract",
        narration: "Reading values out of the source, each with a verbatim quote.",
        model: true,
      },
      {
        id: "copy",
        name: "Generate copy",
        narration: "Generating copy and claim-checking every sentence.",
        model: true,
      },
    ],
    notes: ["Runs the online pipeline, so this endpoint costs money per submission."],
    ...overrides,
  };
}

export function enrichSummary(overrides: Partial<EnrichSummary> = {}): EnrichSummary {
  return {
    sku: "PDSH4816AF",
    class_code: "APP.KIT.DISHWASHER.BUILTIN",
    class_from_fallback: false,
    classification: { method: "retrieval_only", abstained: false, candidates_considered: 1 },
    extraction: {
      requested: 20,
      values: 0,
      gaps: 20,
      rejected_unverifiable: 0,
      citation_coverage: 0,
      escalations: 0,
      latency_ms: 1,
    },
    from_description: { extracted: 2, refused: 0 },
    // A cold start: no calibration data, so nothing clears a validated threshold.
    values: { total: 2, publishable: 0, needing_review: 2 },
    manufacturer_specifications: { total: 0, mapped: 0, unmapped: 0 },
    gaps: { total: 20, required: 8 },
    validation: { checks: 2, failures: 0, warnings: 0, skipped_rules: 6, consistency: 1 },
    certificate: {
      certificate_id: "ec_d5f5a99e2e9a",
      signature_verified: true,
      pipeline_version: "axiom-0.1.0",
      generated_at: "2026-08-23T07:11:29.655340+00:00",
      quality_index: qualityIndex({
        // A first run against a typed description: barely anything is complete and nothing is
        // independently verified, but the description itself suggested a couple of values — which
        // is exactly the state `self_declared` exists to make readable. A completeness of 0.08 with
        // self_declared above it means the work item is retrieval, not a supplier request.
        completeness: 0.08,
        verifiability: 0,
        richness: 0,
        self_declared: 0.25,
        corroborated: 0,
        composite: 0.28,
      }),
      attributes_populated: 2,
      attributes_with_evidence: 2,
    },
    cost: {
      usd: 0.000_412,
      calls: 1,
      escalations: 0,
      input_tokens: 812,
      output_tokens: 14,
      latency_ms: 1_940,
    },
    policy: policySummary({
      threshold: null,
      achievable: false,
      coverage: 0,
      calibration_size: 0,
      reason: "no calibration data",
    }),
    calibrator: "untrained-heuristic",
    // Attempted and found nothing, which is the awkward case: `Frigidaire` has no declared domain,
    // so there was nowhere to look. The note names the remedy rather than reporting a dead end.
    retrieval: {
      attempted: true,
      found: false,
      from_library: false,
      manufacturer: null,
      documents: [],
      requests_made: 0,
      bytes_fetched: 0,
      notes: [
        "no manufacturer domain is declared for 'Frigidaire', so there was no site to search. " +
          "Add it to schema/sourcing.yaml.",
      ],
    },
    source: {
      kind: "submission",
      document_id: "PDSH4816AF.submission@f9d857ff",
      sha256: "f9d857ff" + "0".repeat(56),
      uri: "submission:PDSH4816AF",
      doc_type: "supplier_feed",
      size_bytes: 118,
      pages: 1,
      tables: 0,
      parser: "text",
      was_already_stored: false,
      warnings: [],
      evidential_weight:
        "The submission itself. Values cite the field they were read from, which records what " +
        "was supplied rather than what a manufacturer published.",
    },
    manufacturer: {
      name: "Frigidaire",
      supplier_code: null,
      looks_like_a_distributor: false,
      publishable_as_manufacturer: true,
    },
    brand: { requested: null, resolved: null, method: null },
    channels: [
      { name: "cx1_pim", published: false, value_count: 0, withheld: [] },
      { name: "schema_org", published: false, value_count: 0, withheld: [] },
    ],
    notes: [
      "no manufacturer URL was supplied, so the submission itself is the source document. Every " +
        "citation resolves to a field you typed, at its hash — a real provenance claim, and a " +
        "weaker one than a datasheet.",
    ],
    ...overrides,
  };
}

export function enrichResponse(overrides: Partial<EnrichResponse> = {}): EnrichResponse {
  const summary = overrides.summary ?? enrichSummary();
  return {
    sku: summary.sku,
    slug: summary.sku,
    replaced: false,
    summary,
    queue: {
      pending: 2,
      accepted: 0,
      total: 2,
      by_reason: { below_threshold: 2 },
      blocking_failures: 0,
      warnings: 0,
    },
    bundle: stageBundle(),
    document: sourceDocument(),
    policy: summary.policy,
    calibrator: summary.calibrator,
    delivery: {
      // 29 of 252, measured from a real description-only run. Sparse is the correct answer: the
      // client's own ground truth leaves 173 columns blank.
      populated: 29,
      columns: 252,
      blank: 223,
      withheld: 0,
      compliant: true,
      content_hash: "a1b2c3d4" + "0".repeat(56),
      provenance: { passthrough: 6, derived: 21, extracted: 2 },
      cited_columns: ["Attribute Value 1"],
      notes: [],
      input_notes: [],
      files: {
        csv: "PDSH4816AF.delivery.csv",
        xlsx: "PDSH4816AF.delivery.xlsx",
        provenance: "PDSH4816AF.provenance.json",
      },
    },
    persisted: {
      session: "data/sessions/PDSH4816AF.json",
      bundle: "data/console/PDSH4816AF.bundle.json",
      delivery_csv: "data/enrich/PDSH4816AF.delivery.csv",
      delivery_xlsx: "data/enrich/PDSH4816AF.delivery.xlsx",
      provenance: "data/enrich/PDSH4816AF.provenance.json",
    },
    links: {
      review: "/api/session/PDSH4816AF",
      delivery_csv: "/api/enrich/PDSH4816AF/delivery?output=csv",
      delivery_xlsx: "/api/enrich/PDSH4816AF/delivery?output=xlsx",
      source_artifact: "/api/artifact/" + summary.source.sha256,
    },
    ...overrides,
  };
}
