import { PipelineReplay } from "@/components/pipeline-replay";
import { EmptyState, PageHeader, Panel } from "@/components/primitives";
import { RecordedRunCombobox } from "@/components/recorded-run-combobox";
import {
  getDocument,
  listSkus,
  loadDataset,
} from "@/lib/data";
import { dateTime } from "@/lib/format";
import { modelStageShare, pipelineStages } from "@/lib/stages";

export const metadata = { title: "Process" };

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
      <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
        <PageHeader
          eyebrow="Process"
          title="Recorded process replay"
          detail="Inspect persisted process output without starting a live execution. This view only replays work that has already been recorded."
          meta={<span className="pill pill-quiet">Replay only</span>}
        />
        <Panel>
          {/*
            `unmeasured`: this page reads persisted output, so an absent run means nothing was
            recorded — not that a run happened and did nothing.
          */}
          <EmptyState
            kind="unmeasured"
            title="No recorded run to replay"
            detail={
              <>
                This page reads persisted process output rather than running anything, so it has
                nothing to show until a run has been saved. Produce one with{" "}
                <span className="mono">
                  python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075
                  --include-optional --save-session
                </span>
                .
              </>
            }
          />
        </Panel>
      </div>
    );
  }

  // An unknown or absent `?sku=` falls back to the first rather than 404ing. This is a demo surface
  // opened from a bookmark or typed live; a blank page because a SKU was renamed is a worse failure
  // than quietly showing a real run.
  const bundle = skus.find((entry) => entry.sku === requested) ?? skus[0]!;
  const document = await getDocument(bundle);
  const stages = pipelineStages(bundle, document, dataset.policy, null);
  const share = modelStageShare(stages);

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Process"
        title="What the recorded process did"
        detail={
          <>
            A replay of {stages.length} persisted stages from supplier document to signed,
            publishable record. This view never starts a live execution; {share.model} stages used a
            model when the run was recorded.
          </>
        }
        meta={
          <>
            <span className="pill pill-quiet">Recorded replay</span>
            <span className="mono">{bundle.sku}</span>
          </>
        }
      />

      {skus.length > 1 ? (
        <Panel className="mb-[var(--spacing-section)] p-6">
          <RecordedRunCombobox
            options={skus.map((entry) => entry.sku)}
            selectedSku={bundle.sku}
          />
        </Panel>
      ) : null}

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
