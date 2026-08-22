import Link from "next/link";

import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  EmptyState,
  Meter,
  PageHeader,
  Panel,
  Section,
  SectionHeading,
  Stat,
  StatBand,
} from "@/components/primitives";
import {
  listSkus,
  loadDataset,
  portfolioTotals,
  reviewOrder,
  unresolvedConflicts,
} from "@/lib/data";
import { count, dateTime, percent } from "@/lib/format";

export const metadata = {
  title: "Operations",
  description: "Actionable portfolio health, blocking catalog work, and recorded run context.",
};

export default async function OperationsPage() {
  const dataset = await loadDataset();
  const skus = await listSkus();
  const warnings = dataset.meta.warnings ?? [];
  const totals = portfolioTotals(skus);
  const blocking = reviewOrder(skus).filter(
    (bundle) =>
      unresolvedConflicts(bundle) > 0 ||
      bundle.validation.failures > 0 ||
      bundle.metrics.values_needing_review > 0 ||
      bundle.metrics.gaps_required > 0,
  );
  const recorded = [...skus]
    .sort(
      (a, b) =>
        new Date(b.certificate.generated_at).getTime() -
        new Date(a.certificate.generated_at).getTime(),
    )
    .slice(0, 5);
  const readyShare =
    totals.channelsTotal > 0 ? totals.channelsReady / totals.channelsTotal : null;

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Operations / portfolio control"
        title={blocking.length > 0 ? "Catalog work that needs intervention" : "Portfolio is clear to progress"}
        detail={
          blocking.length > 0
            ? `${blocking.length} of ${skus.length} records are blocked by a validation failure, unresolved evidence, or a required decision. Work is ordered by publication risk.`
            : `All ${skus.length} records have cleared the current decision gates. Review recorded process context or move eligible output into delivery.`
        }
        actions={
          <>
            <Link href="/review" className="btn btn-primary">
              Resolve blocking work
              <ArrowIcon />
            </Link>
            <Link href="/delivery" className="btn btn-quiet">
              Open Publish
            </Link>
          </>
        }
        meta={
          <>
            <span className={`pill ${dataset.meta.live ? "pill-pass" : "pill-warn"}`}>
              {dataset.meta.live ? <CheckIcon /> : <AlertIcon />}
              {dataset.meta.live ? "Live persisted data" : "Offline fixture"}
            </span>
            <span>Recorded {dateTime(dataset.meta.generated_at)}</span>
            <span className="mono">{dataset.meta.pipeline_version ?? "pipeline version unavailable"}</span>
          </>
        }
      />

      <StatBand>
        <Stat
          label="Records blocked"
          value={count(blocking.length)}
          hint={`of ${count(totals.skuCount)} catalog records`}
          tone={blocking.length > 0 ? "fail" : "pass"}
        />
        <Stat
          label="Decisions waiting"
          value={count(totals.needingReview)}
          hint="values routed to human review"
          tone={totals.needingReview > 0 ? "warn" : "pass"}
        />
        <Stat
          label="Required gaps"
          value={count(totals.gapsRequired)}
          hint={`${count(totals.gapsTotal)} gaps recorded in total`}
          tone={totals.gapsRequired > 0 ? "warn" : "pass"}
        />
        <Stat
          label="Delivery readiness"
          value={readyShare === null ? "—" : percent(readyShare, 0)}
          hint={
            readyShare === null
              ? "no channel preflight recorded"
              : `${totals.channelsReady} of ${totals.channelsTotal} channel checks ready`
          }
          tone={readyShare === 1 ? "pass" : "default"}
        />
      </StatBand>

      <Section divider={false} rhythm="lg" labelledBy="blocking-work-heading">
        <SectionHeading
          id="blocking-work-heading"
          level="primary"
          title="Blocking work"
          detail="Conflicting sources first, then validation failures, required gaps, and values below the calibrated threshold."
          action={
            blocking.length > 0 ? (
              <Link href="/review" className="btn btn-quiet">
                View full Resolve queue
                <ArrowIcon />
              </Link>
            ) : null
          }
        />

        {blocking.length === 0 ? (
          <Panel className="mt-7">
            <EmptyState
              title="No records are blocked"
              detail="Every extracted value currently clears its decision gate, no required attribute is missing, and no unresolved cross-source conflict is holding publication."
              action={
                <Link href="/delivery" className="btn btn-primary">
                  Prepare delivery
                  <ArrowIcon />
                </Link>
              }
            />
          </Panel>
        ) : (
          <Panel className="mt-7 overflow-hidden p-0">
            <div className="scroll-x" tabIndex={0} role="region" aria-label="Blocking catalog work">
              <table className="w-full min-w-[52rem] border-collapse text-sm">
              <caption className="sr-only">Catalog records ordered by blocking publication risk</caption>
              <thead className="table-head">
                <tr>
                  <th scope="col" className="px-5 py-2.5 text-left">Record</th>
                  <th scope="col" className="px-5 py-2.5 text-left">Primary blocker</th>
                  <th scope="col" className="px-5 py-2.5 text-left">Completeness</th>
                  <th scope="col" className="px-5 py-2.5 text-right">Review</th>
                  <th scope="col" className="px-5 py-2.5 text-right">Required gaps</th>
                  <th scope="col" className="px-5 py-2.5 text-right">Open</th>
                </tr>
              </thead>
              <tbody>
                {blocking.map((bundle) => {
                  const conflicts = unresolvedConflicts(bundle);
                  const primary =
                    conflicts > 0
                      ? `${conflicts} unresolved source conflict${conflicts === 1 ? "" : "s"}`
                      : bundle.validation.failures > 0
                        ? `${bundle.validation.failures} blocking validation failure${bundle.validation.failures === 1 ? "" : "s"}`
                        : bundle.metrics.gaps_required > 0
                          ? `${bundle.metrics.gaps_required} required attribute gap${bundle.metrics.gaps_required === 1 ? "" : "s"}`
                          : `${bundle.metrics.values_needing_review} value${bundle.metrics.values_needing_review === 1 ? "" : "s"} below threshold`;
                  const tone = conflicts > 0 || bundle.validation.failures > 0 ? "pill-fail" : "pill-warn";

                  return (
                    <tr key={bundle.sku} className="grid-row group hairline-b last:border-b-0">
                      <th scope="row" className="px-5 py-3.5 text-left">
                        <Link href={`/review/${bundle.sku}`} className="font-medium group-hover:text-[var(--accent)]">
                          {bundle.sku}
                        </Link>
                        <span className="mono mt-1 block text-[var(--fg-quiet)]">
                          {bundle.class_code ?? "unclassified"}
                        </span>
                      </th>
                      <td className="px-5 py-3.5">
                        <span className={`pill ${tone}`}>
                          <AlertIcon />
                          {primary}
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <Meter
                            value={bundle.metrics.fill_rate}
                            tone={bundle.metrics.fill_rate >= 0.8 ? "pass" : "warn"}
                            label={`Completeness ${percent(bundle.metrics.fill_rate)}`}
                          />
                          <span className="w-10 text-right tabular-nums text-[var(--fg-secondary)]">
                            {percent(bundle.metrics.fill_rate)}
                          </span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5 text-right tabular-nums">
                        {bundle.metrics.values_needing_review || <span className="figure-zero">0</span>}
                      </td>
                      <td className="px-5 py-3.5 text-right tabular-nums">
                        {bundle.metrics.gaps_required || <span className="figure-zero">0</span>}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <Link href={`/review/${bundle.sku}`} className="btn btn-bare h-7 px-2" aria-label={`Resolve ${bundle.sku}`}>
                          <ArrowIcon />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              </table>
            </div>
          </Panel>
        )}
      </Section>

      <Section rhythm="lg" labelledBy="recorded-context-heading">
        <div className="grid gap-8 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <SectionHeading
              id="recorded-context-heading"
              title="Recorded process context"
              detail="Recent persisted artifacts, not live pipeline execution. Open Process to inspect the exact recorded stage sequence."
            />
            <div className="action-row mt-5">
              <Link href="/pipeline" className="btn btn-quiet">
                Explore recorded runs
                <ArrowIcon />
              </Link>
              <Link href="/certificates" className="btn btn-bare">
                Inspect Audit records
              </Link>
            </div>
          </div>

          <Panel className="overflow-hidden lg:col-span-8">
            {recorded.length === 0 ? (
              <EmptyState
                kind="unmeasured"
                title="No run context recorded"
                detail="Operations reads persisted certificates. Save a pipeline session before expecting run history here."
              />
            ) : (
              <ul>
                {recorded.map((bundle) => (
                  <li key={bundle.sku} className="grid grid-cols-[1fr_auto] gap-4 border-b border-[var(--hairline)] px-5 py-4 last:border-b-0 sm:grid-cols-[1fr_auto_auto]">
                    <div className="min-w-0">
                      <Link href={`/pipeline?sku=${encodeURIComponent(bundle.sku)}`} className="font-medium hover:text-[var(--accent)]">
                        {bundle.sku}
                      </Link>
                      <p className="mono mt-1 truncate text-[var(--fg-quiet)]">{bundle.certificate.certificate_id}</p>
                    </div>
                    <div className="hidden text-right sm:block">
                      <p className="overline">Pipeline</p>
                      <p className="mono mt-1 text-[var(--fg-secondary)]">{bundle.certificate.pipeline_version}</p>
                    </div>
                    <div className="text-right">
                      <p className="overline">Recorded</p>
                      <p className="mt-1 text-meta text-[var(--fg-secondary)]">{dateTime(bundle.certificate.generated_at)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </Section>

      {warnings.length > 0 ? (
        <Section rhythm="tight">
          <Panel className="p-5">
            <p className="overline">Data context</p>
            <ul className="mt-3 flex flex-col gap-2">
              {warnings.map((warning) => (
                <li key={warning} className="flex gap-2 text-meta text-[var(--warn)]">
                  <AlertIcon className="mt-0.5 shrink-0" />
                  {warning}
                </li>
              ))}
            </ul>
          </Panel>
        </Section>
      ) : null}
    </div>
  );
}
