import Link from "next/link";
import { notFound } from "next/navigation";

import { CrossSourcePanel } from "@/components/cross-source-panel";
import { ArrowIcon, Meter } from "@/components/primitives";
import { ReviewWorkspace } from "@/components/review-workspace";
import {
  attributeRows,
  getClassDefinition,
  getDocumentBundle,
  getSku,
  listSkus,
  loadDataset,
  variantGroupFor,
} from "@/lib/data";
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
  const definition = await getClassDefinition(bundle);
  const source = await getDocumentBundle(bundle);
  const rows = attributeRows(definition?.attributes ?? [], bundle.values, bundle.gaps);
  const className = definition?.name ?? bundle.class_code ?? "Unclassified";

  // Which series this part belongs to, if any. A reviewer checking a size-scoped specification
  // needs its siblings within reach — that value is correct for exactly one of them, and the
  // fastest way to sanity-check it is to look at the next size up.
  const series = variantGroupFor(bundle, await listSkus());
  const siblings = series?.members.filter((member) => member.bundle.sku !== bundle.sku) ?? [];

  // Every span in this corpus resolves to page 1; take the page a span actually names so
  // this keeps working when a multi-page PDF arrives.
  const citedPage =
    bundle.values.flatMap((value) => value.evidence).find((span) => span.page !== null)
      ?.page ?? 1;
  const page = source?.pages.find((candidate) => candidate.number === citedPage) ?? null;

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
            <li>{className}</li>
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
                {className.toLowerCase()}
              </span>
              <span aria-hidden className="text-[var(--fg-quiet)]">
                ·
              </span>
              <span className="mono text-[var(--fg-tertiary)]">
                {bundle.record.schema_version}
              </span>
            </p>

            {/*
              Series membership, stated where a reviewer will see it before they judge anything.

              It changes how a value should be read: on a variant, a specification is either from
              this part's own row of the ordering table or inherited from the series, and those carry
              different weight. Rendered as links because the useful next action is almost always to
              compare against the adjacent size.
            */}
            {series ? (
              <p className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-meta text-[var(--fg-quiet)]">
                {/*
                  Read off the group rather than from `parent_sku` directly. The group already
                  resolved which SKU the series was extracted from, and re-deriving it here would
                  duplicate the `undefined`-vs-null handling that `variantGroups` owns.
                */}
                <span className="pill pill-quiet">
                  {series.seriesSku === bundle.sku ? "Series reference" : "Variant"}
                </span>
                <span>
                  One of {series.members.length} in the{" "}
                  <span className="mono">{series.seriesSku}</span> series
                  {siblings.length > 0 ? ":" : ""}
                </span>
                {siblings.map((member) => (
                  <Link
                    key={member.bundle.sku}
                    href={`/review/${member.bundle.sku}`}
                    className="mono rounded-xs text-[var(--accent)] hover:underline"
                  >
                    {member.bundle.sku}
                  </Link>
                ))}
              </p>
            ) : null}
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
        document={source?.document ?? null}
        threshold={dataset.policy.threshold}
        live={dataset.meta.live}
      />

      {/*
        L4 sits below the workspace rather than inside it. The workspace is a per-value queue driven
        by confidence; a cross-source finding is a statement about the *record* against another
        document, and an unresolved conflict is its own kind of review task. Absent for most SKUs,
        because it needs a second source and most have one.
      */}
      {bundle.cross_source?.applicable ? (
        <div className="mt-16">
          <CrossSourcePanel view={bundle.cross_source} />
        </div>
      ) : null}
    </div>
  );
}
