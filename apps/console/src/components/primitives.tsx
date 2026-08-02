/**
 * Shared primitives.
 *
 * Every visual value resolves to a token defined in `globals.css`. Nothing here carries a
 * raw hex, px or shadow. Interaction states are defined on the `.btn` / `.pill` component
 * classes rather than repeated per usage.
 */

import clsx from "clsx";
import type { ReactNode } from "react";

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
 * Section heading. One weight step and one colour step below the page title — hierarchy
 * through weight and colour rather than another size, which keeps a dense screen calm.
 */
export function SectionHeading({
  title,
  detail,
  action,
  className,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-baseline justify-between gap-4", className)}>
      <div className="min-w-0">
        <h2 className="text-lg font-medium">{title}</h2>
        {detail ? (
          <p className="mt-1 text-sm text-[var(--fg-tertiary)]">{detail}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-body font-medium">{title}</p>
      {detail ? (
        <p className="max-w-[42ch] text-sm text-[var(--fg-tertiary)]">{detail}</p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------- figures

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
        <div
          className="absolute inset-y-0 w-px bg-[var(--fg-secondary)]"
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
  span?: 2 | 3 | 4;
}) {
  const spanClass =
    span === 2 ? "col-span-2" : span === 3 ? "col-span-3" : span === 4 ? "col-span-4" : undefined;

  return (
    <div className={clsx("flex flex-col gap-1", spanClass)}>
      <dt className="overline">{label}</dt>
      <dd className={clsx("text-sm", mono ? "mono text-[var(--fg-secondary)]" : "text-[var(--fg)]")}>
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
        "py-1.5 pr-2 pl-2.5 text-[var(--fg-secondary)]",
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
