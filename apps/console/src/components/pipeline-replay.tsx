"use client";

import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";

import { AlertIcon, CheckIcon, Overline, Panel } from "@/components/primitives";
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

        <button type="button" onClick={replay} className="btn btn-quiet">
          Replay
        </button>
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

      <ol className="mt-7 flex flex-col gap-3">
        {stages.slice(0, revealed).map((stage, index) => (
          <li key={stage.id}>
            <StageCard stage={stage} index={index} />
          </li>
        ))}
      </ol>
    </section>
  );
}

const TONE_TEXT: Record<PipelineStage["tone"], string> = {
  pass: "text-[var(--pass)]",
  warn: "text-[var(--warn)]",
  fail: "text-[var(--fail)]",
  quiet: "text-[var(--fg-tertiary)]",
};

const TONE_EDGE: Record<PipelineStage["tone"], string> = {
  pass: "border-l-[var(--pass)]",
  warn: "border-l-[var(--warn)]",
  fail: "border-l-[var(--fail)]",
  // A decorative edge, not a control boundary, so the hairline token is the right one — and it is
  // the only one of the four that is deliberately *not* held to 3:1.
  quiet: "border-l-[var(--hairline-strong)]",
};

function StageCard({ stage, index }: { stage: PipelineStage; index: number }) {
  return (
    <Panel className={clsx("border-l-2 p-6", TONE_EDGE[stage.tone])}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
        <span className="mono text-meta text-[var(--fg-quiet)]">
          {String(index + 1).padStart(2, "0")}
        </span>
        <h3 className="text-sm font-medium">{stage.name}</h3>

        {/*
          Which stages called a model, marked on the stage itself. A reader tracing an unexpected
          value needs to know whether a model was involved in producing it, and that is not
          recoverable from the numbers.
        */}
        <span className={clsx("pill", stage.model ? "pill-accent" : "pill-quiet")}>
          {stage.model ? "model" : "deterministic"}
        </span>

        {stage.latencyMs !== null ? (
          <span className="text-meta text-[var(--fg-quiet)]">
            {stageSeconds(stage.latencyMs)} recorded
          </span>
        ) : null}

        <span className={clsx("ml-auto", TONE_TEXT[stage.tone])}>
          {stage.tone === "fail" ? <AlertIcon /> : <CheckIcon />}
        </span>
      </div>

      <p className={clsx("mt-3 text-body", TONE_TEXT[stage.tone])}>{stage.headline}</p>

      {stage.facts.length > 0 ? (
        <dl className="mt-4 flex flex-wrap gap-x-7 gap-y-2">
          {stage.facts.map((fact) => (
            <div key={fact.label}>
              <dt className="text-meta text-[var(--fg-quiet)]">{fact.label}</dt>
              <dd className="mono mt-0.5 text-sm text-[var(--fg-secondary)]">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <p className="hairline-t mt-5 pt-4 text-meta text-[var(--fg-quiet)]">{stage.note}</p>
    </Panel>
  );
}
