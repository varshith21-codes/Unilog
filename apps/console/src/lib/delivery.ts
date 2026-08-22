/**
 * The delivery-format contract, and the shapes the export endpoint returns.
 *
 * Server-only, like `data.ts`, and for the same reason: the contract is fetched from the API so
 * this console can never describe a column list the exporter does not produce. A hand-copied
 * header here would be a second source of truth for the one thing in the deliverable that is
 * binary — 252 columns in the client's order, or the file cannot be ingested.
 *
 * These types are hand-maintained mirrors of the Python responses, matching the convention in
 * `types.ts`. They are deliberately *not* added to `scripts/check_console_types.py`: that script
 * checks enums and Pydantic models, and these endpoints return plain dicts assembled in the route
 * handler rather than declared models, so there is no Python class for it to diff against.
 */

import { API_BASE } from "./data";

const FETCH_TIMEOUT_MS = 8000;

export interface DeliverySectionSummary {
  group: string;
  provenance: string;
  columns: number;
  note: string | null;
}

export interface DeliveryUploadLimits {
  accepts: string[];
  outputs: string[];
  max_bytes: number;
  max_rows: number;
  max_documents: number;
  max_document_bytes: number;
}

export interface DeliveryFormatView {
  name: string;
  version: string;
  format: string;
  columns: number;
  join_key: string;
  description: string | null;
  populated_in_ground_truth: number | null;
  header: string[];
  sections: DeliverySectionSummary[];
  unavailable_columns: string[];
  input_columns: string[];
  upload: DeliveryUploadLimits;
}

export interface ColumnProfileView {
  header: string;
  total: number;
  populated: number;
  placeholders: number;
  blanks: number;
  distinct: number;
  fill_rate: number;
  placeholder_rate: number;
  carries_data: boolean;
  is_discriminating: boolean;
}

export interface RowOutcomeView {
  mpn: string | null;
  class_code: string | null;
  method: string;
  skipped: string | null;
  populated: number;
  from_description: number;
  from_documents: number;
  from_golden: number;
}

export interface DeliveryRunSummary {
  source: {
    filename: string;
    sheet: string | null;
    rows_in_file: number;
    rows_selected: number;
    input_profile: ColumnProfileView[];
    dead_columns: string[];
  };
  documents: {
    document_id: string;
    pages: number;
    tables: number;
    parser: string;
    doc_type: string;
  }[];
  batch: {
    rows: number;
    skipped: number;
    classification: Record<string, number>;
    from_description: { extracted: number; refused: number };
    from_documents: { extracted: number; refused: number };
    from_golden: number;
  };
  export: {
    format: string;
    rows: number;
    content_hash: string;
    columns_populated: number;
    provenance: Record<string, number>;
    withheld: number;
    constraint_violations: number;
    compliant: boolean;
    rows_with_notes: number;
  };
  columns_populated: { column: string; rows: number }[];
  rows: RowOutcomeView[];
  preview: Record<string, string>[];
  notes: string[];
}

/** The structured 422 the API returns when the upload is not an item master. */
export interface MissingInputColumns {
  error: "missing_input_columns";
  message: string;
  missing: string[];
  found: string[];
  expected: string[];
}

export type FormatResult =
  | { ok: true; data: DeliveryFormatView }
  | { ok: false; error: string };

/**
 * Read the contract from the API.
 *
 * Returns a result rather than throwing, and there is no offline fixture fallback here on purpose.
 * The dataset has one because a dashboard with no data is useless; this page has nothing to show
 * without a reachable API, since the whole feature is "send a file to the pipeline". Rendering an
 * upload form that cannot work would be worse than saying the API is down.
 */
export async function loadDeliveryFormat(): Promise<FormatResult> {
  try {
    const response = await fetch(`${API_BASE}/api/delivery/format`, {
      // The contract changes only when `schema/delivery/*.yaml` does, but a stale cache here would
      // mean describing the wrong file, so it is re-read per request like everything else.
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      return { ok: false, error: `The API returned ${response.status} ${response.statusText}.` };
    }
    return { ok: true, data: (await response.json()) as DeliveryFormatView };
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
