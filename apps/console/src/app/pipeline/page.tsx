import Link from "next/link";

import { PipelineReplay } from "@/components/pipeline-replay";
import { EmptyState, Overline, Panel, SectionHeading } from "@/components/primitives";
import {
  getDocument,
  listSkus,
  loadDataset,
  variantGroupFor,
} from "@/lib/data";
import { dateTime } from "@/lib/format";
import { modelStageShare, pipelineStages } from "@/lib/stages";

export const metadata = { title: "Pipeline" };

/**
 * The demo's second beat, on its own address.
 *
 * The blueprint's script drops three artifacts on screen, clicks Run, and then narrates stage cards
 * lighting up *before* opening any record. That ordering is why this is a route rather than a panel
 * on the review workspace: the stages have to be showable before a record is on screen, and a
 * rehearsed demo needs a URL it can open cold.
 *
 * There is no Run button and no upload, on purpose. Both would need a live model call, which the
 * blueprint's own demo hygiene section rules out — cache the scripted path, assume the WiFi fails.
 * What this page does instead is replay a run that already happened, and say so.
 */
export default async function PipelinePage({
  searchParams,
}: {
  searchParams: Promise<{ sku?: string }>;
}) {
  const { sku: requested } = await searchParams;
  const dataset = await loadDataset();
  const skus = await listSkus();

  if (skus.length === 0) {
    return (
      <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] py-16">
        <EmptyState
          title="No recorded run to replay"
          detail={
            <>
              This page reads persisted pipeline output rather than running anything, so it has
              nothing to show until a run has been saved. Produce one with{" "}
              <span className="mono">
                python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075
                --include-optional --save-session
              </span>
              .
            </>
          }
        />
      </div>
    );
  }

  // An unknown or absent `?sku=` falls back to the first rather than 404ing. This is a demo surface
  // opened from a bookmark or typed live; a blank page because a SKU was renamed is a worse failure
  // than quietly showing a real run.
  const bundle = skus.find((entry) => entry.sku === requested) ?? skus[0]!;
  const document = await getDocument(bundle);
  const series = variantGroupFor(bundle, skus);
  const stages = pipelineStages(bundle, document, dataset.policy, series);
  const share = modelStageShare(stages);

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <header className="py-[var(--spacing-section-lg)]">
        {/*
          Counted, not asserted. The stage list is shorter when a bundle's document is missing and
          longer for an exploded series, so a hardcoded number here would be wrong on most runs.
        */}
        <SectionHeading
          title="What the pipeline did"
          detail={
            <>
              {stages.length} stages from a supplier document to a signed, publishable record.{" "}
              {share.model} of them call a model.
            </>
          }
        />

        {skus.length > 1 ? (
          <Panel className="mt-6 p-6">
            <Overline>Recorded runs</Overline>
            <ul className="mt-3 flex flex-wrap gap-2">
              {skus.map((entry) => (
                <li key={entry.sku}>
                  <Link
                    href={`/pipeline?sku=${encodeURIComponent(entry.sku)}`}
                    aria-current={entry.sku === bundle.sku ? "page" : undefined}
                    className={
                      entry.sku === bundle.sku
                        ? "pill pill-accent mono"
                        : "pill pill-quiet mono hover:text-[var(--fg)]"
                    }
                  >
                    {entry.sku}
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </header>

      <PipelineReplay
        stages={stages}
        sku={bundle.sku}
        recordedAt={dateTime(bundle.certificate.generated_at)}
        totalLatencyMs={bundle.cost?.latency_ms ?? null}
        live={dataset.meta.live}
      />
    </div>
  );
}
