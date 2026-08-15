/**
 * Domain types mirroring the Python models in `packages/axiom`.
 *
 * These are a hand-maintained projection of Pydantic models, not a generated client. The
 * server-side shape is assembled by `axiom.console.projection`, which is shared by the API
 * and the offline fixture exporter so those two cannot drift from each other. This file can
 * still drift from *both*, so the source of truth for every shape below is named in its doc
 * comment. Generating these from the API's OpenAPI schema is the eventual fix.
 */

// ---------------------------------------------------------------- evidence
// axiom.core.evidence

/** Region on a rendered page, in PDF points with the origin at top-left. */
export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export type DocumentType =
  | "spec_sheet"
  | "catalog_page"
  | "installation_manual"
  | "certificate"
  | "safety_data_sheet"
  | "declaration"
  | "drawing"
  | "product_image"
  | "web_page"
  | "supplier_feed"
  | "erp_export"
  | "unknown";

export interface SourceDocument {
  document_id: string;
  uri: string;
  sha256: string;
  doc_type: DocumentType;
  fetched_at: string;
  page_count: number | null;
  revision_label: string | null;
  supplier_id: string | null;
  license_note: string | null;
  /** Added by the fixture exporter, from ParsedDocument. */
  parser: string;
  line_count: number;
  table_count: number;
  warnings: string[];
  size_bytes: number;
  storage_uri: string;
}

/** A verifiable pointer to the exact place a value was found. Page is 1-indexed. */
export interface EvidenceSpan {
  span_id: string;
  document_id: string;
  document_sha256: string;
  quote: string;
  page: number | null;
  bbox: BoundingBox | null;
  /** e.g. `t1:r14:c3` — table, row and column. */
  table_ref: string | null;
  quote_verified: boolean;
  match_score: number | null;
}

// ---------------------------------------------------------------- validation
// axiom.core.validation

export type ValidationLayer = "L0" | "L1" | "L2" | "L3" | "L4" | "L5" | "L6";
export type Verdict = "pass" | "fail" | "warn" | "ambiguous" | "skipped";
export type Severity = "error" | "warning" | "info";

export interface ValidationResult {
  layer: ValidationLayer;
  rule_id: string;
  verdict: Verdict;
  severity: Severity;
  reason: string;
  counterexample: string | null;
  suggested_fix: string | null;
  detail: string | null;
}

/** Only an error-severity failure blocks publication. Mirrors `is_blocking`. */
export function isBlocking(result: ValidationResult): boolean {
  return result.verdict === "fail" && result.severity === "error";
}

export function needsReview(result: ValidationResult): boolean {
  return result.verdict === "fail" || result.verdict === "warn" || result.verdict === "ambiguous";
}

// ---------------------------------------------------------------- values
// axiom.core.values

export type DerivationMethod =
  // extraction family: evidence mandatory
  | "document_extraction"
  | "table_extraction"
  | "image_extraction"
  | "web_extraction"
  | "supplier_feed"
  // derivation family
  | "unit_conversion"
  | "enum_resolution"
  | "computed"
  // inference family: never auto-accepted
  | "part_number_grammar"
  | "family_inference"
  | "statistical_default"
  // legacy: present in the item master, of unknown origin, never publishable without evidence
  | "legacy_record"
  // human
  | "human_entry"
  | "human_correction";

export type ValueStatus =
  | "candidate"
  | "auto_accepted"
  | "queued_for_review"
  | "human_approved"
  | "rejected"
  | "superseded";

export interface Quantity {
  magnitude: number;
  /** UCUM-style unit code, e.g. `psi`, `mm`, `N.m`. */
  unit: string;
}

export interface ValueRange {
  minimum: number;
  maximum: number;
  unit: string;
}

export type CanonicalValue =
  | number
  | string
  | boolean
  | string[]
  | Quantity
  | ValueRange
  | null;

/** axiom.confidence.policy.AcceptanceDecision */
export type DecisionReason =
  | "auto_accepted"
  | "below_threshold"
  | "blocking_validation"
  | "unverified_evidence"
  | "inferred_value"
  | "no_validated_policy";

export interface AcceptanceDecision {
  attribute_code: string;
  score: number;
  accepted: boolean;
  status: ValueStatus;
  reason_code: DecisionReason;
  detail: string;
}

export interface AttributeValue {
  attribute_code: string;
  value_raw: string | null;
  value_canonical: CanonicalValue;
  value_display: string | null;
  method: DerivationMethod;
  confidence: number;
  status: ValueStatus;
  evidence: EvidenceSpan[];
  validations: ValidationResult[];
  model_id: string | null;
  model_tier: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  version: number;
  superseded_by: number | null;
  derived_from: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  /** Projected by the exporter. */
  score: number;
  decision: AcceptanceDecision | null;
  features: Record<string, number>;
  is_publishable: boolean;
  has_verified_evidence: boolean;
  citation_summary: string[];
  /**
   * Present only once a human has ruled on this value. Set by
   * `axiom.console.projection.overlay_review_decisions`, which joins the review session onto
   * the pipeline's own output rather than overwriting it. `human_accept` means the model was
   * right and a reviewer confirmed it; `human_correct` means it was wrong.
   */
  review_reason?: string;
  review_detail?: string;
  /**
   * Validation layer L4's verdict on this attribute, when a cross-source run has been saved for
   * this SKU. Joined at read time by `axiom.console.projection.overlay_cross_source`.
   */
  cross_source?: CrossSourceVerdict | null;
}

/** One source's statement about an attribute, as L4 recorded it. */
export interface CrossSourceObservation {
  document_id: string;
  revision_label: string | null;
  supplier_id: string | null;
  value_display: string | null;
}

/**
 * What a second source did to one attribute.
 *
 * The four states are genuinely different positions and the UI must not blur them:
 *
 * - `corroborated` — two independent documents state the same value. The strongest evidence this
 *   system produces; a single citation proves a value was printed, two prove it was not a typo.
 * - `superseded` — they disagree and a revision marker ordered them. The older figure is stale
 *   rather than wrong, which is a currency problem, not a quality one.
 * - `conflict` — they disagree and nothing could order them. Blocks publication, because picking
 *   a side on no evidence is the thing L4 exists to prevent.
 * - `single_source` — only one document mentions it. Weaker than corroboration, and recorded
 *   rather than omitted so a reviewer can see which values rest on one reading.
 */
export interface CrossSourceVerdict {
  state: "corroborated" | "superseded" | "conflict" | "single_source";
  reason: string | null;
  winner?: string | null;
  observations: CrossSourceObservation[];
}

/** axiom.validate.cross_source.Disagreement */
export interface CrossSourceConflict {
  attribute_code: string;
  resolved: boolean;
  reason: string;
  winner: string | null;
  observations: CrossSourceObservation[];
}

/**
 * axiom.validate.cross_source.CrossSourceReport, as joined onto a bundle.
 *
 * `applicable` is false with fewer than two documents, and that is not a pass — one document
 * cannot corroborate itself. `dry_run` marks findings derived from scripted responses, which must
 * never be presented as a measurement.
 */
export interface CrossSourceView {
  generated_at: string | null;
  dry_run: boolean;
  sources: {
    source: string;
    document_id: string;
    revision_label: string | null;
    revision_method: string | null;
    supplier_id: string | null;
    values: number;
    parser: string;
  }[];
  applicable: boolean;
  documents: number;
  corroborated: number;
  disagreements: number;
  unresolved: number;
  single_source: number;
  passed: boolean;
  corroborated_attributes: string[];
  single_source_attributes: string[];
  conflicts: CrossSourceConflict[];
}

// ---------------------------------------------------------------- equivalence
// axiom.resolve.equivalence, axiom.schema.models

/**
 * axiom.schema.models.Interchange — what a difference in an attribute does to a substitution.
 *
 * Declared per attribute in `schema/attributes/*.yaml`, not in the engine, because whether a
 * handle style blocks a substitution is a merchandising judgement rather than a logic question.
 *
 * - `defining` — differs, so it is a different product. Nothing else rescues it.
 * - `critical` — form or fit differs, so not a drop-in. It may still do the same job.
 * - `functional` — must be met or exceeded, per `SubstitutionRule`.
 * - `cosmetic` — reported as a difference, never as a blocker.
 */
export type Interchange = "defining" | "critical" | "functional" | "cosmetic";

/**
 * axiom.schema.models.SubstitutionRule — the direction a candidate must satisfy.
 *
 * Where the asymmetry lives. A 600 psi valve substitutes for a 400 psi one and the reverse is a
 * downgrade, so comparing for equality would either refuse every safe upgrade or accept every
 * unsafe one.
 */
export type SubstitutionRule = "equal" | "at_least" | "at_most" | "encloses" | "superset";

/**
 * axiom.resolve.equivalence.Verdict.
 *
 * Named `EquivalenceVerdict` because `Verdict` is already taken by the validation layers.
 *
 * `indeterminate` is deliberately not a synonym for `not_equivalent`. "I cannot tell" and "no"
 * are different answers, and collapsing them would hide that the remedy is to enrich a record
 * rather than to reject a part.
 */
export type EquivalenceVerdict =
  | "identical"
  | "drop_in"
  | "functional_equivalent"
  | "not_equivalent"
  | "indeterminate";

/**
 * axiom.resolve.equivalence.Compatibility — one attribute's outcome.
 *
 * The three `unknown_*` states are the point of the whole feature: an attribute nobody
 * established is never a match. `inapplicable` is separate again, and means the attribute is not
 * bound to both product classes — a gate valve has no port type in the ball-valve sense, and
 * recording that as unestablished would make every cross-class comparison look like a data gap.
 */
export type Compatibility =
  | "agrees"
  | "satisfies"
  | "differs"
  | "unknown_reference"
  | "unknown_candidate"
  | "unknown_both"
  | "not_comparable"
  | "inapplicable";

/** axiom.resolve.equivalence.AttributeComparison */
export interface EquivalenceComparison {
  attribute_code: string;
  name: string;
  /** Null when the schema declares no interchange level. Excluded from the verdict, and listed. */
  interchange: Interchange | null;
  substitution: SubstitutionRule;
  compatibility: Compatibility;
  /** False for cosmetic, inapplicable and unclassified attributes. */
  decides: boolean;
  reference_value: CanonicalValue;
  candidate_value: CanonicalValue;
  reference_display: string | null;
  candidate_display: string | null;
  match_kind: string | null;
  detail: string | null;
}

/** axiom.resolve.equivalence.EquivalenceReport — one directional verdict. */
export interface EquivalenceCandidate {
  reference_sku: string;
  candidate_sku: string;
  verdict: EquivalenceVerdict;
  substitutable: boolean;
  /** True for `indeterminate`: a data problem rather than a product problem. */
  needs_enrichment: boolean;
  reason: string;
  reference_class: string | null;
  candidate_class: string | null;
  same_class: boolean;
  reference_brand: string | null;
  candidate_brand: string | null;
  /** Crossing manufacturers is the case a text-similarity match cannot find. */
  cross_brand: boolean;
  deciding: number;
  compared: number;
  agreed: number;
  satisfied: number;
  blocking: number;
  unknown: number;
  cosmetic_differences: number;
  inapplicable: string[];
  unclassified: string[];
  coverage_note: string;
  blocking_detail: EquivalenceComparison[];
  unknown_detail: EquivalenceComparison[];
  satisfied_detail: EquivalenceComparison[];
  cosmetic_detail: EquivalenceComparison[];
  agreed_attributes: string[];
  comparisons: EquivalenceComparison[];
}

/**
 * axiom.resolve.report.CrossReference, as joined onto a bundle by
 * `axiom.console.projection.overlay_equivalence`.
 *
 * `measured` is the field to read before quoting any of this. False means the records compared
 * came from the hand-authored golden corpus, so the verdicts exercise the comparison logic rather
 * than the extraction that would supply it in production — the same distinction the L4 dry-run
 * marker draws.
 */
export interface EquivalenceView {
  generated_at: string | null;
  reference_sku: string;
  /** Where the compared records came from: `pipeline` or `golden`. */
  source: string;
  measured: boolean;
  source_note: string;
  candidates: number;
  substitutable: number;
  indeterminate: number;
  by_verdict: Record<EquivalenceVerdict, number>;
  best_substitute: string | null;
  best_verdict: EquivalenceVerdict | null;
  cross_brand_substitutes: number;
  /** Records in the catalogue that was searched. */
  records: number;
  skus: string[];
  failures: string[];
  candidates_detail: EquivalenceCandidate[];
}

const EXTRACTION_FAMILY: ReadonlySet<DerivationMethod> = new Set<DerivationMethod>([
  "document_extraction",
  "table_extraction",
  "image_extraction",
  "web_extraction",
  "supplier_feed",
]);

const INFERENCE_FAMILY: ReadonlySet<DerivationMethod> = new Set<DerivationMethod>([
  "part_number_grammar",
  "family_inference",
  "statistical_default",
]);

export function requiresEvidence(method: DerivationMethod): boolean {
  return EXTRACTION_FAMILY.has(method);
}

export function isInference(method: DerivationMethod): boolean {
  return INFERENCE_FAMILY.has(method);
}

export function isHuman(method: DerivationMethod): boolean {
  return method === "human_entry" || method === "human_correction";
}

// ---------------------------------------------------------------- gaps
// axiom.core.gaps

export type GapReason =
  | "not_present_in_any_source"
  | "no_source_available"
  | "referred_elsewhere"
  | "extracted_but_unverifiable"
  | "failed_validation"
  | "conflicting_sources"
  | "awaiting_review";

export type RecommendedAction =
  | "request_from_supplier"
  | "human_research"
  | "human_review"
  | "retry_with_better_source"
  | "accept_as_not_applicable"
  | "delist_product";

export interface Gap {
  attribute_code: string;
  reason: GapReason;
  sources_searched: string[];
  detail: string | null;
  revenue_exposure_usd: number | null;
  recommended_action: RecommendedAction | null;
  is_required: boolean;
  detected_at: string;
}

const SUPPLIER_ACTIONABLE: ReadonlySet<GapReason> = new Set<GapReason>([
  "not_present_in_any_source",
  "no_source_available",
  "referred_elsewhere",
  "conflicting_sources",
]);

export function isSupplierActionable(reason: GapReason): boolean {
  return SUPPLIER_ACTIONABLE.has(reason);
}

// ---------------------------------------------------------------- product
// axiom.core.product

export type ClassificationScheme =
  | "internal"
  | "ETIM"
  | "eCl@ss"
  | "UNSPSC"
  | "GS1_GPC"
  | "HTS"
  | "marketplace";

export type LifecycleStatus =
  | "active"
  | "new"
  | "discontinued"
  | "superseded"
  | "special_order";

export interface Classification {
  scheme: ClassificationScheme;
  code: string;
  path: string[];
  confidence: number;
  level_confidences: number[];
  rationale: string | null;
  method: string | null;
  alternatives: Record<string, unknown>[];
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ProductRecordSummary {
  tenant_id: string;
  sku: string;
  mpn: string | null;
  mpn_normalized: string | null;
  gtin: string | null;
  brand: string | null;
  brand_id: string | null;
  supplier_id: string | null;
  /**
   * Set by variant explosion (`axiom.extract.variants.explode`). Null on a standalone product.
   *
   * Also null on the SKU the series was *extracted from*, which is itself one of the variants
   * rather than a separate series node — so the grouping key is `parent_sku ?? sku`. See
   * `variantGroups` in `lib/data.ts`.
   *
   * Optional rather than required, because the checked-in offline fixture was generated before this
   * field was projected and therefore omits it. Absent and null must mean the same thing at the read
   * site: treating `undefined` as "has a parent" would turn every SKU in that fixture into a
   * one-member series. The live API always emits it.
   */
  parent_sku?: string | null;
  lifecycle_status: LifecycleStatus;
  class_code: string | null;
  schema_version: string | null;
  source_document_ids: string[];
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------- schema
// axiom.schema.models

export type Datatype =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "enum"
  | "multi_enum"
  | "quantity"
  | "range"
  | "dimension"
  | "dimension_set";

export type Requirement = "required" | "recommended" | "optional";
export type EvidenceRequirement = "standard" | "strict";

export interface AllowedValue {
  value: string;
  aliases: string[];
  note: string | null;
}

/** Attribute binding joined to its dictionary definition by the exporter. */
export interface AttributeSpec {
  code: string;
  requirement: Requirement;
  weight: number;
  name: string;
  datatype: Datatype;
  description: string;
  canonical_unit: string | null;
  display_preference: string | null;
  quantity_kind: string | null;
  multivalued: boolean;
  compliance_claim: boolean;
  evidence_requirement: EvidenceRequirement;
  example_values: string[];
  allowed_values: AllowedValue[];
  plausible_range: [number, number] | null;
}

export interface CrossFieldRule {
  id: string;
  expr: string;
  severity: "error" | "warning";
  message: string;
  references: string[];
}

export interface ChannelProfile {
  name: string;
  title_template: string | null;
  max_title_chars: number | null;
  unit_system: string | null;
  required: string[];
}

export interface ClassDefinition {
  code: string;
  name: string;
  version: string;
  schema_version: string;
  /**
   * The bare noun a buyer would use — "Dishwasher", not "Built-In Dishwasher". Already resolved
   * server-side to `item_type or name`, so it is never null and callers must not re-implement the
   * fallback. This is the delivery format's `Product Name` column.
   */
  item_type: string;
  /**
   * The internal reporting hierarchy, and genuinely NOT derivable from `browse_path`. The client's
   * own ground truth pairs a browse path of "Appliances & Consumer Electronics > Kitchen
   * Appliances > Built-In Dishwashers" with a reporting path of "Appliances / Large Appliances /
   * Dishwashers". Deriving one from the other produces the wrong string; this is the format's
   * `Dept` / `Class` / `Fine`.
   *
   * May hold fewer than three levels, in which case the trailing delivery columns are blank.
   */
  reporting_path: string[];
  browse_path: string[];
  mappings: Record<string, string>;
  required_codes: string[];
  attributes: AttributeSpec[];
  cross_field_rules: CrossFieldRule[];
  channel_profiles: ChannelProfile[];
}

// ---------------------------------------------------------------- parsed document
// axiom.docintel.models

export interface ParsedLine {
  line_index: number;
  text: string;
  /** [x0, y0, x1, y1] in PDF points, origin top-left. */
  bbox: [number, number, number, number];
}

export interface ParsedTableCell {
  row: number;
  col: number;
  text: string;
  bbox: [number, number, number, number];
}

export interface ParsedTable {
  table_id: string;
  page: number;
  row_count: number;
  col_count: number;
  bbox: [number, number, number, number];
  rows: string[][];
  cells: ParsedTableCell[];
}

export interface ParsedPage {
  number: number;
  width: number;
  height: number;
  lines: ParsedLine[];
  tables: ParsedTable[];
}

// ---------------------------------------------------------------- certificate
// axiom.core.certificate

/**
 * axiom.core.certificate.QualityIndex
 *
 * `richness` is `null` when nothing about it could be observed, which is emphatically not zero. It
 * is scored from channel pre-flight results and generated copy, so a run that produced neither
 * leaves it unmeasured — and `composite` then renormalises over the dimensions that *were*
 * measured rather than dragging the score down by richness's weight.
 *
 * `composite` is served by the API, not recomputed here. It used to be reimplemented in this file,
 * which meant the weighting existed twice and could disagree with itself the moment either side
 * changed. Read `measured_dimensions` to know how many dimensions it spans.
 */
export interface QualityIndex {
  completeness: number;
  verifiability: number;
  consistency: number;
  richness: number | null;
  composite: number;
  measured_dimensions: string[];
  weights?: Record<string, number>;
}

export interface CertificateSummary {
  attributes_required: number;
  attributes_populated: number;
  attributes_with_evidence: number;
  attributes_inferred: number;
  auto_accepted: number;
  queued_for_review: number;
  gaps_total: number;
  gaps_required: number;
  quality_index: QualityIndex;
  cost_usd: number | null;
  wall_clock_seconds: number | null;
}

export interface CertificateAttributeEntry {
  code: string;
  value_canonical: unknown;
  value_display: string | null;
  value_raw: string | null;
  confidence: number;
  method: DerivationMethod;
  model_tier: string | null;
  status: ValueStatus;
  evidence: {
    document: string;
    sha256: string;
    page: number | null;
    bbox: number[] | null;
    quote: string;
    verified: boolean;
  }[];
  validations: Record<string, string | null>[];
}

/** `build_certificate` computes this status; it is not a field on `Classification`. */
export type CertifiedClassificationStatus =
  | "auto"
  | "confirmed"
  | "requires_human_confirmation";

export interface CertificateClassificationEntry {
  scheme: ClassificationScheme;
  code: string;
  path: string[];
  confidence: number;
  method: string | null;
  status: CertifiedClassificationStatus;
  alternatives: Record<string, unknown>[];
}

export interface CertificateGapEntry {
  code: string;
  reason: GapReason;
  sources_searched: string[];
  required: boolean;
  detail?: string;
  revenue_exposure_usd?: number;
  recommended_action?: RecommendedAction;
}

export interface EnrichmentCertificate {
  certificate_id: string;
  sku: string;
  tenant_id: string;
  generated_at: string;
  schema_version: string | null;
  pipeline_version: string;
  classifications: CertificateClassificationEntry[];
  attributes: CertificateAttributeEntry[];
  gaps: CertificateGapEntry[];
  summary: CertificateSummary;
  signature: string;
  signature_verified: boolean;
}

/**
 * The composite, as the pipeline computed it.
 *
 * A reader rather than a calculation, deliberately. This function used to reimplement the weighting
 * in TypeScript, which put the same formula in two languages — and when richness became an
 * optionally-unmeasured dimension requiring renormalisation, the two would have silently disagreed.
 * The Python is the single source of truth and serialises the result.
 */
export function composite(index: QualityIndex): number {
  return index.composite;
}

// ---------------------------------------------------------------- policy & channels

export interface RiskPolicySummary {
  epsilon: number;
  confidence_level: number;
  threshold: number | null;
  coverage: number;
  accepted: number;
  accepted_errors: number;
  observed_error_rate: number;
  error_upper_bound: number;
  calibration_size: number;
  achievable: boolean;
  reason: string;
}

/** One point on the risk–coverage curve. axiom.confidence.policy.RiskCoveragePoint */
export interface RiskCoveragePoint {
  threshold: number;
  coverage: number;
  error_upper_bound: number;
}

/** `GET /api/policy?epsilon=` — a summary plus the whole curve behind it. */
export interface RiskPolicyView extends RiskPolicySummary {
  curve: RiskCoveragePoint[];
}

export interface ChannelReadinessSummary {
  channel: string;
  ready: boolean;
  missing: string[];
  not_publishable: string[];
  warnings: string[];
  blocking_count: number;
}

export interface ChannelExport {
  name: string;
  published: boolean;
  value_count: number;
  withheld: string[];
  readiness: ChannelReadinessSummary;
  title: string | null;
}

// ---------------------------------------------------------------- cost
// axiom.extract.client.UsageLedger + axiom.extract.pricing

export interface PriceSource {
  region: string;
  fetched_at: string;
  source: string;
  priced_models: number;
  unpriced_models: string[];
  age_days: number | null;
  /** True past 90 days. AWS changes prices; a stale table misstates unit economics. */
  stale: boolean;
}

/** axiom.console.projection.serialise_cost */
export interface CostSummary {
  calls: number;
  escalations: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  calls_by_tier: Record<string, number>;
  /** Tokens are attributed to the tier that burned them, not split by call count. */
  input_by_tier: Record<string, number>;
  output_by_tier: Record<string, number>;
  /**
   * Null when a tier that was actually used has no published price. Render that as
   * "unavailable", never as zero — a free-looking SKU reads as a result rather than as a
   * missing input.
   */
  cost_usd: number | null;
  cost_by_tier: Record<string, number> | null;
  priced: boolean;
  price_source: PriceSource | null;
}

// ---------------------------------------------------------------- generated copy
// axiom.generate

export type ClaimKind = "quantity" | "standard" | "designation" | "regulated" | "banned";
export type ClaimVerdict = "supported" | "unsupported" | "banned";

/** axiom.generate.claims.Claim */
export interface Claim {
  kind: ClaimKind;
  text: string;
  verdict: ClaimVerdict;
  reason: string;
  field: string;
  /** Attribute code that substantiates the claim, when one does. */
  supported_by: string | null;
}

export interface ClaimCheckSummary {
  claims: number;
  supported: number;
  unsupported: number;
  banned: number;
  /** True only with zero unsupported and zero banned. Not a score — one bad claim fails. */
  passed: boolean;
}

/** axiom.validate.reasoning — one claim's formal verdict. */
export interface FormalClaim {
  claim: string;
  /** The raw solver finding: `satisfiable`, `invalid`, `impossible`, `tooComplex`, … */
  verdict: string;
  /** Identifiers of the policy rules the claim contradicts. Empty unless it does. */
  rules: string[];
  contradiction: boolean;
  /** The solver declined to translate this claim. Neither proven nor disproven. */
  indeterminate: boolean;
  confidence: number | null;
  detail: string;
}

/**
 * axiom.validate.reasoning.ReasoningReport — validation layer L6.
 *
 * Three states, and conflating any two of them would misrepresent the layer:
 *
 * - `passed && conclusive` — the solver reached a verdict and found no contradiction.
 * - `passed && !conclusive` — nothing was disproven, but nothing was established either. Not a
 *   reason to withhold copy, and not grounds for a "verified" badge.
 * - `!passed` — either a contradiction was proven, or `error` is set because the policy could
 *   not be reached at all. Both withhold publication.
 */
export interface FormalCheck {
  checked: number;
  contradictions: number;
  indeterminate: number;
  violated_rules: string[];
  /** No contradiction proven *and* the policy was reachable. */
  passed: boolean;
  /** At least one claim got a real verdict. Read this, not `passed`, before claiming verification. */
  conclusive: boolean;
  /** Set when the policy could not be consulted. Distinct from a clean report. */
  error: string | null;
  /** The premises the claims were judged against, rendered from publishable values only. */
  premises: string;
  claims: FormalClaim[];
}

/**
 * axiom.generate.copy.GeneratedCopy
 *
 * The claim check travels with the prose, always. Copy shown without its verdict is just text,
 * and the whole argument for generating it is that every assertion was checked against an
 * already-publishable attribute.
 */
export interface GeneratedCopy {
  sku: string;
  published: boolean;
  headline: string;
  short_description: string;
  long_description: string;
  bullets: string[];
  attempts: number;
  prompt_version: string;
  model_id: string | null;
  model_tier: string | null;
  error: string | null;
  claim_check: ClaimCheckSummary;
  claims: Claim[];
  /**
   * The L6 result, or null when no reasoning policy was consulted.
   *
   * Null must render as "not checked". An absent report is not a clean one, and showing an
   * empty summary in its place would read as a pass the system never established.
   */
  formal_check?: FormalCheck | null;
}

// ---------------------------------------------------------------- review decisions
// axiom.review.session

export type ReviewAction = "accept" | "reject" | "correct";

/** axiom.review.session.ReviewOutcome */
export interface ReviewOutcome {
  sku: string;
  attribute_code: string;
  action: ReviewAction;
  reviewer: string;
  before: string | null;
  after: string | null;
  status: ValueStatus;
  recorded_at: string;
  /**
   * Per-attribute reliability before and after this decision. The pair is the learning
   * flywheel made visible: an accept raises it, a reject or correction lowers it, and the
   * auto-accept threshold moves as a result.
   */
  prior_before: number;
  prior_after: number;
  sibling_impact: number;
}

/** axiom.review.session.queue_summary */
export interface QueueSummary {
  pending: number;
  accepted: number;
  total: number;
  by_reason: Record<string, number>;
  blocking_failures: number;
  warnings: number;
}

export interface DecisionResponse {
  outcome: ReviewOutcome;
  summary: QueueSummary;
  item: Record<string, unknown> | null;
}

// ---------------------------------------------------------------- aggregates

export interface ValidationReport {
  checks: number;
  failures: number;
  warnings: number;
  skipped_rules: number;
  consistency: number;
  results: ValidationResult[];
  per_attribute: Record<string, ValidationResult[]>;
}

export interface ExtractionSummary {
  requested: number;
  values: number;
  gaps: number;
  rejected_unverifiable: number;
  citation_coverage: number;
  input_tokens: number;
  output_tokens: number;
  escalations: number;
  latency_ms: number;
}

export interface SkuMetrics {
  fill_rate: number;
  verifiability: number;
  values_total: number;
  values_publishable: number;
  values_needing_review: number;
  gaps_total: number;
  gaps_required: number;
  conflicts: string[];
  /** Present once any value on this SKU has been reviewed. */
  values_reviewed?: number;
}

export interface SkuBundle {
  sku: string;
  /** Key into `ConsoleDataset.documents`. */
  document_id: string;
  /** Key into `ConsoleDataset.class_definitions`. Null if classification abstained. */
  class_code: string | null;
  record: ProductRecordSummary;
  classifications: Classification[];
  classification_summary: Record<string, unknown>;
  classification_candidates: { code: string; score: number; path_text: string }[];
  values: AttributeValue[];
  gaps: Gap[];
  extraction: ExtractionSummary;
  normalization_issues: ValidationResult[];
  validation: ValidationReport;
  certificate: EnrichmentCertificate;
  channels: ChannelExport[];
  metrics: SkuMetrics;
  /** Token spend for this SKU. Absent on bundles written before cost tracking existed. */
  cost?: CostSummary | null;
  /** Generated copy and its claim check. Null unless the run passed --generate-copy. */
  copy?: GeneratedCopy | null;
  /** Audit trail of human decisions, present once any have been recorded. */
  decisions?: ReviewOutcome[];
  /**
   * Validation layer L4. Present only for SKUs a cross-source run has been saved for, because it
   * needs a second document describing the same part and most SKUs have one source.
   */
  cross_source?: CrossSourceView | null;
  /**
   * Cross-reference and equivalence. Present only for SKUs a run has been saved for, and absent
   * from the offline fixture entirely, because the exporter applies no overlays.
   */
  equivalence?: EquivalenceView | null;
}

/** A source document together with its parsed page geometry. */
export interface DocumentBundle {
  document: SourceDocument;
  pages: ParsedPage[];
}

export interface DatasetMeta {
  generated_at: string;
  generator: string;
  /** True when served live from the API; false for the offline fixture. */
  live: boolean;
  calibrator: string;
  policy_source: string;
  notes: string;
  /** Non-fatal problems assembling the dataset, e.g. bundles at mismatched thresholds. */
  warnings?: string[];
  source_bundles?: string[];
  /** Present on the offline fixture only. */
  pipeline_version?: string;
  source_sample?: string;
}

/**
 * Documents and class definitions are keyed maps rather than fields on each SKU.
 *
 * Page geometry dominates the payload size, and several SKUs are routinely cut from one
 * datasheet — five ball valves off one page would otherwise ship five copies of it. Bundles
 * reference their document by id and their class by code.
 */
export interface ConsoleDataset {
  meta: DatasetMeta;
  documents: Record<string, DocumentBundle>;
  class_definitions: Record<string, ClassDefinition>;
  policy: RiskPolicySummary;
  skus: SkuBundle[];
}

// ---------------------------------------------------------------- quality cohort
// axiom.evaluation.cohort

export type CohortArm = "treatment" | "control";

/**
 * The dimensions a cohort compares, plus the composite.
 *
 * `richness` is deliberately not among them: it is scored from channel pre-flight and generated
 * copy, and neither cohort arm has those. Both arms renormalise the composite over these three.
 */
export type CohortDimension =
  | "completeness"
  | "verifiability"
  | "consistency"
  | "composite";

/** axiom.evaluation.cohort.CohortScore — one SKU at one point in time. */
export interface CohortScore {
  completeness: number;
  verifiability: number;
  consistency: number;
  /** Null on both arms: a cohort has no channel exports or copy to score it from. */
  richness: number | null;
  composite: number;
  /**
   * Share of required fields holding *any* value, publishable or not.
   *
   * Reported beside `completeness` rather than instead of it. An item master's fields are
   * populated; they are just unsourced, and collapsing those two facts into one number would
   * let "unverifiable" read as "empty" — a claim a distributor would rightly reject.
   */
  field_presence: number;
  values_present: number;
  values_publishable: number;
  values_with_evidence: number;
  validation_failures: number;
  required_total: number;
  /** Denominator behind `consistency`. Not constant across arms, so a ratio change needs it. */
  checks_run: number;
  /** Which rules failed. Named rather than counted, so a drop is a work item not a worry. */
  failed_rules: string[];
}

export interface CohortMember {
  sku: string;
  arm: CohortArm;
  before: CohortScore;
  after: CohortScore;
  delta: Record<CohortDimension | "field_presence", number>;
}

/**
 * axiom.evaluation.cohort.CohortStudy
 *
 * `available: false` is a normal state, not an error — it means nobody has run the study yet.
 *
 * `trustworthy` is the field to read before quoting any of these numbers. It requires a control
 * arm that did not move: if untouched SKUs appear to have changed, the *scorer* changed between
 * the two measurements and the lift cannot be attributed to enrichment.
 */
export interface CohortStudy {
  available: boolean;
  reason?: string;
  schema_version: string | null;
  treatment_skus: number;
  control_skus: number;
  trustworthy: boolean;
  control_drift: Record<CohortDimension, number> | null;
  drifted_dimensions: string[];
  lift: Record<CohortDimension, number>;
  field_presence: { before: number; after: number };
  treatment_before: Record<CohortDimension, number>;
  treatment_after: Record<CohortDimension, number>;
  notes: string[];
  members: CohortMember[];
}
