"use client";

import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";

import { Overline } from "@/components/primitives";
import { StageCard } from "@/components/stage-card";
import type { PipelineStage } from "@/lib/stages";
import { modelStageShare, stageSeconds } from "@/lib/stages";

/** Long enough to read a headline, short enough that ten stages finish inside the demo beat. */
const STAGE_INTERVAL_MS = 300;

/**
 * The pipeline run, replayed.
 *
 * The demo script wants stage cards lighting up one after another. This does that from persisted
 * data rather than by running anything, which is a deliberate trade the blueprint's own demo hygiene
 * notes ask for: conference WiFi will fail, and one frontier escalation is thirty seconds of dead air
 * in a seven-minute slot.
 *
 * That trade creates the one real risk in this component, which is not technical. An animated
 * sequence of stages completing looks exactly like work happening now. If a viewer draws that
 * conclusion, the console has misled them about the single thing this whole project exists to make
 * checkable — where a number came from. So the label is not decoration and is not optional: the
 * heading says replay, the timestamp says when the real run happened, and the elapsed figures are
 * described as recorded rather than measured just now. Every one of those is load-bearing.
 *
 * Timing is deliberately *not* proportional to the recorded durations. Extraction took seven
 * seconds; replaying seven seconds of a spinner would teach a viewer nothing they cannot read off
 * the card, and the recorded figure is shown as a number instead.
 */
export function PipelineReplay({
  stages,
  sku,
  recordedAt,
  totalLatencyMs,
  live,
}: {
  stages: PipelineStage[];
  sku: string;
  /** When the run being replayed actually happened. */
  recordedAt: string;
  /** Total recorded model latency for the run. */
  totalLatencyMs: number | null;
  /** False when the dataset came from the offline fixture rather than the API. */
  live: boolean;
}) {
  // Start fully revealed. If the effect never runs — no JS, or a server render — the page shows
  // every stage rather than an empty frame that never fills.
  const [revealed, setRevealed] = useState(stages.length);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    // Guarded on the function's existence, not just on `window`. Where `matchMedia` is missing the
    // honest default is no animation: staging is decoration, and the numbers are the content.
    const reduced =
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced) {
      // Not a lesser experience. Every number is already on screen; only the staging is skipped.
      setRevealed(stages.length);
      return;
    }

    setRevealed(0);
    const timer = setInterval(() => {
      setRevealed((current) => {
        if (current >= stages.length) {
          clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, STAGE_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [stages.length, generation]);

  const replay = useCallback(() => setGeneration((value) => value + 1), []);
  const share = modelStageShare(stages);
  const done = revealed >= stages.length;

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="min-w-0">
          <Overline>Recorded run</Overline>
          <h2 className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]">
            Pipeline replay
            <span className="mono ml-3 text-base text-[var(--fg-tertiary)]">{sku}</span>
          </h2>
          <p className="mt-3 max-w-2xl text-body text-[var(--fg-secondary)]">
            Every figure below was read off a run that already happened and was written to disk.
            Nothing is executing now, and nothing is estimated &mdash; the stages are staged for
            legibility, not measured live.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {/*
            Visible progress. The sequence already announced itself to a screen reader and showed
            nothing to anyone else, so a sighted viewer watching cards appear had no way to tell
            whether more were coming — which on a nine-stage reveal is the difference between waiting
            and assuming it had finished.

            `aria-hidden` because the live region below carries the same information properly;
            announcing a bare fraction on every tick would be nine interruptions.
          */}
          {!done ? (
            <p aria-hidden className="text-meta tabular-nums text-[var(--fg-quiet)]">
              {revealed} of {stages.length}
            </p>
          ) : null}

          <button type="button" onClick={replay} className="btn btn-quiet">
            Replay
          </button>
        </div>
      </div>

      {/*
        The provenance strip. `live` distinguishes a recorded real run from the checked-in fixture,
        whose model responses are hand-seeded — and on a screen that animates like a live execution
        that distinction carries more weight than anywhere else in the console.
      */}
      <p className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 text-meta text-[var(--fg-quiet)]">
        <span className={clsx("pill", live ? "pill-quiet" : "pill-warn")}>
          {live ? "Replay of a recorded run" : "Replay of a hand-seeded fixture"}
        </span>
        <span>
          Run recorded <time dateTime={recordedAt}>{recordedAt}</time>
        </span>
        {totalLatencyMs !== null ? (
          <span>
            {stageSeconds(totalLatencyMs)} of model time, recorded then &mdash; not now
          </span>
        ) : null}
      </p>

      {/*
        The ratio is the architectural claim, so it is stated as a fact rather than left to be
        counted off the cards: a model reads and classifies, and every stage that decides whether a
        value may be published is deterministic.
      */}
      <p className="mt-3 text-meta text-[var(--fg-secondary)]">
        <span className="font-medium text-[var(--fg)]">
          {share.model} of {stages.length} stages called a model.
        </span>{" "}
        The other {share.deterministic} are deterministic, including every stage that decides whether
        a value may be published.
      </p>

      {/* A polite live region so the sequence is announced rather than silently appearing. */}
      <p aria-live="polite" className="sr-only">
        {done
          ? `Replay complete: ${stages.length} stages.`
          : `Replaying stage ${revealed} of ${stages.length}.`}
      </p>

      {/*
        Each card enters with the shared rise, which is the whole fix here.

        The staging worked and looked wrong: a card was appended with no transition, so nine stages
        arrived as nine jump-cuts and the sequence read as a rendering glitch rather than a
        progression. A 300ms rise against a 300ms interval means each card is still settling as the
        next begins, which is what makes it read as flow.

        `animate-rise` rather than a `reveal-*` step: the delay here comes from the interval, not from
        a stagger, so stacking a CSS delay on top would double it.

        The reveal's real weakness is not its speed. Nine cards grow the page by roughly 1800px, so a
        viewer watching from the top loses the last few below the fold. Slowing it down would make
        that worse, and auto-scrolling would take the page away from them — so the interval stays and
        the honest fix is a shorter card, which is a content decision rather than a motion one.
      */}
      <ol className="mt-7 flex flex-col gap-3">
        {stages.slice(0, revealed).map((stage, index) => (
          <li key={stage.id} className="animate-rise">
            <StageCard stage={stage} index={index} />
          </li>
        ))}
      </ol>
    </section>
  );
}

// `StageCard`, `TONE_TEXT` and `TONE_EDGE` moved to `components/stage-card.tsx` when `/enrich` needed
// the same card for a run that had just executed. What stayed here is the framing — the replay label,
// the recorded timestamp, the "nothing is executing now" — which is the part that must *not* be
// shared, because it is the claim this screen makes about its own numbers.
