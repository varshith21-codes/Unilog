import Link from "next/link";
import { notFound } from "next/navigation";

import { ArrowIcon, Meter } from "@/components/primitives";
import { ReviewWorkspace } from "@/components/review-workspace";
import { attributeRows, getSku, listSkus, loadDataset } from "@/lib/data";
import { percent } from "@/lib/format";

export async function generateStaticParams() {
  const skus = await listSkus();
  return skus.map((bundle) => ({ sku: bundle.sku }));
}

export async function generateMetadata({ params }: { params: Promise<{ sku: string }> }) {
  const { sku } = await params;
  return { title: `Review ${sku}` };
}

export default async function ReviewSkuPage({
  params,
}: {
  params: Promise<{ sku: string }>;
}) {
  const { sku } = await params;
  const bundle = await getSku(sku);
  if (!bundle) notFound();

  const dataset = await loadDataset();
  const rows = attributeRows(
    dataset.class_definition.attributes,
    bundle.values,
    bundle.gaps,
  );

  // Every span in this corpus resolves to page 1; take the page a span actually names so
  // this keeps working when a multi-page PDF arrives.
  const citedPage =
    bundle.values.flatMap((value) => value.evidence).find((span) => span.page !== null)
      ?.page ?? 1;
  const page = dataset.pages.find((candidate) => candidate.number === citedPage) ?? null;

  const size = bundle.values.find((value) => value.attribute_code === "nominal_size");
  const quality = bundle.certificate.summary.quality_index;

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-28">
      {/* ---------------------------------------------------------------- header */}
      <header className="py-10">
        <nav aria-label="Breadcrumb" className="text-meta text-[var(--fg-quiet)]">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li>
              <Link href="/review" className="rounded-xs hover:text-[var(--fg)]">
                Review
              </Link>
            </li>
            <li aria-hidden>/</li>
            <li>{dataset.class_definition.name}</li>
            <li aria-hidden>/</li>
            <li className="text-[var(--fg-secondary)]">{bundle.sku}</li>
          </ol>
        </nav>

        <div className="mt-5 flex flex-wrap items-end justify-between gap-x-10 gap-y-6">
          <div className="min-w-0">
            <h1 className="text-display font-medium tracking-[var(--tracking-display)]">
              {bundle.sku}
            </h1>
            <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[var(--fg-secondary)]">
              <span>{bundle.record.brand ?? "Unbranded"}</span>
              <span aria-hidden className="text-[var(--fg-quiet)]">
                ·
              </span>
              <span>
                {size?.value_display ?? size?.value_raw ?? "size unknown"}{" "}
                {dataset.class_definition.name.toLowerCase()}
              </span>
              <span aria-hidden className="text-[var(--fg-quiet)]">
                ·
              </span>
              <span className="mono text-[var(--fg-tertiary)]">
                {bundle.record.schema_version}
              </span>
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Link href={`/certificates/${bundle.sku}`} className="btn btn-quiet">
              Certificate
              <ArrowIcon />
            </Link>
          </div>
        </div>

        {/*
          Four numbers a reviewer needs before touching anything.

          A real description list: `dt` for the dimension, one `dd` for the figure and one
          for its denominator. Multiple `dd` per `dt` is valid, and it keeps the definition
          attached to the number rather than floating beside it.
        */}
        <dl className="hairline-t mt-8 grid grid-cols-2 gap-x-8 gap-y-6 pt-6 md:grid-cols-4">
          <div>
            <dt className="overline">Completeness</dt>
            <dd className="mt-2 flex items-center gap-3">
              <span className="text-lg tabular-nums">
                {percent(bundle.metrics.fill_rate)}
              </span>
              <Meter
                value={bundle.metrics.fill_rate}
                tone={bundle.metrics.fill_rate >= 0.8 ? "pass" : "warn"}
                label={`Completeness ${percent(bundle.metrics.fill_rate)}`}
              />
            </dd>
            <dd className="mt-2 text-meta text-[var(--fg-quiet)]">
              {bundle.certificate.summary.attributes_populated} of{" "}
              {bundle.certificate.summary.attributes_required} required
            </dd>
          </div>

          <div>
            <dt className="overline">Verifiability</dt>
            <dd className="mt-2 flex items-center gap-3">
              <span className="text-lg tabular-nums">
                {percent(bundle.metrics.verifiability)}
              </span>
              <Meter
                value={bundle.metrics.verifiability}
                tone="pass"
                label={`Verifiability ${percent(bundle.metrics.verifiability)}`}
              />
            </dd>
            <dd className="mt-2 text-meta text-[var(--fg-quiet)]">
              every published value traced to a span
            </dd>
          </div>

          <div>
            <dt className="overline">Consistency</dt>
            <dd className="mt-2 flex items-center gap-3">
              <span className="text-lg tabular-nums">{percent(quality.consistency)}</span>
              <Meter
                value={quality.consistency}
                tone={bundle.validation.failures > 0 ? "fail" : "pass"}
                label={`Consistency ${percent(quality.consistency)}`}
              />
            </dd>
            <dd className="mt-2 text-meta text-[var(--fg-quiet)]">
              {bundle.validation.checks} checks, {bundle.validation.failures} failed,{" "}
              {bundle.validation.warnings} warned
            </dd>
          </div>

          <div>
            <dt className="overline">Open work</dt>
            <dd className="mt-2 text-lg tabular-nums">
              {bundle.metrics.values_needing_review + bundle.metrics.gaps_required}
            </dd>
            <dd className="mt-2 text-meta text-[var(--fg-quiet)]">
              {bundle.metrics.values_needing_review} values below threshold,{" "}
              {bundle.metrics.gaps_required} required gaps
            </dd>
          </div>
        </dl>
      </header>

      <ReviewWorkspace
        sku={bundle.sku}
        rows={rows}
        page={page}
        document={dataset.document}
        threshold={dataset.policy.threshold}
      />
    </div>
  );
}
