"use client";

/**
 * The run, while it is running.
 *
 * This is the third member of a family whose distinctions are the point, and it is worth stating all
 * three because the same stage cards can be made to tell three different stories:
 *
 * *   `pipeline-replay.tsx` animates stages from persisted data on `/pipeline`, and spends most of its
 *     labelling insisting nothing is executing — because an animated sequence looks exactly like live
 *     work.
 * *   `pipeline-live.tsx` renders a run that just finished, all at once, with no animation — because
 *     the waiting already happened and staging it afterwards would be theatre imitating the thing that
 *     genuinely occurred.
 * *   **This** one is the case neither covers: work happening *now*, reported by the server that is
 *     doing it. So it is the only one of the three where a spinner is truthful.
 *
 * Which makes the design constraint here the inverse of the replay's. There, the risk was implying
 * live work from persisted data. Here the risk is the reverse and subtler: a progress animation that
 * keeps moving on a client timer after the server has stopped reporting. That failure is invisible to
 * a viewer — a spinner turning over a dead run looks identical to a spinner turning over a working
 * one — and it would be the console lying about the one thing it exists to make checkable.
 *
 * So every state on this screen is derived from a snapshot the API published:
 *
 * *   a stage shows a spinner only while the server says `state: "running"`;
 * *   the progress bar's width is `completed / total` from the snapshot, transitioned rather than
 *     animated, so it can only move when the count moves;
 * *   per-stage elapsed figures come from the server's monotonic clock, interpolated forward only by
 *     the measured time since that snapshot arrived;
 * *   when polling fails, the animation *stops* and says so, rather than continuing on faith.
 *
 * The one thing the client owns outright is the total run clock, which is honest because it is timing
 * its own request. It is labelled as such.
 *
 * There is no percentage estimate and no ETA. Stage count is not duration: `classify` and `extract`
 * are two rows out of twelve and routinely most of the wall clock, and an escalation to a larger
 * model can triple `extract` on its own. A bar that claimed 60% while sitting inside the longest
 * stage would be worse than no bar.
 */

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

import { AlertIcon, CheckIcon, MinusIcon, Overline, Panel } from "@/components/primitives";
import type { ProgressSnapshot, ProgressStage } from "@/lib/progress";
import { clockTime, elapsedSeconds, readProgress } from "@/lib/progress";

/**
 * How often to ask the server where it is.
 *
 * 900ms. The thing being reported changes about a dozen times across a run, so a faster poll buys
 * nothing but requests; slower and a short stage — `normalize` is single-digit milliseconds — could
 * begin and end unseen between two polls, which makes the checklist skip a row.
 */
const POLL_MS = 900;

/**
 * How often to advance the clocks between polls.
 *
 * Separate from the poll on purpose, and much faster. A timer that only moves when a poll lands
 * stutters visibly, and a stalled digit is the specific thing that makes a viewer think a run has
 * hung. This interval re-renders from data already held; it fetches nothing.
 */
const TICK_MS = 100;

type Feed =
  /** Before the first poll resolves: the plan, and nothing claimed about it. */
  | { kind: "planned" }
  | { kind: "live"; snapshot: ProgressSnapshot; receivedAt: number }
  /** Polling failed. The run is a separate request and is probably fine; the view stops moving. */
  | { kind: "blind"; detail: string };

export function PipelineProgress({
  plan,
  sku,
  calls,
  /** False once the enrichment request has resolved, which stops the polling. */
  running,
}: {
  plan: ProgressStage[];
  sku: string;
  calls: number;
  running: boolean;
}) {
  const [feed, setFeed] = useState<Feed>({ kind: "planned" });
  const [now, setNow] = useState(() => Date.now());
  const startedAt = useRef(Date.now());

  /**
   * The run this component has locked onto.
   *
   * The API keeps the last finished run's snapshot rather than clearing it, so a poll can legitimately
   * return a *previous* run. Latching the first id seen while a run was in flight and then ignoring
   * every other one means the checklist cannot flip between two runs mid-render — which would look
   * like stages completing and then un-completing.
   */
  const followed = useRef<string | null>(null);

  // ---------------------------------------------------------------- polling
  useEffect(() => {
    if (!running) return;

    startedAt.current = Date.now();
    followed.current = null;

    let cancelled = false;
    const controller = new AbortController();

    async function poll() {
      const read = await readProgress(controller.signal);
      if (cancelled) return;

      if (read.kind === "unavailable") {
        // Only downgrade if nothing has been seen yet. Once a real snapshot has arrived, a single
        // failed poll is a blip, and replacing a populated checklist with an error would discard
        // information that is still true.
        setFeed((current) =>
          current.kind === "live" ? current : { kind: "blind", detail: read.detail },
        );
        return;
      }

      if (read.kind === "idle") {
        // The API has not registered the run yet. Ordinary for the first poll or two: the request has
        // to get through URL validation and the conflict check before progress starts.
        return;
      }

      const { snapshot, receivedAt } = read;
      const inFlight = snapshot.run_in_flight !== false;

      if (followed.current === null) {
        // Only adopt a run the API says is actually in flight. Without this the first poll would
        // latch onto the *previous* run's completed snapshot and show it as this one's.
        if (!inFlight) return;
        followed.current = snapshot.run_id;
      } else if (followed.current !== snapshot.run_id) {
        return;
      }

      setFeed({ kind: "live", snapshot, receivedAt });
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [running]);

  // ---------------------------------------------------------------- clocks
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [running]);

  const stages = feed.kind === "live" ? feed.snapshot.stages : plan;
  const settled = stages.filter((stage) => stage.state === "done" || stage.state === "skipped").length;
  const current = stages.find((stage) => stage.state === "running") ?? null;
  const failed = stages.find((stage) => stage.state === "failed") ?? null;

  /**
   * The pipeline is done but this component is still mounted.
   *
   * A real and initially confusing state, not an edge case. The API publishes `finish()` before it
   * serialises the response, and the server action then revalidates seven routes — each of which
   * re-reads every bundle in `data/console/` to recount the dashboards. On a thousand-SKU catalogue
   * that is seconds of work *after* the last stage ticked green.
   *
   * Without saying so, the checklist sat at 13 of 13 with a spinner nowhere and nothing happening,
   * which reads exactly like a run that finished and lost its result. So this is called out in words,
   * the indeterminate motion stops, and the panel says what is still outstanding.
   */
  const settling = feed.kind === "live" && feed.snapshot.state === "complete";

  // The bar is a count, not a duration, and the label below it says so.
  const share = stages.length === 0 ? 0 : settled / stages.length;
  const runMs = now - startedAt.current;

  /**
   * How long the running stage has been going.
   *
   * The server's figure plus the measured time since that snapshot arrived. Interpolating *forward*
   * from a real measurement is sound; the number is only ever as stale as one poll, and it can never
   * exceed the truth by more than the interval. It is deliberately not applied to finished stages,
   * whose figures are final.
   */
  function liveElapsed(stage: ProgressStage): number | null {
    if (stage.elapsed_ms === null) return null;
    if (stage.state !== "running" || feed.kind !== "live") return stage.elapsed_ms;
    return stage.elapsed_ms + Math.max(0, now - feed.receivedAt);
  }

  const headline =
    failed !== null
      ? "The run stopped"
      : settling
        ? "Pipeline finished"
        : current !== null
          ? current.name
          : feed.kind === "blind"
            ? "Running — stage detail unavailable"
            : "Starting the run";

  return (
    <Panel
      as="section"
      label="Pipeline run in progress"
      className="animate-rise overflow-hidden motion-reduce:animate-none"
      raised
    >
      {/* ------------------------------------------------------------ header */}
      <header className="border-b border-[var(--hairline-strong)] p-5 sm:p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <Overline>Running now</Overline>
          {/*
            The clock, `tabular-nums` so the digits do not jitter the layout as they change, and
            labelled as measured in the browser rather than reported by the run — it is timing the
            request, which is a slightly different thing from the pipeline's own wall clock.
          */}
          <p className="mono text-meta tabular-nums text-[var(--fg-quiet)]">
            {clockTime(runMs)} elapsed
          </p>
        </div>

        <h2 className="mt-2 flex flex-wrap items-baseline gap-x-3 text-xl font-medium tracking-[var(--tracking-heading)]">
          {headline}
          <span className="mono text-base text-[var(--fg-tertiary)]">{sku}</span>
        </h2>

        {/*
          What is happening, in words. The whole reason this component exists: a stage name is a label,
          and the question somebody watching a spinner actually has is what the machine is doing and
          why that step is there. The narration is the server's, per stage.
        */}
        <p className="mt-3 max-w-prose text-body text-[var(--fg-secondary)]">
          {failed !== null
            ? (failed.detail ?? "The run failed. Nothing was published.")
            : settling
              ? "Every stage is done and the result is written to disk. Assembling the run summary " +
                "and recounting the dashboards this SKU now appears on — a few seconds longer on a " +
                "large catalogue. The results open below when it lands."
              : (current?.narration ??
                (feed.kind === "blind"
                  ? "The run is still going — this console cannot read its stage detail right now, " +
                    "so the checklist below has stopped updating rather than guess."
                  : "Validating the submission and opening the run."))}
        </p>

        <div className="mt-5">
          {/*
            `data-active` drives the only indeterminate motion on the screen, and it is switched off
            the moment the run is not running — in the markup, not in the stylesheet, so there is no
            state in which the sweep outlives the work.
          */}
          <div
            className="progress-track"
            data-active={running && failed === null && !settling ? "true" : "false"}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={stages.length}
            aria-valuenow={settled}
            aria-valuetext={`${settled} of ${stages.length} stages complete`}
          >
            <div className="progress-fill" style={{ width: `${Math.round(share * 100)}%` }} />
          </div>

          <p className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-[var(--fg-quiet)]">
            <span className="tabular-nums">
              {settled} of {stages.length} stages
            </span>
            {/*
              Stated rather than left to be inferred from the bar. Two of these rows are model calls
              and routinely most of the wall clock, so a bar at "5 of 12" is not 40% of the wait — and
              a viewer who reads it that way will conclude the run has hung when it has not.
            */}
            <span>Stage count, not time remaining — the two model stages take the longest</span>
            <span className="mono">{calls} model calls this run</span>
          </p>
        </div>
      </header>

      {/*
        One polite live region for the whole sequence, announcing the stage rather than the counter.
        Per-row announcements would be twelve interruptions, and the count is already on screen.
      */}
      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {failed !== null
          ? `The run stopped during ${failed.name}.`
          : settling
            ? `All ${stages.length} stages complete. Assembling the results.`
            : current !== null
              ? `Stage ${settled + 1} of ${stages.length}: ${current.name}. ${current.narration}`
              : `${settled} of ${stages.length} stages complete.`}
      </p>

      {/* ------------------------------------------------------------ the checklist */}
      <ol className="flex flex-col gap-0 p-5 sm:p-6">
        {stages.map((stage, index) => (
          <StageRow
            key={stage.id}
            stage={stage}
            index={index}
            elapsedMs={liveElapsed(stage)}
            planned={feed.kind !== "live"}
          />
        ))}
      </ol>

      {feed.kind === "blind" ? (
        <p className="border-t border-[var(--hairline)] bg-[var(--surface-sunken)] px-5 py-4 text-meta text-[var(--fg-quiet)] sm:px-6">
          Stage progress is unavailable: {feed.detail}. The run itself is a separate request and is
          unaffected — its result will appear here when it returns.
        </p>
      ) : null}
    </Panel>
  );
}

// ------------------------------------------------------------------ one row

const STATE_TEXT: Record<ProgressStage["state"], string> = {
  done: "text-[var(--pass)]",
  running: "text-[var(--accent)]",
  failed: "text-[var(--fail)]",
  skipped: "text-[var(--fg-quiet)]",
  pending: "text-[var(--fg-quiet)]",
};

function StageRow({
  stage,
  index,
  elapsedMs,
  planned,
}: {
  stage: ProgressStage;
  index: number;
  elapsedMs: number | null;
  /** True while these rows are the plan rather than a reported state. Dims the whole list. */
  planned: boolean;
}) {
  const settled = stage.state === "done" || stage.state === "skipped";
  const active = stage.state === "running";

  return (
    <li
      className={clsx(
        "task-rail flex min-w-0 gap-3.5 py-3",
        // Pending rows recede rather than disappear, so the shape of what is left to do is legible.
        // Not `opacity-0`: the list length is information.
        stage.state === "pending" && "opacity-45",
        stage.state === "skipped" && "opacity-70",
        planned && "opacity-60",
      )}
      data-settled={settled ? "true" : "false"}
    >
      {/* The marker column. Fixed width so the rail is straight regardless of glyph. */}
      <span
        className={clsx("mt-0.5 flex size-3.5 shrink-0 items-center justify-center", STATE_TEXT[stage.state])}
      >
        {stage.state === "running" ? (
          <span className="task-spinner" aria-hidden />
        ) : stage.state === "done" ? (
          <CheckIcon />
        ) : stage.state === "failed" ? (
          <AlertIcon />
        ) : stage.state === "skipped" ? (
          <MinusIcon />
        ) : (
          <span className="task-dot" aria-hidden />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="mono text-meta text-[var(--fg-quiet)]">
            {String(index + 1).padStart(2, "0")}
          </span>
          <h3
            className={clsx(
              "text-sm",
              active ? "font-medium text-[var(--fg)]" : "text-[var(--fg-secondary)]",
            )}
          >
            {stage.name}
          </h3>

          {/*
            The model badge, which on this screen is the cost indicator: these are the rows spending
            money, and they are spending it right now rather than in a run somebody reads about later.
          */}
          <span className={clsx("pill", stage.model ? "pill-accent" : "pill-quiet")}>
            {stage.model ? "model" : "deterministic"}
          </span>

          {elapsedMs !== null ? (
            <span className="mono ml-auto text-meta tabular-nums text-[var(--fg-quiet)]">
              {elapsedSeconds(elapsedMs)}
            </span>
          ) : null}
        </div>

        {/*
          Narration while it runs; the result once it has. Two different claims, so never both: the
          narration says what is being attempted and the detail says what came out, and showing them
          together would leave a reader unsure which one describes the state they are looking at.
        */}
        {active ? (
          <p className="mt-1.5 max-w-[88ch] text-meta text-[var(--fg-secondary)]">{stage.narration}</p>
        ) : stage.detail !== null ? (
          <p
            className={clsx(
              "animate-fade-in mt-1.5 max-w-[88ch] text-meta motion-reduce:animate-none",
              stage.state === "failed" ? "text-[var(--fail)]" : "text-[var(--fg-tertiary)]",
            )}
          >
            {stage.detail}
          </p>
        ) : null}
      </div>
    </li>
  );
}
