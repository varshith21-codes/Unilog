/**
 * Proxy for the in-flight run's stage progress.
 *
 * A route handler rather than a server action, and that is the whole reason this file exists. Server
 * actions are POSTs that go through the RSC pipeline and are serialised one at a time per client;
 * polling one every second while a *different* server action is mid-flight — which is exactly the
 * situation here, since `runEnrichment` is itself a server action holding open for up to five
 * minutes — queues the polls behind it and they all arrive after the run has finished. A route
 * handler is an ordinary HTTP GET and is not affected.
 *
 * It still keeps the property the rest of `lib/actions.ts` exists for: the browser never talks to
 * FastAPI directly, so `API_BASE` stays server-side and there is one place to add auth later.
 *
 * **This is free and read-only.** It makes no model call and touches no disk on the API side; it
 * reads a dict the running pipeline is writing to. Which is what makes polling it acceptable: the
 * enrichment endpoint next door costs money per call, and nothing about watching a run should.
 */

import { API_BASE } from "@/lib/data";

/**
 * Short on purpose.
 *
 * The upstream handler does no I/O, so anything slower than this is the API being unreachable rather
 * than busy — and a poll that hangs for eight seconds is worse than a poll that fails, because the
 * watcher stops updating either way and the slow one hides the reason.
 */
const TIMEOUT_MS = 4_000;

export async function GET(): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/enrich/progress`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    // 503 rather than 502, and a body the client can act on. A failed poll must not be treated as a
    // failed *run*: the run is a separate request that is very likely still going, and the honest
    // reading of "I cannot see the stages" is that progress is unavailable, not that the pipeline
    // stopped. The component degrades to a plain in-flight state on this.
    return Response.json(
      { state: "unavailable", detail: `Could not reach the AXIOM API at ${API_BASE} (${reason}).` },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  if (!upstream.ok) {
    return Response.json(
      {
        state: "unavailable",
        detail: `The API returned ${upstream.status} ${upstream.statusText}.`,
      },
      { status: upstream.status, headers: { "cache-control": "no-store" } },
    );
  }

  const body = await upstream.text();
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      // Every layer, explicitly. A cached progress snapshot is a stage list frozen at whatever the
      // first poll saw, which looks precisely like a hung run.
      "cache-control": "no-store, no-cache, must-revalidate",
      "x-content-type-options": "nosniff",
    },
  });
}
