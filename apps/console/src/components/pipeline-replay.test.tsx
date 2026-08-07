import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PipelineReplay } from "@/components/pipeline-replay";
import { pipelineStages } from "@/lib/stages";
import { policySummary, sourceDocument, stageBundle } from "@/test/factories";

const STAGES = () => pipelineStages(stageBundle(), sourceDocument(), policySummary(), null);

/**
 * Reduced motion by default, so the assertions are about content rather than timing.
 *
 * That is not a shortcut around the animation — it is the branch a real user with the OS setting on
 * gets, and it has to show every number. The progressive reveal is exercised separately with fake
 * timers.
 */
function prefersReducedMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

beforeEach(() => prefersReducedMotion(true));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/**
 * The risk in this component is not a rendering bug, it is a false impression.
 *
 * A sequence of stage cards completing looks exactly like work happening now, and it is not — every
 * figure is read off a run that finished earlier and was written to disk. If a viewer concludes
 * otherwise, the console has misled them about provenance, which is the one thing this project exists
 * to make checkable. So most of these tests assert on labelling, and they are the important ones.
 */
describe("saying what it is", () => {
  it("calls itself a replay rather than a run", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText(/Replay of a recorded run/i)).toBeTruthy();
    expect(screen.getByText(/Nothing is executing now/i)).toBeTruthy();
  });

  it("states when the run it is replaying actually happened", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText(/Aug 5, 2026/)).toBeTruthy();
  });

  it("describes the elapsed time as recorded then, not measured now", () => {
    // 13.3s of model time is real, and it is not how long the replay took. Presenting it without
    // that qualifier would be the most plausible way for this screen to mislead.
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText(/13\.3s of model time, recorded then/i)).toBeTruthy();
  });

  it("distinguishes a hand-seeded fixture from a real recorded run", () => {
    // The distinction matters more on this screen than anywhere else in the console, because here the
    // numbers arrive with the visual grammar of live execution.
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={13319}
        live={false}
      />,
    );

    expect(screen.getByText(/Replay of a hand-seeded fixture/i)).toBeTruthy();
    expect(screen.queryByText(/Replay of a recorded run/i)).toBeNull();
  });

  it("omits the elapsed claim entirely when no latency was recorded", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="Aug 5, 2026, 3:16 AM"
        totalLatencyMs={null}
        live
      />,
    );

    expect(screen.queryByText(/of model time/i)).toBeNull();
  });
});

describe("the model boundary", () => {
  it("counts how many stages called a model", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText(/2 of 9 stages called a model/i)).toBeTruthy();
    expect(screen.getByText(/other 7 are deterministic/i)).toBeTruthy();
  });

  it("marks each stage as model or deterministic on the card itself", () => {
    // A reader tracing an unexpected value needs to know whether a model was involved, and that is
    // not recoverable from the stage's numbers.
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getAllByText("model")).toHaveLength(2);
    expect(screen.getAllByText("deterministic")).toHaveLength(7);
  });
});

describe("the stage cards", () => {
  it("shows every stage when the reader prefers reduced motion", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText("Ingest")).toBeTruthy();
    expect(screen.getByText("Extract")).toBeTruthy();
    expect(screen.getByText("Syndicate")).toBeTruthy();
    expect(screen.getByText("15 of 23 attributes")).toBeTruthy();
  });

  it("labels a stage's recorded latency as recorded", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText("7.0s recorded")).toBeTruthy();
  });

  it("announces progress to a screen reader rather than appearing silently", () => {
    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText(/Replay complete: 9 stages/i)).toBeTruthy();
  });
});

describe("the reveal", () => {
  it("stages the cards in order when motion is allowed", () => {
    prefersReducedMotion(false);
    vi.useFakeTimers();

    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    // Nothing yet: the effect resets the count before the first tick.
    expect(screen.queryByText("Ingest")).toBeNull();

    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByText("Ingest")).toBeTruthy();
    expect(screen.queryByText("Syndicate")).toBeNull();

    act(() => vi.advanceTimersByTime(300 * 9));
    expect(screen.getByText("Syndicate")).toBeTruthy();
  });

  it("finishes inside the demo beat it has to fit", () => {
    // Nine stages at 300ms is 2.7s. The blueprint gives this beat sixty seconds of narration, and a
    // reveal that outlasts the sentence describing it is worse than no reveal.
    prefersReducedMotion(false);
    vi.useFakeTimers();

    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText(/Replay complete: 9 stages/i)).toBeTruthy();
  });

  it("shows everything immediately when matchMedia is unavailable", () => {
    // Rather than crashing, or animating on an assumption about a preference it could not read.
    vi.stubGlobal("matchMedia", undefined);

    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );

    expect(screen.getByText("Syndicate")).toBeTruthy();
  });

  it("replays from the start when asked", () => {
    prefersReducedMotion(false);
    vi.useFakeTimers();

    render(
      <PipelineReplay
        stages={STAGES()}
        sku="BA-100-075"
        recordedAt="then"
        totalLatencyMs={13319}
        live
      />,
    );
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText("Syndicate")).toBeTruthy();

    act(() => screen.getByRole("button", { name: "Replay" }).click());
    expect(screen.queryByText("Syndicate")).toBeNull();

    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText("Syndicate")).toBeTruthy();
  });
});
