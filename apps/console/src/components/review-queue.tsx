"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  EmptyState,
  Panel,
  StatusPill,
} from "@/components/primitives";
import { count, percent } from "@/lib/format";
import { paginate } from "@/lib/paginate";
import type { ValueStatus } from "@/lib/types";

export const RESOLVE_PAGE_SIZE = 25;

export type ReviewQueueState = "open" | "unclassified" | "fully-accepted";

export type ReviewQueueRow =
  | {
      code: string;
      name: string;
      complianceClaim: boolean;
      kind: "value";
      primary: string;
      secondary: string;
      status: ValueStatus;
    }
  | {
      code: string;
      name: string;
      complianceClaim: boolean;
      kind: "gap";
      primary: string;
      secondary: string | null;
      status: null;
    };

/** Scalar-only projection of a SKU. Source documents, evidence, and full bundles stay server-side. */
export interface ReviewQueueItem {
  sku: string;
  href: string;
  brand: string | null;
  mpn: string | null;
  normalizedMpn: string | null;
  gtin: string | null;
  supplierId: string | null;
  classCode: string | null;
  className: string | null;
  state: ReviewQueueState;
  /**
   * Why there is no class, when there is none. Null whenever `state` is not `unclassified`.
   *
   * Carried on the item rather than derived here because the two kinds need different work from a
   * reviewer, and the queue used to tell both of them to go and write a class definition — which is
   * wrong advice for a SKU whose problem is that two classes already matched.
   */
  abstention: {
    kind: "no-candidate" | "ambiguous";
    candidates: { code: string; score: number; path_text: string }[];
  } | null;
  completeness: number | null;
  validationFailures: number;
  validationWarnings: number;
  sourceConflictCount: number;
  crossSourceApplicable: boolean;
  corroboratedCount: number;
  openItemCount: number;
  valueOpenCount: number;
  belowThresholdCount: number;
  requiredGapCount: number;
  publicationRiskRank: number;
  rows: ReviewQueueRow[];
}

type WorkFilter =
  | "all"
  | "open"
  | "conflicts"
  | "blocking"
  | "below-threshold"
  | "required-gaps"
  | "unclassified"
  | "needs-adjudication"
  | "fully-accepted";

type SortOption = "risk" | "sku-asc" | "sku-desc" | "most-open" | "lowest-completeness";

const ALL_CLASSES = "__all_classes__";
const UNCLASSIFIED = "__unclassified__";
const ALL_BRANDS = "__all_brands__";
const UNBRANDED = "__unbranded__";

const WORK_FILTER_OPTIONS: ReadonlyArray<{ value: WorkFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "open", label: "Open work" },
  { value: "conflicts", label: "Source conflicts" },
  { value: "blocking", label: "Blocking validation" },
  { value: "below-threshold", label: "Below threshold" },
  { value: "required-gaps", label: "Required gaps" },
  { value: "unclassified", label: "No class matched" },
  { value: "needs-adjudication", label: "Needs adjudication" },
  { value: "fully-accepted", label: "Fully accepted" },
];

const WORK_FILTER_PREDICATES: Record<WorkFilter, (item: ReviewQueueItem) => boolean> = {
  all: () => true,
  open: (item) =>
    item.state === "unclassified" ||
    item.sourceConflictCount > 0 ||
    item.validationFailures > 0 ||
    item.openItemCount > 0,
  conflicts: (item) => item.sourceConflictCount > 0,
  blocking: (item) => item.validationFailures > 0,
  "below-threshold": (item) => item.belowThresholdCount > 0,
  "required-gaps": (item) => item.requiredGapCount > 0,
  // "No class matched" is now the narrow reading — a SKU nothing in the schema fits. The SKUs that
  // matched two classes and could not be separated have their own filter, because the remedy is a
  // decision rather than a class definition and mixing them made the queue unactionable.
  unclassified: (item) =>
    item.state === "unclassified" && item.abstention?.kind !== "ambiguous",
  "needs-adjudication": (item) => item.abstention?.kind === "ambiguous",
  "fully-accepted": (item) => item.state === "fully-accepted",
};

const SORT_OPTIONS: ReadonlyArray<{ value: SortOption; label: string }> = [
  { value: "risk", label: "Publication risk" },
  { value: "sku-asc", label: "SKU A–Z" },
  { value: "sku-desc", label: "SKU Z–A" },
  { value: "most-open", label: "Most open items" },
  { value: "lowest-completeness", label: "Lowest completeness" },
];

function normalize(value: string | null): string {
  return value?.trim().toLocaleLowerCase() ?? "";
}

function compareSku(a: ReviewQueueItem, b: ReviewQueueItem): number {
  const base = a.sku.localeCompare(b.sku, "en", { numeric: true, sensitivity: "base" });
  if (base !== 0) return base;
  return a.sku.localeCompare(b.sku, "en", { numeric: true, sensitivity: "variant" });
}

function compareRisk(a: ReviewQueueItem, b: ReviewQueueItem): number {
  return a.publicationRiskRank - b.publicationRiskRank || compareSku(a, b);
}

const SORT_COMPARATORS: Record<
  SortOption,
  (a: ReviewQueueItem, b: ReviewQueueItem) => number
> = {
  risk: compareRisk,
  "sku-asc": compareSku,
  "sku-desc": (a, b) => compareSku(b, a),
  "most-open": (a, b) => b.openItemCount - a.openItemCount || compareRisk(a, b),
  "lowest-completeness": (a, b) => {
    // Unmeasured classification is not zero completeness. Keep it after measured records and expose
    // it through the dedicated Unclassified filter rather than inventing a score for it.
    if (a.completeness === null && b.completeness !== null) return 1;
    if (a.completeness !== null && b.completeness === null) return -1;
    if (a.completeness !== null && b.completeness !== null) {
      const completeness = a.completeness - b.completeness;
      if (completeness !== 0) return completeness;
    }
    return compareRisk(a, b);
  },
};

function matchesSearch(item: ReviewQueueItem, query: string): boolean {
  if (query === "") return true;
  return [
    item.sku,
    item.brand,
    item.mpn,
    item.normalizedMpn,
    item.gtin,
    item.supplierId,
    item.classCode,
    item.className,
  ].some((value) => normalize(value).includes(query));
}

/**
 * A SKU that reached two classes and no decision.
 *
 * Distinct from the "no class could be established" state on purpose. That one says the schema has
 * no definition matching the description and the remedy is to write one. This one says the opposite:
 * retrieval found classes, it found more than one, and neither dominated the other by enough to be
 * taken without judgement. Adding a thirty-third class would not resolve a tie between two that
 * already exist.
 *
 * The contenders and their scores are shown because they are the reviewer's actual work: the leading
 * candidate is usually right, and seeing "Lighting 0.216 against Tapes & Sealants 0.175" is enough
 * to settle a Satco Tape Light in a second. Presenting the margin rather than hiding it is also
 * honest about how close the call was.
 */
function UnclassifiedAmbiguous({
  candidates,
}: {
  candidates: { code: string; score: number; path_text: string }[];
}) {
  const [leader, runnerUp] = candidates;
  const margin =
    leader && runnerUp && leader.score > 0 ? runnerUp.score / leader.score : null;

  return (
    <div className="px-6 py-5">
      <p className="text-sm font-medium">Two classes matched; neither dominated</p>
      <p className="mt-2 max-w-[68ch] text-sm text-[var(--fg-secondary)]">
        Retrieval reached {candidates.length} candidate classes and refused to choose between them,
        because the margin was too small to call without judgement
        {margin !== null ? ` — the runner-up scored ${percent(margin)} of the leader` : ""}. Nothing
        has been extracted or scored, because the attributes to ask for are defined per class. This
        is not a missing class definition: it needs adjudication, either from a reviewer here or from
        a model on the enrichment path.
      </p>
      <ul className="mt-4 space-y-1">
        {candidates.map((candidate, index) => (
          <li key={candidate.code} className="flex flex-wrap items-baseline gap-x-3 text-sm">
            <span className="mono text-[var(--fg-primary)]">{candidate.code}</span>
            <span className="text-[var(--fg-tertiary)]">{candidate.path_text}</span>
            <span className="mono text-meta text-[var(--fg-quiet)]">
              {candidate.score.toFixed(4)}
              {index === 0 ? " · leading" : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReviewCard({ item }: { item: ReviewQueueItem }) {
  return (
    <Panel className="overflow-hidden p-0">
      <div className="hairline-b flex flex-wrap items-center justify-between gap-4 bg-[var(--surface-sunken)] px-6 py-4">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 className="text-lg font-medium">
            <Link
              href={item.href}
              className="rounded-xs transition-colors duration-[var(--duration-fast)] hover:text-[var(--accent)]"
            >
              {item.sku}
            </Link>
          </h2>
          {item.state === "unclassified" ? (
            <p className="text-meta text-[var(--fg-tertiary)]">
              {item.abstention?.kind === "ambiguous" ? (
                <>
                  <span className="text-[var(--warn)]">needs adjudication</span> ·{" "}
                  {item.abstention.candidates.length} classes matched
                </>
              ) : (
                <>
                  <span className="text-[var(--warn)]">not classified</span> · no attributes
                  evaluated
                </>
              )}
            </p>
          ) : (
            <p className="text-meta text-[var(--fg-tertiary)]">
              {percent(item.completeness ?? 0)} complete ·{" "}
              {item.validationFailures > 0 ? (
                <span className="text-[var(--fail)]">{item.validationFailures} blocking</span>
              ) : item.validationWarnings > 0 ? (
                <span className="text-[var(--warn)]">
                  {item.validationWarnings} warning{item.validationWarnings === 1 ? "" : "s"}
                </span>
              ) : (
                <span className="text-[var(--pass)]">checks clean</span>
              )}
            </p>
          )}
          {item.sourceConflictCount > 0 ? (
            <span className="pill pill-fail">
              <AlertIcon />
              {item.sourceConflictCount} source conflict{item.sourceConflictCount === 1 ? "" : "s"}
            </span>
          ) : item.crossSourceApplicable ? (
            <span className="pill pill-pass">
              <CheckIcon />
              {item.corroboratedCount} corroborated
            </span>
          ) : null}
        </div>

        <Link href={item.href} className="btn btn-quiet h-7">
          Resolve SKU
          <ArrowIcon />
        </Link>
      </div>

      {item.state === "unclassified" && item.abstention?.kind === "ambiguous" ? (
        <UnclassifiedAmbiguous candidates={item.abstention.candidates} />
      ) : item.state === "unclassified" ? (
        <EmptyState
          kind="unmeasured"
          title="No class could be established"
          detail="Retrieval found no class in the schema that this description matches, and it abstains rather than guessing. Nothing has been extracted, scored or gapped for this record: the attributes to ask for are defined per class, and there is no class yet. Closing this means adding a class definition under schema/classes/, not loosening the classifier."
        />
      ) : item.state === "fully-accepted" ? (
        <EmptyState
          title="Fully accepted"
          detail="Every attribute this class requires cleared the threshold with verified evidence."
        />
      ) : (
        <ul>
          {item.rows.map((row) => (
            <li
              key={row.code}
              className="grid-row group hairline-b grid grid-cols-[1fr_auto] items-center gap-x-5 gap-y-1.5 px-6 py-3.5 last:border-b-0 sm:grid-cols-[16rem_1fr_auto]"
            >
              <div className="flex min-w-0 items-center gap-2">
                <Link
                  href={item.href}
                  className="truncate rounded-xs text-sm font-medium transition-colors duration-[var(--duration-fast)] group-hover:text-[var(--accent)] hover:text-[var(--accent)]"
                >
                  {row.name}
                </Link>
                {row.complianceClaim ? (
                  <span className="pill pill-accent shrink-0">Claim</span>
                ) : null}
              </div>

              <p className="col-span-2 min-w-0 truncate text-meta text-[var(--fg-tertiary)] sm:col-span-1">
                {row.primary}
                {row.secondary ? (
                  <span className="text-[var(--fg-quiet)]"> · {row.secondary}</span>
                ) : null}
              </p>

              <div className="row-start-1 justify-self-end sm:row-start-auto">
                {row.kind === "value" ? (
                  <StatusPill status={row.status} />
                ) : (
                  <span className="pill pill-warn">
                    <AlertIcon />
                    Gap
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function ReviewQueue({
  items,
  initialPage,
}: {
  items: ReviewQueueItem[];
  initialPage: number;
}) {
  const [search, setSearch] = useState("");
  const [workFilter, setWorkFilter] = useState<WorkFilter>("all");
  const [classFilter, setClassFilter] = useState(ALL_CLASSES);
  const [brandFilter, setBrandFilter] = useState(ALL_BRANDS);
  const [sort, setSort] = useState<SortOption>("risk");
  const [currentPage, setCurrentPage] = useState(initialPage);
  const queueTopRef = useRef<HTMLDivElement>(null);
  const resultStatusRef = useRef<HTMLParagraphElement>(null);

  const classOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const item of items) {
      if (item.classCode) {
        options.set(
          item.classCode,
          item.className ? `${item.className} · ${item.classCode}` : item.classCode,
        );
      }
    }
    return [...options]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, "en", { sensitivity: "base" }));
  }, [items]);

  const brandOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const item of items) {
      const value = normalize(item.brand);
      if (value && item.brand) options.set(value, item.brand.trim());
    }
    return [...options]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, "en", { sensitivity: "base" }));
  }, [items]);

  const hasUnclassified = items.some((item) => item.classCode === null);
  const hasUnbranded = items.some((item) => normalize(item.brand) === "");
  const normalizedSearch = normalize(search);

  const matching = useMemo(
    () =>
      items
        .filter(
          (item) =>
            matchesSearch(item, normalizedSearch) &&
            WORK_FILTER_PREDICATES[workFilter](item) &&
            (classFilter === ALL_CLASSES ||
              (classFilter === UNCLASSIFIED
                ? item.classCode === null
                : item.classCode === classFilter)) &&
            (brandFilter === ALL_BRANDS ||
              (brandFilter === UNBRANDED
                ? normalize(item.brand) === ""
                : normalize(item.brand) === brandFilter)),
        )
        .sort(SORT_COMPARATORS[sort]),
    [brandFilter, classFilter, items, normalizedSearch, sort, workFilter],
  );

  const page = paginate(matching, currentPage, RESOLVE_PAGE_SIZE);
  const defaultsActive =
    normalizedSearch === "" &&
    workFilter === "all" &&
    classFilter === ALL_CLASSES &&
    brandFilter === ALL_BRANDS &&
    sort === "risk";

  function resetPage() {
    setCurrentPage(1);
  }

  function clearControls() {
    setSearch("");
    setWorkFilter("all");
    setClassFilter(ALL_CLASSES);
    setBrandFilter(ALL_BRANDS);
    setSort("risk");
    resetPage();
  }

  function goToPage(nextPage: number) {
    setCurrentPage(nextPage);
    window.requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      resultStatusRef.current?.focus({ preventScroll: true });
      queueTopRef.current?.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
    });
  }

  return (
    <div
      ref={queueTopRef}
      className="mt-[var(--spacing-section)] scroll-mt-[var(--header-height)]"
    >
      <Panel as="section" label="Resolve queue controls" className="p-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          <label className="flex min-w-0 flex-col gap-2 sm:col-span-2 xl:col-span-2">
            <span className="overline">Search catalogue</span>
            <input
              className="review-queue-control"
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
              placeholder="SKU, MPN, GTIN, supplier, class…"
              autoComplete="off"
            />
          </label>

          <label className="flex min-w-0 flex-col gap-2">
            <span className="overline">Work status</span>
            <select
              className="review-queue-control"
              value={workFilter}
              onChange={(event) => {
                setWorkFilter(event.target.value as WorkFilter);
                resetPage();
              }}
            >
              {WORK_FILTER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-2">
            <span className="overline">Class</span>
            <select
              className="review-queue-control"
              value={classFilter}
              onChange={(event) => {
                setClassFilter(event.target.value);
                resetPage();
              }}
            >
              <option value={ALL_CLASSES}>All classes</option>
              {hasUnclassified ? <option value={UNCLASSIFIED}>Unclassified</option> : null}
              {classOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-2">
            <span className="overline">Brand</span>
            <select
              className="review-queue-control"
              value={brandFilter}
              onChange={(event) => {
                setBrandFilter(event.target.value);
                resetPage();
              }}
            >
              <option value={ALL_BRANDS}>All brands</option>
              {hasUnbranded ? <option value={UNBRANDED}>Unbranded</option> : null}
              {brandOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-2">
            <span className="overline">Sort</span>
            <select
              className="review-queue-control"
              value={sort}
              onChange={(event) => {
                setSort(event.target.value as SortOption);
                resetPage();
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="hairline-t mt-4 flex flex-wrap items-center justify-between gap-3 pt-3">
          <div>
            <p
              ref={resultStatusRef}
              role="status"
              aria-live="polite"
              aria-atomic="true"
              tabIndex={-1}
              className="text-sm text-[var(--fg-secondary)]"
            >
              <span className="tabular-nums font-medium text-[var(--fg)]">
                {count(matching.length)}
              </span>{" "}
              matching SKU{matching.length === 1 ? "" : "s"}
              {matching.length > 0 ? (
                <>
                  {" · showing "}
                  <span className="tabular-nums">
                    {count(page.from)}–{count(page.to)} · page {count(page.page)} of{" "}
                    {count(page.pageCount)}
                  </span>
                </>
              ) : null}
            </p>
            <p className="text-meta text-[var(--fg-quiet)]">
              Search, filters, and sort apply to the full catalogue before paging.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-quiet"
            disabled={defaultsActive}
            onClick={clearControls}
          >
            Clear controls
          </button>
        </div>
      </Panel>

      {matching.length === 0 ? (
        <Panel className="mt-5">
          <EmptyState
            title="No SKUs match these controls"
            detail="The catalogue is loaded, but the current search and filters exclude every record. Clear the controls to return to the publication-risk queue."
            action={
              <button type="button" className="btn btn-quiet" onClick={clearControls}>
                Reset queue
              </button>
            }
          />
        </Panel>
      ) : (
        <>
          <div className="mt-5 flex flex-col gap-6">
            {page.items.map((item) => (
              <ReviewCard key={item.sku} item={item} />
            ))}
          </div>

          <nav
            aria-label="Resolve queue pages"
            className="mt-5 flex flex-wrap items-center justify-between gap-4 text-meta text-[var(--fg-tertiary)]"
          >
            <p>
              Showing{" "}
              <span className="tabular-nums text-[var(--fg-secondary)]">
                {count(page.from)}–{count(page.to)}
              </span>{" "}
              of{" "}
              <span className="tabular-nums text-[var(--fg-secondary)]">
                {count(page.total)}
              </span>{" "}
              SKUs · page <span className="tabular-nums">{count(page.page)}</span> of{" "}
              <span className="tabular-nums">{count(page.pageCount)}</span>
            </p>

            <div className="action-row">
              <button
                type="button"
                className="btn btn-quiet h-7"
                disabled={!page.hasPrevious}
                onClick={() => goToPage(page.page - 1)}
              >
                <ArrowIcon className="rotate-180" />
                Previous
              </button>
              <button
                type="button"
                className="btn btn-quiet h-7"
                disabled={!page.hasNext}
                onClick={() => goToPage(page.page + 1)}
              >
                Next
                <ArrowIcon />
              </button>
            </div>
          </nav>
        </>
      )}
    </div>
  );
}
