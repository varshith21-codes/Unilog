import type { Metadata } from "next";

import { CohortPanel } from "@/components/cohort-panel";
import { EmptyState, Overline, Panel } from "@/components/primitives";
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
    /*
     * Container and gutter, which this route was missing entirely.
     *
     * Every other screen wraps itself in the shell container; this one did not, so its heading and
     * its widest table ran flush to the viewport edge at every breakpoint. The shell is applied
     * here rather than in the layout because `main` also carries full-bleed children — the data
     * source banner sets its own container for the same reason.
     */
    <div className="mx-auto max-w-[var(--container-shell)] px-[var(--spacing-gutter)] pb-24">
      <header className="py-[var(--spacing-section-lg)]">
        <Overline>Before and after</Overline>
        {/*
          `text-display`, matching every other page title. This was `text-2xl`, which is not a step
          in this system's scale at all — it resolves to a Tailwind default and left the one page
          that reports the product's headline result with the smallest title in the console.
        */}
        <h1 className="mt-4 max-w-[22ch] text-display font-medium tracking-[var(--tracking-display)]">
          Quality Index
        </h1>
        <p className="mt-5 max-w-[68ch] text-body text-[var(--fg-secondary)]">
          The catalogue before enrichment and after, scored on the same four dimensions by the
          same code. A control arm of untouched SKUs sits alongside, not to prove enrichment
          worked, but to prove the measurement itself did not move between the two readings.
        </p>
      </header>

      {study.available ? (
        <CohortPanel study={study} />
      ) : (
        <Panel className="overflow-hidden">
          {/*
            `unmeasured`, not `empty`. There is no cohort here because the study was never run, not
            because it ran and found nothing — and a buyer reading this panel must not come away
            thinking enrichment was measured at zero lift.
          */}
          <EmptyState
            kind="unmeasured"
            title="No cohort study has been run"
            detail={
              study.reason ??
              "The study compares an ERP item master against enriched output, so it needs both."
            }
          />
          <div className="hairline-t bg-[var(--surface-sunken)] px-7 py-6">
            <p className="text-meta text-[var(--fg-quiet)]">
              Build the &ldquo;before&rdquo; state from a supplier flat file, then compare:
            </p>
            <pre className="mono scroll-x mt-3 text-meta leading-relaxed text-[var(--fg-secondary)]">
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
