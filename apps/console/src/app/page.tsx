import Link from "next/link";

import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  EmptyState,
  KeyValue,
  Meter,
  Overline,
  Panel,
  Section,
  SectionHeading,
  Stat,
  StatBand,
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
            without its components is unauditable, so both travel together.

            No reveal on this panel. It holds the one figure a reader opens this page for, and an
            entrance animation on the headline number is the definition of polish getting in the way
            of the product. */}
        <Panel raised className="p-7 lg:col-span-5">
          <div className="flex items-start justify-between gap-6">
            <Stat
              label="Quality index"
              value={percent(totals.meanComposite, 1)}
              hint={`Weighted composite across ${totals.skuCount} SKUs`}
            />
            <span className="pill pill-quiet mt-1">Mean</span>
          </div>

          {/* A rule between the composite and its components. They are not four more figures at the
              same level — they are what the one above is made of. */}
          <dl className="hairline-t mt-7 flex flex-col gap-4 pt-6">
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
      <StatBand>
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
      </StatBand>

      {/*
        ---------------------------------------------------------------- cost meter

        No rule of its own: the counter band above closes with one, and two rules separated by
        nothing but space is a rule too many.
      */}
      <Section divider={false}>
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
          <Panel className="mt-7">
            {/*
              `unmeasured`. No cost recorded is not a cost of zero, and this panel is the one place
              on the page where a reader could plausibly read the second from the first.
            */}
            <EmptyState
              kind="unmeasured"
              title="No cost recorded"
              detail={
                <>
                  Nothing was priced, which is not the same as nothing having been spent. Fetch the
                  price table with{" "}
                  <span className="mono">python scripts/fetch_bedrock_prices.py --write</span> and
                  re-run the pipeline.
                </>
              }
            />
          </Panel>
        ) : (
          <>
            <div className="mt-7 grid gap-6 lg:grid-cols-12">
              <Panel className="reveal reveal-1 p-7 lg:col-span-5">
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

              <Panel className="reveal reveal-2 p-7 lg:col-span-7">
                <Overline>Where the spend went</Overline>

                {/*
                  Per-tier, because the cascade's entire justification is that most work lands
                  on the cheapest model. If the frontier tier dominated this table, the tier
                  ordering would need revisiting rather than defending.

                  Widths are declared rather than left to the content. Four of these five columns are
                  numeric and their widest plausible value is known, so fixing them stops the table
                  reflowing between runs and keeps the tier names — the only column a reader scans
                  vertically — flush left against the panel edge.
                */}
                <table className="mt-5 w-full border-collapse text-sm">
                  <caption className="sr-only">Cost by model tier</caption>
                  <colgroup>
                    <col />
                    <col className="w-[5.5rem]" />
                    <col className="w-[6.5rem]" />
                    <col className="w-[6rem]" />
                    <col className="w-[4.5rem]" />
                  </colgroup>
                  <thead className="table-head bg-transparent">
                    <tr>
                      <th scope="col" className="py-2 text-left">
                        Tier
                      </th>
                      <th scope="col" className="py-2 text-right">
                        Calls
                      </th>
                      <th scope="col" className="py-2 text-right">
                        Tokens
                      </th>
                      <th scope="col" className="py-2 text-right">
                        Cost
                      </th>
                      <th scope="col" className="py-2 text-right">
                        Share
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {tierRows.map((row) => (
                      <tr key={row.tier} className="grid-row">
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
      </Section>

      {/*
        ---------------------------------------------------------------- the risk dial

        `level="primary"` and the wider rhythm. This control is the product's central claim, and it
        had been reading as the fourth of six equally-weighted panels. Nothing about it moves except
        its heading size and the space around it, which is the point: the page needed a hierarchy,
        not another card.
      */}
      <Section rhythm="lg">
        <SectionHeading
          level="primary"
          title="Acceptance policy"
          detail="Choose an error budget; see what it costs in coverage."
          action={
            <span className={`pill ${dataset.policy.achievable ? "pill-pass" : "pill-warn"}`}>
              {dataset.policy.achievable ? "Validated" : "Not validated"}
            </span>
          }
        />

        <div className="mt-8">
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
      </Section>

      {/*
        ---------------------------------------------------------------- sources

        Restructured from a twelve-column grid holding a single seven-column panel, which left five
        columns of nothing beside it on any screen wide enough to notice — an accident rather than a
        composition, and the most visible one on the page.

        It is now an asymmetric split: the label and the reason the documents are addressed this way
        on the left, the document's own fields on the right. That gives the section a shape no other
        section on the page has, which is what stops six sections reading as one list, and it uses
        the full measure without inventing a panel to fill the gap.
      */}
      <Section>
        <div className="grid gap-x-10 gap-y-7 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <SectionHeading
              title={sources.length === 1 ? "Source document" : "Source documents"}
              detail="Content-addressed, so every citation stays stable."
            />
            <p className="mt-4 max-w-[46ch] text-meta text-[var(--fg-quiet)]">
              A citation names a digest, not a filename. Re-download the same datasheet and it
              resolves to the same document; edit one byte and every span that pointed into it stops
              resolving rather than quietly pointing somewhere else.
            </p>
          </div>

          <div className="lg:col-span-8">
            {sources.length === 0 ? (
              <Panel>
                {/*
                  `empty`, not `unmeasured`: a run with no documents is a run that has not happened,
                  which is a real absence rather than an unobserved quantity.
                */}
                <EmptyState
                  title="No source documents"
                  detail={
                    <>
                      Nothing has been ingested, so there is nothing for a citation to resolve
                      against. Produce a run with{" "}
                      <span className="mono">python scripts/run_pipeline.py</span>.
                    </>
                  }
                />
              </Panel>
            ) : (
              <ul className="flex flex-col gap-6">
                {sources.map(({ document, pages }) => (
                  <li key={document.document_id} className="not-first:hairline-t not-first:pt-6">
                    {/* Grid lives on the `dl` itself; an intermediate wrapper would nest a second
                        `div` between the list and its `dt`/`dd` pairs, which is not valid. */}
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
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
                      <KeyValue label="Structure" span="full">
                        {document.page_count ?? pages.length} page
                        {(document.page_count ?? pages.length) === 1 ? "" : "s"},{" "}
                        {document.line_count} lines, {document.table_count} table
                        {document.table_count === 1 ? "" : "s"}
                      </KeyValue>
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Section>

      {/*
        ---------------------------------------------------------------- queue

        The one section on this page that is work rather than context, and it had been the only
        section with no rhythm of its own — it opened flush against the panel above it, sixth in a
        stack of six, at the same heading weight as the cost table. `level="primary"` and the wide
        rhythm say what it is. The count in the heading detail is the other half: a queue whose
        length you have to read a table to discover is not a queue.
      */}
      <Section rhythm="lg" labelledBy="review-queue-heading">
        <SectionHeading
          id="review-queue-heading"
          level="primary"
          title="Review queue"
          detail="Ordered by blocking failures, then required gaps, then values below threshold."
          action={
            <span className={`pill ${totals.needingReview > 0 ? "pill-warn" : "pill-pass"}`}>
              {totals.needingReview > 0 ? (
                <>
                  <AlertIcon />
                  {count(totals.needingReview)} to decide
                </>
              ) : (
                <>
                  <CheckIcon />
                  Nothing waiting
                </>
              )}
            </span>
          }
        />

        {queue.length === 0 ? (
          <Panel className="mt-7">
            {/*
              A table with a header row and no body reads as broken, which is what this rendered
              before. `empty` rather than `unmeasured`: the queue really is empty, and saying so is
              a result — every SKU cleared the threshold.
            */}
            <EmptyState
              title="Nothing in the queue"
              detail="Every extracted value cleared the acceptance threshold with verified evidence, and no required attribute is missing. Nothing needs a reviewer."
              action={
                <Link href="/certificates" className="btn btn-quiet">
                  Certificates
                  <ArrowIcon />
                </Link>
              }
            />
          </Panel>
        ) : (
          <Panel className="scroll-x mt-7 overflow-hidden p-0">
            <table className="w-full min-w-[54rem] border-collapse text-sm">
              <caption className="sr-only">
                SKUs ordered by how urgently they need review
              </caption>
              {/*
                Declared widths for every column whose content has a known ceiling, so the table
                does not redistribute itself between runs and the four narrow tallies sit in a
                predictable rail on the right. Only SKU and Class flex.
              */}
              <colgroup>
                <col />
                <col />
                <col className="w-[13rem]" />
                <col className="w-[6rem]" />
                <col className="w-[5.5rem]" />
                <col className="w-[6rem]" />
                <col className="w-[7rem]" />
                <col className="w-[3.5rem]" />
              </colgroup>
              <thead className="table-head">
                <tr>
                  <th scope="col" className="px-5 py-2.5 text-left">
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
                  <th scope="col" className="px-5 py-2.5 text-left">
                    Class
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-left">
                    Completeness
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right">
                    Verified
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right">
                    Review
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right">
                    Gaps
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right">
                    Checks
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right">
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
                    /*
                      `group` so the whole row is the hover target rather than each link separately.
                      A row eight columns wide where the affordance only appears under the cursor's
                      exact position makes a reviewer aim; hovering anywhere now brings up both the
                      identity and the action.
                    */
                    <tr key={bundle.sku} className="grid-row group hairline-b last:border-b-0">
                      <th scope="row" className="px-5 py-3.5 text-left font-medium">
                        <Link
                          href={`/review/${bundle.sku}`}
                          className="rounded-xs transition-colors duration-[var(--duration-fast)] group-hover:text-[var(--accent)] hover:text-[var(--accent)]"
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
                          /* A measured zero. Quiet, so it does not read as work in a column of work. */
                          <span className="figure-zero">0</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-right tabular-nums">
                        {bundle.metrics.gaps_required > 0 ? (
                          <span className="text-[var(--warn)]">
                            {bundle.metrics.gaps_required}
                          </span>
                        ) : (
                          <span className="figure-zero">0</span>
                        )}
                        <span className="figure-zero">/{bundle.metrics.gaps_total}</span>
                      </td>
                      {/*
                        The one column whose value is a word rather than a number. It was bare
                        coloured text right-aligned against a rail of figures, where "clean" and
                        "2 failed" had no shared glyph to align on and the colour was the only
                        signal — meaning encoded in hue alone, which fails for anyone who cannot
                        separate the three. A pill gives it an edge to sit against and lets the
                        clean case carry a check rather than a shade of green.
                      */}
                      <td className="px-5 py-3.5 text-right">
                        {bundle.validation.failures > 0 ? (
                          <span className="pill pill-fail">
                            {bundle.validation.failures} failed
                          </span>
                        ) : bundle.validation.warnings > 0 ? (
                          <span className="pill pill-warn">
                            {bundle.validation.warnings} warn
                          </span>
                        ) : (
                          <span className="pill pill-pass">
                            <CheckIcon />
                            clean
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <Link
                          href={`/review/${bundle.sku}`}
                          className="btn btn-bare h-7 px-2 text-[var(--fg-quiet)]
                                     transition-colors duration-[var(--duration-fast)]
                                     group-hover:text-[var(--fg)]"
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
        )}

        {/*
          Provenance of the page itself.

          `live` distinguishes real model output served by the API from the checked-in
          fixture, whose model responses are hand-seeded. Anyone reading a number off this
          screen needs to know which one they are looking at, so it is stated rather than
          implied.
        */}
        <div className="mt-5 flex flex-col gap-2">
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
      </Section>

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
        <Section key={group.seriesSku} rhythm="lg">
          <VariantSeriesPanel group={group} />
        </Section>
      ))}
    </div>
  );
}
