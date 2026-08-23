/**
 * Proxy for an enrichment run's delivery file.
 *
 * A route handler rather than a server action, for the same reason `api/delivery/export` is one: the
 * response is a file. Server actions return serialisable values, so a workbook would have to be
 * base64'd through the RSC payload and reassembled in the browser — twice the bytes, and
 * `Content-Disposition` lost, which is the header that gives the download its filename.
 *
 * GET rather than POST, and that is the substantive difference from the export proxy. This does not
 * run anything: the file was written when the SKU was enriched, and serving it is a disk read. That
 * is exactly why the files are written at run time — a download that re-ran the pipeline would spend
 * two more model calls to produce bytes we already had, and could hand back something different from
 * what the screen reported.
 *
 * The SKU arrives as a query parameter rather than a path segment. A part number is not a path
 * segment: `52C3-5/8-UPC` percent-encoded into one is decoded back into a separator by the time it
 * routes, which is the bug `axiom.core.naming` exists to fix. The caller sends the **slug**, which the
 * enrich response hands back for precisely this purpose, and the API validates it against its own
 * whitelist before touching the filesystem.
 */

import type { NextRequest } from "next/server";

import { API_BASE } from "@/lib/data";

/** A disk read of a one-row file. Nothing here should take seconds. */
const TIMEOUT_MS = 30_000;

const OUTPUTS = new Set(["csv", "xlsx"]);

/** The filename, and the run summary the API reports numerically. */
const FORWARDED = [
  "content-type",
  "content-disposition",
  "x-axiom-rows",
  "x-axiom-columns-populated",
  "x-axiom-columns-total",
  "x-axiom-withheld",
  "x-axiom-content-hash",
];

export async function GET(request: NextRequest): Promise<Response> {
  const slug = request.nextUrl.searchParams.get("sku")?.trim() ?? "";
  const output = request.nextUrl.searchParams.get("output")?.trim() ?? "csv";

  if (!slug) {
    return problem(400, "Which SKU? Pass ?sku= with the slug the enrichment run returned.");
  }
  if (!OUTPUTS.has(output)) {
    return problem(400, `output must be csv or xlsx; got "${output}".`);
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${API_BASE}/api/enrich/${encodeURIComponent(slug)}/delivery?output=${output}`,
      { cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) },
    );
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return problem(
      502,
      `Could not reach the AXIOM API at ${API_BASE} (${reason}). Start it with ` +
        `"python -m uvicorn apps.api.main:app --port 8000".`,
    );
  }

  if (!upstream.ok) {
    // Passed through rather than reworded. A 404 here means the file is not on disk, and the API's
    // own message explains which entry points write one.
    const detail = await readDetail(upstream);
    return problem(
      upstream.status,
      detail ?? `The API returned ${upstream.status} ${upstream.statusText}.`,
    );
  }

  const headers = new Headers();
  for (const name of FORWARDED) {
    const value = upstream.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("x-content-type-options", "nosniff");
  headers.set("cache-control", "no-store");

  return new Response(upstream.body, { status: upstream.status, headers });
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail !== null && typeof body.detail === "object" && "message" in body.detail) {
      return String((body.detail as { message: unknown }).message);
    }
    return null;
  } catch {
    return null;
  }
}

function problem(status: number, message: string): Response {
  // Shaped like FastAPI's error body, so the client has one error path to read whether the failure
  // happened here or upstream.
  return Response.json({ detail: message }, { status });
}
