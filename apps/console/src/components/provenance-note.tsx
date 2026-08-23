import type { SkuMetrics } from "@/lib/types";
import { percent } from "@/lib/format";

/**
 * Reads a completeness number *with* its provenance, because the number alone is ambiguous.
 *
 * `fill_rate` counts only attributes an independent source established. That is the honest
 * definition and it makes zero the commonest value in a catalogue nobody has retrieved documents
 * for — which leaves a reader unable to tell three very different situations apart:
 *
 * - **0% complete, 0% self-declared.** Nothing to go on. The description is uninformative and no
 *   document exists. This is a supplier request.
 * - **0% complete, 80% self-declared.** The description implies most of the required attributes and
 *   nothing has confirmed any of it. This is a *retrieval* job, and it is the cheapest work in the
 *   catalogue because every gap already names the value to check.
 * - **60% complete.** A manufacturer document was read and states these values.
 *
 * The middle case is the one this component exists for. It used to render as a comfortable
 * completeness score, because a specification parsed out of the customer's own `Part_Desc` was
 * counted as evidence — so a SKU backed by nothing but the file we were asked to enrich reported
 * roughly 40% complete and 100% verifiable. Showing the split makes that impossible to misread
 * without hiding the fact that the parse happened and was useful.
 */
export function ProvenanceNote({
  metrics,
  className = "",
}: {
  metrics: SkuMetrics;
  className?: string;
}) {
  const { fill_rate: established, self_declared_rate: suggested } = metrics;

  // Nothing established, but the description proposed something. The important case.
  if (established === 0 && suggested > 0) {
    return (
      <p className={`text-[13px] leading-snug text-[var(--fg-secondary)] ${className}`}>
        <span className="pill warn">Unconfirmed</span>{" "}
        The customer description implies {percent(suggested)} of the required attributes. No
        independent source confirms any of it yet, so nothing is established.
      </p>
    );
  }

  if (established === 0) {
    return (
      <p className={`text-[13px] leading-snug text-[var(--fg-secondary)] ${className}`}>
        Nothing established, and the customer description implies nothing either. Needs a
        manufacturer document or a supplier request.
      </p>
    );
  }

  const outstanding = Math.max(0, suggested - metrics.corroborated_rate);
  return (
    <p className={`text-[13px] leading-snug text-[var(--fg-secondary)] ${className}`}>
      {percent(established)} established from an independent source.
      {outstanding > 0 ? (
        <>
          {" "}
          A further {percent(outstanding)} is implied by the customer description and still
          unconfirmed.
        </>
      ) : null}
    </p>
  );
}

/**
 * The compact form, for a table cell where a sentence will not fit.
 *
 * Renders the established figure and, separately, how much the description merely suggests. Two
 * numbers rather than one, and never summed: their sum is the figure this console used to report and
 * it was not a measurement of anything.
 */
export function ProvenanceSplit({ metrics }: { metrics: SkuMetrics }) {
  const suggested = Math.max(
    0,
    metrics.self_declared_rate - metrics.corroborated_rate,
  );
  if (suggested === 0) return null;
  return (
    <span
      className="text-[12px] tabular-nums text-[var(--fg-tertiary)]"
      title={`The customer's own description implies a further ${percent(suggested)} of the required attributes, and no independent source has confirmed it. Not counted as completeness.`}
    >
      +{percent(suggested)} unconfirmed
    </span>
  );
}
