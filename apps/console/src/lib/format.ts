/**
 * Presentation helpers. Pure functions, safe in both server and client components.
 *
 * Enum labels live here rather than in components so a rename in the Python enums is a
 * one-file change on this side, and so the same wording appears on every screen.
 */

import type {
  CanonicalValue,
  DecisionReason,
  DerivationMethod,
  GapReason,
  Quantity,
  RecommendedAction,
  Requirement,
  ValidationLayer,
  ValueStatus,
  Verdict,
} from "./types";

// ---------------------------------------------------------------- numbers

export function percent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/** Confidence and calibrated scores are read comparatively, so keep 3 decimals. */
export function score(value: number): string {
  return value.toFixed(3);
}

export function count(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} kB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Money, at whatever precision the magnitude deserves.
 *
 * Per-SKU costs here are fractions of a cent, so a fixed two decimals would render every one
 * of them as "$0.00" — which reads as free rather than as cheap, and throws away the only
 * interesting property of the number. Catalogue-scale totals get the usual two.
 */
export function usd(value: number): string {
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) {
    return `$${value.toFixed(6).replace(/0+$/, "").replace(/\.$/, ".0")}`;
  }
  return `$${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)}`;
}

/** Compact token counts: 3,779 stays exact, 4,100,000 becomes 4.1M. */
export function tokens(value: number): string {
  if (value < 10_000) return count(value);
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

// ---------------------------------------------------------------- values

function isQuantity(value: unknown): value is Quantity {
  return (
    typeof value === "object" &&
    value !== null &&
    "magnitude" in value &&
    "unit" in value &&
    !("minimum" in value)
  );
}

function isRange(value: unknown): value is { minimum: number; maximum: number; unit: string } {
  return (
    typeof value === "object" && value !== null && "minimum" in value && "maximum" in value
  );
}

/**
 * Render a canonical value.
 *
 * Prefer `value_display` where the pipeline produced one — it already applied the
 * attribute's display preference (imperial fractions for nominal sizes, for instance).
 * This is the fallback for when it did not.
 */
export function canonical(value: CanonicalValue): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.join(", ");
  if (isRange(value)) return `${value.minimum} to ${value.maximum} ${value.unit}`;
  if (isQuantity(value)) return `${value.magnitude} ${value.unit}`;
  return String(value);
}

// ---------------------------------------------------------------- identifiers

export function shortHash(sha256: string, length = 8): string {
  const clean = sha256.replace(/^sha256:/, "");
  return clean.slice(0, length);
}

/** `t1:r3:c2` reads better as `table 1, row 3, col 2` in prose contexts. */
export function tableRef(ref: string | null): string | null {
  if (!ref) return null;
  const match = /^t(\d+)(?::r(\d+))?(?::c(\d+))?$/.exec(ref);
  if (!match) return ref;
  const [, table, row, col] = match;
  const parts = [`table ${table}`];
  if (row) parts.push(`row ${row}`);
  if (col) parts.push(`col ${col}`);
  return parts.join(", ");
}

// ---------------------------------------------------------------- dates

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function dateOnly(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ---------------------------------------------------------------- enum labels

export const STATUS_LABEL: Record<ValueStatus, string> = {
  candidate: "Candidate",
  auto_accepted: "Auto-accepted",
  queued_for_review: "Needs review",
  human_approved: "Approved",
  rejected: "Rejected",
  superseded: "Superseded",
};

export const METHOD_LABEL: Record<DerivationMethod, string> = {
  document_extraction: "Document",
  table_extraction: "Table",
  image_extraction: "Image",
  web_extraction: "Web",
  supplier_feed: "Supplier feed",
  unit_conversion: "Unit conversion",
  enum_resolution: "Enum resolution",
  computed: "Computed",
  part_number_grammar: "Part-number grammar",
  family_inference: "Family inference",
  statistical_default: "Statistical default",
  human_entry: "Human entry",
  human_correction: "Human correction",
  legacy_record: "Legacy record",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  pass: "Pass",
  fail: "Fail",
  warn: "Warning",
  ambiguous: "Ambiguous",
  skipped: "Skipped",
};

export const LAYER_LABEL: Record<ValidationLayer, string> = {
  L0: "Type and format",
  L1: "Dimensional",
  L2: "Domain rule",
  L3: "Statistical",
  L4: "Cross-source",
  L5: "Groundedness",
  L6: "Formal",
};

export const GAP_REASON_LABEL: Record<GapReason, string> = {
  not_present_in_any_source: "Not stated in any source",
  no_source_available: "No source available",
  referred_elsewhere: "Source defers the value",
  extracted_but_unverifiable: "Quote could not be verified",
  failed_validation: "Failed validation",
  conflicting_sources: "Sources disagree",
  awaiting_review: "Awaiting review",
};

export const ACTION_LABEL: Record<RecommendedAction, string> = {
  request_from_supplier: "Request from supplier",
  human_research: "Research",
  human_review: "Review",
  retry_with_better_source: "Retry with a better source",
  accept_as_not_applicable: "Mark not applicable",
  delist_product: "Delist — source says withdrawn",
};

export const DECISION_LABEL: Record<DecisionReason, string> = {
  auto_accepted: "Above threshold",
  below_threshold: "Below threshold",
  blocking_validation: "Blocked by validation",
  unverified_evidence: "Evidence unverified",
  inferred_value: "Inferred, needs confirmation",
  no_validated_policy: "No validated policy",
};

export const REQUIREMENT_LABEL: Record<Requirement, string> = {
  required: "Required",
  recommended: "Recommended",
  optional: "Optional",
};

/** Human-readable feature names for the confidence explanation panel. */
export const FEATURE_LABEL: Record<string, string> = {
  evidence_match_score: "Quote match score",
  evidence_verified: "Evidence verified",
  citation_precision: "Citation precision",
  quote_specificity: "Quote specificity",
  self_reported_certainty: "Model certainty",
  validation_pass_rate: "Validation pass rate",
  validation_clean: "No blocking failures",
  is_extraction: "Extracted from source",
  is_inference: "Inferred",
  normalized: "Normalised to canonical",
  attribute_prior: "Attribute prior",
  source_prior: "Supplier prior",
  tier_cost: "Model tier",
};

export function featureLabel(key: string): string {
  return FEATURE_LABEL[key] ?? key.replace(/_/g, " ");
}

/** Sentence case from a snake_case enum value, for anything without an explicit label. */
export function humanise(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
