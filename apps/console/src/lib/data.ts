/**
 * Data access layer. This is the seam.
 *
 * Reads from the FastAPI service at `apps/api`, which serves output persisted by real pipeline
 * runs (`run_pipeline.py --save-session`). No model call happens on a page load: the API reads
 * bundles off disk, so a dashboard render is cheap and returns the same answer twice.
 *
 * Falls back to the checked-in fixture from `scripts/export_console_fixture.py` when the API is
 * unreachable. That fallback is deliberately *labelled* rather than silent — `meta.live` is
 * false and the UI says so. Showing hand-seeded numbers while implying they came from a live
 * model would be the single most dishonest thing this console could do, so the distinction is
 * carried in the data itself rather than left to a comment.
 *
 * Server-only. Fetching here keeps a ~400 KB payload out of the client bundle entirely;
 * screens pass down only the slices they render.
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";

import { composite } from "./types";
import type {
  AttributeSpec,
  AttributeValue,
  ClassDefinition,
  CohortStudy,
  ConsoleDataset,
  DocumentBundle,
  Gap,
  ParsedPage,
  PriceSource,
  RiskPolicyView,
  SkuBundle,
  SourceDocument,
} from "./types";

const FIXTURE_PATH = path.join(process.cwd(), "src", "data", "fixture.json");

/** Override when the API is not on localhost, e.g. in a container or on a deployed host. */
export const API_BASE = process.env.AXIOM_API_URL ?? "http://127.0.0.1:8000";

/** Long enough for a cold Python process, short enough not to hang a page render. */
const FETCH_TIMEOUT_MS = 8000;

/**
 * Memoised per request.
 *
 * `no-store` because review decisions mutate this data — a cached dataset would show a
 * reviewer their own decision failing to take effect.
 */
export const loadDataset = cache(async (): Promise<ConsoleDataset> => {
  try {
    const response = await fetch(`${API_BASE}/api/console/dataset`, {
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new Error(`API returned ${response.status} ${response.statusText}`);
    }
    return (await response.json()) as ConsoleDataset;
  } catch (cause) {
    return loadFixture(cause);
  }
});

async function loadFixture(cause: unknown): Promise<ConsoleDataset> {
  const reason = cause instanceof Error ? cause.message : String(cause);
  const raw = await readFile(FIXTURE_PATH, "utf8").catch(() => null);

  if (raw === null) {
    throw new Error(
      `Could not reach the AXIOM API at ${API_BASE} (${reason}), and no offline fixture ` +
        `exists at src/data/fixture.json. Start the API with ` +
        `"python -m uvicorn apps.api.main:app --port 8000", or generate a fixture with ` +
        `"python scripts/export_console_fixture.py".`,
    );
  }

  const dataset = JSON.parse(raw) as ConsoleDataset;
  return {
    ...dataset,
    meta: {
      ...dataset.meta,
      live: false,
      warnings: [
        `The API at ${API_BASE} is unreachable (${reason}). Showing the offline fixture, ` +
          `whose model responses are hand-seeded rather than produced by a real model call.`,
        ...(dataset.meta.warnings ?? []),
      ],
    },
  };
}

export async function listSkus(): Promise<SkuBundle[]> {
  const { skus } = await loadDataset();
  return skus;
}

export async function getSku(sku: string): Promise<SkuBundle | null> {
  const { skus } = await loadDataset();
  return skus.find((entry) => entry.sku === sku) ?? null;
}

// ------------------------------------------------------------------ resolving the maps
//
// Documents and class definitions are stored once and referenced by key, so every screen that
// needs them goes through these. Each returns null rather than throwing: a bundle whose class
// abstained legitimately has no definition, and a screen should degrade rather than 500.

export async function getDocumentBundle(bundle: SkuBundle): Promise<DocumentBundle | null> {
  const { documents } = await loadDataset();
  return documents[bundle.document_id] ?? null;
}

export async function getDocument(bundle: SkuBundle): Promise<SourceDocument | null> {
  return (await getDocumentBundle(bundle))?.document ?? null;
}

export async function getPages(bundle: SkuBundle): Promise<ParsedPage[]> {
  return (await getDocumentBundle(bundle))?.pages ?? [];
}

export async function getClassDefinition(bundle: SkuBundle): Promise<ClassDefinition | null> {
  const { class_definitions } = await loadDataset();
  return bundle.class_code ? (class_definitions[bundle.class_code] ?? null) : null;
}

/** Every distinct source document in the catalog, for the overview's provenance panel. */
export async function listDocuments(): Promise<DocumentBundle[]> {
  const { documents } = await loadDataset();
  return Object.values(documents).sort((a, b) =>
    a.document.document_id.localeCompare(b.document.document_id),
  );
}

/**
 * The acceptance policy *with* its risk–coverage curve, for the initial server render.
 *
 * The dataset carries only the summary of the policy its values were decided under. The curve
 * behind it is a separate computation over the calibration set, so it is fetched separately and
 * degrades to a curve-less summary if the API is unavailable — the dial then renders an
 * explanation instead of an empty chart.
 */
export const loadPolicy = cache(async (epsilon: number): Promise<RiskPolicyView> => {
  const { policy } = await loadDataset();
  const fallback: RiskPolicyView = { ...policy, epsilon, curve: [] };

  try {
    const response = await fetch(`${API_BASE}/api/policy?epsilon=${epsilon}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) return fallback;
    return (await response.json()) as RiskPolicyView;
  } catch {
    return fallback;
  }
});

/**
 * The before/after quality cohort.
 *
 * Absent by default, because it needs an item master to compare against and most installations
 * will not have supplied one. That is reported as `available: false` with a reason rather than as
 * an error: "nobody has run this yet" and "this is broken" are different states and the page says
 * which one it is.
 */
export const loadCohort = cache(async (): Promise<CohortStudy> => {
  const unavailable = (reason: string): CohortStudy => ({
    available: false,
    reason,
    schema_version: null,
    treatment_skus: 0,
    control_skus: 0,
    trustworthy: false,
    control_drift: null,
    drifted_dimensions: [],
    lift: { completeness: 0, verifiability: 0, consistency: 0, composite: 0 },
    field_presence: { before: 0, after: 0 },
    treatment_before: { completeness: 0, verifiability: 0, consistency: 0, composite: 0 },
    treatment_after: { completeness: 0, verifiability: 0, consistency: 0, composite: 0 },
    notes: [],
    members: [],
  });

  try {
    const response = await fetch(`${API_BASE}/api/cohort`, {
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      return unavailable(`the API returned ${response.status} ${response.statusText}`);
    }
    return (await response.json()) as CohortStudy;
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    return unavailable(
      `the AXIOM API at ${API_BASE} is unreachable (${reason}), and the cohort study is served ` +
        `from it rather than checked into the console bundle.`,
    );
  }
});

/** Unresolved cross-source conflicts on a SKU. Zero when no L4 run has been saved for it. */
export function unresolvedConflicts(bundle: SkuBundle): number {
  return bundle.cross_source?.unresolved ?? 0;
}

// ------------------------------------------------------------------ variant series
//
// One ordering table on one datasheet becomes N orderable products. That is the claim variant
// explosion makes, and it invites an obvious and correct suspicion: did you generate five products,
// or did you copy one product five times?
//
// The answer is already in the bundles, so it is derived here rather than served as a new artifact.
// Each variant's values carry the provenance that settles it: some were read from that variant's own
// row of the table, some were inherited from the shared series specification, and some were withheld
// because the source states them only for another size. Counting those three is what turns "five
// SKUs" into evidence about how they differ.

/** One product in a series, with the provenance breakdown that shows how it differs from its siblings. */
export interface VariantMember {
  bundle: SkuBundle;
  /**
   * True for the SKU the series was extracted from.
   *
   * Marked rather than promoted. It is special in how extraction happened, not in the catalogue,
   * and ordering it first would imply a hierarchy the data does not have.
   */
  isReference: boolean;
  /** Values read from this variant's own row of the ordering table, cited to an exact cell. */
  fromOwnRow: number;
  /** Values inherited from the shared series specification, keeping their original citation. */
  inherited: number;
  /**
   * Attributes the source states only for a different size.
   *
   * Not missing data. Recording it as inapplicable is what stops a reviewer chasing a supplier for
   * a figure that was never meant to exist for their part.
   */
  inapplicable: number;
}

export interface VariantGroup {
  /** SKU of the record the series was exploded from, which is also the grouping key. */
  seriesSku: string;
  /** Sorted by SKU, which for a size-ordered part number is size order. */
  members: VariantMember[];
  /**
   * Whether the reference SKU's own bundle is in the dataset.
   *
   * False when only some children were persisted. The series is still real and still worth showing;
   * the panel says the reference is absent rather than silently listing a partial family.
   */
  referencePresent: boolean;
}

/**
 * This SKU's parent pointer, with absent and null collapsed.
 *
 * The one place the distinction is allowed to exist. The committed offline fixture predates the
 * field, so its bundles have no `parent_sku` at all — and `undefined !== null` is true, which would
 * silently classify every SKU in that fixture as a variant of itself and render a wall of
 * one-member series. Normalised once here rather than guarded at each of the three read sites.
 */
function parentOf(bundle: SkuBundle): string | null {
  return bundle.record.parent_sku ?? null;
}

function variantMember(bundle: SkuBundle, seriesSku: string): VariantMember {
  return {
    bundle,
    isReference: bundle.sku === seriesSku,
    // `table_extraction` is set only by `variants._from_cell`, so this counts cells rather than
    // guessing from prose.
    fromOwnRow: bundle.values.filter((value) => value.method === "table_extraction").length,
    // `derived_from` is written only by `variants._inherited`. It is a general field on the Python
    // model, so if a second writer ever appears this count needs narrowing.
    inherited: bundle.values.filter((value) => value.derived_from !== null).length,
    // Likewise `accept_as_not_applicable` is produced only by the withheld-note branch of `explode`.
    inapplicable: bundle.gaps.filter(
      (gap) => gap.recommended_action === "accept_as_not_applicable",
    ).length,
  };
}

/**
 * Group the catalogue into variant series.
 *
 * A group qualifies when **any** member declares a `parent_sku`, which is the only positive signal
 * that explosion produced it. Grouping on size alone would be wrong in both directions: two
 * unrelated standalone SKUs never share a key, but a single surviving child of a reference that was
 * never persisted is a one-member group that is still a genuine series.
 *
 * Returns an empty array for a catalogue of standalone products, which is what the two committed
 * bundles are.
 */
export function variantGroups(skus: SkuBundle[]): VariantGroup[] {
  const grouped = new Map<string, SkuBundle[]>();
  for (const bundle of skus) {
    const key = parentOf(bundle) ?? bundle.sku;
    const members = grouped.get(key);
    if (members) members.push(bundle);
    else grouped.set(key, [bundle]);
  }

  return [...grouped.entries()]
    .filter(([, members]) => members.some((bundle) => parentOf(bundle) !== null))
    .map(([seriesSku, members]) => ({
      seriesSku,
      members: [...members]
        .sort((a, b) => a.sku.localeCompare(b.sku))
        .map((bundle) => variantMember(bundle, seriesSku)),
      referencePresent: members.some((bundle) => bundle.sku === seriesSku),
    }))
    .sort((a, b) => a.seriesSku.localeCompare(b.seriesSku));
}

/** The series a SKU belongs to, or null when it is a standalone product. */
export function variantGroupFor(bundle: SkuBundle, skus: SkuBundle[]): VariantGroup | null {
  const key = parentOf(bundle) ?? bundle.sku;
  return variantGroups(skus).find((group) => group.seriesSku === key) ?? null;
}

/**
 * Review order: the SKUs a reviewer should open first.
 *
 * Unresolved cross-source conflicts come first, then blocking validation failures, then required
 * gaps, then queued values. Sorting by a single composite score would bury a compliance failure
 * behind a pile of missing carton weights, which is exactly the wrong triage.
 *
 * L4 leads because it is the only finding where the system is holding two contradictory answers and
 * has deliberately refused to choose. Everything below it is one answer the pipeline is unsure
 * about; this is two answers it cannot reconcile, and it blocks publication until a human rules.
 */
export function reviewOrder(skus: SkuBundle[]): SkuBundle[] {
  return [...skus].sort((a, b) => {
    const conflicts = unresolvedConflicts(b) - unresolvedConflicts(a);
    if (conflicts !== 0) return conflicts;

    if (a.validation.failures !== b.validation.failures) {
      return b.validation.failures - a.validation.failures;
    }
    if (a.metrics.gaps_required !== b.metrics.gaps_required) {
      return b.metrics.gaps_required - a.metrics.gaps_required;
    }
    if (a.metrics.values_needing_review !== b.metrics.values_needing_review) {
      return b.metrics.values_needing_review - a.metrics.values_needing_review;
    }
    return a.sku.localeCompare(b.sku);
  });
}

/** Portfolio totals for the overview scoreboard. */
export interface PortfolioTotals {
  skuCount: number;
  valuesTotal: number;
  valuesPublishable: number;
  needingReview: number;
  gapsRequired: number;
  gapsTotal: number;
  blockingFailures: number;
  warnings: number;
  meanCompleteness: number;
  meanVerifiability: number;
  meanConsistency: number;
  /**
   * Mean over the SKUs that actually have a richness score, or null when none do.
   *
   * Averaged across the scored subset rather than the whole portfolio. Treating an unmeasured
   * richness as a zero in the denominator would report a catalogue as asset-poor when the truth is
   * that nobody generated copy for it.
   */
  meanRichness: number | null;
  meanComposite: number;
  channelsReady: number;
  channelsTotal: number;
}

/**
 * Token spend across the catalog, and what it implies at scale.
 *
 * `priced` is false when any SKU in the sample could not be costed, because a mean taken over
 * a subset would be quietly optimistic — the unpriced SKUs are the ones that escalated to an
 * expensive tier, so dropping them biases the average downward.
 */
export interface CostTotals {
  priced: boolean;
  skusPriced: number;
  skusTotal: number;
  totalUsd: number;
  meanPerSkuUsd: number;
  meanPerValueUsd: number;
  calls: number;
  escalations: number;
  inputTokens: number;
  outputTokens: number;
  byTierUsd: Record<string, number>;
  source: PriceSource | null;
  /** Straight-line extrapolation. Honest only if this sample's documents are typical. */
  project(skuCount: number): number;
}

export function costTotals(skus: SkuBundle[]): CostTotals {
  const costed = skus.filter((bundle) => bundle.cost?.priced && bundle.cost.cost_usd !== null);

  const byTierUsd: Record<string, number> = {};
  let totalUsd = 0;
  let values = 0;
  let calls = 0;
  let escalations = 0;
  let inputTokens = 0;
  let outputTokens = 0;

  for (const bundle of costed) {
    const cost = bundle.cost!;
    totalUsd += cost.cost_usd ?? 0;
    values += bundle.metrics.values_total;
    calls += cost.calls;
    escalations += cost.escalations;
    inputTokens += cost.input_tokens;
    outputTokens += cost.output_tokens;
    for (const [tier, usd] of Object.entries(cost.cost_by_tier ?? {})) {
      byTierUsd[tier] = (byTierUsd[tier] ?? 0) + usd;
    }
  }

  const meanPerSkuUsd = costed.length > 0 ? totalUsd / costed.length : 0;

  return {
    priced: costed.length > 0 && costed.length === skus.length,
    skusPriced: costed.length,
    skusTotal: skus.length,
    totalUsd,
    meanPerSkuUsd,
    meanPerValueUsd: values > 0 ? totalUsd / values : 0,
    calls,
    escalations,
    inputTokens,
    outputTokens,
    byTierUsd,
    source: costed[0]?.cost?.price_source ?? null,
    project: (skuCount: number) => meanPerSkuUsd * skuCount,
  };
}

export function portfolioTotals(skus: SkuBundle[]): PortfolioTotals {
  const n = Math.max(skus.length, 1);
  const mean = (pick: (bundle: SkuBundle) => number) =>
    skus.reduce((sum, bundle) => sum + pick(bundle), 0) / n;
  const meanOf = (values: number[]) =>
    values.length > 0 ? values.reduce((sum, v) => sum + v, 0) / values.length : null;

  return {
    skuCount: skus.length,
    valuesTotal: skus.reduce((sum, s) => sum + s.metrics.values_total, 0),
    valuesPublishable: skus.reduce((sum, s) => sum + s.metrics.values_publishable, 0),
    needingReview: skus.reduce((sum, s) => sum + s.metrics.values_needing_review, 0),
    gapsRequired: skus.reduce((sum, s) => sum + s.metrics.gaps_required, 0),
    gapsTotal: skus.reduce((sum, s) => sum + s.metrics.gaps_total, 0),
    blockingFailures: skus.reduce((sum, s) => sum + s.validation.failures, 0),
    warnings: skus.reduce((sum, s) => sum + s.validation.warnings, 0),
    meanCompleteness: mean((s) => s.certificate.summary.quality_index.completeness),
    meanVerifiability: mean((s) => s.certificate.summary.quality_index.verifiability),
    meanConsistency: mean((s) => s.certificate.summary.quality_index.consistency),
    meanRichness: meanOf(
      skus
        .map((s) => s.certificate.summary.quality_index.richness)
        .filter((value): value is number => value !== null),
    ),
    // The pipeline's own composite, not a third reimplementation of the weighting.
    meanComposite: mean((s) => composite(s.certificate.summary.quality_index)),
    channelsReady: skus.reduce(
      (sum, s) => sum + s.channels.filter((c) => c.published).length,
      0,
    ),
    channelsTotal: skus.reduce((sum, s) => sum + s.channels.length, 0),
  };
}

/**
 * One row per attribute the class defines, joined to its value or its gap.
 *
 * The review workspace is driven by the *schema*, not by what extraction happened to
 * return. An attribute with no value and no gap is itself a finding, and a value-first
 * list would hide it.
 */
export interface AttributeRow {
  spec: AttributeSpec;
  value: AttributeValue | null;
  gap: Gap | null;
}

export function attributeRows(
  specs: AttributeSpec[],
  values: AttributeValue[],
  gaps: Gap[],
): AttributeRow[] {
  const valueByCode = new Map(values.map((v) => [v.attribute_code, v]));
  const gapByCode = new Map(gaps.map((g) => [g.attribute_code, g]));

  const requirementRank: Record<AttributeSpec["requirement"], number> = {
    required: 0,
    recommended: 1,
    optional: 2,
  };

  return specs
    .map((spec) => ({
      spec,
      value: valueByCode.get(spec.code) ?? null,
      gap: gapByCode.get(spec.code) ?? null,
    }))
    .sort((a, b) => {
      const byRequirement =
        requirementRank[a.spec.requirement] - requirementRank[b.spec.requirement];
      if (byRequirement !== 0) return byRequirement;
      if (b.spec.weight !== a.spec.weight) return b.spec.weight - a.spec.weight;
      return a.spec.name.localeCompare(b.spec.name);
    });
}

/**
 * Attributes a reviewer must act on, in the order they should be worked.
 *
 * Values needing confirmation come before gaps. They are different jobs — verify against
 * evidence versus obtain from a source — and interleaving them makes a reviewer switch mode
 * on every row. Requirement and weight order is preserved within each group.
 */
export function reviewRows(rows: AttributeRow[]): AttributeRow[] {
  const open = rows.filter(
    (row) =>
      (row.value !== null && row.value.status !== "auto_accepted") ||
      (row.gap !== null && row.gap.is_required),
  );
  return [
    ...open.filter((row) => row.value !== null),
    ...open.filter((row) => row.value === null),
  ];
}
