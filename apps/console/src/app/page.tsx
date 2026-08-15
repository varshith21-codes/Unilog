import Link from "next/link";

import {
  AlertIcon,
  ArrowIcon,
  KeyValue,
  Meter,
  Overline,
  Panel,
  SectionHeading,
  Stat,
} from "@/components/primitives";
import { RiskDial } from "@/components/risk-dial";
import { VariantSeriesPanel } from "@/components/variant-series-panel";
import {
  costTotals,
  listDocuments,
  listSkus,
  loadDataset,
  loadPolicy,
  portfolioTotals,
  reviewOrder,
  variantGroups,
} from "@/lib/data";
import { count, dateOnly, percent, shortHash, tokens, usd } from "@/lib/format";

export const metadata = { title: "Overview" };

/** Longest browse-path prefix every class shares, so the masthead can name the vertical. */
function commonBrowsePath(paths: string[][]): string[] {
  const first = paths[0];
  if (!first) return [];

  const shared: string[] = [];
  for (let i = 0; i < first.length; i += 1) {
    const segment = first[i];
    if (segment === undefined) break;
    if (!paths.every((path) => path[i] === segment)) break;
    shared.push(segment);
  }
  return shared;
}

export default async function OverviewPage() {
  const dataset = await loadDataset();
  const skus = await listSkus();
  const totals = portfolioTotals(skus);
  const queue = reviewOrder(skus);
  const sources = await listDocuments();
  const cost = costTotals(skus);
  const policyView = await loadPolicy(dataset.policy.epsilon);
  const series = variantGroups(skus);

  // Dearest tier first: the question this table answers is "what is costing me money", and
  // alphabetical or cascade order buries the answer.
  const tierRows = Object.entries(cost.byTierUsd)
    .map(([tier, tierUsd]) => ({
      tier,
      usd: tierUsd,
      calls: skus.reduce((sum, s) => sum + (s.cost?.calls_by_tier[tier] ?? 0), 0),
      tokens: skus.reduce(
        (sum, s) =>
          sum + (s.cost?.input_by_tier[tier] ?? 0) + (s.cost?.output_by_tier[tier] ?? 0),
        0,
      ),
      share: cost.totalUsd > 0 ? tierUsd / cost.totalUsd : 0,
    }))
    .sort((a, b) => b.usd - a.usd);

  const classes = Object.values(dataset.class_definitions);
  const shared = commonBrowsePath(classes.map((definition) => definition.browse_path));
  const only = classes.length === 1 ? classes[0] : null;

  // With one class the heading names it. With several, naming one of them would misdescribe
  // the catalog, so it falls back to the shared vertical and a count.
  const heading = only?.name ?? (shared.at(-1) ?? "Catalog");
  const overline = (only ? only.browse_path : shared).join(" / ") || "Catalog";

  const dimensions = [
    { label: "Completeness", value: totals.meanCompleteness, weight: 0.35 },
    { label: "Verifiability", value: totals.meanVerifiability, weight: 0.3 },
    { label: "Consistency", value: totals.meanConsistency, weight: 0.25 },
    // Null when no SKU in the portfolio has a richness score. Rendered as unmeasured rather than
    // as zero, because the two look identical in a bar chart and mean opposite things.
    { label: "Richness", value: totals.meanRichness, weight: 0.1 },
  ] satisfies { label: string; value: number | null; weight: number }[];

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      {/* ---------------------------------------------------------------- masthead */}
      <header className="grid gap-10 py-[var(--spacing-section-lg)] lg:grid-cols-12 lg:gap-12">
        <div className="lg:col-span-7">
          <Overline>{overline}</Overline>
          <h1 className="mt-4 max-w-[24ch] text-display font-medium tracking-[var(--tracking-display)]">
            {heading}
          </h1>
          <p className="mt-5 max-w-[54ch] text-body text-[var(--fg-secondary)]">
            {count(totals.valuesTotal)} attribute values across {totals.skuCount} SKUs
            {classes.length > 1 ? ` in ${classes.length} product classes` : ""}, each traced to
            a verbatim span in{" "}
            {sources.length === 1 ? "the source datasheet" : `${sources.length} source documents`}.{" "}
            {totals.needingReview > 0 ? (
              <>
                {count(totals.needingReview)} need a reviewer before they can publish.
              </>
            ) : (
              <>Everything currently extracted is publishable.</>
            )}
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-2">
            <Link href="/review" className="btn btn-primary">
              Open review queue
              <ArrowIcon />
            </Link>
            <Link href="/certificates" className="btn btn-quiet">
              Certificates
            </Link>
          </div>
        </div>

        {/* Composite quality, with its four weighted dimensions underneath. A composite
            without its components is unauditable, so both travel together. */}
        <Panel raised className="p-7 lg:col-span-5">
          <div className="flex items-start justify-between gap-6">
            <Stat
              label="Quality index"
              value={percent(totals.meanComposite, 1)}
              hint={`Weighted composite across ${totals.skuCount} SKUs`}
            />
            <span className="pill pill-quiet mt-1">Mean</span>
          </div>

          <dl className="mt-8 flex flex-col gap-4">
            {dimensions.map((dimension) => (
              <div
                key={dimension.label}
                className="grid grid-cols-[1fr_auto] items-baseline gap-x-3 gap-y-2"
              >
                <dt className="text-sm text-[var(--fg-secondary)]">
                  {dimension.label}
                  <span className="ml-1.5 text-meta text-[var(--fg-quiet)]">
                    ×{dimension.weight}
                  </span>
                </dt>
                <dd className="text-sm tabular-nums">
                  {dimension.value === null ? (
                    <span className="text-[var(--fg-quiet)]">&mdash;</span>
                  ) : (
                    percent(dimension.value, 1)
                  )}
                </dd>
                <dd className="col-span-2">
                  {dimension.value !== null ? (
                    <Meter
                      value={dimension.value}
                      tone={dimension.value === 0 ? "quiet" : "accent"}
                      label={`${dimension.label} ${percent(dimension.value, 1)}`}
                    />
                  ) : null}
                </dd>
              </div>
            ))}
          </dl>

          {totals.meanRichness === null ? (
            <p className="mt-6 text-meta text-[var(--fg-quiet)]">
              Richness is scored from channel readiness and copy depth. No SKU in this portfolio has
              either, so it is excluded from the composite rather than counted as zero — the figure
              above spans the three dimensions that were measured.
            </p>
          ) : null}
        </Panel>
      </header>

      {/* ---------------------------------------------------------------- counters */}
      <section className="hairline-t hairline-b grid grid-cols-2 gap-x-6 gap-y-9 py-9 md:grid-cols-4">
        <Stat
          label="Publishable"
          value={count(totals.valuesPublishable)}
          hint={`of ${count(totals.valuesTotal)} extracted values`}
          tone="pass"
        />
        <Stat
          label="Needs review"
          value={count(totals.needingReview)}
          hint="below threshold or failing a check"
          tone={totals.needingReview > 0 ? "warn" : "default"}
        />
        <Stat
          label="Required gaps"
          value={count(totals.gapsRequired)}
          hint={`of ${count(totals.gapsTotal)} total gaps`}
          tone={totals.gapsRequired > 0 ? "warn" : "default"}
        />
        <Stat
          label="Channels ready"
          value={`${totals.channelsReady}/${totals.channelsTotal}`}
          hint="passing preflight across all SKUs"
        />
      </section>

      {/* ---------------------------------------------------------------- cost meter */}
      <section className="py-[var(--spacing-section)]">
        <SectionHeading
          title="Cost to enrich"
          detail="Bedrock token spend, priced from the AWS Price List API."
          action={
            cost.source ? (
              <span className={`pill ${cost.source.stale ? "pill-warn" : "pill-quiet"}`}>
                {cost.source.stale ? "Price table stale" : `Prices ${cost.source.region}`}
              </span>
            ) : null
          }
        />

        {cost.skusPriced === 0 ? (
          <Panel className="mt-6 p-7">
            <p className="text-body text-[var(--fg-secondary)]">
              No cost recorded. Fetch the price table with{" "}
              <span className="mono">python scripts/fetch_bedrock_prices.py --write</span> and
              re-run the pipeline.
            </p>
          </Panel>
        ) : (
          <>
            <div className="mt-6 grid gap-6 lg:grid-cols-12">
              <Panel className="p-7 lg:col-span-5">
                <Overline>Mean per SKU</Overline>
                <p className="figure mt-3">{usd(cost.meanPerSkuUsd)}</p>
                <p className="mt-2 text-meta text-[var(--fg-quiet)]">
                  {usd(cost.meanPerValueUsd)} per attribute value ·{" "}
                  {tokens(cost.inputTokens)} in / {tokens(cost.outputTokens)} out ·{" "}
                  {cost.calls} calls, {cost.escalations} escalations
                </p>

                {/*
                  The number a buyer actually needs. A tenth of a cent is not decision-grade;
                  "what does my whole catalogue cost" is.
                */}
                <dl className="hairline-t mt-6 grid grid-cols-3 gap-4 pt-5">
                  {[10_000, 100_000, 500_000].map((scale) => (
                    <div key={scale}>
                      <dt className="text-meta text-[var(--fg-quiet)]">
                        {count(scale)} SKUs
                      </dt>
                      <dd className="mt-1 text-lg tabular-nums">
                        {usd(cost.project(scale))}
                      </dd>
                    </div>
                  ))}
                </dl>

                <p className="mt-5 max-w-[54ch] text-meta text-[var(--fg-quiet)]">
                  Straight-line extrapolation from {cost.skusPriced}{" "}
                  {cost.skusPriced === 1 ? "SKU" : "SKUs"}. It holds only if these documents
                  are typical: longer datasheets cost more, and every escalation to a frontier
                  model costs several times a first-pass call.
                </p>
              </Panel>

              <Panel className="p-7 lg:col-span-7">
                <Overline>Where the spend went</Overline>

                {/*
                  Per-tier, because the cascade's entire justification is that most work lands
                  on the cheapest model. If the frontier tier dominated this table, the tier
                  ordering would need revisiting rather than defending.
                */}
                <table className="mt-5 w-full border-collapse text-sm">
                  <caption className="sr-only">Cost by model tier</caption>
                  <thead>
                    <tr className="text-meta text-[var(--fg-quiet)]">
                      <th scope="col" className="hairline-b py-2 text-left font-medium">
                        Tier
                      </th>
                      <th scope="col" className="hairline-b py-2 text-right font-medium">
                        Calls
                      </th>
                      <th scope="col" className="hairline-b py-2 text-right font-medium">
                        Tokens
                      </th>
                      <th scope="col" className="hairline-b py-2 text-right font-medium">
                        Cost
                      </th>
                      <th scope="col" className="hairline-b py-2 text-right font-medium">
                        Share
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {tierRows.map((row) => (
                      <tr key={row.tier}>
                        <td className="hairline-b py-2.5">{row.tier}</td>
                        <td className="hairline-b py-2.5 text-right tabular-nums">
                          {count(row.calls)}
                        </td>
                        <td className="hairline-b py-2.5 text-right tabular-nums">
                          {tokens(row.tokens)}
                        </td>
                        <td className="hairline-b py-2.5 text-right tabular-nums">
                          {usd(row.usd)}
                        </td>
                        <td className="hairline-b py-2.5 text-right tabular-nums text-[var(--fg-tertiary)]">
                          {percent(row.share, 0)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {cost.source ? (
                  <p className="mt-5 text-meta text-[var(--fg-quiet)]">
                    On-demand rates for {cost.source.priced_models} models, fetched{" "}
                    {dateOnly(cost.source.fetched_at)} from the {cost.source.source}. Prices are
                    never hand-entered: an unpriced model reports no cost rather than a guess.
                  </p>
                ) : null}
              </Panel>
            </div>

            {!cost.priced ? (
              <p className="mt-4 flex gap-2 text-meta text-[var(--warn)]">
                <AlertIcon className="mt-0.5 shrink-0" />
                {cost.skusTotal - cost.skusPriced} of {cost.skusTotal} SKUs could not be
                costed, so this average is taken over a subset. Unpriced runs are usually the
                ones that escalated, which biases the figure downward.
              </p>
            ) : null}
          </>
        )}
      </section>

      {/* ---------------------------------------------------------------- the risk dial */}
      <section className="py-[var(--spacing-section)]">
        <SectionHeading
          title="Acceptance policy"
          detail="Choose an error budget; see what it costs in coverage."
          action={
            <span className={`pill ${dataset.policy.achievable ? "pill-pass" : "pill-warn"}`}>
              {dataset.policy.achievable ? "Validated" : "Not validated"}
            </span>
          }
        />

        <div className="mt-6">
          <RiskDial initial={policyView} />
        </div>

        {/*
          The provenance of the policy itself. Stating this plainly matters more than any
          number above it: a threshold learned from a synthetic set is a demo, not a guarantee.
        */}
        <div className="mt-6 flex gap-3 rounded-lg bg-[var(--warn-quiet)] p-3.5">
          <AlertIcon className="mt-0.5 shrink-0 text-[var(--warn)]" />
          <p className="max-w-[86ch] text-sm text-[var(--fg-secondary)]">
            This threshold comes from{" "}
            <span className="font-medium text-[var(--fg)]">{dataset.meta.policy_source}</span>,
            and the calibrator is {dataset.meta.calibrator.replace(/-/g, " ")}. Moving the dial
            is a what-if: it does not reclassify anything already published, and coverage should
            be earned from real review decisions before these numbers are relied on.
          </p>
        </div>
      </section>

      {/* ---------------------------------------------------------------- sources */}
      <section className="grid gap-6 pb-[var(--spacing-section)] lg:grid-cols-12">
        <Panel className="p-7 lg:col-span-7">
          <SectionHeading
            title={sources.length === 1 ? "Source document" : "Source documents"}
            detail="Content-addressed, so every citation stays stable."
          />

          <ul className="mt-7 flex flex-col gap-6">
            {sources.map(({ document, pages }) => (
              <li key={document.document_id} className="not-first:hairline-t not-first:pt-6">
                {/* Grid lives on the `dl` itself; an intermediate wrapper would nest a second
                    `div` between the list and its `dt`/`dd` pairs, which is not valid. */}
                <dl className="grid grid-cols-2 gap-x-5 gap-y-5">
                  <KeyValue label="Document" span={2}>
                    {document.document_id}
                  </KeyValue>
                  <KeyValue label="SHA-256" mono span={2}>
                    {shortHash(document.sha256, 24)}…
                  </KeyValue>
                  <KeyValue label="Revision">
                    {document.revision_label ?? "Unlabelled"}
                  </KeyValue>
                  <KeyValue label="Type">{document.doc_type.replace(/_/g, " ")}</KeyValue>
                  <KeyValue label="Parser">{document.parser}</KeyValue>
                  <KeyValue label="Retrieved">{dateOnly(document.fetched_at)}</KeyValue>
                  <KeyValue label="Structure" span={2}>
                    {document.page_count ?? pages.length} page
                    {(document.page_count ?? pages.length) === 1 ? "" : "s"},{" "}
                    {document.line_count} lines, {document.table_count} table
                    {document.table_count === 1 ? "" : "s"}
                  </KeyValue>
                </dl>
              </li>
            ))}
            {sources.length === 0 ? (
              <li className="text-sm text-[var(--fg-tertiary)]">
                No source documents. Run the pipeline to produce some.
              </li>
            ) : null}
          </ul>
        </Panel>
      </section>

      {/* ---------------------------------------------------------------- queue */}
      <section>
        <SectionHeading
          title="Review queue"
          detail="Ordered by blocking failures, then required gaps, then values below threshold."
        />

        <Panel className="scroll-x mt-6 overflow-hidden p-0">
          <table className="w-full min-w-[52rem] border-collapse text-sm">
            <caption className="sr-only">
              SKUs ordered by how urgently they need review
            </caption>
            <thead>
              <tr className="hairline-b bg-[var(--surface-sunken)]">
                <th scope="col" className="px-5 py-3 text-left font-medium">
                  SKU
                </th>
                {/*
                  Class, not size. This column read `nominal_size` and was labelled "Size", which
                  is a valve attribute — correct while the schema held nothing but valves, and
                  blank on every row the moment a lamp or a dishwasher enters the queue. The class
                  is the one thing every row has, and it is what a reviewer needs first: the same
                  screen now mixes verticals, and which attributes are even expected depends on
                  which class you are looking at.
                */}
                <th scope="col" className="px-5 py-3 text-left font-medium">
                  Class
                </th>
                <th scope="col" className="w-44 px-5 py-3 text-left font-medium">
                  Completeness
                </th>
                <th scope="col" className="px-5 py-3 text-right font-medium">
                  Verified
                </th>
                <th scope="col" className="px-5 py-3 text-right font-medium">
                  Review
                </th>
                <th scope="col" className="px-5 py-3 text-right font-medium">
                  Gaps
                </th>
                <th scope="col" className="px-5 py-3 text-right font-medium">
                  Checks
                </th>
                <th scope="col" className="px-5 py-3 text-right font-medium">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {queue.map((bundle) => {
                const definition = bundle.class_code
                  ? dataset.class_definitions[bundle.class_code]
                  : undefined;
                return (
                  <tr key={bundle.sku} className="grid-row hairline-b last:border-b-0">
                    <th scope="row" className="px-5 py-3.5 text-left font-medium">
                      <Link
                        href={`/review/${bundle.sku}`}
                        className="rounded-xs hover:text-[var(--accent)]"
                      >
                        {bundle.sku}
                      </Link>
                    </th>
                    <td className="px-5 py-3.5 text-[var(--fg-secondary)]">
                      {definition ? (
                        definition.item_type
                      ) : (
                        <span className="text-[var(--fg-quiet)]">Unclassified</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <Meter
                          value={bundle.metrics.fill_rate}
                          tone={bundle.metrics.fill_rate >= 0.8 ? "pass" : "warn"}
                          label={`Completeness ${percent(bundle.metrics.fill_rate)}`}
                        />
                        <span className="w-9 shrink-0 text-right tabular-nums text-[var(--fg-secondary)]">
                          {percent(bundle.metrics.fill_rate)}
                        </span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-right tabular-nums">
                      {percent(bundle.metrics.verifiability)}
                    </td>
                    <td className="px-5 py-3.5 text-right tabular-nums">
                      {bundle.metrics.values_needing_review > 0 ? (
                        <span className="text-[var(--warn)]">
                          {bundle.metrics.values_needing_review}
                        </span>
                      ) : (
                        <span className="text-[var(--fg-quiet)]">0</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-right tabular-nums">
                      {bundle.metrics.gaps_required > 0 ? (
                        <span className="text-[var(--warn)]">
                          {bundle.metrics.gaps_required}
                        </span>
                      ) : (
                        <span className="text-[var(--fg-quiet)]">0</span>
                      )}
                      <span className="text-[var(--fg-quiet)]">
                        /{bundle.metrics.gaps_total}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-right tabular-nums">
                      {bundle.validation.failures > 0 ? (
                        <span className="text-[var(--fail)]">
                          {bundle.validation.failures} failed
                        </span>
                      ) : bundle.validation.warnings > 0 ? (
                        <span className="text-[var(--warn)]">
                          {bundle.validation.warnings} warn
                        </span>
                      ) : (
                        <span className="text-[var(--pass)]">clean</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <Link
                        href={`/review/${bundle.sku}`}
                        className="btn btn-bare h-7 px-2"
                        aria-label={`Review ${bundle.sku}`}
                      >
                        <ArrowIcon />
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        {/*
          Provenance of the page itself.

          `live` distinguishes real model output served by the API from the checked-in
          fixture, whose model responses are hand-seeded. Anyone reading a number off this
          screen needs to know which one they are looking at, so it is stated rather than
          implied.
        */}
        <div className="mt-4 flex flex-col gap-2">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-[var(--fg-quiet)]">
            <span className={`pill ${dataset.meta.live ? "pill-pass" : "pill-warn"}`}>
              {dataset.meta.live ? "Live pipeline output" : "Offline fixture"}
            </span>
            <span>
              Generated {dateOnly(dataset.meta.generated_at)} by {dataset.meta.generator}
              {dataset.meta.pipeline_version ? ` · ${dataset.meta.pipeline_version}` : ""} ·
              calibrator {dataset.meta.calibrator}
            </span>
            {classes.length > 0 ? (
              <span className="mono">
                schema {classes.map((definition) => definition.schema_version).join(", ")}
              </span>
            ) : null}
          </p>

          {(dataset.meta.warnings ?? []).map((warning) => (
            <p
              key={warning}
              className="flex gap-2 text-meta text-[var(--warn)]"
              role="status"
            >
              <AlertIcon className="mt-0.5 shrink-0" />
              {warning}
            </p>
          ))}
        </div>
      </section>

      {/*
        ---------------------------------------------------------------- variant series

        Below the queue rather than above it. A series is context about how the catalogue was
        built; the queue is work somebody has to do, and work comes first.

        Absent entirely for a catalogue of standalone products, with no empty state. Every other
        panel here describes something that should exist and is missing if it does not — a cost
        figure, a source document — whereas most catalogues legitimately have no variant series at
        all, and an empty "no series found" panel would report the normal case as a shortfall.
      */}
      {series.map((group) => (
        <div key={group.seriesSku} className="mt-[var(--spacing-section-lg)]">
          <VariantSeriesPanel group={group} />
        </div>
      ))}
    </div>
  );
}
