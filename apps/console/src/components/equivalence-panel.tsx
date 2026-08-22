import clsx from "clsx";

import { AlertIcon, CheckIcon, MinusIcon, Overline, Panel, SectionHeading } from "@/components/primitives";
import { humanise } from "@/lib/format";
import type {
  EquivalenceCandidate,
  EquivalenceComparison,
  EquivalenceVerdict,
  EquivalenceView,
} from "@/lib/types";

/**
 * Cross-reference and equivalence, on the review workspace.
 *
 * The screen is built around keeping three answers apart, because two of them look similar in a
 * summary and mean opposite things to a buyer:
 *
 * - **not equivalent** is a finding about the *product*. Something differs that matters, and the
 *   remedy is to offer something else.
 * - **indeterminate** is a finding about the *record*. Nothing is known to differ; something could
 *   not be established, so no claim can be made. The remedy is to enrich, not to reject, and a UI
 *   that rendered this as a rejection would send a merchandiser looking for a different valve when
 *   the actual problem was a missing attribute.
 * - **functional equivalent** is a qualified yes. It performs the job and installation changes.
 *   Showing it as a plain match would ship the wrong fittings; showing it as a failure would lose
 *   a sale that was there to be had.
 *
 * The panel also refuses to present a verdict without its basis. `coverage_note` travels beside
 * every candidate, because "drop-in on eleven attributes" and "drop-in on three" are different
 * strengths of the same word.
 */
export function EquivalencePanel({ view }: { view: EquivalenceView }) {
  const substitutes = view.candidates_detail.filter((candidate) => candidate.substitutable);
  const indeterminate = view.candidates_detail.filter(
    (candidate) => candidate.verdict === "indeterminate",
  );
  const rejected = view.candidates_detail.filter(
    (candidate) => candidate.verdict === "not_equivalent",
  );

  return (
    <section>
      <SectionHeading
        title="Cross-reference"
        detail={
          <>
            Compatibility judged on normalised specification values under declared interchange
            semantics &mdash; not on how similar two descriptions read. Substitution is
            directional: this asks what can replace{" "}
            <span className="mono">{view.reference_sku}</span>, which is a different question from
            what it can replace.
          </>
        }
        action={
          <span
            className={clsx(
              "pill",
              substitutes.length > 0
                ? "pill-pass"
                : indeterminate.length > 0
                  ? "pill-warn"
                  : "pill-quiet",
            )}
          >
            {substitutes.length > 0 ? <CheckIcon /> : indeterminate.length > 0 ? <AlertIcon /> : <MinusIcon />}
            {substitutes.length > 0
              ? `${substitutes.length} substitute${substitutes.length === 1 ? "" : "s"}`
              : indeterminate.length > 0
                ? `${indeterminate.length} undetermined`
                : "No substitute"}
          </span>
        }
      />

      {!view.measured ? (
        /*
         * Verdicts computed over hand-authored records must never read as a measurement of the
         * pipeline. Same distinction the offline-fixture banner and the L4 dry-run notice draw,
         * for the same reason.
         */
        <Panel className="mt-6 border-l-2 border-l-[var(--warn)] p-6">
          <p className="text-sm text-[var(--fg-secondary)]">
            <span className="font-medium text-[var(--fg)]">Corpus records.</span> {view.source_note}{" "}
            The comparison logic is real; the values it compared were not extracted by this run.
          </p>
        </Panel>
      ) : null}

      {/* ------------------------------------------------------------ what was searched */}
      <Panel className="mt-6 p-7">
        <div className="grid gap-6 sm:grid-cols-3">
          <div>
            <Overline>Catalogue searched</Overline>
            <p className="mt-3 flex items-baseline gap-2">
              <span className="figure">{view.candidates}</span>
              <span className="text-meta text-[var(--fg-quiet)]">
                of {view.records} records
              </span>
            </p>
          </div>
          <div>
            <Overline>Substitutable</Overline>
            <p className="mt-3 flex items-baseline gap-2">
              <span
                className={clsx(
                  "figure",
                  substitutes.length > 0 ? "text-[var(--pass)]" : "text-[var(--fg-tertiary)]",
                )}
              >
                {substitutes.length}
              </span>
              <span className="text-meta text-[var(--fg-quiet)]">
                {view.cross_brand_substitutes} across brands
              </span>
            </p>
          </div>
          <div>
            <Overline>Undetermined</Overline>
            <p className="mt-3 flex items-baseline gap-2">
              <span
                className={clsx(
                  "figure",
                  indeterminate.length > 0 ? "text-[var(--warn)]" : "text-[var(--fg-tertiary)]",
                )}
              >
                {indeterminate.length}
              </span>
              <span className="text-meta text-[var(--fg-quiet)]">need enrichment</span>
            </p>
          </div>
        </div>
        <p className="hairline-t mt-5 pt-4 text-meta text-[var(--fg-quiet)]">
          An attribute nobody established is never counted as a match. If the candidate&rsquo;s end
          connection was never extracted, it is unknown whether it threads into the same pipe
          &mdash; and reporting no difference found as compatible is how a wrong part ships.
        </p>
      </Panel>

      {/* ------------------------------------------------------------ the substitutes */}
      {substitutes.length > 0 ? (
        <div className="mt-6 flex flex-col gap-4">
          {substitutes.map((candidate) => (
            <CandidateCard key={candidate.candidate_sku} candidate={candidate} />
          ))}
        </div>
      ) : (
        <Panel className="mt-6 p-6">
          <p className="text-sm text-[var(--fg-secondary)]">
            <span className="font-medium text-[var(--fg)]">Nothing in this catalogue
            substitutes.</span>{" "}
            That is a finding about the corpus rather than a failure of the comparison: these
            datasheets come from three manufacturers using different alloys and different rating
            classes, so the parts genuinely do not interchange.
          </p>
        </Panel>
      )}

      {/* ------------------------------------------------------------ enrich, do not reject */}
      {indeterminate.length > 0 ? (
        <div className="mt-6">
          <Overline>Undetermined &mdash; a data gap, not a rejection</Overline>
          <div className="mt-3 flex flex-col gap-4">
            {indeterminate.map((candidate) => (
              <CandidateCard key={candidate.candidate_sku} candidate={candidate} />
            ))}
          </div>
          <p className="mt-4 text-meta text-[var(--fg-secondary)]">
            Nothing here is known to differ. Something could not be established on one side, so no
            claim can be made either way &mdash; enrich these records rather than rule the parts
            out.
          </p>
        </div>
      ) : null}

      {/* ------------------------------------------------------------ why the rest failed */}
      {rejected.length > 0 ? (
        <Panel className="mt-6 p-0 overflow-hidden">
          <div className="p-7 pb-4">
            <Overline>Not equivalent</Overline>
            <p className="mt-2 text-meta text-[var(--fg-quiet)]">
              Named rather than counted. Which attribute refused a substitution is a sourcing fact
              a merchandiser can act on.
            </p>
          </div>
          <div className="scroll-x" tabIndex={0} role="region" aria-label="Rejected equivalence candidates">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Candidates that cannot replace {view.reference_sku}, and the attribute that refused
              </caption>
              <thead className="table-head">
                <tr>
                  <th scope="col" className="w-[13rem] px-7 py-2.5 text-left">
                    Candidate
                  </th>
                  <th scope="col" className="w-[10rem] px-4 py-2.5 text-left">
                    Brand
                  </th>
                  <th scope="col" className="px-4 py-2.5 text-left">
                    Refused on
                  </th>
                  <th scope="col" className="w-[7rem] px-7 py-2.5 text-right">
                    Basis
                  </th>
                </tr>
              </thead>
              <tbody>
                {rejected.map((candidate) => (
                  <tr key={candidate.candidate_sku} className="grid-row hairline-b last:border-b-0">
                    <th scope="row" className="mono px-7 py-3 text-left align-top font-normal">
                      {candidate.candidate_sku}
                    </th>
                    <td className="px-4 py-3 align-top text-[var(--fg-secondary)]">
                      {candidate.candidate_brand ?? <span className="figure-zero">&mdash;</span>}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <ul className="flex flex-wrap gap-1.5">
                        {candidate.blocking_detail.map((comparison) => (
                          <li key={comparison.attribute_code} className="pill pill-quiet">
                            {humanise(comparison.attribute_code)}
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td className="mono px-7 py-3 text-right align-top text-meta tabular-nums text-[var(--fg-quiet)]">
                      {candidate.compared}/{candidate.deciding}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </section>
  );
}

const VERDICT_LABEL: Record<EquivalenceVerdict, string> = {
  identical: "Identical",
  drop_in: "Drop-in replacement",
  functional_equivalent: "Functional equivalent",
  not_equivalent: "Not equivalent",
  indeterminate: "Cannot be determined",
};

const VERDICT_TONE: Record<EquivalenceVerdict, string> = {
  identical: "pill-pass",
  drop_in: "pill-pass",
  functional_equivalent: "pill-warn",
  not_equivalent: "pill-fail",
  indeterminate: "pill-warn",
};

const VERDICT_ACCENT: Record<EquivalenceVerdict, string> = {
  identical: "border-l-[var(--pass)]",
  drop_in: "border-l-[var(--pass)]",
  functional_equivalent: "border-l-[var(--warn)]",
  not_equivalent: "border-l-[var(--fail)]",
  indeterminate: "border-l-[var(--warn)]",
};

function CandidateCard({ candidate }: { candidate: EquivalenceCandidate }) {
  return (
    <Panel className={clsx("border-l-2 p-6", VERDICT_ACCENT[candidate.verdict])}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx("pill", VERDICT_TONE[candidate.verdict])}>
          {VERDICT_LABEL[candidate.verdict]}
        </span>
        <span className="mono text-sm">{candidate.candidate_sku}</span>
        {candidate.candidate_brand ? (
          <span className="text-meta text-[var(--fg-quiet)]">
            {candidate.candidate_brand}
            {candidate.cross_brand ? " · different manufacturer" : ""}
          </span>
        ) : null}
      </div>

      <p className="mt-3 text-sm text-[var(--fg-secondary)]">{candidate.reason}</p>

      {/*
        The basis, always. "Drop-in on eleven attributes" and "drop-in on three" are different
        strengths of the same word, and a verdict shown without its denominator invites the
        stronger reading.
      */}
      <p className="mt-2 text-meta text-[var(--fg-quiet)]">{candidate.coverage_note}</p>

      {candidate.unknown_detail.length > 0 ? (
        <ComparisonList
          label="Not established"
          comparisons={candidate.unknown_detail}
          tone="warn"
        />
      ) : null}

      {candidate.blocking_detail.length > 0 ? (
        <ComparisonList label="Differs" comparisons={candidate.blocking_detail} tone="fail" />
      ) : null}

      {candidate.satisfied_detail.length > 0 ? (
        <ComparisonList
          label="Candidate exceeds the reference"
          comparisons={candidate.satisfied_detail}
          tone="pass"
        />
      ) : null}

      {candidate.cosmetic_detail.length > 0 ? (
        <ComparisonList
          label="Differs, does not affect interchangeability"
          comparisons={candidate.cosmetic_detail}
          tone="quiet"
        />
      ) : null}

      {candidate.agreed_attributes.length > 0 ? (
        <div className="mt-4">
          <Overline>Agrees ({candidate.agreed_attributes.length})</Overline>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {candidate.agreed_attributes.map((code) => (
              <li key={code} className="pill pill-quiet">
                {humanise(code)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!candidate.same_class ? (
        <p className="hairline-t mt-4 pt-3 text-meta text-[var(--fg-secondary)]">
          Different product classes. The behaviour that distinguishes them &mdash; throttling,
          flow characteristic, service position &mdash; is not modelled by any attribute here, so a
          drop-in claim is deliberately withheld rather than inferred from their absence.
        </p>
      ) : null}
    </Panel>
  );
}

const TONE_CLASS: Record<"pass" | "warn" | "fail" | "quiet", string> = {
  pass: "text-[var(--pass)]",
  warn: "text-[var(--warn)]",
  fail: "text-[var(--fail)]",
  quiet: "text-[var(--fg-quiet)]",
};

function ComparisonList({
  label,
  comparisons,
  tone,
}: {
  label: string;
  comparisons: EquivalenceComparison[];
  tone: "pass" | "warn" | "fail" | "quiet";
}) {
  return (
    <div className="mt-4">
      <Overline className={TONE_CLASS[tone]}>{label}</Overline>
      <ul className="mt-2 flex flex-col gap-1.5">
        {comparisons.map((comparison) => (
          <li
            key={comparison.attribute_code}
            className="grid gap-x-3 gap-y-0.5 rounded-lg bg-[var(--surface-sunken)] px-3.5 py-2.5 sm:grid-cols-[1fr_auto]"
          >
            <span className="text-sm">{comparison.name}</span>
            <span className="text-meta text-[var(--fg-secondary)] sm:text-right">
              <span className="mono">{comparison.reference_display ?? "not established"}</span>
              <span aria-hidden className="text-[var(--fg-quiet)]">
                {" → "}
              </span>
              <span className="mono">{comparison.candidate_display ?? "not established"}</span>
            </span>
            {comparison.interchange ? (
              <span className="text-meta text-[var(--fg-quiet)]">
                {humanise(comparison.interchange)}
                {comparison.substitution !== "equal"
                  ? ` · must be ${comparison.substitution.replace(/_/g, " ")}`
                  : ""}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
