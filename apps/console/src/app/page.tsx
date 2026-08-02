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
import { listSkus, loadDataset, portfolioTotals, reviewOrder } from "@/lib/data";
import { count, dateOnly, percent, shortHash } from "@/lib/format";

export const metadata = { title: "Overview" };

export default async function OverviewPage() {
  const dataset = await loadDataset();
  const skus = await listSkus();
  const totals = portfolioTotals(skus);
  const queue = reviewOrder(skus);

  const dimensions = [
    { label: "Completeness", value: totals.meanCompleteness, weight: 0.35 },
    { label: "Verifiability", value: totals.meanVerifiability, weight: 0.3 },
    { label: "Consistency", value: totals.meanConsistency, weight: 0.25 },
    { label: "Richness", value: 0, weight: 0.1 },
  ];

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      {/* ---------------------------------------------------------------- masthead */}
      <header className="grid gap-10 py-[var(--spacing-section-lg)] lg:grid-cols-12 lg:gap-12">
        <div className="lg:col-span-7">
          <Overline>
            {dataset.class_definition.browse_path.join(" / ")}
          </Overline>
          <h1 className="mt-4 max-w-[24ch] text-display font-medium tracking-[var(--tracking-display)]">
            {dataset.class_definition.name}
          </h1>
          <p className="mt-5 max-w-[54ch] text-body text-[var(--fg-secondary)]">
            {count(totals.valuesTotal)} attribute values across {totals.skuCount} SKUs, each
            traced to a verbatim span in the source datasheet.{" "}
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
                <dd className="text-sm tabular-nums">{percent(dimension.value, 1)}</dd>
                <dd className="col-span-2">
                  <Meter
                    value={dimension.value}
                    tone={dimension.value === 0 ? "quiet" : "accent"}
                    label={`${dimension.label} ${percent(dimension.value, 1)}`}
                  />
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-6 text-meta text-[var(--fg-quiet)]">
            Richness scores generated marketing copy, which this run did not produce.
          </p>
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

      {/* ---------------------------------------------------------------- policy + source */}
      <section className="grid gap-6 py-[var(--spacing-section)] lg:grid-cols-12">
        <Panel className="p-7 lg:col-span-7">
          <SectionHeading
            title="Acceptance policy"
            detail="The threshold above which a value publishes without a reviewer."
            action={
              <span
                className={`pill ${dataset.policy.achievable ? "pill-pass" : "pill-warn"}`}
              >
                {dataset.policy.achievable ? "Validated" : "Not validated"}
              </span>
            }
          />

          <p className="mt-6 max-w-[62ch] text-body text-[var(--fg-secondary)]">
            {dataset.policy.reason.charAt(0).toUpperCase() + dataset.policy.reason.slice(1)}.
          </p>

          <div className="mt-8">
            <div className="flex items-baseline justify-between gap-3">
              <Overline>Coverage at threshold</Overline>
              <span className="text-sm tabular-nums">
                {percent(dataset.policy.coverage, 1)}
              </span>
            </div>
            <div className="mt-2">
              <Meter
                value={dataset.policy.coverage}
                threshold={dataset.policy.threshold}
                tone="accent"
                label={`Coverage ${percent(dataset.policy.coverage, 1)}`}
              />
            </div>
          </div>

          <dl className="mt-8 grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
            <KeyValue label="Threshold">
              {dataset.policy.threshold === null
                ? "—"
                : dataset.policy.threshold.toFixed(3)}
            </KeyValue>
            <KeyValue label="Error budget">{percent(dataset.policy.epsilon)}</KeyValue>
            <KeyValue label="Confidence">
              {percent(dataset.policy.confidence_level)}
            </KeyValue>
            <KeyValue label="Calibration set">
              {count(dataset.policy.calibration_size)}
            </KeyValue>
          </dl>

          {/* The provenance of the policy itself. Stating this plainly matters more than
              the numbers above it. */}
          <div className="mt-7 flex gap-3 rounded-lg bg-[var(--warn-quiet)] p-3.5">
            <AlertIcon className="mt-0.5 shrink-0 text-[var(--warn)]" />
            <p className="text-sm text-[var(--fg-secondary)]">
              This threshold comes from a{" "}
              <span className="font-medium text-[var(--fg)]">
                {dataset.meta.policy_source}
              </span>{" "}
              set, not from reviewer outcomes. The calibrator is{" "}
              {dataset.meta.calibrator.replace(/-/g, " ")}. Automation coverage should be
              earned from real review decisions before these numbers are trusted.
            </p>
          </div>
        </Panel>

        <Panel className="p-7 lg:col-span-5">
          <SectionHeading
            title="Source document"
            detail="Content-addressed, so every citation stays stable."
          />

          {/* Grid lives on the `dl` itself; an intermediate wrapper would nest a second
              `div` between the list and its `dt`/`dd` pairs, which is not valid. */}
          <dl className="mt-7 grid grid-cols-2 gap-x-5 gap-y-5">
            <KeyValue label="Document" span={2}>
              {dataset.document.document_id}
            </KeyValue>
            <KeyValue label="SHA-256" mono span={2}>
              {shortHash(dataset.document.sha256, 24)}…
            </KeyValue>
            <KeyValue label="Revision">
              {dataset.document.revision_label ?? "Unlabelled"}
            </KeyValue>
            <KeyValue label="Type">
              {dataset.document.doc_type.replace(/_/g, " ")}
            </KeyValue>
            <KeyValue label="Parser">{dataset.document.parser}</KeyValue>
            <KeyValue label="Retrieved">{dateOnly(dataset.document.fetched_at)}</KeyValue>
            <KeyValue label="Structure" span={2}>
              {dataset.document.page_count ?? dataset.pages.length} page
              {(dataset.document.page_count ?? dataset.pages.length) === 1 ? "" : "s"},{" "}
              {dataset.document.line_count} lines, {dataset.document.table_count} table
              {dataset.document.table_count === 1 ? "" : "s"}
            </KeyValue>
          </dl>
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
                <th scope="col" className="px-5 py-3 text-left font-medium">
                  Size
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
                const size = bundle.values.find(
                  (value) => value.attribute_code === "nominal_size",
                );
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
                      {size?.value_display ?? size?.value_raw ?? "—"}
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

        <p className="mt-4 text-meta text-[var(--fg-quiet)]">
          Generated {dateOnly(dataset.meta.generated_at)} by {dataset.meta.generator} ·{" "}
          {dataset.meta.pipeline_version} · schema{" "}
          {dataset.class_definition.schema_version}
        </p>
      </section>
    </div>
  );
}
