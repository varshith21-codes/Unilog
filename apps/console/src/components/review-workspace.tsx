"use client";

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EvidenceTextFallback, EvidenceViewer } from "@/components/evidence-viewer";
import {
  AlertIcon,
  AnchorIcon,
  CheckIcon,
  DecisionNote,
  KeyValue,
  Meter,
  MethodPill,
  Overline,
  Quote,
  RequirementPill,
  StatusPill,
  VerdictPill,
} from "@/components/primitives";
import {
  ACTION_LABEL,
  GAP_REASON_LABEL,
  LAYER_LABEL,
  canonical,
  featureLabel,
  percent,
  score as fmtScore,
  shortHash,
  tableRef,
} from "@/lib/format";
import { submitDecision } from "@/lib/actions";
import type { AttributeRow } from "@/lib/data";
import type {
  BoundingBox,
  EvidenceSpan,
  ParsedPage,
  ReviewAction,
  ReviewOutcome,
  SourceDocument,
} from "@/lib/types";

type Filter = "review" | "all" | "gaps";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "review", label: "Needs review" },
  { id: "gaps", label: "Gaps" },
  { id: "all", label: "All attributes" },
];

export interface ReviewWorkspaceProps {
  sku: string;
  rows: AttributeRow[];
  page: ParsedPage | null;
  /** Null when the bundle references a document the dataset no longer carries. */
  document: SourceDocument | null;
  threshold: number | null;
  /** False when the page is rendering the offline fixture, where decisions cannot persist. */
  live: boolean;
}

export function ReviewWorkspace({
  sku,
  rows,
  page,
  document: sourceDocument,
  threshold,
  live,
}: ReviewWorkspaceProps) {
  const [filter, setFilter] = useState<Filter>("review");
  const [announcement, setAnnouncement] = useState("");
  const listRef = useRef<HTMLUListElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  /*
   * Decisions are recorded, not staged.
   *
   * An earlier version accumulated them locally behind a Commit button, because there was no
   * API to send them to. There is now, and batching was the wrong model anyway: each decision
   * updates the per-attribute prior, which moves the acceptance threshold, and a reviewer needs
   * to see that happen while the value is still in front of them. `outcomes` keeps what the
   * server returned so the prior movement can be shown.
   */
  const [outcomes, setOutcomes] = useState<Record<string, ReviewOutcome>>({});
  const [inFlight, setInFlight] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const decide = useCallback(
    async (code: string, action: ReviewAction, name: string, correctedValue?: string) => {
      if (!live) {
        setError(
          "This page is rendering the offline fixture, so a decision has nowhere to go. " +
            "Start the API and reload to review against real pipeline output.",
        );
        return;
      }

      setError(null);
      setInFlight((current) => new Set(current).add(code));
      setAnnouncement(`Recording ${action} for ${name}`);

      const result = await submitDecision({
        sku,
        attributeCode: code,
        action,
        correctedValue,
      });

      setInFlight((current) => {
        const next = new Set(current);
        next.delete(code);
        return next;
      });

      if (!result.ok) {
        setError(result.error);
        setAnnouncement(`${name} could not be recorded`);
        return;
      }

      const { outcome } = result.data;
      setOutcomes((current) => ({ ...current, [code]: outcome }));

      const movement = outcome.prior_after - outcome.prior_before;
      const direction = movement >= 0 ? "raised" : "lowered";
      setAnnouncement(
        `${name} recorded as ${action}. Attribute reliability ${direction} to ` +
          `${outcome.prior_after.toFixed(3)}.`,
      );
    },
    [live, sku],
  );

  const visible = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "gaps") return rows.filter((row) => row.gap !== null);

    const open = rows.filter(
      (row) =>
        (row.value !== null && row.value.status !== "auto_accepted") ||
        (row.gap !== null && row.gap.is_required),
    );

    // Values before gaps. These are different jobs: a value needs verifying against its
    // evidence, a gap needs obtaining from somewhere. Grouping them keeps the reviewer in
    // one mode at a time, and it means the first item always has something to look at.
    // Requirement and weight order is preserved inside each group.
    return [
      ...open.filter((row) => row.value !== null),
      ...open.filter((row) => row.value === null),
    ];
  }, [rows, filter]);

  const [selected, setSelected] = useState<string | null>(
    () => visible[0]?.spec.code ?? rows[0]?.spec.code ?? null,
  );

  // Keep the selection inside the visible set when the filter changes.
  useEffect(() => {
    if (visible.length === 0) {
      setSelected(null);
      return;
    }
    setSelected((current) => {
      if (current && visible.some((row) => row.spec.code === current)) return current;
      return visible[0]?.spec.code ?? null;
    });
  }, [visible]);

  const activeIndex = visible.findIndex((row) => row.spec.code === selected);
  const active = activeIndex >= 0 ? visible[activeIndex] : undefined;

  const move = useCallback(
    (delta: number) => {
      if (visible.length === 0) return;
      const from = activeIndex < 0 ? 0 : activeIndex;
      const next = Math.max(0, Math.min(visible.length - 1, from + delta));
      const code = visible[next]?.spec.code;
      if (!code) return;
      setSelected(code);
      listRef.current
        ?.querySelector(`[data-code="${CSS.escape(code)}"]`)
        ?.scrollIntoView({ block: "nearest" });
    },
    [activeIndex, visible],
  );

  /*
   * Keyboard-first triage, scoped to the workspace.
   *
   * A document-level listener would fire `a` and `x` while focus was on the theme toggle or
   * a nav link, recording decisions the user never asked for. Requiring focus to be inside
   * the workspace subtree keeps the shortcuts local without forcing the reviewer to click
   * into a specific control first. Arrow keys are handled by the listbox itself, so they
   * are deliberately absent here — intercepting them globally would break page scrolling.
   */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }

      const root = rootRef.current;
      const focused = document.activeElement;
      const insideWorkspace =
        root !== null && (focused === document.body || root.contains(focused));
      if (!insideWorkspace) return;

      switch (event.key) {
        case "j":
          event.preventDefault();
          move(1);
          break;
        case "k":
          event.preventDefault();
          move(-1);
          break;
        case "a":
          if (active?.value) {
            event.preventDefault();
            void decide(active.spec.code, "accept", active.spec.name);
          }
          break;
        case "x":
          if (active?.value) {
            event.preventDefault();
            void decide(active.spec.code, "reject", active.spec.name);
          }
          break;
        case "e":
          if (active?.value) {
            event.preventDefault();
            const replacement = window.prompt(
              `Corrected value for ${active.spec.name}`,
              active.value.value_display ?? active.value.value_raw ?? "",
            );
            if (replacement?.trim()) {
              void decide(active.spec.code, "correct", active.spec.name, replacement.trim());
            }
          }
          break;
        default:
          break;
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, move, decide]);

  const recorded = Object.values(outcomes);

  // Every other span on the page, drawn faintly so the highlighted one has context.
  const contextBoxes = useMemo<BoundingBox[]>(() => {
    const activeSpanId = active?.value?.evidence[0]?.span_id;
    const boxes: BoundingBox[] = [];
    for (const row of rows) {
      for (const span of row.value?.evidence ?? []) {
        if (span.bbox && span.span_id !== activeSpanId) boxes.push(span.bbox);
      }
    }
    return boxes;
  }, [rows, active]);

  const activeSpan = active?.value?.evidence[0] ?? null;

  return (
    <div ref={rootRef} className="grid gap-6 lg:grid-cols-12">
      {/* Staged decisions are announced here; the visible confirmation is the bottom bar. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {/* ------------------------------------------------------------ attribute list */}
      <div className="lg:col-span-5">
        <div className="flex items-center justify-between gap-3">
          {/*
            A group of toggle buttons, not a tablist. There are no tabpanels and no roving
            tabindex here, so `role="tablist"` would promise a keyboard contract this does
            not implement. `aria-pressed` describes what these actually are.
          */}
          <div
            role="group"
            aria-label="Filter attributes"
            className="flex gap-0.5 rounded-lg bg-[var(--surface-inset)] p-0.5"
          >
            {FILTERS.map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={filter === option.id}
                onClick={() => setFilter(option.id)}
                className={clsx(
                  "rounded-md px-2.5 py-1 text-meta font-medium",
                  "transition-colors duration-[var(--duration-fast)]",
                  filter === option.id
                    ? "bg-[var(--surface-raised)] text-[var(--fg)] shadow-xs"
                    : "text-[var(--fg-tertiary)] hover:text-[var(--fg)]",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <p className="text-meta text-[var(--fg-quiet)] tabular-nums">
            {visible.length} of {rows.length}
          </p>
        </div>

        {/*
          Single-select listbox using the `aria-activedescendant` pattern.

          The `ul` owns focus and key handling; options are `li role="option"` directly, so
          ownership is `listbox > option` as the spec requires. Options are deliberately not
          individually focusable — a focusable child inside an activedescendant listbox sets
          up two competing focus models, and 17 attributes would become 18 tab stops.
        */}
        <ul
          ref={listRef}
          role="listbox"
          aria-label={`Attributes for ${sku}`}
          tabIndex={0}
          aria-activedescendant={selected ? `row-${selected}` : undefined}
          onKeyDown={(event) => {
            switch (event.key) {
              case "ArrowDown":
                event.preventDefault();
                move(1);
                break;
              case "ArrowUp":
                event.preventDefault();
                move(-1);
                break;
              case "Home":
                event.preventDefault();
                move(-visible.length);
                break;
              case "End":
                event.preventDefault();
                move(visible.length);
                break;
              default:
                break;
            }
          }}
          className="panel mt-3 max-h-[min(38rem,calc(100dvh-16rem))] overflow-y-auto p-1"
        >
          {visible.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-[var(--fg-tertiary)]">
              Nothing in this view.
            </li>
          ) : null}

          {visible.map((row) => {
            const isActive = row.spec.code === selected;
            const outcome = outcomes[row.spec.code];
            const busy = inFlight.has(row.spec.code);
            const blocking =
              row.value?.validations.some(
                (v) => v.verdict === "fail" && v.severity === "error",
              ) ?? false;

            return (
              <li
                  key={row.spec.code}
                  id={`row-${row.spec.code}`}
                  data-code={row.spec.code}
                  role="option"
                  aria-selected={isActive}
                  onClick={() => setSelected(row.spec.code)}
                  className={clsx(
                    "grid w-full cursor-pointer grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1",
                    "rounded-lg px-3.5 py-2.5 text-left",
                    "transition-colors duration-[var(--duration-instant)]",
                    isActive
                      ? "bg-[var(--accent-quiet)] shadow-[inset_2px_0_0_0_var(--accent)]"
                      : "hover:bg-[var(--surface-hover)]",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium">{row.spec.name}</span>
                    {row.spec.compliance_claim ? (
                      <span
                        className="pill pill-accent shrink-0"
                        title="Compliance claim: never satisfiable by inference"
                      >
                        Claim
                      </span>
                    ) : null}
                  </span>

                  <span className="flex shrink-0 items-center gap-1.5">
                    {busy ? (
                      <span className="pill pill-quiet">Saving…</span>
                    ) : outcome ? (
                      <StatusPill status={outcome.status} />
                    ) : blocking ? (
                      <span className="pill pill-fail">
                        <AlertIcon />
                        Blocked
                      </span>
                    ) : row.value ? (
                      <StatusPill status={row.value.status} />
                    ) : row.gap ? (
                      <span
                        className={clsx("pill", row.gap.is_required ? "pill-warn" : "pill-quiet")}
                      >
                        Gap
                      </span>
                    ) : (
                      <span className="pill pill-quiet">Not attempted</span>
                    )}
                  </span>

                  <span className="col-span-2 flex items-baseline gap-2 text-meta">
                    <span
                      className={clsx(
                        "truncate",
                        row.value ? "text-[var(--fg-secondary)]" : "text-[var(--fg-quiet)]",
                      )}
                    >
                      {row.value
                        ? (outcome?.after ??
                          row.value.value_display ??
                          canonical(row.value.value_canonical) ??
                          row.value.value_raw)
                        : row.gap
                          ? GAP_REASON_LABEL[row.gap.reason]
                          : "No candidate produced"}
                    </span>
                    {row.value ? (
                      <span className="ml-auto shrink-0 tabular-nums text-[var(--fg-quiet)]">
                        {fmtScore(row.value.score)}
                      </span>
                    ) : null}
                  </span>
              </li>
            );
          })}
        </ul>

        <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-[var(--fg-quiet)]">
          <span>
            <Kbd>J</Kbd> <Kbd>K</Kbd> move
          </span>
          <span>
            <Kbd>A</Kbd> accept
          </span>
          <span>
            <Kbd>X</Kbd> reject
          </span>
          <span>
            <Kbd>E</Kbd> correct
          </span>
        </p>
      </div>

      {/* ------------------------------------------------------------ evidence pane */}
      <div className="lg:col-span-7">
        {active ? (
          <div key={active.spec.code} className="animate-fade-in flex flex-col gap-5">
            {/* ---- attribute header ---- */}
            <div className="panel p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <RequirementPill requirement={active.spec.requirement} />
                    <span className="pill pill-quiet">{active.spec.datatype}</span>
                    {active.spec.canonical_unit ? (
                      <span className="pill pill-quiet">
                        {active.spec.canonical_unit}
                      </span>
                    ) : null}
                    <span className="text-meta text-[var(--fg-quiet)]">
                      weight ×{active.spec.weight}
                    </span>
                  </div>
                  <h2 className="mt-3 text-xl font-medium tracking-[var(--tracking-heading)]">
                    {active.spec.name}
                  </h2>
                  <p className="mono mt-1 text-[var(--fg-quiet)]">{active.spec.code}</p>
                </div>

                {active.value ? (
                  <div className="text-right">
                    <Overline>Value</Overline>
                    <p className="mt-1.5 text-xl font-medium">
                      {active.value.value_display ?? canonical(active.value.value_canonical)}
                    </p>
                    {active.value.value_raw &&
                    active.value.value_raw !== active.value.value_display ? (
                      <p className="mono mt-1 text-[var(--fg-quiet)]">
                        raw: {active.value.value_raw}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>

              <p className="mt-5 max-w-[68ch] text-sm text-[var(--fg-secondary)]">
                {active.spec.description}
              </p>
            </div>

            {active.value ? (
              <ValueDetail
                row={active}
                page={page}
                document={sourceDocument}
                threshold={threshold}
                contextBoxes={contextBoxes}
                span={activeSpan}
                outcome={outcomes[active.spec.code]}
                busy={inFlight.has(active.spec.code)}
                live={live}
                onDecide={(action, correctedValue) =>
                  decide(active.spec.code, action, active.spec.name, correctedValue)
                }
              />
            ) : (
              <GapDetail row={active} />
            )}
          </div>
        ) : (
          <div className="panel p-14 text-center">
            <p className="text-body font-medium">Nothing selected</p>
            <p className="mt-1 text-sm text-[var(--fg-tertiary)]">
              Choose an attribute to see its evidence.
            </p>
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------ recorded / errors */}
      {error !== null || recorded.length > 0 ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center p-3 sm:p-4">
          <div
            className="animate-rise panel-raised pointer-events-auto flex max-w-full flex-wrap
                       items-center justify-center gap-x-4 gap-y-2 px-4 py-3 shadow-lg"
          >
            {error !== null ? (
              <>
                <span className="pill pill-fail">
                  <AlertIcon />
                  Not recorded
                </span>
                <p className="max-w-[60ch] text-sm text-[var(--fg-secondary)]">{error}</p>
                <button type="button" className="btn btn-bare h-7" onClick={() => setError(null)}>
                  Dismiss
                </button>
              </>
            ) : (
              <>
                <p className="text-sm">
                  <span className="font-medium tabular-nums">{recorded.length}</span>{" "}
                  {recorded.length === 1 ? "decision" : "decisions"} recorded
                </p>
                <span className="pill pill-pass">
                  <CheckIcon />
                  Persisted
                </span>
                {/*
                  The reason a reviewer should care that this persisted: it moved the priors,
                  which moves what gets auto-accepted next run. Showing the aggregate movement
                  is what makes the flywheel legible instead of asserted.
                */}
                <PriorMovement outcomes={recorded} />
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- prior movement

function PriorMovement({ outcomes }: { outcomes: ReviewOutcome[] }) {
  const net = outcomes.reduce(
    (sum, outcome) => sum + (outcome.prior_after - outcome.prior_before),
    0,
  );
  const raised = outcomes.filter((o) => o.prior_after >= o.prior_before).length;
  const lowered = outcomes.length - raised;

  return (
    <p className="text-meta text-[var(--fg-quiet)]">
      Priors {net >= 0 ? "up" : "down"}{" "}
      <span className="tabular-nums text-[var(--fg-secondary)]">
        {net >= 0 ? "+" : ""}
        {net.toFixed(3)}
      </span>{" "}
      · {raised} confirmed
      {lowered > 0 ? `, ${lowered} corrected` : ""}
    </p>
  );
}

// ---------------------------------------------------------------- value detail

function ValueDetail({
  row,
  page,
  document: sourceDocument,
  threshold,
  contextBoxes,
  span,
  outcome,
  busy,
  live,
  onDecide,
}: {
  row: AttributeRow;
  page: ParsedPage | null;
  document: SourceDocument | null;
  threshold: number | null;
  contextBoxes: BoundingBox[];
  span: EvidenceSpan | null;
  outcome: ReviewOutcome | undefined;
  busy: boolean;
  live: boolean;
  onDecide: (action: ReviewAction, correctedValue?: string) => void;
}) {
  const value = row.value;
  if (!value) return null;

  const blocking = value.validations.filter(
    (v) => v.verdict === "fail" && v.severity === "error",
  );
  const other = value.validations.filter(
    (v) => !(v.verdict === "fail" && v.severity === "error"),
  );

  const features = Object.entries(value.features).sort(([, a], [, b]) => b - a);

  return (
    <>
      {/* ---- confidence ---- */}
      <div className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Overline>Confidence</Overline>
          <div className="flex items-center gap-2">
            <MethodPill method={value.method} />
            <StatusPill status={outcome?.status ?? value.status} />
          </div>
        </div>

        <div className="mt-5 flex items-baseline gap-3">
          <p className="figure">{fmtScore(value.score)}</p>
          {threshold !== null ? (
            <p className="text-sm text-[var(--fg-tertiary)]">
              threshold {threshold.toFixed(3)}
            </p>
          ) : null}
        </div>

        <div className="mt-4">
          <Meter
            value={value.score}
            threshold={threshold}
            tone={value.status === "auto_accepted" ? "pass" : "warn"}
            label={`Calibrated score ${fmtScore(value.score)}`}
          />
        </div>

        {value.decision ? (
          <p className="mt-3">
            <DecisionNote
              reason={value.decision.reason_code}
              detail={value.decision.detail}
            />
          </p>
        ) : null}

        <details className="group mt-5">
          <summary className="cursor-pointer text-sm text-[var(--fg-tertiary)] transition-colors duration-150 hover:text-[var(--fg)]">
            Signals behind this score
          </summary>
          <dl className="mt-4 flex flex-col gap-2.5">
            {features.map(([key, magnitude]) => (
              <div key={key} className="grid grid-cols-[11rem_1fr_2.75rem] items-center gap-3">
                <dt className="truncate text-meta text-[var(--fg-secondary)]">
                  {featureLabel(key)}
                </dt>
                <dd>
                  <Meter value={magnitude} tone="quiet" label={featureLabel(key)} />
                </dd>
                <dd className="text-right text-meta tabular-nums text-[var(--fg-tertiary)]">
                  {magnitude.toFixed(2)}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      </div>

      {/* ---- evidence ---- */}
      <div className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Overline>Evidence</Overline>
          {span ? (
            <span className={clsx("pill", span.quote_verified ? "pill-pass" : "pill-fail")}>
              {span.quote_verified ? <CheckIcon /> : <AlertIcon />}
              {span.quote_verified ? "Quote verified" : "Unverified"}
              {span.match_score !== null ? ` · ${percent(span.match_score, 0)}` : ""}
            </span>
          ) : null}
        </div>

        {span ? (
          <>
            <Quote className="mt-4">{span.quote}</Quote>

            <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
              <KeyValue label="Document">{span.document_id}</KeyValue>
              <KeyValue label="Page">{span.page ?? "—"}</KeyValue>
              <KeyValue label="Location">
                {tableRef(span.table_ref) ?? "Body text"}
              </KeyValue>
              <KeyValue label="Digest" mono>
                {shortHash(span.document_sha256)}…
              </KeyValue>
            </dl>

            <div className="mt-6">
              <div className="mb-2.5 flex items-center gap-2">
                <AnchorIcon className="text-[var(--fg-quiet)]" />
                <p className="text-meta text-[var(--fg-quiet)]">
                  {sourceDocument?.revision_label ??
                    sourceDocument?.document_id ??
                    span.document_id}
                  {span.page !== null ? ` · page ${span.page}` : ""}
                </p>
              </div>
              {page ? (
                <EvidenceViewer
                  page={page}
                  highlight={span.bbox}
                  context={contextBoxes}
                  /*
                   * Only PDF-parsed sources have a bitmap worth rendering. The span's own hash is
                   * used rather than the document's, because the span is what the citation points
                   * at — if the two ever disagreed, the citation would be the one telling the truth.
                   */
                  pdfSha256={
                    sourceDocument?.parser === "pdfplumber" ? span.document_sha256 : null
                  }
                />
              ) : (
                <EvidenceTextFallback quote={span.quote} />
              )}
            </div>
          </>
        ) : (
          <p className="mt-4 text-sm text-[var(--fg-tertiary)]">
            This value was derived rather than read from a document, so it carries no span.
            Its provenance is the value it was computed from
            {value.derived_from ? `: ${value.derived_from}` : ""}.
          </p>
        )}
      </div>

      {/* ---- validation ---- */}
      <div className="panel p-6">
        <Overline>Validation</Overline>

        {blocking.length === 0 && other.length === 0 ? (
          <p className="mt-4 text-sm text-[var(--fg-tertiary)]">
            No checks were applicable to this value.
          </p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {[...blocking, ...other].map((result) => (
              <li
                key={`${result.layer}-${result.rule_id}`}
                className="flex flex-col gap-2 rounded-lg bg-[var(--surface-sunken)] p-3.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <VerdictPill verdict={result.verdict} />
                  <span className="mono text-[var(--fg-secondary)]">{result.rule_id}</span>
                  <span className="text-meta text-[var(--fg-quiet)]">
                    {result.layer} · {LAYER_LABEL[result.layer]}
                  </span>
                </div>
                <p className="text-sm text-[var(--fg-secondary)]">{result.reason}</p>
                {result.detail ? (
                  <p className="mono text-[var(--fg-quiet)]">{result.detail}</p>
                ) : null}
                {result.counterexample ? (
                  <p className="text-meta text-[var(--fail)]">
                    Counterexample: {result.counterexample}
                  </p>
                ) : null}
                {result.suggested_fix ? (
                  <p className="text-meta text-[var(--fg-secondary)]">
                    Fix: {result.suggested_fix}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ---- provenance + actions ---- */}
      <div className="panel p-6">
        <Overline>Reproducibility</Overline>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <KeyValue label="Model" mono>
            {value.model_id ?? "—"}
          </KeyValue>
          <KeyValue label="Tier">{value.model_tier ?? "—"}</KeyValue>
          <KeyValue label="Prompt" mono>
            {value.prompt_version ?? "—"}
          </KeyValue>
          <KeyValue label="Schema" mono>
            {value.schema_version ?? "—"}
          </KeyValue>
        </dl>

        <div className="hairline-t mt-6 flex flex-wrap items-center gap-2 pt-5">
          <button
            type="button"
            className={clsx("btn", outcome?.action === "accept" ? "btn-primary" : "btn-quiet")}
            onClick={() => onDecide("accept")}
            disabled={busy || !live}
            aria-pressed={outcome?.action === "accept"}
          >
            <CheckIcon />
            {outcome?.action === "accept" ? "Accepted" : "Accept"}
          </button>

          {/* Destructive, so it must not resolve to the same accent fill as Accept. */}
          <button
            type="button"
            className={clsx("btn", outcome?.action === "reject" ? "btn-danger" : "btn-quiet")}
            onClick={() => onDecide("reject")}
            disabled={busy || !live}
            aria-pressed={outcome?.action === "reject"}
          >
            {outcome?.action === "reject" ? "Rejected" : "Reject"}
          </button>

          <button
            type="button"
            className={clsx("btn", outcome?.action === "correct" ? "btn-primary" : "btn-quiet")}
            onClick={() => {
              const replacement = window.prompt(
                `Corrected value for ${row.spec.name}`,
                outcome?.after ?? value.value_display ?? value.value_raw ?? "",
              );
              if (replacement?.trim()) onDecide("correct", replacement.trim());
            }}
            disabled={busy || !live}
            aria-pressed={outcome?.action === "correct"}
          >
            {outcome?.action === "correct" ? "Corrected" : "Correct"}
          </button>

          {busy ? (
            <span className="text-meta text-[var(--fg-quiet)]" role="status">
              Saving…
            </span>
          ) : null}

          {/*
            The prior movement, shown next to the value that caused it. A reviewer who cannot
            see that their decision changed anything has no reason to believe the queue will
            ever get shorter.
          */}
          {outcome ? (
            <p className="ml-auto text-meta text-[var(--fg-quiet)]">
              Reliability for <span className="mono">{row.spec.code}</span>{" "}
              {outcome.prior_after >= outcome.prior_before ? "rose" : "fell"} to{" "}
              <span className="tabular-nums text-[var(--fg-secondary)]">
                {outcome.prior_after.toFixed(3)}
              </span>{" "}
              from {outcome.prior_before.toFixed(3)} over {outcome.sibling_impact}{" "}
              {outcome.sibling_impact === 1 ? "sample" : "samples"}
            </p>
          ) : !live ? (
            <p className="ml-auto text-meta text-[var(--warn)]">
              Offline fixture — start the API to record decisions.
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- gap detail

function GapDetail({ row }: { row: AttributeRow }) {
  const gap = row.gap;

  if (!gap) {
    return (
      <div className="panel p-6">
        <Overline>No candidate</Overline>
        <p className="mt-4 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
          Extraction produced neither a value nor a gap for this attribute. That is itself a
          defect: an attribute the class defines should always resolve to one or the other,
          so the negative result stays auditable.
        </p>
      </div>
    );
  }

  return (
    <div className="panel p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Overline>Gap</Overline>
        <span className={clsx("pill", gap.is_required ? "pill-warn" : "pill-quiet")}>
          {gap.is_required ? "Required" : "Not required"}
        </span>
      </div>

      <p className="mt-4 text-lg font-medium">{GAP_REASON_LABEL[gap.reason]}</p>

      {gap.detail ? (
        <p className="mt-3 max-w-[68ch] text-sm text-[var(--fg-secondary)]">{gap.detail}</p>
      ) : null}

      <dl className="mt-6 flex flex-col gap-5">
        <KeyValue label="Sources searched">
          {gap.sources_searched.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {gap.sources_searched.map((source) => (
                <li key={source} className="mono text-[var(--fg-secondary)]">
                  {source}
                </li>
              ))}
            </ul>
          ) : (
            "None recorded"
          )}
        </KeyValue>

        {gap.recommended_action ? (
          <KeyValue label="Recommended action">
            {ACTION_LABEL[gap.recommended_action]}
          </KeyValue>
        ) : null}

        {gap.revenue_exposure_usd !== null ? (
          <KeyValue label="Revenue exposure">
            ${gap.revenue_exposure_usd.toLocaleString("en-US")}
          </KeyValue>
        ) : null}
      </dl>

      {row.spec.compliance_claim ? (
        <div className="mt-6 flex gap-3 rounded-lg bg-[var(--accent-quiet)] p-3.5">
          <AlertIcon className="mt-0.5 shrink-0 text-[var(--accent)]" />
          <p className="text-sm text-[var(--fg-secondary)]">
            This attribute is a compliance claim, so it may never be satisfied by inference.
            Closing this gap requires a certificate or declaration naming this part number.
          </p>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- kbd

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="inline-flex h-[1.125rem] min-w-[1.125rem] items-center justify-center rounded-xs
                 border border-[var(--hairline-strong)] bg-[var(--surface-raised)] px-1
                 font-sans text-micro font-medium text-[var(--fg-secondary)]"
    >
      {children}
    </kbd>
  );
}
