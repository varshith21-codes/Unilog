import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PipelineLive } from "@/components/pipeline-live";
import { PipelineReplay } from "@/components/pipeline-replay";
import { pipelineStages } from "@/lib/stages";
import { enrichSummary, policySummary, sourceDocument, stageBundle } from "@/test/factories";

const STAGES = () => pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

afterEach(cleanup);

/**
 * These two components render the same cards and make opposite claims, which is the only interesting
 * thing about either of them.
 *
 * `/pipeline` reads a run off disk and animates it, so its risk is a viewer concluding that work is
 * happening now. `/enrich` really did just execute and really did just spend money, so its risk is
 * the reverse — understating that, and letting somebody treat a billed run as a free preview.
 *
 * So the assertions here are mostly about wording, and the last block is the one that matters most:
 * it checks that extracting `StageCard` into a shared module did not let either component's framing
 * leak into the other.
 */
describe("saying what it is", () => {
  it("calls itself an execution rather than a replay", () => {
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText(/Pipeline executed/i)).toBeTruthy();
    expect(screen.getByText(/Executed just now/i)).toBeTruthy();
    expect(screen.getByText(/measured by the run that just finished/i)).toBeTruthy();
  });

  it("describes the elapsed time as measured on this run, not recorded earlier", () => {
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText(/of model time, measured on this run/i)).toBeTruthy();
    expect(screen.queryByText(/recorded then/i)).toBeNull();
  });

  it("labels a stage's latency as elapsed rather than recorded", () => {
    // The figure is identical in both views. The word is the only thing that says whether it was
    // measured a moment ago or read off disk.
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText("7.0s elapsed")).toBeTruthy();
    expect(screen.queryByText("7.0s recorded")).toBeNull();
  });

  it("omits the elapsed claim when nothing was timed", () => {
    const summary = enrichSummary();
    render(
      <PipelineLive
        stages={STAGES()}
        sku="PDSH4816AF"
        summary={{ ...summary, cost: { ...summary.cost, latency_ms: 0 } }}
      />,
    );

    expect(screen.queryByText(/of model time/i)).toBeNull();
  });
});

describe("which source the values came from", () => {
  it("says the submission was the source when no URL was fetched", () => {
    // The single most important thing to read next to any value on this screen: "the manufacturer
    // published this" and "the person submitting it typed this" are different claims.
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText(/Source: your submission/i)).toBeTruthy();
    expect(screen.queryByText(/Source: manufacturer document/i)).toBeNull();
  });

  it("says a manufacturer document was the source when one was fetched", () => {
    const summary = enrichSummary();
    render(
      <PipelineLive
        stages={STAGES()}
        sku="PDSH4816AF"
        summary={{ ...summary, source: { ...summary.source, kind: "document" } }}
      />,
    );

    expect(screen.getByText(/Source: manufacturer document/i)).toBeTruthy();
    expect(screen.queryByText(/Source: your submission/i)).toBeNull();
  });
});

describe("the cost", () => {
  it("is on the screen that caused it", () => {
    // Every other view in this console reads persisted output and is free. Burying this number would
    // make it something somebody discovers on an invoice rather than when they pressed the button.
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText("Cost")).toBeTruthy();
    expect(screen.getByText("$0.000412")).toBeTruthy();
    expect(screen.getByText("Model calls")).toBeTruthy();
  });

  it("says unpriced rather than zero when a tier has no published price", () => {
    // A plausible made-up cost is worse than no cost, because it invites decisions. $0.00 would read
    // as free.
    const summary = enrichSummary();
    render(
      <PipelineLive
        stages={STAGES()}
        sku="PDSH4816AF"
        summary={{ ...summary, cost: { ...summary.cost, usd: null } }}
      />,
    );

    expect(screen.getByText("unpriced")).toBeTruthy();
    expect(screen.getByText(/A partial figure would understate it/i)).toBeTruthy();
  });

  it("reports escalations, since they are where an unexpected bill comes from", () => {
    const summary = enrichSummary();
    render(
      <PipelineLive
        stages={STAGES()}
        sku="PDSH4816AF"
        summary={{ ...summary, cost: { ...summary.cost, calls: 3, escalations: 1 } }}
      />,
    );

    expect(screen.getByText(/3 \(1 escalated\)/)).toBeTruthy();
  });
});

describe("the stages", () => {
  it("renders every stage at once, with no reveal to sit through", () => {
    // The waiting already happened, in the form. Staging the cards afterwards would be theatre
    // imitating the thing that just genuinely occurred.
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText("Ingest")).toBeTruthy();
    expect(screen.getByText("Extract")).toBeTruthy();
    expect(screen.getByText("Syndicate")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Replay" })).toBeNull();
  });

  it("still marks each stage as model or deterministic", () => {
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getAllByText("model")).toHaveLength(2);
    expect(screen.getAllByText("deterministic")).toHaveLength(7);
  });

  it("counts the model stages, because the ratio is the architectural claim", () => {
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.getByText(/2 of 9 stages called a model/i)).toBeTruthy();
  });
});

describe("the two framings do not leak into each other", () => {
  /**
   * The regression this file exists for.
   *
   * `StageCard` is now shared. The temptation when sharing it was to give it a `live` flag and let
   * one component render both framings, which would have put the most consequential honesty decision
   * in this console behind a boolean somebody could get wrong at a call site. These two tests are
   * what would fail if that happened.
   */
  it("the live view never claims nothing is executing", () => {
    render(<PipelineLive stages={STAGES()} sku="PDSH4816AF" summary={enrichSummary()} />);

    expect(screen.queryByText(/Nothing is executing now/i)).toBeNull();
    expect(screen.queryByText(/Replay of a recorded run/i)).toBeNull();
    expect(screen.queryByText(/Recorded run/i)).toBeNull();
  });

  it("the replay view never claims something just executed", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.queryByText(/Executed just now/i)).toBeNull();
    expect(screen.queryByText(/Pipeline executed/i)).toBeNull();
    expect(screen.queryByText(/measured on this run/i)).toBeNull();
    // And it keeps saying the thing it has always said.
    expect(screen.getByText(/Nothing is executing now/i)).toBeTruthy();
  });
});
