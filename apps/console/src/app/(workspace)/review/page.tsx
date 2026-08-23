import { ReviewQueue, type ReviewQueueItem } from "@/components/review-queue";
import {
  AlertIcon,
  EmptyState,
  Overline,
  PageHeader,
  Panel,
  Section,
  SectionHeading,
  Stat,
  StatBand,
} from "@/components/primitives";
import {
  attributeRows,
  classificationAbstention,
  listSkus,
  loadDataset,
  needsClassification,
  reviewOrder,
  reviewRows,
  unresolvedConflicts,
} from "@/lib/data";
import { GAP_REASON_LABEL, canonical, count, percent, score } from "@/lib/format";
import { pageParam } from "@/lib/paginate";
import { reviewHref } from "@/lib/sku";

export const metadata = { title: "Resolve" };

export default async function ReviewIndexPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string | string[] }>;
}) {
  const dataset = await loadDataset();
  const skus = reviewOrder(await listSkus());
  const params = await searchParams;

  // Each SKU is scored against its own class. The queue receives only the scalar fields it needs;
  // evidence spans, documents, schemas, and the complete SkuBundle remain on the server.
  const groups = skus.map((bundle, publicationRiskRank) => {
    const classDefinition = bundle.class_code
      ? dataset.class_definitions[bundle.class_code]
      : undefined;
    const open = reviewRows(
      attributeRows(classDefinition?.attributes ?? [], bundle.values, bundle.gaps),
    );
    const valueOpenCount = open.filter((row) => row.value !== null).length;
    const belowThresholdCount = open.filter(
      (row) => row.value?.decision?.reason_code === "below_threshold",
    ).length;
    const unclassified = needsClassification(bundle);

    const item: ReviewQueueItem = {
      sku: bundle.sku,
      href: reviewHref(bundle.sku),
      brand: bundle.record.brand,
      mpn: bundle.record.mpn,
      normalizedMpn: bundle.record.mpn_normalized,
      gtin: bundle.record.gtin,
      supplierId: bundle.record.supplier_id,
      classCode: bundle.class_code,
      className: classDefinition?.name ?? null,
      state: unclassified ? "unclassified" : open.length === 0 ? "fully-accepted" : "open",
      abstention: classificationAbstention(bundle),
      completeness: unclassified ? null : bundle.metrics.fill_rate,
      validationFailures: bundle.validation.failures,
      validationWarnings: bundle.validation.warnings,
      sourceConflictCount: unresolvedConflicts(bundle),
      crossSourceApplicable: bundle.cross_source?.applicable ?? false,
      corroboratedCount: bundle.cross_source?.corroborated ?? 0,
      openItemCount: open.length,
      valueOpenCount,
      belowThresholdCount,
      requiredGapCount: open.length - valueOpenCount,
      publicationRiskRank,
      rows: open.map((row) => {
        if (row.value) {
          return {
            code: row.spec.code,
            name: row.spec.name,
            complianceClaim: row.spec.compliance_claim,
            kind: "value" as const,
            primary: row.value.value_display ?? canonical(row.value.value_canonical),
            secondary: `score ${score(row.value.score)}${
              row.value.decision ? ` · ${row.value.decision.detail}` : ""
            }`,
            status: row.value.status,
          };
        }

        return {
          code: row.spec.code,
          name: row.spec.name,
          complianceClaim: row.spec.compliance_claim,
          kind: "gap" as const,
          primary: row.gap ? GAP_REASON_LABEL[row.gap.reason] : "No candidate produced",
          secondary: row.gap?.detail ?? null,
          status: null,
        };
      }),
    };

    return { bundle, open, item };
  });

  // These metrics deliberately describe the entire catalogue. The client toolbar reports its own
  // matching count so narrowing the queue never changes the page-level operational denominator.
  const openTotal = groups.reduce((sum, group) => sum + group.open.length, 0);
  const valuesOpen = groups.reduce(
    (sum, group) => sum + group.open.filter((row) => row.value !== null).length,
    0,
  );
  const gapsOpen = openTotal - valuesOpen;
  const unclassified = skus.filter(needsClassification).length;
  // Split out because the two need different work and the combined figure hid that: a SKU with no
  // matching class needs a class definition, a SKU with two matching classes needs a decision.
  const awaitingAdjudication = skus.filter(
    (bundle) => classificationAbstention(bundle)?.kind === "ambiguous",
  ).length;
  const noClassMatched = unclassified - awaitingAdjudication;

  return (
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <PageHeader
        eyebrow="Resolve"
        title={
          openTotal === 0 ? "Nothing waiting on resolution" : "Attributes needing a decision"
        }
        detail={
          <>
            Values below the acceptance threshold, plus required attributes no source could
            establish. Defaults to publication risk, with blocking failures first.
          </>
        }
        meta={
          <>
            <span>{count(openTotal)} open items</span>
            <span>{count(groups.length)} SKUs in the catalogue</span>
            {unclassified > 0 ? (
              <span className="pill pill-warn">
                <AlertIcon />
                {count(unclassified)} unclassified
              </span>
            ) : null}
          </>
        }
      />

      <StatBand>
        <Stat
          label="Open items"
          value={count(openTotal)}
          hint={`across ${count(groups.length - unclassified)} classified SKUs`}
          tone={openTotal > 0 ? "warn" : "pass"}
        />
        <Stat label="Below threshold" value={count(valuesOpen)} hint="values to confirm" />
        <Stat label="Required gaps" value={count(gapsOpen)} hint="no value could be read" />
        {unclassified > 0 ? (
          <Stat
            label="Unclassified"
            value={count(unclassified)}
            hint={
              awaitingAdjudication > 0
                ? `${count(noClassMatched)} no class matched · ${count(
                    awaitingAdjudication,
                  )} awaiting adjudication`
                : "no class matched; nothing evaluated"
            }
            tone="warn"
          />
        ) : (
          <Stat
            label="Threshold"
            value={dataset.policy.threshold === null ? "—" : dataset.policy.threshold.toFixed(3)}
            hint={`${percent(dataset.policy.epsilon)} error budget at ${percent(
              dataset.policy.confidence_level,
            )} confidence`}
          />
        )}
      </StatBand>

      {groups.length > 0 ? (
        <ReviewQueue items={groups.map((group) => group.item)} initialPage={pageParam(params.page)} />
      ) : (
        <Panel className="mt-[var(--spacing-section)]">
          <EmptyState
            kind="unmeasured"
            title="No SKUs loaded"
            detail={
              <>
                Nothing has been read from a recorded process run. Generate console data for one
                SKU from a datasheet with{" "}
                <span className="mono">
                  python scripts/run_pipeline.py &lt;source&gt; --sku &lt;sku&gt; --save-session
                </span>
                , or for a whole item master with{" "}
                <span className="mono">
                  python scripts/export_console_catalogue.py &lt;input.csv&gt;
                </span>
                .
              </>
            }
          />
        </Panel>
      )}

      <Section rhythm="lg">
        <SectionHeading
          title="Why an attribute lands here"
          detail="Three distinct reasons, worked differently."
        />
        <div className="mt-7 grid gap-6 md:grid-cols-3">
          <Panel className="p-6">
            <Overline>Below threshold</Overline>
            <p className="mt-3 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
              A value exists and its quote verified, but the calibrated score sat under the
              acceptance threshold. The reviewer confirms or corrects it, and that decision is what
              later trains the calibrator.
            </p>
          </Panel>
          <Panel className="p-6">
            <Overline>Required gap</Overline>
            <p className="mt-3 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
              No source stated the value. Nothing to confirm, so the work is to obtain it — usually
              a supplier request, sometimes a better document. The gap records every source already
              searched so the negative result stays auditable.
            </p>
          </Panel>
          <Panel className="p-6">
            <Overline>Not classified</Overline>
            <p className="mt-3 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
              Neither of the above, because nothing was asked. Retrieval matched no class in the
              schema and abstained instead of guessing, so this record has no required attributes
              to be missing and no values to score. It is not clean — it is unexamined, and the work
              is a class definition rather than a reviewer decision.
            </p>
          </Panel>
        </div>
      </Section>
    </div>
  );
}
