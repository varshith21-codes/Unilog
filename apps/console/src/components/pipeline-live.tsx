"use client";

/**
 * The stages of a run that just executed.
 *
 * The inverse of `pipeline-replay.tsx`, and the pairing is the point. That component animates cards
 * from persisted data and spends most of its labelling insisting it is *not* live, because an animated
 * sequence looks exactly like work happening now. This one has the opposite problem: the work really
 * did just happen, it cost real money, and the risk is understating that rather than overstating it.
 *
 * So the framing is reversed field by field:
 *
 * *   the heading says the run executed, not that it is being replayed;
 * *   the timestamp is "just now" with the wall-clock figure the run actually measured;
 * *   the per-stage latency reads `elapsed` rather than `recorded`;
 * *   the cost is on screen, because a screen that spends money should say what it spent.
 *
 * There is no reveal animation and no Replay button. The reveal on `/pipeline` exists to give a demo
 * beat something to watch while nothing is executing; here the waiting already happened, in the form,
 * and staging the cards afterwards would be theatre imitating the thing that just genuinely occurred.
 * Every number is rendered at once.
 */

import clsx from "clsx";

import { KeyValue, Overline } from "@/components/primitives";
import { StageCard } from "@/components/stage-card";
import type { EnrichSummary } from "@/lib/enrich";
import type { PipelineStage } from "@/lib/stages";
import { modelStageShare, stageSeconds } from "@/lib/stages";

export function PipelineLive({
  stages,
  sku,
  summary,
}: {
  stages: PipelineStage[];
  sku: string;
  summary: EnrichSummary;
}) {
  const share = modelStageShare(stages);
  const { cost, source } = summary;
  const fromDocument = source.kind === "document";

  return (
    <section aria-labelledby="pipeline-live-heading">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="min-w-0">
          <Overline>Completed run</Overline>
          <h2
            id="pipeline-live-heading"
            className="mt-2 text-xl font-medium tracking-[var(--tracking-heading)]"
          >
            Pipeline executed
            <span className="mono ml-3 text-base text-[var(--fg-tertiary)]">{sku}</span>
          </h2>
          <p className="mt-3 max-w-2xl text-body text-[var(--fg-secondary)]">
            Every figure below was measured by the run that just finished. {share.model} of{" "}
            {stages.length} stages called a model; the rest are deterministic, including every stage
            that decides whether a value may be published.
          </p>
        </div>
      </div>

      {/*
        The provenance strip, inverted from the replay's. There the badge exists to deny that anything
        ran; here it confirms it, and names which of the two source shapes the values came from —
        which is the single most important thing to read next to any value on this screen.
      */}
      <p className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 text-meta text-[var(--fg-quiet)]">
        <span className="pill pill-accent">Executed just now</span>
        <span className={clsx("pill", fromDocument ? "pill-quiet" : "pill-warn")}>
          {fromDocument ? "Source: manufacturer document" : "Source: your submission"}
        </span>
        {cost.latency_ms > 0 ? (
          <span>{stageSeconds(cost.latency_ms)} of model time, measured on this run</span>
        ) : null}
      </p>

      {/*
        What it cost, on the screen that caused it. Every other view in this console reads persisted
        output and is free; this is the one that bills, and burying that would make the number
        somebody discovers later rather than the number they saw when they pressed the button.
      */}
      <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-5 border-y border-[var(--hairline-strong)] py-5 lg:grid-cols-4">
        <KeyValue label="Model calls" mono>
          {cost.calls}
          {cost.escalations > 0 ? ` (${cost.escalations} escalated)` : ""}
        </KeyValue>
        <KeyValue label="Tokens" mono>
          {cost.input_tokens.toLocaleString()} in / {cost.output_tokens.toLocaleString()} out
        </KeyValue>
        <KeyValue label="Cost" mono>
          {cost.usd === null ? "unpriced" : `$${cost.usd.toFixed(6)}`}
        </KeyValue>
        <KeyValue label="Wall clock" mono>
          {(cost.latency_ms / 1000).toFixed(1)}s
        </KeyValue>
      </dl>

      {cost.usd === null ? (
        <p className="mt-3 text-meta text-[var(--fg-quiet)]">
          No published price for one of the tiers this run used, so no total is reported. A partial
          figure would understate it.
        </p>
      ) : null}

      <ol className="mt-7 flex flex-col gap-3">
        {stages.map((stage, index) => (
          <li key={stage.id}>
            {/*
              `elapsed`, not `recorded`. The figure is identical in both views and the word is the
              only thing that says whether it was measured a moment ago or read off disk.
            */}
            <StageCard stage={stage} index={index} latencyLabel="elapsed" />
          </li>
        ))}
      </ol>
    </section>
  );
}
