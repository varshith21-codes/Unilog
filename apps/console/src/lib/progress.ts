/**
 * The shape of a run's live progress, and how the browser reads it.
 *
 * Client-safe with nothing behind it, on the same rule `enrich-request.ts` documents at length:
 * importing a *value* from `enrich.ts` pulls `data.ts` and therefore `node:fs/promises` into whatever
 * bundle imports it, which fails the client build outright. `PipelineProgress` needs `readProgress`
 * at runtime in the browser, so it lives here.
 *
 * The fetch target is the console's own route handler at `/api/enrich/progress`, not the FastAPI
 * service. Relative, so it needs no base URL and no CORS, and it keeps `API_BASE` server-side.
 *
 * Mirrors `axiom.pipeline.progress.RunProgress.snapshot`. Hand-maintained, like `types.ts` and
 * `enrich.ts`: the Python side assembles a plain dict rather than declaring a response model, so
 * there is no Pydantic class for `scripts/check_console_types.py` to diff this against.
 */

/** One row of the checklist. `pending` before it runs, `skipped` when the run did not need it. */
export type StageState = "pending" | "running" | "done" | "skipped" | "failed";

export interface ProgressStage {
  id: string;
  name: string;
  /** Present tense: what this stage is doing, and why it exists. Shown while it runs. */
  narration: string;
  /**
   * Whether a model was called.
   *
   * Carried per stage because the ratio is the architectural claim, and because on *this* screen it
   * is also the cost: these are the rows that spend money, being spent right now.
   */
  model: boolean;
  state: StageState;
  /** The one number worth reading off the stage, written when it finishes. Null until then. */
  detail: string | null;
  /** Measured, not estimated. Null for a stage that has not started. */
  elapsed_ms: number | null;
}

export interface ProgressSnapshot {
  run_id: string;
  sku: string;
  state: "running" | "complete" | "failed";
  started_at: string;
  elapsed_ms: number;
  /** The id of the stage currently executing, or null between stages and at the end. */
  current: string | null;
  completed: number;
  total: number;
  message: string | null;
  notes: string[];
  stages: ProgressStage[];
  /** Whether the API still holds its single-run mutex. Absent on older API builds. */
  run_in_flight?: boolean;
}

/**
 * What a poll returned.
 *
 * `unavailable` is deliberately a first-class outcome rather than an error. A failed poll does not
 * mean a failed run — the run is a separate request that is almost certainly still going — so the
 * caller has to be able to tell "I cannot see the stages" apart from "the pipeline stopped". Folding
 * those together would make a flaky network look like a broken pipeline.
 */
export type ProgressRead =
  | { kind: "snapshot"; snapshot: ProgressSnapshot; receivedAt: number }
  | { kind: "idle" }
  | { kind: "unavailable"; detail: string };

/** Read the current snapshot. Never throws: a poll that fails is a value, like everything else here. */
export async function readProgress(signal?: AbortSignal): Promise<ProgressRead> {
  let response: Response;
  try {
    response = await fetch("/api/enrich/progress", { cache: "no-store", signal });
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return { kind: "unavailable", detail: reason };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "unavailable", detail: `The progress endpoint returned ${response.status}.` };
  }

  if (!response.ok) {
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `The progress endpoint returned ${response.status}.`;
    return { kind: "unavailable", detail };
  }

  if (body === null || typeof body !== "object") {
    return { kind: "unavailable", detail: "The progress endpoint returned an unreadable body." };
  }

  const record = body as Record<string, unknown>;
  // `idle` means nothing has run in this API process yet, which is an ordinary answer and not a
  // failure — a watcher polling on a timer should not treat the common case as an error.
  if (record.state === "idle" || !Array.isArray(record.stages)) {
    return { kind: "idle" };
  }

  return { kind: "snapshot", snapshot: record as unknown as ProgressSnapshot, receivedAt: Date.now() };
}

/**
 * The checklist to show before the first poll comes back.
 *
 * Derived from the plan `GET /api/enrich/limits` serves, so the rows appear the instant somebody
 * presses the button rather than a poll interval later — an empty panel for the first second reads as
 * a broken screen, which is the opposite of what this is for.
 *
 * Every row is `pending`, with no timings and no details, because none of that has happened. This is
 * a statement of intent and the component labels it as one; a real state only ever comes from a
 * snapshot. Getting that wrong would be the same mistake `pipeline-replay.tsx` spends its docstring
 * warning about, in the one place where it would actually mislead.
 *
 * The two drops mirror `axiom.pipeline.progress.plan_stages`. They are the only two the client can
 * know in advance, and the server's own plan supersedes this as soon as it arrives — so a
 * disagreement lasts one poll and resolves in favour of the run.
 */
export function optimisticPlan(
  plan: { id: string; name: string; narration: string; model: boolean }[],
  options: { retrieve: boolean; hasUrl: boolean; generateCopy: boolean },
): ProgressStage[] {
  const dropped = new Set<string>();
  // Nowhere to look: either retrieval is off, or the answer was supplied.
  if (!options.retrieve || options.hasUrl) dropped.add("retrieve");
  if (!options.generateCopy) dropped.add("copy");

  return plan
    .filter((stage) => !dropped.has(stage.id))
    .map((stage) => ({ ...stage, state: "pending" as StageState, detail: null, elapsed_ms: null }));
}

/** Seconds to one decimal, matching `stageSeconds` in `lib/stages.ts`. */
export function elapsedSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

/** `m:ss` for the run total, where a bare seconds count stops being readable. */
export function clockTime(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
