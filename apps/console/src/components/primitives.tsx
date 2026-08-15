/**
 * Shared primitives.
 *
 * Every visual value resolves to a token defined in `globals.css`. Nothing here carries a
 * raw hex, px or shadow. Interaction states are defined on the `.btn` / `.pill` component
 * classes rather than repeated per usage.
 */

import clsx from "clsx";
import { Children, type ReactNode } from "react";

import {
  DECISION_LABEL,
  METHOD_LABEL,
  REQUIREMENT_LABEL,
  STATUS_LABEL,
  VERDICT_LABEL,
} from "@/lib/format";
import type {
  DecisionReason,
  DerivationMethod,
  Requirement,
  ValueStatus,
  Verdict,
} from "@/lib/types";

// ---------------------------------------------------------------- icons
// Inline rather than a dependency: five glyphs do not justify an icon package, and these
// inherit currentColor so they stay correct in both themes.

type IconProps = { className?: string };

export function CheckIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className={clsx("size-3", className)} fill="none">
      <path
        d="M2.5 6.4 4.6 8.5 9.5 3.6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AlertIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className={clsx("size-3", className)} fill="none">
      <path
        d="M6 2.2v4.1M6 8.9v.6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <circle cx="6" cy="6" r="4.6" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

export function MinusIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className={clsx("size-3", className)} fill="none">
      <path d="M3.2 6h5.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function ArrowIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className={clsx("size-3", className)} fill="none">
      <path
        d="M4.4 2.6 7.8 6l-3.4 3.4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AnchorIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className={clsx("size-3", className)} fill="none">
      <path
        d="M2.4 6.6 5.4 9.6M9.6 3.4 6.6 6.4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
      <path
        d="M4.2 4.8 7.2 1.8a1.7 1.7 0 0 1 2.4 2.4L6.6 7.2"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------- layout

/**
 * Surface container.
 *
 * Defaults to a `div`. A `section` without an accessible name produces a landmark that
 * announces as "section" and nothing else, so callers must opt in by passing both
 * `as="section"` and a label.
 */
export function Panel({
  children,
  className,
  raised = false,
  as: Tag = "div",
  label,
}: {
  children: ReactNode;
  className?: string;
  raised?: boolean;
  as?: "section" | "div" | "article" | "aside";
  label?: string;
}) {
  return (
    <Tag
      className={clsx(raised ? "panel-raised" : "panel", className)}
      aria-label={Tag === "div" ? undefined : label}
    >
      {children}
    </Tag>
  );
}

export function Overline({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={clsx("overline", className)}>{children}</p>;
}

/**
 * Section shell. Owns the vertical rhythm and the rule that separates one section from the next.
 *
 * Sections were previously spaced by whatever each page reached for — `py-[var(--spacing-section)]`
 * on one screen, a fixed `mt-16` on another, nothing at all on a third. Fixed spacing is the worse
 * of the two: it does not scale with the viewport, so the same page that felt tight at 360px felt
 * arbitrary at 1600px. Routing every section through one component means the rhythm is a property
 * of the design system rather than of whoever wrote the page.
 *
 * `divider` is the other half. A long screen of stacked panels with no rules between them reads as
 * one undifferentiated column; a hairline above each section is what turns it into a document with
 * structure, and it costs a border rather than a card.
 *
 * `<section>` without an accessible name is exposed as generic rather than as a landmark, so this
 * does not add navigational noise. Pass `labelledBy` when the section's own heading should name it.
 */
export function Section({
  children,
  className,
  divider = true,
  rhythm = "base",
  labelledBy,
  id,
}: {
  children: ReactNode;
  className?: string;
  /** A hairline above the section. Off for the first section after a masthead. */
  divider?: boolean;
  /** `lg` for a section that opens a new chapter of the page rather than continuing one. */
  rhythm?: "base" | "lg" | "tight";
  labelledBy?: string;
  id?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={clsx(
        divider && "hairline-t",
        /*
         * Asymmetric on purpose: more space above the rule than below it. A rule sitting
         * equidistant between two sections belongs to neither, so the eye has to decide which
         * heading it introduces. Weighting the gap upward attaches it to the heading underneath,
         * which is the editorial convention and the one that makes a long page scannable.
         *
         * The lower half is derived from the same token rather than fixed, so the whole rhythm
         * still scales with the viewport.
         */
        rhythm === "lg"
          ? "mt-[var(--spacing-section-lg)] pt-[calc(var(--spacing-section)*0.6)]"
          : rhythm === "tight"
            ? "mt-[var(--spacing-section)] pt-[calc(var(--spacing-section)*0.4)]"
            : "mt-[var(--spacing-section)] pt-[calc(var(--spacing-section)*0.55)]",
        // With no rule there is nothing for the padding to sit under, so the margin carries the
        // whole gap.
        !divider && "pt-0",
        className,
      )}
    >
      {children}
    </section>
  );
}

/**
 * Section heading. One weight step and one colour step below the page title — hierarchy
 * through weight and colour rather than another size, which keeps a dense screen calm.
 *
 * `level="primary"` promotes the heading one step up the type scale. It exists because a page of
 * uniformly-weighted section headings tells the reader nothing about which section matters: on the
 * overview, the review queue is work somebody has to do and the cost table is context, and those
 * two had been reading as equals. One size step is the cheapest way to say so, and it stays inside
 * the existing scale rather than inventing a sixth heading size.
 */
export function SectionHeading({
  title,
  detail,
  action,
  className,
  level = "default",
  id,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  className?: string;
  level?: "default" | "primary";
  id?: string;
}) {
  return (
    <div className={clsx("flex flex-wrap items-baseline justify-between gap-x-6 gap-y-3", className)}>
      <div className="min-w-0">
        <h2
          id={id}
          className={clsx(
            "font-medium",
            level === "primary"
              ? "text-xl tracking-[var(--tracking-heading)]"
              : "text-lg",
          )}
        >
          {title}
        </h2>
        {detail ? (
          <p className="mt-1.5 max-w-[76ch] text-sm text-[var(--fg-tertiary)]">{detail}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/**
 * Designed empty state.
 *
 * `kind` is the load-bearing prop, and it is not styling. This product distinguishes "there is
 * nothing in this set" from "this was never measured" everywhere else — a quiet `0` against an
 * em-dash in a table cell, a suppressed meter against a zero-width one — and an empty panel is
 * where that distinction is easiest to lose and most expensive to lose. A reviewer who reads
 * "nothing to review" as "nothing was checked" draws the opposite conclusion from the true one.
 *
 * So the mark carries it: a solid border and a minus for an empty set, a dashed border and the
 * same em-dash the tables use for something unmeasured. The mark is `aria-hidden` and the
 * distinction is repeated in text for a screen reader, because shape alone is not a label.
 */
export function EmptyState({
  title,
  detail,
  action,
  kind = "empty",
}: {
  title: string;
  /**
   * `ReactNode` rather than `string`, matching `SectionHeading`. An empty state usually has to name
   * the command that fills it, and a command reads as a command only when it can be marked up.
   */
  detail?: ReactNode;
  action?: ReactNode;
  /** `unmeasured` when the panel is empty because nothing was observed, not because nothing exists. */
  kind?: "empty" | "unmeasured";
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3.5 px-6 py-12 text-center">
      <span
        aria-hidden
        className={clsx(
          "grid size-9 place-items-center rounded-md text-[var(--fg-quiet)]",
          "bg-[var(--surface-sunken)]",
          /*
           * The dashed border is at `--fg-quiet`, not at the decorative hairline the solid variant
           * uses. It is carrying meaning — dashed is what says "not measured" — so it is held to the
           * 3:1 a graphical object needs, and `--hairline-strong` does not clear that against a
           * sunken fill in either theme.
           */
          kind === "unmeasured"
            ? "border border-dashed border-[var(--fg-quiet)]"
            : "border border-[var(--hairline-strong)]",
        )}
      >
        {kind === "unmeasured" ? <span className="mono">&mdash;</span> : <MinusIcon />}
      </span>

      <div className="flex flex-col gap-1.5">
        <p className="text-body font-medium">
          {title}
          <span className="sr-only">
            {kind === "unmeasured" ? " — not measured" : " — nothing to show"}
          </span>
        </p>
        {detail ? (
          <p className="max-w-[46ch] text-sm text-[var(--fg-tertiary)]">{detail}</p>
        ) : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------- figures

/**
 * The rule-bounded band of headline figures that opens each index page.
 *
 * A band rather than a row of cards: four figures that share a denominator belong in one horizontal
 * field, and boxing each would put three borders between numbers the reader is comparing. Vertical
 * rules between the columns instead, because at four columns the gap alone left it ambiguous whether
 * a hint belonged to the figure above it or the one beside it.
 *
 * A component rather than the same twenty classes on three pages. The column rules and the stagger
 * steps have to change together and have to survive a reflow from four columns to two — the padding
 * has to drop to zero on whichever column starts a row, and which column that is depends on the
 * breakpoint. Expressing that with `first:` and `md:` variants on one element does not work: variant
 * ordering decides which of `first:pl-0` and `md:px-6` wins, and it is the wrong one.
 */
const BAND_COLUMN = [
  "pr-5 md:pr-6",
  "hairline-l pl-5 md:pl-6 md:pr-6",
  "pr-5 md:hairline-l md:pl-6 md:pr-6",
  "hairline-l pl-5 md:pl-6",
] as const;

const BAND_REVEAL = ["reveal-1", "reveal-2", "reveal-3", "reveal-4"] as const;

export function StatBand({ children }: { children: ReactNode }) {
  return (
    <section className="hairline-t hairline-b grid grid-cols-2 gap-y-9 py-10 md:grid-cols-4">
      {Children.map(children, (child, index) => (
        <div
          className={clsx(
            "reveal",
            BAND_REVEAL[index % BAND_REVEAL.length],
            BAND_COLUMN[index % BAND_COLUMN.length],
          )}
        >
          {child}
        </div>
      ))}
    </section>
  );
}

/**
 * A single headline number. `hint` carries the denominator or the definition — a figure
 * without its denominator is a number, not a measurement.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: ReactNode;
  tone?: "default" | "warn" | "fail" | "pass";
}) {
  const toneClass = {
    default: "text-[var(--fg)]",
    pass: "text-[var(--pass)]",
    warn: "text-[var(--warn)]",
    fail: "text-[var(--fail)]",
  }[tone];

  return (
    <div className="flex flex-col gap-2">
      <Overline>{label}</Overline>
      <p className={clsx("figure", toneClass)}>{value}</p>
      {hint ? <p className="text-meta text-[var(--fg-tertiary)]">{hint}</p> : null}
    </div>
  );
}

/**
 * Horizontal quality bar. Solid fill, no gradient; a hairline tick marks the threshold so
 * the bar answers "is this above the line" rather than merely "how big is it".
 */
export function Meter({
  value,
  threshold,
  tone = "accent",
  label,
}: {
  value: number;
  threshold?: number | null;
  tone?: "accent" | "pass" | "warn" | "fail" | "quiet";
  label?: string;
}) {
  const fill = {
    accent: "var(--accent)",
    pass: "var(--pass)",
    warn: "var(--warn)",
    fail: "var(--fail)",
    quiet: "var(--fg-quiet)",
  }[tone];

  const pct = Math.max(0, Math.min(1, value)) * 100;

  return (
    /*
     * The fill is the graphical object that conveys the value, so it is held to 3:1
     * against both the track and the surrounding surface. The track is scaffolding: it
     * marks where 100% sits, and only needs to be perceptible.
     *
     * `aria-hidden` because every caller renders the same figure as adjacent text. A
     * screen reader announcing it twice is noise, not redundancy.
     */
    <div
      aria-hidden
      title={label}
      className="relative h-1 w-full overflow-hidden rounded-full bg-[var(--track)]"
    >
      <div
        className="absolute inset-y-0 left-0 rounded-full"
        style={{ width: `${pct}%`, backgroundColor: fill }}
      />
      {typeof threshold === "number" ? (
        /*
         * The threshold mark, which is the reason this is a meter and not a progress bar: the
         * question is "is this above the line", and a bar without the line only answers "how big".
         *
         * A single hairline was not answering it. Over the unfilled track a 1px rule at
         * `--fg-secondary` is legible, but the moment the value passes the threshold the mark sits
         * on the fill — where it lands around 1.4:1 and effectively vanishes, so the bar stopped
         * showing the line exactly in the case where the reader wants confirmation it was cleared.
         *
         * Two pixels wide with a one-pixel surface-coloured halo fixes both regions: the body
         * carries it against the track, the halo carries it against the fill.
         */
        <div
          className="absolute inset-y-0 w-0.5 bg-[var(--fg-secondary)] outline-1 outline-[var(--surface)]"
          style={{ left: `${Math.max(0, Math.min(1, threshold)) * 100}%` }}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- pills

const STATUS_TONE: Record<ValueStatus, string> = {
  auto_accepted: "pill-pass",
  human_approved: "pill-pass",
  queued_for_review: "pill-warn",
  rejected: "pill-fail",
  candidate: "pill-quiet",
  superseded: "pill-quiet",
};

export function StatusPill({ status }: { status: ValueStatus }) {
  const isAccepted = status === "auto_accepted" || status === "human_approved";
  return (
    <span className={clsx("pill", STATUS_TONE[status])}>
      {isAccepted ? <CheckIcon /> : status === "queued_for_review" ? <AlertIcon /> : null}
      {STATUS_LABEL[status]}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, string> = {
  pass: "pill-pass",
  fail: "pill-fail",
  warn: "pill-warn",
  ambiguous: "pill-quiet",
  skipped: "pill-quiet",
};

export function VerdictPill({ verdict }: { verdict: Verdict }) {
  return (
    <span className={clsx("pill", VERDICT_TONE[verdict])}>
      {verdict === "pass" ? <CheckIcon /> : null}
      {verdict === "fail" || verdict === "warn" ? <AlertIcon /> : null}
      {verdict === "skipped" ? <MinusIcon /> : null}
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

/**
 * Derivation method. Inference is called out because an inferred value may never satisfy a
 * compliance claim, and that distinction has to be visible without opening the row.
 */
export function MethodPill({ method }: { method: DerivationMethod }) {
  const inference =
    method === "part_number_grammar" ||
    method === "family_inference" ||
    method === "statistical_default";
  return (
    <span className={clsx("pill", inference ? "pill-warn" : "pill-quiet")}>
      {METHOD_LABEL[method]}
    </span>
  );
}

export function RequirementPill({ requirement }: { requirement: Requirement }) {
  return (
    <span
      className={clsx(
        "pill",
        requirement === "required" ? "pill-accent" : "pill-quiet",
      )}
    >
      {REQUIREMENT_LABEL[requirement]}
    </span>
  );
}

export function DecisionNote({ reason, detail }: { reason: DecisionReason; detail?: string }) {
  return (
    <span className="text-meta text-[var(--fg-tertiary)]">
      {DECISION_LABEL[reason]}
      {detail ? <span className="text-[var(--fg-quiet)]"> · {detail}</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------- misc

/**
 * One term/description pair.
 *
 * Renders a `div` wrapping `dt` + `dd`, which is the one wrapper HTML permits inside a `dl`.
 * Callers must therefore place these as direct children of the `dl` and put any grid on the
 * `dl` itself — `span` is here so a pair can still opt out of the column rhythm.
 */
export function KeyValue({
  label,
  children,
  mono = false,
  span,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
  /**
   * Column span. `"full"` is the responsive case, for a `dl` that is two columns on a phone and four
   * on a tablet.
   *
   * It exists because a fixed `span={4}` is a trap on such a grid: an item spanning more columns than
   * the template declares does not clamp, it makes the grid generate implicit auto-sized columns —
   * so the row that was meant to run full width instead widens the whole grid past its container.
   */
  span?: 2 | 3 | 4 | "full";
}) {
  const spanClass =
    span === 2
      ? "col-span-2"
      : span === 3
        ? "col-span-3"
        : span === 4
          ? "col-span-4"
          : span === "full"
            ? "col-span-2 sm:col-span-4"
            : undefined;

  return (
    <div className={clsx("flex flex-col gap-1", spanClass)}>
      <dt className="overline">{label}</dt>
      {/*
        `break-words`. Half the values that land here are hashes, document ids and model names —
        unbreakable tokens with no space to wrap at — and at 360px one of them is enough to push the
        whole page sideways.
      */}
      <dd
        className={clsx(
          "text-sm break-words",
          mono ? "mono text-[var(--fg-secondary)]" : "text-[var(--fg)]",
        )}
      >
        {children}
      </dd>
    </div>
  );
}

/**
 * Verbatim extract from a source document.
 *
 * `blockquote` rather than `q`: this is a block-level quotation and carries no quotation
 * marks, which is what `q` would insert. Monospace because the exact characters are the
 * point — a reviewer is comparing this string against the page.
 */
export function Quote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <blockquote
      className={clsx(
        "mono border-l-2 border-[var(--hairline-accent)] bg-[var(--surface-sunken)]",
        // A datasheet quote can be a long unbroken run of part numbers and slashes. Wrapping it
        // mid-token is not ideal for character-by-character comparison; pushing the page sideways at
        // 360px is worse.
        "py-1.5 pr-2 pl-2.5 break-words text-[var(--fg-secondary)]",
        className,
      )}
    >
      {children}
    </blockquote>
  );
}



/**
 * Loading placeholder.
 *
 * Dimensions must match the content that replaces it, otherwise the swap causes a layout
 * shift, which is worse than showing nothing. A gradient sweep is the usual shimmer, but
 * this system has no gradients, so it pulses opacity instead — calmer, and it animates a
 * composited property.
 */
export function Skeleton({
  className,
  rounded = "md",
}: {
  className?: string;
  rounded?: "xs" | "sm" | "md" | "lg" | "full";
}) {
  const radius = {
    xs: "rounded-xs",
    sm: "rounded-sm",
    md: "rounded-md",
    lg: "rounded-lg",
    full: "rounded-full",
  }[rounded];

  return (
    <div
      aria-hidden
      className={clsx(
        "animate-pulse bg-[var(--surface-inset)] motion-reduce:animate-none",
        radius,
        className,
      )}
    />
  );
}
