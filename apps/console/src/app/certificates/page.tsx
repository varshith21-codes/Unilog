import Link from "next/link";

import {
  ArrowIcon,
  CheckIcon,
  Meter,
  Overline,
  Panel,
  Stat,
} from "@/components/primitives";
import { listSkus, portfolioTotals } from "@/lib/data";
import { composite } from "@/lib/types";
import { count, dateOnly, percent, shortHash } from "@/lib/format";

export const metadata = { title: "Certificates" };

export default async function CertificatesPage() {
  const skus = await listSkus();
  const totals = portfolioTotals(skus);
  const allVerified = skus.every((bundle) => bundle.certificate.signature_verified);

  const rows = [...skus].sort(
    (a, b) =>
      composite(b.certificate.summary.quality_index) -
      composite(a.certificate.summary.quality_index),
  );

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <header className="py-[var(--spacing-section-lg)]">
        <Overline>Audit artifacts</Overline>
        <h1 className="mt-4 max-w-[24ch] text-display font-medium tracking-[var(--tracking-display)]">
          Enrichment certificates
        </h1>
        <p className="mt-5 max-w-[62ch] text-body text-[var(--fg-secondary)]">
          One signed record per SKU listing every certified value, the span it came from, and
          every gap that remains. Only publishable values are certified — candidates and
          queued values are counted but never presented as established facts.
        </p>
      </header>

      <section className="hairline-t hairline-b grid grid-cols-2 gap-x-6 gap-y-8 py-8 md:grid-cols-4">
        <Stat
          label="Certificates"
          value={count(rows.length)}
          hint="one per SKU in this run"
        />
        <Stat
          label="Signatures"
          value={allVerified ? "All valid" : "Check failed"}
          hint="content hash recomputed on read"
          tone={allVerified ? "pass" : "fail"}
        />
        <Stat
          label="Certified values"
          value={count(totals.valuesPublishable)}
          hint={`of ${count(totals.valuesTotal)} extracted`}
        />
        <Stat
          label="Mean quality"
          value={percent(totals.meanComposite, 1)}
          hint="weighted composite"
        />
      </section>

      <Panel className="scroll-x mt-[var(--spacing-section)] overflow-hidden p-0">
        <table className="w-full min-w-[54rem] border-collapse text-sm">
          <caption className="sr-only">Certificates ordered by quality index</caption>
          <thead>
            <tr className="hairline-b bg-[var(--surface-sunken)]">
              <th scope="col" className="px-6 py-3 text-left font-medium">
                SKU
              </th>
              <th scope="col" className="px-6 py-3 text-left font-medium">
                Certificate
              </th>
              <th scope="col" className="w-40 px-6 py-3 text-left font-medium">
                Quality
              </th>
              <th scope="col" className="px-6 py-3 text-right font-medium">
                Certified
              </th>
              <th scope="col" className="px-6 py-3 text-right font-medium">
                Gaps
              </th>
              <th scope="col" className="px-6 py-3 text-left font-medium">
                Signature
              </th>
              <th scope="col" className="px-6 py-3 text-right font-medium">
                <span className="sr-only">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((bundle) => {
              const summary = bundle.certificate.summary;
              const value = composite(summary.quality_index);
              return (
                <tr key={bundle.sku} className="grid-row hairline-b last:border-b-0">
                  <th scope="row" className="px-6 py-4 text-left font-medium">
                    <Link
                      href={`/certificates/${bundle.sku}`}
                      className="rounded-xs transition-colors duration-150 hover:text-[var(--accent)]"
                    >
                      {bundle.sku}
                    </Link>
                  </th>
                  <td className="mono px-6 py-4 text-[var(--fg-tertiary)]">
                    {bundle.certificate.certificate_id}
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <Meter
                        value={value}
                        tone={value >= 0.8 ? "pass" : "accent"}
                        label={`Quality ${percent(value, 1)}`}
                      />
                      <span className="w-11 shrink-0 text-right tabular-nums text-[var(--fg-secondary)]">
                        {percent(value, 1)}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right tabular-nums">
                    {summary.attributes_populated}
                    <span className="text-[var(--fg-quiet)]">
                      /{summary.attributes_required}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right tabular-nums">
                    {summary.gaps_required > 0 ? (
                      <span className="text-[var(--warn)]">{summary.gaps_required}</span>
                    ) : (
                      <span className="text-[var(--fg-quiet)]">0</span>
                    )}
                    <span className="text-[var(--fg-quiet)]">/{summary.gaps_total}</span>
                  </td>
                  <td className="px-6 py-4">
                    {bundle.certificate.signature_verified ? (
                      <span className="pill pill-pass">
                        <CheckIcon />
                        {shortHash(bundle.certificate.signature, 10)}…
                      </span>
                    ) : (
                      <span className="pill pill-fail">Invalid</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <Link
                      href={`/certificates/${bundle.sku}`}
                      className="btn btn-bare h-7 px-2"
                      aria-label={`Open certificate for ${bundle.sku}`}
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
        Generated {dateOnly(rows[0]?.certificate.generated_at ?? new Date().toISOString())} ·
        pipeline {rows[0]?.certificate.pipeline_version ?? "unknown"}
      </p>
    </div>
  );
}
