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
import type { AttributeRow } from "@/lib/data";
import type { BoundingBox, EvidenceSpan, ParsedPage, SourceDocument } from "@/lib/types";

type Staged = "approved" | "rejected";
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
  document: SourceDocument;
  threshold: number | null;
}

export function ReviewWorkspace({
  sku,
  rows,
  page,
  document: sourceDocument,
  threshold,
}: ReviewWorkspaceProps) {
  const [filter, setFilter] = useState<Filter>("review");
  const [staged, setStaged] = useState<Record<string, Staged>>({});
  const [announcement, setAnnouncement] = useState("");
  const listRef = useRef<HTMLUListElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

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

  const stage = useCallback(
    (code: string, decision: Staged, name: string) => {
      setStaged((current) => {
        const next = { ...current };
        if (next[code] === decision) {
          delete next[code];
          setAnnouncement(`${name} decision cleared`);
        } else {
          next[code] = decision;
          setAnnouncement(`${name} staged as ${decision}`);
        }
        return next;
      });
    },
    [],
  );

  /*
   * Keyboard-first triage, scoped to the workspace.
   *
   * A document-level listener would fire `a` and `x` while focus was on the theme toggle or
   * a nav link, staging decisions the user never asked for. Requiring focus to be inside
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
            stage(active.spec.code, "approved", active.spec.name);
          }
          break;
        case "x":
          if (active?.value) {
            event.preventDefault();
            stage(active.spec.code, "rejected", active.spec.name);
          }
          break;
        default:
          break;
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, move, stage]);

  const stagedCount = Object.keys(staged).length;

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
            const decision = staged[row.spec.code];
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
                    {decision === "approved" ? (
                      <span className="pill pill-pass">
                        <CheckIcon />
                        Staged
                      </span>
                    ) : decision === "rejected" ? (
                      <span className="pill pill-fail">Rejected</span>
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
                        ? (row.value.value_display ??
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
            <Kbd>A</Kbd> approve
          </span>
          <span>
            <Kbd>X</Kbd> reject
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
                staged={staged[active.spec.code]}
                onStage={(decision) => stage(active.spec.code, decision, active.spec.name)}
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

      {/* ------------------------------------------------------------ staged bar */}
      {stagedCount > 0 ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center p-3 sm:p-4">
          <div
            className="animate-rise panel-raised pointer-events-auto flex max-w-full flex-wrap
                       items-center justify-center gap-x-4 gap-y-2 px-4 py-3 shadow-lg"
          >
            <p className="text-sm">
              <span className="font-medium tabular-nums">{stagedCount}</span>{" "}
              {stagedCount === 1 ? "decision" : "decisions"} staged
            </p>
            <span className="pill pill-warn">Not persisted</span>
            <button
              type="button"
              className="btn btn-bare h-7"
              onClick={() => {
                setStaged({});
                setAnnouncement("All staged decisions discarded");
              }}
            >
              Discard
            </button>
            {/*
              Disabled via `aria-disabled` rather than the `disabled` attribute so the
              control stays in the tab order and can explain itself. A button a keyboard
              user cannot reach cannot tell them why it is unavailable.
            */}
            <button
              type="button"
              className="btn btn-primary h-7"
              aria-disabled
              aria-describedby="commit-blocked"
              onClick={(event) => event.preventDefault()}
            >
              Commit
            </button>
            <span id="commit-blocked" className="sr-only">
              Committing requires the API, which is not yet built.
            </span>
          </div>
        </div>
      ) : null}
    </div>
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
  staged,
  onStage,
}: {
  row: AttributeRow;
  page: ParsedPage | null;
  document: SourceDocument;
  threshold: number | null;
  contextBoxes: BoundingBox[];
  span: EvidenceSpan | null;
  staged: Staged | undefined;
  onStage: (decision: Staged) => void;
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
            <StatusPill status={staged === "approved" ? "human_approved" : value.status} />
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
                  {sourceDocument.revision_label ?? sourceDocument.document_id}
                  {span.page !== null ? ` · page ${span.page}` : ""}
                </p>
              </div>
              {page ? (
                <EvidenceViewer
                  page={page}
                  highlight={span.bbox}
                  context={contextBoxes}
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
            className={clsx("btn", staged === "approved" ? "btn-primary" : "btn-quiet")}
            onClick={() => onStage("approved")}
            aria-pressed={staged === "approved"}
          >
            <CheckIcon />
            {staged === "approved" ? "Approved" : "Approve"}
          </button>
          {/* Destructive, so it must not resolve to the same accent fill as Approve. */}
          <button
            type="button"
            className={clsx("btn", staged === "rejected" ? "btn-danger" : "btn-quiet")}
            onClick={() => onStage("rejected")}
            aria-pressed={staged === "rejected"}
          >
            {staged === "rejected" ? "Rejected" : "Reject"}
          </button>
          <p className="ml-auto text-meta text-[var(--fg-quiet)]">
            Decisions stage locally until <span className="mono">apps/api</span> exists.
          </p>
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
