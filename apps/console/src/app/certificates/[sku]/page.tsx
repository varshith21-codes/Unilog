import Link from "next/link";
import { notFound } from "next/navigation";

import { GeneratedCopyPanel } from "@/components/generated-copy";
import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  KeyValue,
  Meter,
  MethodPill,
  Overline,
  Panel,
  Quote,
  SectionHeading,
} from "@/components/primitives";
import { getClassDefinition, getSku, listSkus } from "@/lib/data";
import {
  ACTION_LABEL,
  GAP_REASON_LABEL,
  canonical,
  dateTime,
  humanise,
  percent,
  tableRef,
} from "@/lib/format";
import { composite } from "@/lib/types";
import type { CanonicalValue } from "@/lib/types";

export async function generateStaticParams() {
  const skus = await listSkus();
  return skus.map((bundle) => ({ sku: bundle.sku }));
}

export async function generateMetadata({ params }: { params: Promise<{ sku: string }> }) {
  const { sku } = await params;
  return { title: `Certificate ${sku}` };
}

export default async function CertificatePage({
  params,
}: {
  params: Promise<{ sku: string }>;
}) {
  const { sku } = await params;
  const bundle = await getSku(sku);
  if (!bundle) notFound();

  const definition = await getClassDefinition(bundle);
  const certificate = bundle.certificate;
  const summary = certificate.summary;
  const quality = summary.quality_index;
  const specByCode = new Map(
    (definition?.attributes ?? []).map((spec) => [spec.code, spec] as const),
  );

  const dimensions = [
    { label: "Completeness", value: quality.completeness, weight: quality.weights.completeness ?? 0 },
    { label: "Verifiability", value: quality.verifiability, weight: quality.weights.verifiability ?? 0 },
    { label: "Consistency", value: quality.consistency, weight: quality.weights.consistency ?? 0 },
    { label: "Richness", value: quality.richness, weight: quality.weights.richness ?? 0 },
  ];

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      {/* ---------------------------------------------------------------- header */}
      <header className="py-10">
        <nav aria-label="Breadcrumb" className="text-meta text-[var(--fg-quiet)]">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li>
              <Link href="/certificates" className="rounded-xs hover:text-[var(--fg)]">
                Certificates
              </Link>
            </li>
            <li aria-hidden>/</li>
            <li className="text-[var(--fg-secondary)]">{certificate.sku}</li>
          </ol>
        </nav>

        <div className="mt-5 flex flex-wrap items-end justify-between gap-x-10 gap-y-6">
          <div>
            <Overline>Enrichment certificate</Overline>
            <h1 className="mt-3 text-display font-medium tracking-[var(--tracking-display)]">
              {certificate.sku}
            </h1>
            <p className="mono mt-3 text-[var(--fg-tertiary)]">
              {certificate.certificate_id} · {dateTime(certificate.generated_at)}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Link href={`/review/${certificate.sku}`} className="btn btn-quiet">
              Review workspace
              <ArrowIcon />
            </Link>
          </div>
        </div>
      </header>

      {/* ---------------------------------------------------------------- quality */}
      <section className="grid gap-6 lg:grid-cols-12">
        <Panel raised className="p-7 lg:col-span-5">
          <Overline>Quality index</Overline>
          <p className="figure mt-3">{percent(composite(quality), 1)}</p>
          <p className="mt-2 text-meta text-[var(--fg-quiet)]">
            Weighted composite of the four dimensions below
          </p>

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
        </Panel>

        <div className="lg:col-span-7">
          <Panel className="p-7">
            <Overline>Contents</Overline>
            <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-3">
              <KeyValue label="Certified values">
                {summary.attributes_populated} of {summary.attributes_required} required
              </KeyValue>
              <KeyValue label="With verified evidence">
                {summary.attributes_with_evidence}
              </KeyValue>
              <KeyValue label="Inferred">{summary.attributes_inferred}</KeyValue>
              <KeyValue label="Auto-accepted">{summary.auto_accepted}</KeyValue>
              <KeyValue label="Queued for review">{summary.queued_for_review}</KeyValue>
              <KeyValue label="Gaps">
                {summary.gaps_required} required of {summary.gaps_total}
              </KeyValue>
              <KeyValue label="Schema" mono>
                {certificate.schema_version ?? "—"}
              </KeyValue>
              <KeyValue label="Pipeline" mono>
                {certificate.pipeline_version}
              </KeyValue>
              <KeyValue label="Tenant">{certificate.tenant_id}</KeyValue>
            </dl>
          </Panel>

          {/* Signature block. The recomputed-hash check is the whole point of the artifact,
              so it gets its own surface rather than a badge in a corner. */}
          <Panel
            className="mt-6 flex flex-wrap items-center justify-between gap-4 p-6"
          >
            <div className="flex items-start gap-3">
              {certificate.signature_verified ? (
                <CheckIcon className="mt-1 shrink-0 text-[var(--pass)]" />
              ) : (
                <AlertIcon className="mt-1 shrink-0 text-[var(--fail)]" />
              )}
              <div>
                <p className="text-sm font-medium">
                  {certificate.signature_verified
                    ? "Signature valid"
                    : "Signature does not match"}
                </p>
                <p className="mt-1 max-w-[52ch] text-meta text-[var(--fg-tertiary)]">
                  {certificate.signature_verified
                    ? "The content hash was recomputed on read and matches, so nothing has been altered since generation."
                    : "The recomputed content hash differs from the recorded signature. Treat this record as untrusted."}
                </p>
              </div>
            </div>
            <p className="mono break-all text-[var(--fg-quiet)]">
              {certificate.signature.replace(/^sha256:/, "").slice(0, 32)}…
            </p>
          </Panel>
        </div>
      </section>

      {/* ---------------------------------------------------------------- classification */}
      <section className="mt-16">
        <SectionHeading
          title="Classification"
          detail="Multi-target by design: the browse tree and the technical class answer different questions."
        />
        <div className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {certificate.classifications.map((entry) => (
            <Panel key={`${entry.scheme}-${entry.code}`} className="p-6">
              <div className="flex items-center justify-between gap-3">
                <Overline>{entry.scheme}</Overline>
                <span
                  className={
                    entry.status === "requires_human_confirmation"
                      ? "pill pill-warn"
                      : entry.status === "confirmed"
                        ? "pill pill-pass"
                        : "pill pill-quiet"
                  }
                >
                  {humanise(entry.status)}
                </span>
              </div>
              <p className="mono mt-3 text-body text-[var(--fg)]">{entry.code}</p>
              {entry.path.length > 0 ? (
                <p className="mt-2 text-meta text-[var(--fg-tertiary)]">
                  {entry.path.join(" / ")}
                </p>
              ) : null}
              <div className="mt-4 flex items-center gap-3">
                <Meter
                  value={entry.confidence}
                  tone="accent"
                  label={`Confidence ${percent(entry.confidence, 1)}`}
                />
                <span className="shrink-0 text-meta tabular-nums text-[var(--fg-secondary)]">
                  {percent(entry.confidence, 1)}
                </span>
              </div>
              {entry.method ? (
                <p className="mt-3 text-meta text-[var(--fg-quiet)]">via {entry.method}</p>
              ) : null}
            </Panel>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- attributes */}
      <section className="mt-16">
        <SectionHeading
          title="Certified values"
          detail={`${certificate.attributes.length} values, each with the verbatim span it was read from.`}
        />

        <div className="mt-6 flex flex-col gap-3">
          {certificate.attributes.map((entry) => {
            const spec = specByCode.get(entry.code);
            const span = entry.evidence[0];
            return (
              <Panel key={entry.code} className="p-6">
                <div className="grid gap-5 lg:grid-cols-12">
                  <div className="lg:col-span-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-body font-medium">{spec?.name ?? entry.code}</h3>
                      {spec?.compliance_claim ? (
                        <span className="pill pill-accent">Claim</span>
                      ) : null}
                    </div>
                    <p className="mono mt-1 text-[var(--fg-quiet)]">{entry.code}</p>

                    <p className="mt-4 text-lg font-medium">
                      {entry.value_display ??
                        canonical(entry.value_canonical as CanonicalValue)}
                    </p>
                    {entry.value_raw && entry.value_raw !== entry.value_display ? (
                      <p className="mono mt-1 text-[var(--fg-quiet)]">
                        raw: {entry.value_raw}
                      </p>
                    ) : null}

                    <div className="mt-4 flex flex-wrap items-center gap-2">
                      <MethodPill method={entry.method} />
                      {entry.model_tier ? (
                        <span className="pill pill-quiet">{entry.model_tier}</span>
                      ) : null}
                      <span className="text-meta tabular-nums text-[var(--fg-tertiary)]">
                        confidence {entry.confidence.toFixed(2)}
                      </span>
                    </div>
                  </div>

                  <div className="lg:col-span-8">
                    {span ? (
                      <>
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <Overline>Citation</Overline>
                          <span
                            className={span.verified ? "pill pill-pass" : "pill pill-fail"}
                          >
                            {span.verified ? <CheckIcon /> : <AlertIcon />}
                            {span.verified ? "Verified" : "Unverified"}
                          </span>
                        </div>
                        <Quote className="mt-3">{span.quote}</Quote>
                        <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-[var(--fg-quiet)]">
                          <span className="mono">{span.document}</span>
                          <span aria-hidden>·</span>
                          <span>page {span.page ?? "—"}</span>
                          {span.bbox ? (
                            <>
                              <span aria-hidden>·</span>
                              <span className="mono">
                                [{span.bbox.map((n) => n.toFixed(0)).join(", ")}]
                              </span>
                            </>
                          ) : null}
                          <span aria-hidden>·</span>
                          <span className="mono">{span.sha256}</span>
                        </p>
                      </>
                    ) : (
                      <p className="text-sm text-[var(--fg-tertiary)]">
                        Derived value; provenance is the value it was computed from.
                      </p>
                    )}

                    {entry.validations.length > 0 ? (
                      <ul className="mt-4 flex flex-wrap gap-2">
                        {entry.validations.map((check, index) => (
                          <li
                            key={`${entry.code}-${index}`}
                            className="mono rounded-xs bg-[var(--surface-inset)] px-2 py-1 text-[var(--fg-tertiary)]"
                          >
                            {check.layer} {check.rule} {check.verdict}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </div>
              </Panel>
            );
          })}
        </div>
      </section>

      {/* ---------------------------------------------------------------- gaps */}
      <section className="mt-16">
        <SectionHeading
          title="Gaps"
          detail="Recorded rather than hidden. A negative result with its search trail is auditable; silence is not."
        />

        <Panel className="scroll-x mt-6 overflow-hidden p-0">
          <table className="w-full min-w-[48rem] border-collapse text-sm">
            <caption className="sr-only">Attributes no source could establish</caption>
            <thead>
              <tr className="hairline-b bg-[var(--surface-sunken)]">
                <th scope="col" className="px-6 py-3 text-left font-medium">
                  Attribute
                </th>
                <th scope="col" className="px-6 py-3 text-left font-medium">
                  Reason
                </th>
                <th scope="col" className="px-6 py-3 text-left font-medium">
                  Recommended action
                </th>
                <th scope="col" className="px-6 py-3 text-right font-medium">
                  Required
                </th>
              </tr>
            </thead>
            <tbody>
              {certificate.gaps.map((gap) => (
                <tr key={gap.code} className="grid-row hairline-b last:border-b-0">
                  <th scope="row" className="px-6 py-3.5 text-left font-medium">
                    {specByCode.get(gap.code)?.name ?? gap.code}
                    <span className="mono ml-2 font-normal text-[var(--fg-quiet)]">
                      {gap.code}
                    </span>
                  </th>
                  <td className="px-6 py-3.5 text-[var(--fg-secondary)]">
                    {GAP_REASON_LABEL[gap.reason]}
                    {gap.detail ? (
                      <span className="block text-meta text-[var(--fg-quiet)]">
                        {gap.detail}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-6 py-3.5 text-[var(--fg-secondary)]">
                    {gap.recommended_action ? ACTION_LABEL[gap.recommended_action] : "—"}
                  </td>
                  <td className="px-6 py-3.5 text-right">
                    {gap.required ? (
                      <span className="pill pill-warn">Required</span>
                    ) : (
                      <span className="pill pill-quiet">Optional</span>
                    )}
                  </td>
                </tr>
              ))}
              {certificate.gaps.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-10 text-center text-[var(--fg-tertiary)]">
                    No gaps. Every attribute this class defines resolved to a value.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </Panel>
      </section>

      {/* ---------------------------------------------------------------- channels */}
      <section className="mt-16">
        <SectionHeading
          title="Channel readiness"
          detail="Preflight against each destination's own required set, run before anything is published."
        />

        <div className="mt-6 grid gap-5 md:grid-cols-3">
          {bundle.channels.map((channel) => (
            <Panel key={channel.name} className="p-6">
              <div className="flex items-center justify-between gap-3">
                <Overline>{channel.name.replace(/_/g, " ")}</Overline>
                <span className={channel.published ? "pill pill-pass" : "pill pill-warn"}>
                  {channel.published ? "Ready" : "Blocked"}
                </span>
              </div>

              <p className="mt-4 text-sm text-[var(--fg-secondary)]">
                {channel.value_count} value{channel.value_count === 1 ? "" : "s"} would
                publish
                {channel.withheld.length > 0
                  ? `, ${channel.withheld.length} withheld`
                  : ""}
                .
              </p>

              {channel.readiness.missing.length > 0 ? (
                <div className="mt-4">
                  <p className="text-meta text-[var(--fg-quiet)]">Missing</p>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {channel.readiness.missing.map((code) => (
                      <li key={code} className="pill pill-warn">
                        {specByCode.get(code)?.name ?? code}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {channel.readiness.not_publishable.length > 0 ? (
                <div className="mt-4">
                  <p className="text-meta text-[var(--fg-quiet)]">Queued, not publishable</p>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {channel.readiness.not_publishable.map((code) => (
                      <li key={code} className="pill pill-quiet">
                        {specByCode.get(code)?.name ?? code}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {channel.title ? (
                <div className="mt-4">
                  <p className="text-meta text-[var(--fg-quiet)]">Rendered title</p>
                  <p className="mt-1.5 text-sm text-[var(--fg-secondary)]">{channel.title}</p>
                </div>
              ) : null}
            </Panel>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- generated copy */}
      {bundle.copy ? (
        <div className="mt-16">
          <GeneratedCopyPanel copy={bundle.copy} />
        </div>
      ) : null}

      {/* ---------------------------------------------------------------- locator legend */}
      <section className="mt-16">
        <Panel className="p-7">
          <Overline>Reading a citation</Overline>
          <p className="mt-3 max-w-[70ch] text-sm text-[var(--fg-secondary)]">
            Each citation names the content-addressed document, the 1-indexed page, and the
            region on that page as <span className="mono">[x0, y0, x1, y1]</span> in PDF
            points with the origin at top-left. Table citations add a cell reference such as{" "}
            <span className="mono">t1:r3:c2</span> — {tableRef("t1:r3:c2")} — so the exact row
            is recoverable even if the page is re-rendered at another size.
          </p>
        </Panel>
      </section>
    </div>
  );
}
