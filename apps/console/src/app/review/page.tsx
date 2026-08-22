import Link from "next/link";

import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  EmptyState,
  Overline,
  PageHeader,
  Panel,
  Section,
  SectionHeading,
  Stat,
  StatBand,
  StatusPill,
} from "@/components/primitives";
import {
  attributeRows,
  listSkus,
  loadDataset,
  reviewOrder,
  reviewRows,
  unresolvedConflicts,
} from "@/lib/data";
import { GAP_REASON_LABEL, canonical, count, percent, score } from "@/lib/format";

export const metadata = { title: "Resolve" };

export default async function ReviewIndexPage() {
  const dataset = await loadDataset();
  const skus = reviewOrder(await listSkus());

  // Each SKU is scored against *its own* class, not one shared definition. A catalog spanning
  // ball valves and gate valves has different required attributes per class, so joining
  // against a single class here would invent gaps for attributes the class never declared.
  const groups = skus.map((bundle) => ({
    bundle,
    open: reviewRows(
      attributeRows(
        bundle.class_code
          ? (dataset.class_definitions[bundle.class_code]?.attributes ?? [])
          : [],
        bundle.values,
        bundle.gaps,
      ),
    ),
  }));

  const openTotal = groups.reduce((sum, group) => sum + group.open.length, 0);
  const valuesOpen = groups.reduce(
    (sum, group) => sum + group.open.filter((row) => row.value !== null).length,
    0,
  );
  const gapsOpen = openTotal - valuesOpen;

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
            establish. Ordered so blocking failures come first.
          </>
        }
        meta={
          <>
            <span>{count(openTotal)} open items</span>
            <span>{groups.length} SKUs evaluated</span>
          </>
        }
      />

      <StatBand>
        <Stat
          label="Open items"
          value={count(openTotal)}
          hint={`across ${groups.length} SKUs`}
          tone={openTotal > 0 ? "warn" : "pass"}
        />
        <Stat label="Below threshold" value={count(valuesOpen)} hint="values to confirm" />
        <Stat label="Required gaps" value={count(gapsOpen)} hint="no value could be read" />
        <Stat
          label="Threshold"
          /*
            An unset threshold is an em-dash, not a zero. A `0.000` threshold would mean every value
            auto-accepts, which is the opposite of what a missing calibration means.
          */
          value={dataset.policy.threshold === null ? "—" : dataset.policy.threshold.toFixed(3)}
          hint={`${percent(dataset.policy.epsilon)} error budget at ${percent(
            dataset.policy.confidence_level,
          )} confidence`}
        />
      </StatBand>

      <div className="mt-[var(--spacing-section)] flex flex-col gap-6">
        {groups.map(({ bundle, open }) => (
          <Panel key={bundle.sku} className="overflow-hidden p-0">
            <div className="hairline-b flex flex-wrap items-center justify-between gap-4 bg-[var(--surface-sunken)] px-6 py-4">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 className="text-lg font-medium">
                  <Link
                    href={`/review/${bundle.sku}`}
                    className="rounded-xs transition-colors duration-[var(--duration-fast)] hover:text-[var(--accent)]"
                  >
                    {bundle.sku}
                  </Link>
                </h2>
                <p className="text-meta text-[var(--fg-tertiary)]">
                  {percent(bundle.metrics.fill_rate)} complete ·{" "}
                  {bundle.validation.failures > 0 ? (
                    <span className="text-[var(--fail)]">
                      {bundle.validation.failures} blocking
                    </span>
                  ) : bundle.validation.warnings > 0 ? (
                    <span className="text-[var(--warn)]">
                      {bundle.validation.warnings} warning
                      {bundle.validation.warnings === 1 ? "" : "s"}
                    </span>
                  ) : (
                    <span className="text-[var(--pass)]">checks clean</span>
                  )}
                </p>
                {/*
                  L4 gets its own badge rather than folding into the failure count. An unresolved
                  conflict is not one value the pipeline is unsure about — it is two contradictory
                  answers it refused to choose between, and it sorts to the top of this queue.
                */}
                {unresolvedConflicts(bundle) > 0 ? (
                  <span className="pill pill-fail">
                    <AlertIcon />
                    {unresolvedConflicts(bundle)} source conflict
                    {unresolvedConflicts(bundle) === 1 ? "" : "s"}
                  </span>
                ) : bundle.cross_source?.applicable ? (
                  <span className="pill pill-pass">
                    <CheckIcon />
                    {bundle.cross_source.corroborated} corroborated
                  </span>
                ) : null}
              </div>

              <Link href={`/review/${bundle.sku}`} className="btn btn-quiet h-7">
                Resolve SKU
                <ArrowIcon />
              </Link>
            </div>

            {open.length === 0 ? (
              /*
                `empty`, deliberately. This SKU was measured and came back clean — the reviewer has
                nothing to do, which is a result rather than an absence of one.
              */
              <EmptyState
                title="Fully accepted"
                detail="Every attribute this class requires cleared the threshold with verified evidence."
              />
            ) : (
              <ul>
                {open.map((row) => (
                  <li
                    key={row.spec.code}
                    className="grid-row group hairline-b grid grid-cols-[1fr_auto] items-center gap-x-5 gap-y-1.5 px-6 py-3.5 last:border-b-0 sm:grid-cols-[16rem_1fr_auto]"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Link
                        href={`/review/${bundle.sku}`}
                        className="truncate rounded-xs text-sm font-medium transition-colors duration-[var(--duration-fast)] group-hover:text-[var(--accent)] hover:text-[var(--accent)]"
                      >
                        {row.spec.name}
                      </Link>
                      {row.spec.compliance_claim ? (
                        <span className="pill pill-accent shrink-0">Claim</span>
                      ) : null}
                    </div>

                    <p className="col-span-2 min-w-0 truncate text-meta text-[var(--fg-tertiary)] sm:col-span-1">
                      {row.value ? (
                        <>
                          {row.value.value_display ?? canonical(row.value.value_canonical)}
                          <span className="text-[var(--fg-quiet)]">
                            {" "}
                            · score {score(row.value.score)}
                            {row.value.decision ? ` · ${row.value.decision.detail}` : ""}
                          </span>
                        </>
                      ) : row.gap ? (
                        <>
                          {GAP_REASON_LABEL[row.gap.reason]}
                          {row.gap.detail ? (
                            <span className="text-[var(--fg-quiet)]"> · {row.gap.detail}</span>
                          ) : null}
                        </>
                      ) : (
                        "No candidate produced"
                      )}
                    </p>

                    <div className="row-start-1 justify-self-end sm:row-start-auto">
                      {row.value ? (
                        <StatusPill status={row.value.status} />
                      ) : (
                        <span className="pill pill-warn">
                          <AlertIcon />
                          Gap
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        ))}
      </div>

      {groups.length === 0 ? (
        <Panel className="mt-[var(--spacing-section)]">
          {/*
            `unmeasured`: no SKUs loaded means no run has been read, not a catalogue that was
            examined and found to contain nothing.
          */}
          <EmptyState
            kind="unmeasured"
            title="No SKUs loaded"
            detail={
              <>
                Nothing has been read from a recorded process run. Generate console data with{" "}
                <span className="mono">python scripts/export_console_fixture.py</span>.
              </>
            }
          />
        </Panel>
      ) : null}

      {/*
        The legend, at the foot of the page rather than mid-column. It explains the two kinds of row
        above it and is read once; giving it the same weight as the queue itself would put reference
        material between a reviewer and their work.
      */}
      <Section rhythm="lg">
        <SectionHeading
          title="Why an attribute lands here"
          detail="Two distinct reasons, worked differently."
        />
        <div className="mt-7 grid gap-6 md:grid-cols-2">
          <Panel className="p-6">
            <Overline>Below threshold</Overline>
            <p className="mt-3 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
              A value exists and its quote verified, but the calibrated score sat under the
              acceptance threshold. The reviewer confirms or corrects it, and that decision is
              what later trains the calibrator.
            </p>
          </Panel>
          <Panel className="p-6">
            <Overline>Required gap</Overline>
            <p className="mt-3 max-w-[62ch] text-sm text-[var(--fg-secondary)]">
              No source stated the value. Nothing to confirm, so the work is to obtain it —
              usually a supplier request, sometimes a better document. The gap records every
              source already searched so the negative result stays auditable.
            </p>
          </Panel>
        </div>
      </Section>
    </div>
  );
}
