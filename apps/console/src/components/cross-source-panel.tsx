import clsx from "clsx";

import { AlertIcon, CheckIcon, Overline, Panel, SectionHeading } from "@/components/primitives";
import { humanise } from "@/lib/format";
import type { CrossSourceConflict, CrossSourceView } from "@/lib/types";

/**
 * Validation layer L4, in the review workspace.
 *
 * This is the only layer that can catch a *confidently wrong* value. L0 through L3 each reason
 * about a single observation — whether it parses, whether its unit is coherent, whether it
 * contradicts a sibling field, whether it is plausible for the class — and all four pass on a
 * figure that is well-formed and false, because a wrong number printed in a datasheet is a
 * well-formed number. A second source is the cheapest way to notice.
 *
 * The panel is built around keeping three states visibly apart, because two of them look similar
 * in a summary and mean opposite things:
 *
 * - a **resolved** disagreement is a currency problem. The older catalogue is stale, not wrong, and
 *   it is shown as a warning rather than a failure.
 * - an **unresolved** one blocks publication and is a review task, because nothing could order the
 *   two sources and picking a side on no evidence is what this layer exists to prevent.
 * - **corroboration** is a positive finding rather than the absence of a problem, and is worth
 *   surfacing as such: it is the strongest evidence the pipeline can produce.
 */
export function CrossSourcePanel({ view }: { view: CrossSourceView }) {
  const resolved = view.conflicts.filter((conflict) => conflict.resolved);
  const unresolved = view.conflicts.filter((conflict) => !conflict.resolved);

  return (
    <section>
      <SectionHeading
        title="Cross-source agreement"
        detail={
          <>
            Validation layer L4. Every other layer judges one observation at a time; this one asks
            whether a second document says the same thing.
          </>
        }
        action={
          <span
            className={clsx(
              "pill",
              unresolved.length > 0 ? "pill-fail" : resolved.length > 0 ? "pill-warn" : "pill-pass",
            )}
          >
            {unresolved.length > 0 ? <AlertIcon /> : <CheckIcon />}
            {unresolved.length > 0
              ? `${unresolved.length} unresolved`
              : resolved.length > 0
                ? `${resolved.length} superseded`
                : "Sources agree"}
          </span>
        }
      />

      {view.dry_run ? (
        /*
         * Findings from scripted responses must never read as a measurement. This is the same
         * distinction the offline-fixture banner draws, for the same reason.
         */
        <Panel className="mt-6 border-l-2 border-l-[var(--warn)] p-6">
          <p className="text-sm text-[var(--fg-secondary)]">
            <span className="font-medium text-[var(--fg)]">Scripted run.</span> These findings come
            from a <span className="mono">--dry-run</span> of{" "}
            <span className="mono">cross_validate.py</span>, whose model responses are hand-written
            so the mechanism can be demonstrated without spending tokens. The comparison logic is
            real; the extracted values are not a measurement.
          </p>
        </Panel>
      ) : null}

      {/* ------------------------------------------------------------ the sources */}
      <Panel className="mt-6 p-7">
        <Overline>Sources compared</Overline>
        <ul className="mt-4 flex flex-col gap-3">
          {view.sources.map((source) => (
            <li
              key={source.document_id}
              className="grid gap-x-4 gap-y-1 sm:grid-cols-[1fr_auto] sm:items-baseline"
            >
              <div className="min-w-0">
                <p className="mono truncate text-sm">{source.document_id}</p>
                <p className="mt-0.5 truncate text-meta text-[var(--fg-quiet)]">{source.source}</p>
              </div>
              <p className="text-meta text-[var(--fg-secondary)] sm:text-right">
                {source.revision_label ? (
                  <span className="mono">{source.revision_label}</span>
                ) : (
                  <span className="text-[var(--warn)]">no revision marker</span>
                )}
                <span className="text-[var(--fg-quiet)]">
                  {" · "}
                  {source.values} {source.values === 1 ? "value" : "values"}
                  {source.supplier_id ? ` · ${source.supplier_id}` : ""}
                </span>
              </p>
            </li>
          ))}
        </ul>
        <p className="hairline-t mt-5 pt-4 text-meta text-[var(--fg-quiet)]">
          Revision markers are read off the page, not from the filesystem. When a copy was
          downloaded says nothing about when the specification was written, so ordering by retrieval
          time would let collection order decide which figure wins.
        </p>
      </Panel>

      {/* --------------------------------------------------- unresolved: the review task */}
      {unresolved.length > 0 ? (
        <div className="mt-6 flex flex-col gap-4">
          {unresolved.map((conflict) => (
            <ConflictCard key={conflict.attribute_code} conflict={conflict} blocking />
          ))}
          <p className="text-meta text-[var(--fg-secondary)]">
            Left for a human deliberately. Nothing ordered these sources, and choosing between them
            automatically would be a guess wearing a verdict&rsquo;s clothing.
          </p>
        </div>
      ) : null}

      {/* --------------------------------------------------- resolved: stale, not wrong */}
      {resolved.length > 0 ? (
        <div className="mt-6">
          <Overline>Superseded by a newer revision</Overline>
          <div className="mt-3 flex flex-col gap-4">
            {resolved.map((conflict) => (
              <ConflictCard key={conflict.attribute_code} conflict={conflict} />
            ))}
          </div>
        </div>
      ) : null}

      {/* --------------------------------------------------- the positive finding */}
      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <Panel className="p-7">
          <Overline>Corroborated</Overline>
          <p className="mt-3 flex items-baseline gap-2">
            <span className="figure text-[var(--pass)]">{view.corroborated}</span>
            <span className="text-meta text-[var(--fg-quiet)]">
              of {view.corroborated + view.disagreements + view.single_source} attributes
            </span>
          </p>
          {view.corroborated_attributes.length > 0 ? (
            <ul className="mt-4 flex flex-wrap gap-1.5">
              {view.corroborated_attributes.map((code) => (
                <li key={code} className="pill pill-pass">
                  {humanise(code)}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-4 text-meta text-[var(--fg-quiet)]">
            Two independent documents state the same value. A single citation proves a value was
            printed; two prove it was not a typo.
          </p>
        </Panel>

        <Panel className="p-7">
          <Overline>Single source</Overline>
          <p className="mt-3 flex items-baseline gap-2">
            <span className="figure text-[var(--fg-tertiary)]">{view.single_source}</span>
            <span className="text-meta text-[var(--fg-quiet)]">not corroborated</span>
          </p>
          {view.single_source_attributes.length > 0 ? (
            <ul className="mt-4 flex flex-wrap gap-1.5">
              {view.single_source_attributes.map((code) => (
                <li key={code} className="pill pill-quiet">
                  {humanise(code)}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-4 text-meta text-[var(--fg-quiet)]">
            Only one document mentions these, so agreement could not be checked. Recorded as skipped
            rather than passed &mdash; a layer that could not run has established nothing.
          </p>
        </Panel>
      </div>
    </section>
  );
}

function ConflictCard({
  conflict,
  blocking = false,
}: {
  conflict: CrossSourceConflict;
  blocking?: boolean;
}) {
  return (
    <Panel
      className={clsx(
        "p-6",
        blocking ? "border-l-2 border-l-[var(--fail)]" : "border-l-2 border-l-[var(--warn)]",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx("pill", blocking ? "pill-fail" : "pill-warn")}>
          {blocking ? "Unresolved conflict" : "Superseded"}
        </span>
        <span className="mono text-meta text-[var(--fg-quiet)]">{conflict.attribute_code}</span>
      </div>

      <ul className="mt-4 flex flex-col gap-2">
        {conflict.observations.map((observation) => {
          // The winner is rendered by the report as "'600 psi' (doc Rev C 2024-08)", so matching on
          // the document id is what identifies it without re-deriving the precedence rule here.
          const won =
            conflict.resolved &&
            conflict.winner !== null &&
            conflict.winner.includes(observation.document_id);

          return (
            <li
              key={`${observation.document_id}-${observation.value_display}`}
              className="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-3 rounded-lg bg-[var(--surface-sunken)] px-3.5 py-2.5"
            >
              <span
                aria-label={won ? "Preferred" : undefined}
                className={won ? "text-[var(--pass)]" : "text-[var(--fg-quiet)]"}
              >
                {won ? <CheckIcon /> : <span aria-hidden>·</span>}
              </span>
              <span className={clsx("text-sm", won ? "text-[var(--fg)]" : "text-[var(--fg-secondary)]")}>
                {observation.value_display ?? "—"}
              </span>
              <span className="mono text-meta text-[var(--fg-quiet)]">
                {observation.document_id}
                {observation.revision_label ? ` · ${observation.revision_label}` : ""}
              </span>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 text-meta text-[var(--fg-secondary)]">{conflict.reason}</p>
    </Panel>
  );
}
