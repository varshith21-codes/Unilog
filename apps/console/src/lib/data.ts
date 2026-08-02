/**
 * Data access layer. This is the seam.
 *
 * Today it reads a JSON fixture produced by `scripts/export_console_fixture.py`, which
 * drives the real Python pipeline and serialises the result. There is no HTTP API yet —
 * the blueprint puts one at `apps/api` (FastAPI) and it has not been built.
 *
 * When that lands, only the body of `loadDataset` changes: swap the file read for a
 * `fetch`, keep the return type, and every screen keeps working. Nothing above this module
 * knows where the data came from, and no component imports the fixture directly.
 *
 * Server-only. Reading from the filesystem here keeps a ~1 MB payload out of the client
 * bundle entirely; screens pass down only the slices they render.
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";

import type {
  AttributeSpec,
  AttributeValue,
  ConsoleDataset,
  Gap,
  ParsedPage,
  SkuBundle,
} from "./types";

const FIXTURE_PATH = path.join(process.cwd(), "src", "data", "fixture.json");

/** Memoised per request. Replace the body with a `fetch` when `apps/api` exists. */
export const loadDataset = cache(async (): Promise<ConsoleDataset> => {
  const raw = await readFile(FIXTURE_PATH, "utf8");
  return JSON.parse(raw) as ConsoleDataset;
});

export async function listSkus(): Promise<SkuBundle[]> {
  const { skus } = await loadDataset();
  return skus;
}

export async function getSku(sku: string): Promise<SkuBundle | null> {
  const { skus } = await loadDataset();
  return skus.find((entry) => entry.sku === sku) ?? null;
}

export async function getPages(): Promise<ParsedPage[]> {
  const { pages } = await loadDataset();
  return pages;
}

/**
 * Review order: the SKUs a reviewer should open first.
 *
 * Blocking validation failures outrank everything, then required gaps, then queued values.
 * Sorting by a single composite score would bury a compliance failure behind a pile of
 * missing carton weights, which is exactly the wrong triage.
 */
export function reviewOrder(skus: SkuBundle[]): SkuBundle[] {
  return [...skus].sort((a, b) => {
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
  meanComposite: number;
  channelsReady: number;
  channelsTotal: number;
}

export function portfolioTotals(skus: SkuBundle[]): PortfolioTotals {
  const n = Math.max(skus.length, 1);
  const mean = (pick: (bundle: SkuBundle) => number) =>
    skus.reduce((sum, bundle) => sum + pick(bundle), 0) / n;

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
    meanComposite: mean((s) => {
      const q = s.certificate.summary.quality_index;
      const w = q.weights;
      return (
        q.completeness * (w.completeness ?? 0) +
        q.verifiability * (w.verifiability ?? 0) +
        q.consistency * (w.consistency ?? 0) +
        q.richness * (w.richness ?? 0)
      );
    }),
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
