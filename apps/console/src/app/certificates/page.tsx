import Link from "next/link";

import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  EmptyState,
  Meter,
  Overline,
  Panel,
  Section,
  Stat,
  StatBand,
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

      <StatBand>
        <Stat label="Certificates" value={count(rows.length)} hint="one per SKU in this run" />
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
      </StatBand>

      <Section divider={false}>
        {rows.length === 0 ? (
          <Panel>
            {/*
              The table used to render its header over an empty body, and the footer below it read
              "Generated <today> · pipeline unknown" — a date invented on the spot for a run that
              never happened. Both are gone.
            */}
            <EmptyState
              title="No certificates"
              detail={
                <>
                  A certificate is written when a SKU completes a pipeline run, so there is nothing
                  to sign until one has. Produce a run with{" "}
                  <span className="mono">python scripts/run_pipeline.py --save-session</span>.
                </>
              }
            />
          </Panel>
        ) : (
          <>
            <Panel className="scroll-x overflow-hidden p-0">
              <table className="w-full min-w-[56rem] border-collapse text-sm">
                <caption className="sr-only">Certificates ordered by quality index</caption>
                {/*
                  The certificate id is monospace and fixed-length, and the four columns after it
                  have known ceilings — so only the SKU column needs to flex. Declaring the rest
                  keeps the signature pills in one vertical line, which is the column a reader
                  actually scans down.
                */}
                <colgroup>
                  <col />
                  <col className="w-[15rem]" />
                  <col className="w-[12rem]" />
                  <col className="w-[6.5rem]" />
                  <col className="w-[6rem]" />
                  <col className="w-[9.5rem]" />
                  <col className="w-[3.5rem]" />
                </colgroup>
                <thead className="table-head">
                  <tr>
                    <th scope="col" className="px-6 py-2.5 text-left">
                      SKU
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-left">
                      Certificate
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-left">
                      Quality
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-right">
                      Certified
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-right">
                      Gaps
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-left">
                      Signature
                    </th>
                    <th scope="col" className="px-6 py-2.5 text-right">
                      <span className="sr-only">Open</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((bundle) => {
                    const summary = bundle.certificate.summary;
                    const value = composite(summary.quality_index);
                    return (
                      <tr key={bundle.sku} className="grid-row group hairline-b last:border-b-0">
                        <th scope="row" className="px-6 py-4 text-left font-medium">
                          <Link
                            href={`/certificates/${bundle.sku}`}
                            className="rounded-xs transition-colors duration-[var(--duration-fast)] group-hover:text-[var(--accent)] hover:text-[var(--accent)]"
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
                          <span className="figure-zero">/{summary.attributes_required}</span>
                        </td>
                        <td className="px-6 py-4 text-right tabular-nums">
                          {summary.gaps_required > 0 ? (
                            <span className="text-[var(--warn)]">{summary.gaps_required}</span>
                          ) : (
                            <span className="figure-zero">0</span>
                          )}
                          <span className="figure-zero">/{summary.gaps_total}</span>
                        </td>
                        <td className="px-6 py-4">
                          {bundle.certificate.signature_verified ? (
                            <span className="pill pill-pass mono">
                              <CheckIcon />
                              {shortHash(bundle.certificate.signature, 10)}…
                            </span>
                          ) : (
                            <span className="pill pill-fail">
                              <AlertIcon />
                              Invalid
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4 text-right">
                          <Link
                            href={`/certificates/${bundle.sku}`}
                            className="btn btn-bare h-7 px-2 text-[var(--fg-quiet)]
                                       transition-colors duration-[var(--duration-fast)]
                                       group-hover:text-[var(--fg)]"
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
              Generated {dateOnly(rows[0]!.certificate.generated_at)} · pipeline{" "}
              {rows[0]!.certificate.pipeline_version}
            </p>
          </>
        )}
      </Section>
    </div>
  );
}
