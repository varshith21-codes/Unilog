"use server";

/**
 * Server actions for review decisions.
 *
 * These exist so the browser never talks to the FastAPI service directly. Two reasons, and the
 * second is the one that matters:
 *
 * 1.  No CORS configuration, because the request originates from the Next.js server rather than
 *     the page.
 * 2.  The API URL and eventually its credentials stay server-side. The review API mutates data
 *     and is currently unauthenticated; putting it behind the app's own origin means adding
 *     auth later is a change in one place rather than a change to every fetch in every
 *     component.
 *
 * Failures are returned as values, not thrown. A rejected decision is an ordinary outcome a
 * reviewer needs to see and retry, not an exception that should blank the workspace.
 */

import { revalidatePath } from "next/cache";

import { API_BASE } from "./data";
import { skuSlug } from "./sku";
import type { DecisionResponse, ReviewAction, RiskPolicyView } from "./types";

export type DecisionResult =
  | { ok: true; data: DecisionResponse }
  | { ok: false; error: string };

export type PolicyResult =
  | { ok: true; data: RiskPolicyView }
  | { ok: false; error: string };

/**
 * Recompute the acceptance policy for a different error budget.
 *
 * This is a read-only what-if. It does not change what is published — the dataset reports the
 * policy its values were actually decided under, and moving this dial explores alternatives
 * rather than retroactively reclassifying anything.
 */
export async function fetchPolicy(epsilon: number): Promise<PolicyResult> {
  if (!Number.isFinite(epsilon) || epsilon <= 0 || epsilon >= 1) {
    return { ok: false, error: `Error budget must be between 0 and 1, got ${epsilon}.` };
  }

  try {
    const response = await fetch(
      `${API_BASE}/api/policy?epsilon=${encodeURIComponent(epsilon)}`,
      { cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) },
    );
    if (!response.ok) {
      return { ok: false, error: `The API returned ${response.status} ${response.statusText}.` };
    }
    return { ok: true, data: (await response.json()) as RiskPolicyView };
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return { ok: false, error: `Could not reach the AXIOM API at ${API_BASE} (${reason}).` };
  }
}

export interface DecisionInput {
  sku: string;
  attributeCode: string;
  action: ReviewAction;
  reviewer?: string;
  correctedValue?: string;
}

const TIMEOUT_MS = 8000;

export async function submitDecision(input: DecisionInput): Promise<DecisionResult> {
  const { sku, attributeCode, action, reviewer = "reviewer@local", correctedValue } = input;

  if (action === "correct" && !correctedValue?.trim()) {
    return { ok: false, error: "A correction needs a replacement value." };
  }

  if (!sku.trim()) {
    return { ok: false, error: "A decision needs a SKU." };
  }
  if (!attributeCode || /[/\\]/.test(attributeCode)) {
    return { ok: false, error: `Invalid attribute code: ${attributeCode}` };
  }

  // The SKU is slugged rather than percent-encoded, and that is the difference between a decision
  // that records and one that 404s. `encodeURIComponent("52C3-5/8-UPC")` puts `%2F` in a path
  // segment, which the server decodes back into a separator before routing — so the request arrives
  // at a route that does not exist, for a part number that does. The slug carries no character a
  // path treats specially, and it is the same identifier the session file is named with. See
  // lib/sku.ts and axiom.core.naming.
  const url =
    `${API_BASE}/api/session/${skuSlug(sku)}` +
    `/decision/${encodeURIComponent(attributeCode)}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        reviewer,
        corrected_value: correctedValue ?? null,
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return {
      ok: false,
      error:
        `Could not reach the AXIOM API at ${API_BASE} (${reason}). Start it with ` +
        `"python -m uvicorn apps.api.main:app --port 8000".`,
    };
  }

  if (!response.ok) {
    return { ok: false, error: await describeFailure(response, sku) };
  }

  const data = (await response.json()) as DecisionResponse;

  // The decision changed the session on disk, so anything rendering it is now stale. The path
  // revalidated has to be the one that was routed, which is the slug.
  revalidatePath("/review");
  revalidatePath(`/review/${skuSlug(sku)}`);
  revalidatePath("/");

  return { ok: true, data };
}

async function describeFailure(response: Response, sku: string): Promise<string> {
  const detail = await readDetail(response);

  if (response.status === 404) {
    return (
      detail ??
      `No review session for ${sku}. Generate one with ` +
        `"python scripts/run_pipeline.py <source> --sku ${sku} --save-session".`
    );
  }
  if (response.status === 422) {
    return detail ?? "The API rejected the request shape.";
  }
  return detail ?? `The API returned ${response.status} ${response.statusText}.`;
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    // FastAPI validation errors arrive as a list of objects.
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((entry) =>
          entry && typeof entry === "object" && "msg" in entry ? String(entry.msg) : null,
        )
        .filter((message): message is string => message !== null);
      return messages.length > 0 ? messages.join("; ") : null;
    }
    return null;
  } catch {
    return null;
  }
}
