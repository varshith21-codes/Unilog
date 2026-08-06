import type { Metadata } from "next";

import { CohortPanel } from "@/components/cohort-panel";
import { EmptyState, Panel } from "@/components/primitives";
import { loadCohort } from "@/lib/data";

export const metadata: Metadata = {
  title: "Quality Index",
  description: "Before and after: what enrichment changed about the catalogue.",
};

/**
 * The Quality Index page: the before/after cohort.
 *
 * Every other screen in this console answers "is this value right?". This one answers the question
 * a buyer asks, which is "what is my catalogue worth now?" — and answers it against the state the
 * catalogue was in beforehand, scored by the same index through the same code.
 *
 * Served from the API rather than a checked-in fixture, because unlike the dataset there is no
 * honest way to seed it: a hand-written before/after comparison is a marketing claim, not a
 * measurement. If the study has not been run, the page says so and explains how.
 */
export default async function QualityPage() {
  // No DataSourceBanner here: it lives in the layout, because an unreachable API affects every
  // page rather than this one. When it is down, `loadCohort` reports unavailable and the empty
  // state below explains what to run — the two messages do not contradict each other.
  const study = await loadCohort();

  return (
    <div className="flex flex-col gap-10">
      <header>
        <h1 className="text-2xl font-medium tracking-[var(--tracking-heading)]">Quality Index</h1>
        <p className="mt-2 max-w-[70ch] text-body text-[var(--fg-secondary)]">
          The catalogue before enrichment and after, scored on the same four dimensions by the
          same code. A control arm of untouched SKUs sits alongside, not to prove enrichment
          worked, but to prove the measurement itself did not move between the two readings.
        </p>
      </header>

      {study.available ? (
        <CohortPanel study={study} />
      ) : (
        <Panel>
          <EmptyState
            title="No cohort study has been run"
            detail={
              study.reason ??
              "The study compares an ERP item master against enriched output, so it needs both."
            }
          />
          <div className="hairline-t px-7 py-6">
            <p className="text-meta text-[var(--fg-quiet)]">
              Build the &ldquo;before&rdquo; state from a supplier flat file, then compare:
            </p>
            <pre className="mono mt-3 overflow-x-auto text-meta leading-relaxed text-[var(--fg-secondary)]">
              {`python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv \\
    --supplier milwaukee --map "WT/EA (lb)=each_weight" --out data/ingest

python scripts/run_cohort.py --write`}
            </pre>
          </div>
        </Panel>
      )}
    </div>
  );
}
