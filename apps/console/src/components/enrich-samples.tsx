"use client";

/**
 * A shelf of real products to run, for whoever is testing this screen.
 *
 * The run form asks for a part number and a manufacturer, and the fastest route to a misleading
 * first impression is to type something the system has never heard of and watch it correctly report
 * that it found nothing. These rows are copied verbatim from the client's item master and from SKUs
 * already in the corpus, so a click produces a run that exercises a code path somebody has looked at.
 *
 * Two decisions worth stating, because both could reasonably have gone the other way.
 *
 * **It fills the form rather than running.** A one-click "run this" button on a screen where every
 * press costs money is a trap, and it would put the cost figure after the decision instead of before
 * it. Clicking a sample populates the fields and moves focus to the submit control; the run is still
 * a deliberate second press against a visible call count.
 *
 * **The thin cases are on the shelf too, and are labelled.** Three of these should come back sparse —
 * a buying co-op in the manufacturer column, a distributor-prefixed 3M code, a part number with a
 * slash in it. A sample set containing only the parts that resolve beautifully is a demo reel; a
 * tester who never sees a correct refusal has not tested the thing that matters most here, which is
 * whether this system declines to invent data when it has none.
 */

import clsx from "clsx";
import { useId, useState } from "react";

import { Overline, Panel } from "@/components/primitives";
import type { EnrichSample } from "@/data/enrich-samples";
import { ENRICH_SAMPLES, sampleGroups } from "@/data/enrich-samples";

export function EnrichSamples({
  onPick,
  disabled = false,
  /** The part number currently in the form, so the chosen row can show as chosen. */
  activeMpn,
}: {
  onPick: (sample: EnrichSample) => void;
  disabled?: boolean;
  activeMpn: string;
}) {
  const ids = useId();
  const headingId = `${ids}-samples-heading`;
  const [open, setOpen] = useState(false);

  const groups = sampleGroups();

  return (
    <Panel as="section" label="Sample products" className="overflow-hidden">
      <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-[var(--hairline-strong)] p-5">
        <div className="min-w-0">
          <Overline>For testing</Overline>
          <h2 id={headingId} className="mt-2 text-base font-medium">
            Run one of these instead of typing
          </h2>
          <p className="mt-1.5 max-w-prose text-meta text-[var(--fg-tertiary)]">
            Real rows from the item master and the existing corpus. Clicking one fills the form in;
            it does not start a run, because a run spends model calls and that stays a deliberate
            press.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={`${ids}-samples-list`}
          className="btn btn-quiet shrink-0"
        >
          {open ? "Hide" : `Show ${ENRICH_SAMPLES.length}`}
        </button>
      </header>

      {open ? (
        <div id={`${ids}-samples-list`} className="flex flex-col">
          {groups.map((group) => (
            <section key={group} aria-label={group}>
              <h3 className="overline border-b border-[var(--hairline)] bg-[var(--surface-sunken)] px-5 py-2.5">
                {group}
              </h3>
              <ul className="flex flex-col">
                {ENRICH_SAMPLES.filter((sample) => sample.group === group).map((sample) => {
                  const active = activeMpn.trim() === sample.mpn;
                  return (
                    <li key={sample.mpn} className="border-b border-[var(--hairline)] last:border-b-0">
                      {/*
                        A button, not a link. It mutates the form on this page; a link would imply
                        navigation and would break the back button's meaning.
                      */}
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => onPick(sample)}
                        className={clsx(
                          "grid-row w-full px-5 py-3.5 text-left",
                          disabled
                            ? "cursor-not-allowed opacity-60"
                            : "cursor-pointer hover:bg-[var(--surface-hover)] active:opacity-80",
                          active && "bg-[var(--surface-hover)]",
                        )}
                      >
                        <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                          <span className="mono text-sm text-[var(--fg)]">{sample.mpn}</span>
                          <span className="text-meta text-[var(--fg-tertiary)]">
                            {sample.manufacturer}
                          </span>
                          {/*
                            The expectation badge. `warn` does not mean "broken" — it means a sparse
                            or refused result is the correct outcome, which is the distinction a
                            tester most needs drawn for them before they press the button.
                          */}
                          <span
                            className={clsx(
                              "pill ml-auto shrink-0",
                              sample.tone === "warn" ? "pill-warn" : "pill-quiet",
                            )}
                          >
                            {sample.tone === "warn" ? "expect thin" : "expect a document"}
                          </span>
                          {active ? <span className="pill pill-accent shrink-0">in the form</span> : null}
                        </span>
                        <span className="mt-1.5 block max-w-[92ch] text-meta text-[var(--fg-quiet)]">
                          {sample.description}
                        </span>
                        <span className="mt-2 block max-w-[92ch] text-meta text-[var(--fg-tertiary)]">
                          {sample.expect}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}
