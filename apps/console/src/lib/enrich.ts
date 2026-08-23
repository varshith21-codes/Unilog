/**
 * The single-SKU enrichment endpoint: what it accepts, and what it returns.
 *
 * Server-only, like `data.ts` and `delivery.ts`. The limits are fetched rather than hard-coded here
 * for the same reason the delivery contract is: a form advertising a cap this API does not enforce
 * would be describing a different service.
 *
 * These types are hand-maintained mirrors of the Python responses, matching the convention in
 * `types.ts`. Like `delivery.ts` they are deliberately *not* added to
 * `scripts/check_console_types.py`: that script diffs enums and Pydantic models, and this endpoint
 * assembles a plain dict in the handler rather than declaring a response model, so there is no Python
 * class for it to compare against. `EnrichRequest` on the Python side *is* a Pydantic model, so the
 * request half below is the part worth keeping in step by hand.
 *
 * The one thing worth knowing before reading further: **this endpoint spends money.** Every
 * submission is two real Bedrock calls, three with copy generation. Everything else in this console
 * reads persisted output; this is the only screen that causes a model to run.
 */

import { API_BASE } from "./data";
import type {
  QualityIndex,
  QueueSummary,
  RiskPolicySummary,
  SkuBundle,
  SourceDocument,
} from "./types";

// The request shaping lives in `enrich-request.ts` and is re-exported here so server-side callers
// have one import. That split is load-bearing: importing a *value* from this module pulls `data.ts`
// and therefore `node:fs/promises` into the bundle, which fails the client build. See the docstring
// on `enrich-request.ts` before merging them.
export type { EnrichInput } from "./enrich-request";
export { enrichBody, isSubmittable } from "./enrich-request";

/** Short, because this only reads a static description of the endpoint's caps. */
const LIMITS_TIMEOUT_MS = 8000;

/** `GET /api/enrich/limits`. */
export interface EnrichLimits {
  required: string[];
  optional: string[];
  /** Fields where at least one must be present. Not expressible as "required" on either one. */
  one_of: string[];
  max_description_chars: number;
  max_url_chars: number;
  max_document_bytes: number;
  fetch_timeout_seconds: number;
  url_schemes: string[];
  concurrent_runs: number;
  outputs: string[];
  model_calls_per_run: { without_copy: number; with_copy: number };
  notes: string[];
}

// ---------------------------------------------------------------- response

/** How the source document was obtained, and what that makes its values worth. */
export interface EnrichSourceSummary {
  /** `document` when a URL was fetched, `submission` when the typed fields are the source. */
  kind: "document" | "submission";
  document_id: string;
  sha256: string;
  uri: string;
  doc_type: string;
  size_bytes: number;
  pages: number;
  tables: number;
  parser: string;
  was_already_stored: boolean;
  warnings: string[];
  /** Stated in prose because the distinction is the point, not a badge. */
  evidential_weight: string;
}

/** What retrieval tried, when no URL was supplied. See `axiom.pipeline.retrieval`. */
export interface EnrichRetrievalSummary {
  attempted: boolean;
  found: boolean;
  /** True when a stored document already covered the part, so no request was made at all. */
  from_library?: boolean;
  manufacturer?: { id: string; name: string; domain: string } | null;
  documents?: {
    document_id: string;
    sha256: string;
    uri: string;
    doc_type: string;
    pages: number;
    tables: number;
  }[];
  requests_made?: number;
  bytes_fetched?: number;
  notes?: string[];
}

export interface EnrichSummary {
  sku: string;
  class_code: string | null;
  /** True when classification abstained and the submitted class was used instead. */
  class_from_fallback: boolean;
  classification: Record<string, unknown>;
  extraction: {
    requested: number;
    values: number;
    gaps: number;
    rejected_unverifiable: number;
    citation_coverage: number;
    escalations: number;
    latency_ms: number;
    [key: string]: unknown;
  };
  from_description: { extracted: number; refused: number };
  values: { total: number; publishable: number; needing_review: number };
  gaps: { total: number; required: number };
  validation: { checks: number; failures: number; warnings: number; [key: string]: unknown };
  certificate: {
    certificate_id: string;
    signature_verified: boolean;
    pipeline_version: string;
    generated_at: string;
    quality_index: QualityIndex;
    attributes_populated: number;
    attributes_with_evidence: number;
  };
  cost: {
    usd: number | null;
    calls: number;
    escalations: number;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number;
  };
  policy: RiskPolicySummary;
  calibrator: string;
  retrieval: EnrichRetrievalSummary;
  source: EnrichSourceSummary;
  manufacturer: {
    name: string | null;
    supplier_code: string | null;
    looks_like_a_distributor: boolean;
    publishable_as_manufacturer: boolean;
  };
  brand: { requested: string | null; resolved: string | null; method: string | null };
  channels: { name: string; published: boolean; value_count: number; withheld: string[] }[];
  notes: string[];
}

export interface EnrichDeliverySummary {
  populated: number;
  columns: number;
  blank: number;
  withheld: number;
  compliant: boolean;
  content_hash: string;
  provenance: Record<string, number>;
  cited_columns: string[];
  notes: string[];
  input_notes: string[];
  files: { csv: string; xlsx: string; provenance: string };
}

export interface EnrichResponse {
  /** The true part number. */
  sku: string;
  /** The identifier every other route accepts. See `lib/sku.ts` and `axiom.core.naming`. */
  slug: string;
  replaced: boolean;
  summary: EnrichSummary;
  queue: QueueSummary;
  /** The same projection the dashboards render, so the stage cards need no second request. */
  bundle: SkuBundle;
  document: SourceDocument;
  policy: RiskPolicySummary;
  calibrator: string;
  delivery: EnrichDeliverySummary;
  persisted: Record<string, string>;
  links: Record<string, string>;
}

// ---------------------------------------------------------------- failures

/**
 * Why a submission was refused, in the shape the form needs to act on it.
 *
 * A discriminated union rather than a message, because these are not variations on "it failed" —
 * each one has a different remedy and a different place on the screen. `insufficient_input` points at
 * two fields; `conflict` offers a button; `unavailable` is an operator problem the person filling in
 * the form cannot fix.
 */
export type EnrichFailure =
  | { kind: "insufficient_input"; message: string; missing: string[] }
  | { kind: "invalid"; message: string; field?: string }
  | { kind: "conflict"; message: string; sku: string; slug: string; enrichedAt: string | null }
  | { kind: "busy"; message: string }
  | { kind: "source_unreachable"; message: string; url: string }
  | { kind: "unavailable"; message: string; detail: string; region: string | null }
  | { kind: "unreachable"; message: string }
  | { kind: "error"; message: string; status: number };

export type EnrichResult =
  | { ok: true; data: EnrichResponse }
  | { ok: false; failure: EnrichFailure };

export type LimitsResult = { ok: true; data: EnrichLimits } | { ok: false; error: string };

/** Read the endpoint's caps, so the form describes what is actually enforced. */
export async function loadEnrichLimits(): Promise<LimitsResult> {
  try {
    const response = await fetch(`${API_BASE}/api/enrich/limits`, {
      cache: "no-store",
      signal: AbortSignal.timeout(LIMITS_TIMEOUT_MS),
    });
    if (!response.ok) {
      return { ok: false, error: `The API returned ${response.status} ${response.statusText}.` };
    }
    return { ok: true, data: (await response.json()) as EnrichLimits };
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return {
      ok: false,
      error:
        `Could not reach the AXIOM API at ${API_BASE} (${reason}). Start it with ` +
        `"python -m uvicorn apps.api.main:app --port 8000".`,
    };
  }
}

/**
 * Map a failed response onto the union above.
 *
 * The status code alone is not enough: 422 covers both "you filled in neither field" and "that URL is
 * not https", and those belong in different places on the form. So the structured `error` key the API
 * sends is the discriminator, and the status is the fallback for anything that did not send one.
 */
export async function readEnrichFailure(response: Response): Promise<EnrichFailure> {
  const detail = await readDetail(response);

  if (detail !== null && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    const message = typeof record.message === "string" ? record.message : "";

    switch (record.error) {
      case "insufficient_input":
        return {
          kind: "insufficient_input",
          message,
          missing: Array.isArray(record.missing) ? record.missing.map(String) : [],
        };
      case "insecure_url":
        return {
          kind: "invalid",
          message,
          field: typeof record.field === "string" ? record.field : undefined,
        };
      case "already_enriched":
        return {
          kind: "conflict",
          message,
          sku: String(record.sku ?? ""),
          slug: String(record.slug ?? ""),
          enrichedAt: typeof record.enriched_at === "string" ? record.enriched_at : null,
        };
      case "source_unreachable":
        return { kind: "source_unreachable", message, url: String(record.url ?? "") };
      case "model_unavailable":
        return {
          kind: "unavailable",
          message,
          detail: String(record.detail ?? ""),
          region: typeof record.region === "string" ? record.region : null,
        };
      default:
        break;
    }
  }

  const text =
    typeof detail === "string"
      ? detail
      : `The API returned ${response.status} ${response.statusText}.`;

  if (response.status === 429) return { kind: "busy", message: text };
  if (response.status === 422 || response.status === 400) {
    return { kind: "invalid", message: text };
  }
  return { kind: "error", message: text, status: response.status };
}

async function readDetail(response: Response): Promise<unknown> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    // FastAPI's own validation errors arrive as a list of objects. Flattened to a sentence here
    // because a field-by-field render would duplicate what the form already checks.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry) =>
          entry !== null && typeof entry === "object" && "msg" in entry
            ? String((entry as { msg: unknown }).msg)
            : null,
        )
        .filter((message): message is string => message !== null);
      return messages.length > 0 ? messages.join("; ") : null;
    }
    return detail ?? null;
  } catch {
    return null;
  }
}
