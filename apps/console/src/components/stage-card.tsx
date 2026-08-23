"use client";

/**
 * One pipeline stage, as a card.
 *
 * Lifted out of `pipeline-replay.tsx` when a second screen needed it. `/pipeline` replays a run that
 * happened earlier; `/enrich` shows one that just finished. The cards are identical because they are
 * projections of the same `PipelineStage[]` — what differs is the framing around them, and that is
 * exactly the part that must not be shared.
 *
 * So this component deliberately carries **no** claim about *when* the work happened. It reports what
 * a stage did, whether a model was involved, and the latency the run recorded. Whether that is a
 * replay or an execution is asserted by the surrounding view, in words, once — because a component
 * that took a `live` flag would put the most important honesty decision on this screen behind a
 * boolean somebody could get wrong at a call site.
 */

import clsx from "clsx";

import { AlertIcon, CheckIcon, Panel } from "@/components/primitives";
import type { PipelineStage } from "@/lib/stages";
import { stageSeconds } from "@/lib/stages";

export const TONE_TEXT: Record<PipelineStage["tone"], string> = {
  pass: "text-[var(--pass)]",
  warn: "text-[var(--warn)]",
  fail: "text-[var(--fail)]",
  quiet: "text-[var(--fg-tertiary)]",
};

export const TONE_EDGE: Record<PipelineStage["tone"], string> = {
  pass: "border-l-[var(--pass)]",
  warn: "border-l-[var(--warn)]",
  fail: "border-l-[var(--fail)]",
  // A decorative edge, not a control boundary, so the hairline token is the right one — and it is
  // the only one of the four that is deliberately *not* held to 3:1.
  quiet: "border-l-[var(--hairline-strong)]",
};

export function StageCard({
  stage,
  index,
  latencyLabel = "recorded",
}: {
  stage: PipelineStage;
  index: number;
  /**
   * The word after the elapsed figure. `recorded` on a replay, `elapsed` on a live run.
   *
   * The one thing that does vary, because the figure itself is ambiguous without it: 7.0s is the
   * model time either way, and only the caller knows whether it was measured a moment ago or read off
   * disk.
   */
  latencyLabel?: string;
}) {
  return (
    /*
      `p-5` rather than `p-6`, and tighter separators below. Nine of these stack during a reveal, so
      every row of padding is multiplied by nine — this is the one lever that reduces how far the
      sequence pushes the last cards past the fold without touching the timing.
    */
    <Panel className={clsx("border-l-2 p-5", TONE_EDGE[stage.tone])}>
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
            {stageSeconds(stage.latencyMs)} {latencyLabel}
          </span>
        ) : null}

        <span className={clsx("ml-auto", TONE_TEXT[stage.tone])}>
          {stage.tone === "fail" ? <AlertIcon /> : <CheckIcon />}
        </span>
      </div>

      <p className={clsx("mt-2.5 max-w-[80ch] text-body", TONE_TEXT[stage.tone])}>
        {stage.headline}
      </p>

      {stage.facts.length > 0 ? (
        <dl className="mt-3.5 flex flex-wrap gap-x-7 gap-y-2">
          {stage.facts.map((fact) => (
            <div key={fact.label}>
              <dt className="text-meta text-[var(--fg-quiet)]">{fact.label}</dt>
              <dd className="mono mt-0.5 text-sm text-[var(--fg-secondary)]">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <p className="hairline-t mt-4 max-w-[92ch] pt-3.5 text-meta text-[var(--fg-quiet)]">
        {stage.note}
      </p>
    </Panel>
  );
}
