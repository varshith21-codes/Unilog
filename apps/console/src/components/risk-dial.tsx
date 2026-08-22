"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

import { AlertIcon, EmptyState, Meter } from "@/components/primitives";
import { fetchPolicy } from "@/lib/actions";
import { count, percent, score as fmtScore } from "@/lib/format";
import type { RiskCoveragePoint, RiskPolicyView } from "@/lib/types";

/**
 * The risk dial: choose an error budget, see what it costs in coverage.
 *
 * This is the whole thesis in one control. Everything else in the system exists to make the
 * number on this chart trustworthy — the evidence contract, the validation layers, the Wilson
 * bound — and a reviewer or a buyer should be able to move the budget and watch the operating
 * point move rather than take the claim on faith.
 *
 * Drawn by hand in SVG. A charting library would be ~40 kB of client JavaScript to render one
 * monotone line, thirty points, and two rules.
 */

const WIDTH = 560;
const HEIGHT = 260;
const PAD = { top: 16, right: 16, bottom: 40, left: 52 };

/** Presets in percent. The slider covers the same range continuously. */
const BUDGETS = [1, 2, 5, 10, 20];
const MIN_BUDGET = 1;
const MAX_BUDGET = 20;

export interface RiskDialProps {
  /** The policy the displayed data was actually decided under. */
  initial: RiskPolicyView;
}

export function RiskDial({ initial }: RiskDialProps) {
  const [epsilonPct, setEpsilonPct] = useState(Math.round(initial.epsilon * 100));
  const [policy, setPolicy] = useState<RiskPolicyView>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const load = useCallback((pct: number) => {
    startTransition(async () => {
      const result = await fetchPolicy(pct / 100);
      if (result.ok) {
        setPolicy(result.data);
        setError(null);
      } else {
        setError(result.error);
      }
    });
  }, []);

  // Debounced so dragging the slider does not fire a request per pixel.
  useEffect(() => {
    if (epsilonPct === Math.round(policy.epsilon * 100)) return;
    const timer = setTimeout(() => load(epsilonPct), 180);
    return () => clearTimeout(timer);
  }, [epsilonPct, policy.epsilon, load]);

  const curve = policy.curve ?? [];
  const budget = epsilonPct / 100;

  const scales = useMemo(() => buildScales(curve, budget), [curve, budget]);
  const operating = useMemo(() => findOperatingPoint(curve, policy), [curve, policy]);

  return (
    <div className="grid gap-6 lg:grid-cols-12">
      {/* ------------------------------------------------------------ the control */}
      <div className="lg:col-span-4">
        <label htmlFor="risk-budget" className="overline">
          Error budget
        </label>
        <div className="mt-3 flex items-baseline gap-2">
          <output htmlFor="risk-budget" className="figure tabular-nums">
            {epsilonPct}%
          </output>
          <span className="text-meta text-[var(--fg-quiet)]">
            at {percent(policy.confidence_level)} confidence
          </span>
        </div>

        {/*
          Track and fill are a `Meter` underneath the input, not the input's own pseudo-elements.

          Styling `::-webkit-slider-runnable-track` means giving up the native fill, and the usual
          way back — a huge box-shadow on the thumb clipped by an `overflow: hidden` track — fails
          loudly in any engine that declines to clip it. Rendering the bar as the same component
          every other bar on this screen uses costs one wrapper and inherits colours that are
          already verified against the surface.
        */}
        {/*
          The height comes from `.slider`, which grows to a 44px target on a coarse pointer, and the
          wrapper sizes to it. `top-1/2 -translate-y-1/2` on the bar rather than relying on
          `items-center`: an absolutely-positioned flex child's static position is resolved from the
          alignment properties, which is correct per spec but not worth depending on for the one
          control this whole screen is about.
        */}
        <div className="relative mt-5 flex items-center">
          <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2">
            <Meter
              value={(epsilonPct - MIN_BUDGET) / (MAX_BUDGET - MIN_BUDGET)}
              tone="accent"
              label={`Error budget ${epsilonPct} percent`}
            />
          </div>
          <input
            id="risk-budget"
            type="range"
            min={MIN_BUDGET}
            max={MAX_BUDGET}
            step={1}
            value={epsilonPct}
            onChange={(event) => setEpsilonPct(Number(event.target.value))}
            aria-describedby="risk-verdict"
            className="slider relative"
          />
        </div>

        <div className="mt-4 flex flex-wrap gap-1.5">
          {BUDGETS.map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => setEpsilonPct(preset)}
              aria-pressed={epsilonPct === preset}
              /*
                `pill-button` rather than a bare pill. These are controls, and they had a rest state
                and nothing else — no hover, no press, no disabled. Six states now come from one
                place instead of being re-decided per usage.
              */
              className={`pill pill-button ${epsilonPct === preset ? "pill-accent" : "pill-quiet"}`}
            >
              {preset}%
            </button>
          ))}
        </div>

        {/*
          The verdict, stated plainly. "Not achievable" is the interesting case and must not
          look like an error: it means the data cannot support a guarantee that tight, which is
          the system declining to promise something rather than failing.
        */}
        {/*
          `data-pending` on the whole readout rather than on the verdict pill alone.

          It was on the pill and nothing in the stylesheet responded to it, so refetching the policy
          had no visible state at all: a reviewer dragging the slider watched four stale numbers sit
          still and could not tell whether the request was in flight or the answer had not changed.
          Dimming rather than blanking, because the previous reading is still true until the new one
          lands — a skeleton here would throw away information that is still usable.
        */}
        <div
          id="risk-verdict"
          aria-live="polite"
          aria-busy={pending || undefined}
          data-pending={pending ? "true" : undefined}
          className="mt-7"
        >
          {error !== null ? (
            <p className="flex gap-2 text-sm text-[var(--fail)]">
              <AlertIcon className="mt-0.5 shrink-0" />
              {error}
            </p>
          ) : (
            <>
              <span className={`pill ${policy.achievable ? "pill-pass" : "pill-warn"}`}>
                {policy.achievable ? "Achievable" : "Not achievable"}
              </span>

              <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4">
                <div>
                  <dt className="text-meta text-[var(--fg-quiet)]">Coverage</dt>
                  <dd className="mt-1 text-lg tabular-nums">
                    {policy.achievable ? percent(policy.coverage, 1) : "0%"}
                  </dd>
                </div>
                <div>
                  <dt className="text-meta text-[var(--fg-quiet)]">Threshold</dt>
                  <dd className="mt-1 text-lg tabular-nums">
                    {policy.achievable && policy.threshold !== null
                      ? fmtScore(policy.threshold)
                      : "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-meta text-[var(--fg-quiet)]">Worst-case error</dt>
                  <dd className="mt-1 text-lg tabular-nums">
                    {percent(policy.error_upper_bound, 2)}
                  </dd>
                </div>
                <div>
                  <dt className="text-meta text-[var(--fg-quiet)]">Calibration</dt>
                  <dd className="mt-1 text-lg tabular-nums">
                    {count(policy.calibration_size)}
                  </dd>
                </div>
              </dl>

              <p className="mt-4 max-w-[44ch] text-meta text-[var(--fg-secondary)]">
                {policy.reason.charAt(0).toUpperCase() + policy.reason.slice(1)}.
              </p>
            </>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------ the curve */}
      <div className="lg:col-span-8">
        <Curve curve={curve} scales={scales} budget={budget} operating={operating} />

        {/*
          This needs saying, because the chart looks backwards to anyone who expects "stricter
          is safer". With zero observed errors, raising the threshold shrinks the accepted
          sample, and a smaller sample widens the Wilson bound. So the guarantee gets *weaker*
          as coverage falls. What buys a tighter bound is more reviewed data, not more caution.
        */}
        <p className="mt-4 max-w-[74ch] text-meta text-[var(--fg-quiet)]">
          The bound tightens as coverage rises, which is not a mistake. There are no observed
          errors in this calibration set, so a stricter threshold only shrinks the accepted
          sample — and a one-sided Wilson bound on a smaller sample is wider. A tighter
          guarantee comes from more reviewed values, not from more caution.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- chart

interface Scales {
  x: (coverage: number) => number;
  y: (bound: number) => number;
  maxBound: number;
}

function buildScales(curve: RiskCoveragePoint[], budget: number): Scales {
  const bounds = curve.map((point) => point.error_upper_bound);
  // Always include the budget line so it never falls outside the plotted area.
  const maxBound = Math.max(0.05, budget * 1.25, ...bounds) * 1.08;

  const plotWidth = WIDTH - PAD.left - PAD.right;
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;

  return {
    x: (coverage) => PAD.left + coverage * plotWidth,
    y: (bound) => PAD.top + plotHeight - (bound / maxBound) * plotHeight,
    maxBound,
  };
}

/** The point the policy actually selected, so the chart marks a real row rather than a guess. */
function findOperatingPoint(
  curve: RiskCoveragePoint[],
  policy: RiskPolicyView,
): RiskCoveragePoint | null {
  if (!policy.achievable || policy.threshold === null) return null;
  return (
    curve.find((point) => Math.abs(point.threshold - policy.threshold!) < 1e-6) ?? {
      threshold: policy.threshold,
      coverage: policy.coverage,
      error_upper_bound: policy.error_upper_bound,
    }
  );
}

function Curve({
  curve,
  scales,
  budget,
  operating,
}: {
  curve: RiskCoveragePoint[];
  scales: Scales;
  budget: number;
  operating: RiskCoveragePoint | null;
}) {
  if (curve.length === 0) {
    return (
      /*
        The chart's own aspect ratio rather than a hardcoded 260px, so the placeholder occupies
        exactly the space the curve will and the swap does not shift the page. `unmeasured`: there is
        no curve because no backtest has run, and a reader must not take an absent curve for a
        policy that was evaluated and found to guarantee nothing.
      */
      <div className="panel flex items-center justify-center" style={{ aspectRatio: WIDTH / HEIGHT }}>
        <EmptyState
          kind="unmeasured"
          title="No calibration data"
          detail={
            <>
              The risk&ndash;coverage curve is computed from reviewed outcomes, and none have been
              recorded. Produce them with{" "}
              <span className="mono">python scripts/run_backtest.py --write</span>.
            </>
          }
        />
      </div>
    );
  }

  const ordered = [...curve].sort((a, b) => a.coverage - b.coverage);
  const line = ordered
    .map(
      (point, index) =>
        `${index === 0 ? "M" : "L"}${scales.x(point.coverage).toFixed(1)},${scales
          .y(point.error_upper_bound)
          .toFixed(1)}`,
    )
    .join(" ");

  const budgetY = scales.y(budget);
  const plotRight = WIDTH - PAD.right;
  const plotBottom = HEIGHT - PAD.bottom;

  return (
    <figure
      className="panel scroll-x p-4"
      tabIndex={0}
      role="region"
      aria-label="Scrollable risk coverage chart"
    >
      <figcaption className="sr-only">
        Risk–coverage curve. Each point is a candidate threshold, showing what share of values
        would publish automatically and the worst-case error rate on them.
      </figcaption>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full min-w-[35rem]"
        role="img"
        aria-label={
          operating
            ? `Risk coverage curve. Selected operating point: ${percent(
                operating.coverage,
                1,
              )} coverage at a worst-case error rate of ${percent(
                operating.error_upper_bound,
                2,
              )}.`
            : "Risk coverage curve. No threshold satisfies the current error budget."
        }
      >
        {/* feasible region: at or below the budget */}
        <rect
          x={PAD.left}
          y={budgetY}
          width={plotRight - PAD.left}
          height={Math.max(0, plotBottom - budgetY)}
          fill="var(--pass)"
          opacity={0.07}
        />

        {/* axes */}
        <line
          x1={PAD.left}
          y1={plotBottom}
          x2={plotRight}
          y2={plotBottom}
          stroke="var(--hairline)"
        />
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={plotBottom} stroke="var(--hairline)" />

        {/* y ticks */}
        {yTicks(scales.maxBound).map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              y1={scales.y(tick)}
              x2={plotRight}
              y2={scales.y(tick)}
              stroke="var(--hairline)"
              strokeDasharray="2 4"
              opacity={0.5}
            />
            <text
              x={PAD.left - 8}
              y={scales.y(tick) + 3.5}
              textAnchor="end"
              className="fill-[var(--fg-quiet)] text-micro tabular-nums"
            >
              {percent(tick, 0)}
            </text>
          </g>
        ))}

        {/* x ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <text
            key={tick}
            x={scales.x(tick)}
            y={plotBottom + 15}
            textAnchor="middle"
            className="fill-[var(--fg-quiet)] text-micro tabular-nums"
          >
            {percent(tick, 0)}
          </text>
        ))}

        <text
          x={PAD.left + (plotRight - PAD.left) / 2}
          y={HEIGHT - 6}
          textAnchor="middle"
          className="fill-[var(--fg-tertiary)] text-micro"
        >
          Coverage — share of values published without a reviewer
        </text>

        {/* the budget */}
        <line
          x1={PAD.left}
          y1={budgetY}
          x2={plotRight}
          y2={budgetY}
          stroke="var(--warn)"
          strokeWidth={1.5}
          strokeDasharray="5 3"
        />
        <text
          x={plotRight - 4}
          y={budgetY - 5}
          textAnchor="end"
          className="fill-[var(--warn)] text-[9px] tabular-nums"
        >
          budget {percent(budget, 0)}
        </text>

        {/* the curve */}
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2} />
        {ordered.map((point) => (
          <circle
            key={point.threshold}
            cx={scales.x(point.coverage)}
            cy={scales.y(point.error_upper_bound)}
            r={2}
            fill="var(--accent)"
            opacity={0.55}
          />
        ))}

        {/* where the policy landed */}
        {operating ? (
          <g>
            <line
              x1={scales.x(operating.coverage)}
              y1={PAD.top}
              x2={scales.x(operating.coverage)}
              y2={plotBottom}
              stroke="var(--pass)"
              strokeWidth={1}
              opacity={0.5}
            />
            {/*
              The ring is `--surface`, which is what this marker actually sits on. It was `--canvas`,
              a step darker in light mode than the panel behind the chart, so the operating point —
              the one mark on this figure a reader is meant to find — wore a visible halo of the
              wrong colour.
            */}
            <circle
              cx={scales.x(operating.coverage)}
              cy={scales.y(operating.error_upper_bound)}
              r={5}
              fill="var(--pass)"
              stroke="var(--surface)"
              strokeWidth={2}
            />
          </g>
        ) : null}

        <text
          x={PAD.left}
          y={PAD.top - 4}
          className="fill-[var(--fg-tertiary)] text-micro"
        >
          Worst-case error rate
        </text>
      </svg>
    </figure>
  );
}

function yTicks(maxBound: number): number[] {
  const step = maxBound > 0.15 ? 0.05 : maxBound > 0.06 ? 0.02 : 0.01;
  const ticks: number[] = [];
  for (let value = 0; value <= maxBound; value += step) {
    ticks.push(Number(value.toFixed(4)));
  }
  return ticks;
}
