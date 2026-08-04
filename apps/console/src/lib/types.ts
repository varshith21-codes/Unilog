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
  | "accept_as_not_applicable";

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

export interface QualityIndex {
  completeness: number;
  verifiability: number;
  consistency: number;
  richness: number;
  weights: Record<string, number>;
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

/** Weighted composite. Mirrors `QualityIndex.composite`. */
export function composite(index: QualityIndex): number {
  const w = index.weights;
  return (
    index.completeness * (w.completeness ?? 0) +
    index.verifiability * (w.verifiability ?? 0) +
    index.consistency * (w.consistency ?? 0) +
    index.richness * (w.richness ?? 0)
  );
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
  /** Audit trail of human decisions, present once any have been recorded. */
  decisions?: ReviewOutcome[];
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
