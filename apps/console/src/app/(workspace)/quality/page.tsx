import type { Metadata } from "next";

import { CohortPanel } from "@/components/cohort-panel";
import {
  AlertIcon,
  EmptyState,
  PageHeader,
  Panel,
  Section,
  SectionHeading,
  Stat,
} from "@/components/primitives";
import { RiskDial } from "@/components/risk-dial";
import { costTotals, listSkus, loadCohort, loadDataset, loadPolicy } from "@/lib/data";
import { count, dateOnly, percent, tokens, usd } from "@/lib/format";

export const metadata: Metadata = {
  title: "Intelligence",
  description: "Measured catalog impact, acceptance policy, and enrichment economics.",
};

export default async function IntelligencePage() {
  const [study, dataset, skus] = await Promise.all([
    loadCohort(),
    loadDataset(),
    listSkus(),
  ]);
  const [policyView] = await Promise.all([loadPolicy(dataset.policy.epsilon)]);
  const cost = costTotals(skus);
  const costed = skus.filter((bundle) => bundle.cost?.priced && bundle.cost.cost_usd !== null);
  const tierRows = Object.entries(cost.byTierUsd)
    .map(([tier, tierUsd]) => ({
      tier,
      usd: tierUsd,
      calls: costed.reduce((sum, bundle) => sum + (bundle.cost?.calls_by_tier[tier] ?? 0), 0),
      tokens: costed.reduce(
        (sum, bundle) =>
          sum +
          (bundle.cost?.input_by_tier[tier] ?? 0) +
          (bundle.cost?.output_by_tier[tier] ?? 0),
        0,
      ),
      share: cost.totalUsd > 0 ? tierUsd / cost.totalUsd : 0,
    }))
    .sort((a, b) => b.usd - a.usd);

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Intelligence / measured decisions"
        title="Impact, policy, and unit economics"
        detail="Separate what enrichment changed, what confidence policy permits, and what the recorded model cascade cost. Unavailable measurements remain explicitly unavailable."
        meta={
          <>
            <span className={`pill ${study.available ? "pill-pass" : "pill-quiet"}`}>
              Cohort {study.available ? "measured" : "not measured"}
            </span>
            <span className={`pill ${dataset.policy.achievable ? "pill-pass" : "pill-warn"}`}>
              Policy {dataset.policy.achievable ? "validated" : "not validated"}
            </span>
            <span className={`pill ${cost.priced ? "pill-pass" : "pill-warn"}`}>
              Cost {cost.priced ? "fully priced" : "partial or unavailable"}
            </span>
          </>
        }
      />

      <Section divider={false} labelledBy="impact-heading">
        <SectionHeading
          id="impact-heading"
          level="primary"
          title="Cohort impact"
          detail="The same quality code scores treatment before and after; an untouched control arm tests whether the measurement itself drifted."
        />
        <div className="mt-7">
          {study.available ? (
            <CohortPanel study={study} />
          ) : (
            <Panel className="overflow-hidden">
              <EmptyState
                kind="unmeasured"
                title="No cohort study has been run"
                detail={
                  study.reason ??
                  "The study needs both an ERP item-master baseline and enriched output before it can make an impact claim."
                }
              />
              <div className="hairline-t bg-[var(--surface-sunken)] px-6 py-5">
                <p className="text-meta text-[var(--fg-quiet)]">Create the baseline, then compare:</p>
                <pre className="mono scroll-x mt-3 text-meta leading-relaxed text-[var(--fg-secondary)]">{`python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv \\
    --supplier milwaukee --map "WT/EA (lb)=each_weight" --out data/ingest

python scripts/run_cohort.py --write`}</pre>
              </div>
            </Panel>
          )}
        </div>
      </Section>

      <Section rhythm="lg" labelledBy="policy-heading">
        <SectionHeading
          id="policy-heading"
          level="primary"
          title="Acceptance policy"
          detail="Adjust the tolerated error budget to inspect its calibrated coverage tradeoff. This is a what-if; it does not reclassify published values."
          action={
            <span className={`pill ${dataset.policy.achievable ? "pill-pass" : "pill-warn"}`}>
              {dataset.policy.achievable ? "Calibration available" : "Operating point unavailable"}
            </span>
          }
        />
        <div className="mt-7">
          <RiskDial initial={policyView} />
        </div>
        <div className="mt-5 flex gap-3 border-l-2 border-[var(--warn)] bg-[var(--warn-quiet)] p-4">
          <AlertIcon className="mt-0.5 shrink-0 text-[var(--warn)]" />
          <p className="max-w-[88ch] text-sm text-[var(--fg-secondary)]">
            The current threshold comes from <strong className="font-medium text-[var(--fg)]">{dataset.meta.policy_source}</strong> using a {dataset.meta.calibrator.replace(/-/g, " ")} calibrator. Coverage must be earned from real review decisions before this policy can support a production guarantee.
          </p>
        </div>
      </Section>

      <Section rhythm="lg" labelledBy="cost-heading">
        <SectionHeading
          id="cost-heading"
          level="primary"
          title="Enrichment economics"
          detail="Recorded Bedrock usage and straight-line catalog projections. Only runs with authoritative pricing enter the calculation."
          action={
            cost.source ? (
              <span className={`pill ${cost.source.stale ? "pill-warn" : "pill-quiet"}`}>
                {cost.source.stale ? "Price table stale" : `Prices · ${cost.source.region}`}
              </span>
            ) : null
          }
        />

        {cost.skusPriced === 0 ? (
          <Panel className="mt-7">
            <EmptyState
              kind="unmeasured"
              title="No cost was recorded"
              detail={
                <>
                  This is not a zero-dollar result. Fetch the price table with <span className="mono">python scripts/fetch_bedrock_prices.py --write</span> and run the pipeline again.
                </>
              }
            />
          </Panel>
        ) : (
          <div className="mt-7 grid gap-6 xl:grid-cols-12">
            <Panel raised className="p-6 xl:col-span-5">
              <Stat
                label="Mean per SKU"
                value={usd(cost.meanPerSkuUsd)}
                hint={`${usd(cost.meanPerValueUsd)} per extracted value across ${cost.skusPriced} priced SKU${cost.skusPriced === 1 ? "" : "s"}`}
              />
              <dl className="hairline-t mt-6 grid grid-cols-3 gap-4 pt-5">
                {[10_000, 100_000, 500_000].map((scale) => (
                  <div key={scale}>
                    <dt className="overline">{count(scale)} SKUs</dt>
                    <dd className="mt-2 text-lg tabular-nums">{usd(cost.project(scale))}</dd>
                  </div>
                ))}
              </dl>
              <dl className="hairline-t mt-6 grid grid-cols-2 gap-5 pt-5">
                <div>
                  <dt className="overline">Model calls</dt>
                  <dd className="mono mt-2 text-[var(--fg-secondary)]">{count(cost.calls)}</dd>
                </div>
                <div>
                  <dt className="overline">Escalations</dt>
                  <dd className="mono mt-2 text-[var(--fg-secondary)]">{count(cost.escalations)}</dd>
                </div>
                <div>
                  <dt className="overline">Input tokens</dt>
                  <dd className="mono mt-2 text-[var(--fg-secondary)]">{tokens(cost.inputTokens)}</dd>
                </div>
                <div>
                  <dt className="overline">Output tokens</dt>
                  <dd className="mono mt-2 text-[var(--fg-secondary)]">{tokens(cost.outputTokens)}</dd>
                </div>
              </dl>
              <p className="mt-6 text-meta text-[var(--fg-quiet)]">
                Projections assume these documents are representative. Longer documents and model escalations change the unit cost.
              </p>
            </Panel>

            <Panel className="overflow-hidden xl:col-span-7">
              <div className="hairline-b px-5 py-4">
                <p className="overline">Spend by model tier</p>
              </div>
              <div className="scroll-x" tabIndex={0} role="region" aria-label="Model tier cost breakdown">
                <table className="w-full min-w-[34rem] text-sm">
                  <thead className="table-head">
                    <tr>
                      <th scope="col" className="px-5 py-2.5 text-left">Tier</th>
                      <th scope="col" className="px-5 py-2.5 text-right">Calls</th>
                      <th scope="col" className="px-5 py-2.5 text-right">Tokens</th>
                      <th scope="col" className="px-5 py-2.5 text-right">Cost</th>
                      <th scope="col" className="px-5 py-2.5 text-right">Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tierRows.map((row) => (
                      <tr key={row.tier} className="grid-row hairline-b last:border-b-0">
                        <th scope="row" className="px-5 py-3 text-left font-medium">{row.tier}</th>
                        <td className="px-5 py-3 text-right tabular-nums">{count(row.calls)}</td>
                        <td className="px-5 py-3 text-right tabular-nums">{tokens(row.tokens)}</td>
                        <td className="px-5 py-3 text-right tabular-nums">{usd(row.usd)}</td>
                        <td className="px-5 py-3 text-right tabular-nums text-[var(--fg-tertiary)]">{percent(row.share, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {cost.source ? (
                <p className="hairline-t px-5 py-4 text-meta text-[var(--fg-quiet)]">
                  On-demand rates for {cost.source.priced_models} models, fetched {dateOnly(cost.source.fetched_at)} from {cost.source.source}. Unpriced models remain unmeasured rather than estimated.
                </p>
              ) : null}
            </Panel>
          </div>
        )}

        {!cost.priced && cost.skusPriced > 0 ? (
          <p className="mt-4 flex gap-2 text-meta text-[var(--warn)]">
            <AlertIcon className="mt-0.5 shrink-0" />
            {cost.skusTotal - cost.skusPriced} of {cost.skusTotal} SKUs were not priced, so the displayed mean uses a subset and may be biased downward.
          </p>
        ) : null}
      </Section>
    </div>
  );
}
