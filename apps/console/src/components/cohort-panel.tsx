import clsx from "clsx";

import {
  AlertIcon,
  CheckIcon,
  Meter,
  Overline,
  Panel,
  Section,
  SectionHeading,
  Stat,
} from "@/components/primitives";
import { percent } from "@/lib/format";
import type { CohortDimension, CohortMember, CohortStudy } from "@/lib/types";

/**
 * The dimensions a cohort compares. Richness is absent by design, not by omission: it is scored
 * from channel pre-flight results and generated copy, and neither arm of a cohort has them — the
 * before-state is an item master with no exports, and the after-state is reconstructed from a
 * bundle rather than re-run. Both arms therefore renormalise over these three.
 */
const DIMENSIONS: { key: CohortDimension; label: string; detail: string }[] = [
  {
    key: "completeness",
    label: "Completeness",
    detail: "Required fields holding a value that can be published",
  },
  {
    key: "verifiability",
    label: "Verifiability",
    detail: "Published values whose quote was located in a source document",
  },
  {
    key: "consistency",
    label: "Consistency",
    detail: "Values with no blocking validation failure",
  },
];

/**
 * The before/after cohort.
 *
 * Three things this deliberately does not do, because each would overstate the result:
 *
 * 1. **It does not lead with the composite.** A single number invites a headline and hides which
 *    dimension moved. Completeness and verifiability move for entirely different reasons.
 * 2. **It shows both completeness numbers.** An item master's fields *are* populated; they are
 *    just unsourced. Reporting only the publishable figure would let "unverifiable" read as
 *    "empty", which a distributor would rightly reject about their own data.
 * 3. **It refuses to vouch for anything without a clean control arm.** If untouched SKUs appear to
 *    have moved, the scorer changed between readings and every number here is suspect.
 */
export function CohortPanel({ study }: { study: CohortStudy }) {
  const before = study.treatment_before;
  const after = study.treatment_after;

  return (
    <div>
      <TrustBanner study={study} />

      <Section rhythm="base">
        <SectionHeading
          level="primary"
          title="The two completeness numbers"
          detail="They answer different questions, and the gap between them is the finding."
        />
        <div className="mt-7 grid gap-6 sm:grid-cols-2">
          <Panel raised className="reveal reveal-1 p-7">
            <Stat
              label="Field presence"
              value={`${percent(study.field_presence.before)} → ${percent(
                study.field_presence.after,
              )}`}
              hint="Required fields holding anything at all. What a conventional PIM completeness report shows."
            />
          </Panel>
          <Panel raised className="reveal reveal-2 p-7">
            <Stat
              label="Publishable"
              value={`${percent(before.completeness)} → ${percent(after.completeness)}`}
              hint="Required fields holding something citable. A value nobody can source is a gap wearing a value's clothing."
              tone="pass"
            />
          </Panel>
        </div>
        <p className="mt-5 max-w-[76ch] text-sm text-[var(--fg-secondary)]">
          The item master reports itself{" "}
          <span className="tabular-nums text-[var(--fg)]">
            {percent(study.field_presence.before)}
          </span>{" "}
          complete and is{" "}
          <span className="tabular-nums text-[var(--fail)]">{percent(before.verifiability)}</span>{" "}
          verifiable. That gap, not the headline lift, is what legacy catalogue data actually looks
          like: the fields are full, and none of it can be traced to a source.
        </p>
      </Section>

      <Section>
        <SectionHeading
          title="Quality dimensions"
          detail={`${study.treatment_skus} enriched ${
            study.treatment_skus === 1 ? "SKU" : "SKUs"
          }, scored before and after against the same required-attribute set.`}
        />
        <Panel className="mt-7 divide-y divide-[var(--hairline)]">
          {DIMENSIONS.map((dimension) => (
            <DimensionRow
              key={dimension.key}
              label={dimension.label}
              detail={dimension.detail}
              before={before[dimension.key]}
              after={after[dimension.key]}
              lift={study.lift[dimension.key]}
            />
          ))}
        </Panel>

        {/*
          Three composites, and the third is not a peer of the other two.
          `raised` on the lift alone is what says so: before and after are readings, the lift is the
          claim they support, and a row of three identical cards asked the reader to work that out.
        */}
        <div className="mt-6 grid gap-6 sm:grid-cols-3">
          <Panel className="p-7">
            <Stat
              label="Composite before"
              value={percent(before.composite)}
              hint="Weighted across the three dimensions measured here, renormalised: completeness 35%, verifiability 30%, consistency 25%"
            />
          </Panel>
          <Panel className="p-7">
            <Stat label="Composite after" value={percent(after.composite)} tone="pass" />
          </Panel>
          <Panel raised className="p-7">
            <Stat
              label="Lift"
              value={`${study.lift.composite >= 0 ? "+" : ""}${percent(study.lift.composite)}`}
              hint="Comparable between arms, but not to a certificate's composite — that one usually includes richness, which a cohort cannot observe."
              tone={study.lift.composite > 0 ? "pass" : "warn"}
            />
          </Panel>
        </div>
      </Section>

      <ConsistencyNote study={study} />

      <Section>
        <SectionHeading
          title="Per SKU"
          detail="Sorted by composite lift, least improved first — that is where the remaining work is."
        />
        <Panel className="mt-7 overflow-hidden p-0">
          <div className="scroll-x" tabIndex={0} role="region" aria-label="Cohort impact by SKU">
            <table className="w-full min-w-[58rem] text-sm">
            {/* This table had no caption. Every other one in the console does. */}
            <caption className="sr-only">
              Each SKU in the study, its arm, and how each quality dimension moved, ordered by
              composite lift with the least improved first
            </caption>
            <thead className="table-head">
              <tr className="text-left">
                <Th>SKU</Th>
                <Th>Arm</Th>
                <Th numeric>Fields present</Th>
                <Th numeric>Publishable</Th>
                <Th numeric>Verifiable</Th>
                <Th numeric>Composite</Th>
                <Th numeric>Lift</Th>
                <Th>Remaining failures</Th>
              </tr>
            </thead>
            <tbody>
              {[...study.members]
                .sort((a, b) => a.delta.composite - b.delta.composite)
                .map((member) => (
                  <MemberRow key={member.sku} member={member} />
                ))}
            </tbody>
            </table>
          </div>
        </Panel>
      </Section>

      {study.notes.length > 0 ? (
        <Section rhythm="tight">
          <Overline>Method notes</Overline>
          <ul className="mt-3 flex flex-col gap-2">
            {study.notes.map((note) => (
              <li key={note} className="max-w-[86ch] text-meta text-[var(--fg-secondary)]">
                {note}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

/**
 * Whether these numbers can be quoted.
 *
 * Rendered first and unmissably, because a cohort study is the one measurement here whose output
 * is a sales claim. A drifted control means the scorer moved between readings, which makes the
 * lift an artefact — and that has to be louder than the lift itself.
 */
function TrustBanner({ study }: { study: CohortStudy }) {
  if (study.control_skus > 0 && study.drifted_dimensions.length > 0) {
    return (
      <Panel raised className="border-l-2 border-l-[var(--fail)] p-6">
        <div className="flex items-center gap-2">
          <span className="pill pill-fail">
            <AlertIcon />
            Study not valid
          </span>
        </div>
        <p className="mt-3 max-w-[80ch] text-sm text-[var(--fg-secondary)]">
          The control arm moved on{" "}
          <span className="text-[var(--fg)]">{study.drifted_dimensions.join(", ")}</span>. Those
          SKUs were never enriched, so their scores cannot legitimately change — which means the
          measurement itself changed between the two readings, not the data. Someone edited the
          required-attribute set, the index weights, or a validation rule. Re-run both arms against
          one schema before quoting anything below.
        </p>
      </Panel>
    );
  }

  if (study.control_skus === 0) {
    return (
      <Panel raised className="border-l-2 border-l-[var(--warn)] p-6">
        <div className="flex items-center gap-2">
          <span className="pill pill-warn">
            <AlertIcon />
            No control arm
          </span>
        </div>
        <p className="mt-3 max-w-[80ch] text-sm text-[var(--fg-secondary)]">
          The deltas below are reported but not vouched for. Without a holdout of untouched SKUs
          there is nothing separating &ldquo;the pipeline improved the data&rdquo; from
          &ldquo;somebody adjusted the index weights&rdquo;. Add one with{" "}
          <span className="mono">run_cohort.py --control &lt;SKU&gt;</span>.
        </p>
      </Panel>
    );
  }

  return (
    <Panel raised className="border-l-2 border-l-[var(--pass)] p-6">
      <div className="flex items-center gap-2">
        <span className="pill pill-pass">
          <CheckIcon />
          Control held
        </span>
        <span className="text-meta text-[var(--fg-quiet)]">
          {study.control_skus} untouched {study.control_skus === 1 ? "SKU" : "SKUs"}
        </span>
      </div>
      <p className="mt-3 max-w-[80ch] text-sm text-[var(--fg-secondary)]">
        The control arm scored identically at both readings on every dimension, so the scorer did
        not move and the lift is attributable to enrichment rather than to a change in how quality
        is measured. This is a narrower claim than a randomised trial — the control was never
        enriched, so it cannot isolate a placebo effect — and it is the claim this design supports.
      </p>
    </Panel>
  );
}

/**
 * Consistency falling while completeness rises reads as a regression and generally is not.
 *
 * Shown only when it actually happens, and stated as the facts — the two denominators and the rule
 * ids — rather than as a tidy causal story. The mechanism varies by corpus, and asserting an
 * explanation the numbers do not support would be worse than offering none.
 */
function ConsistencyNote({ study }: { study: CohortStudy }) {
  // Returns its own section wrapper rather than a bare panel, so the page rhythm does not depend on
  // whether this note happened to render.
  if (!(study.lift.consistency < 0 && study.lift.completeness > 0)) return null;

  const treatment = study.members.filter((m) => m.arm === "treatment");
  const checksBefore = treatment.reduce((sum, m) => sum + m.before.checks_run, 0);
  const checksAfter = treatment.reduce((sum, m) => sum + m.after.checks_run, 0);
  const beforeRules = new Set(treatment.flatMap((m) => m.before.failed_rules));
  const newlyFailing = [
    ...new Set(treatment.flatMap((m) => m.after.failed_rules).filter((r) => !beforeRules.has(r))),
  ].sort();

  return (
    <Section rhythm="tight">
      <Panel className="p-7">
        <Overline>Why consistency fell</Overline>
        <p className="mt-3 max-w-[80ch] text-sm text-[var(--fg-secondary)]">
          Consistency is a ratio, and its denominator is not the same on both sides. A rule only
          evaluates where the values it references are present and canonical, so an item master of
          unparsed strings is scored against a different set of checks than an enriched record. The
          before-state&rsquo;s {percent(study.treatment_before.consistency)} is{" "}
          {percent(study.treatment_before.consistency)} <em>of what could be checked</em>, which is
          not the same claim as being consistent.
        </p>

        {/*
          The two denominators, on their own inset surface. They are the evidence for the paragraph
          above rather than more prose, and running them as another flat `dl` on the panel face left
          the reader to work out which of the four numbers was the point.
        */}
        <dl className="mt-5 grid gap-x-8 gap-y-3 rounded-lg bg-[var(--surface-sunken)] px-4 py-3.5 sm:grid-cols-2">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-meta text-[var(--fg-tertiary)]">Values scored</dt>
            <dd className="mono tabular-nums text-[var(--fg)]">
              {treatment.reduce((s, m) => s + m.before.values_present, 0)} →{" "}
              {treatment.reduce((s, m) => s + m.after.values_present, 0)}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-meta text-[var(--fg-tertiary)]">Checks evaluated</dt>
            <dd className="mono tabular-nums text-[var(--fg)]">
              {checksBefore} → {checksAfter}
            </dd>
          </div>
        </dl>

        {newlyFailing.length > 0 ? (
          <div className="hairline-t mt-6 pt-5">
            <p className="max-w-[80ch] text-meta text-[var(--fg-secondary)]">
              Rules failing after enrichment that did not fail before. Each is a real contradiction
              in the source data that was previously invisible, not damage done to it:
            </p>
            <ul className="mt-3 flex flex-wrap gap-2">
              {newlyFailing.map((rule) => (
                <li key={rule} className="mono pill pill-warn">
                  {rule}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Panel>
    </Section>
  );
}

function DimensionRow({
  label,
  detail,
  before,
  after,
  lift,
}: {
  label: string;
  detail: string;
  before: number;
  after: number;
  lift: number;
}) {
  return (
    <div className="grid items-center gap-4 p-6 sm:grid-cols-[14rem_1fr_auto]">
      <div className="min-w-0">
        <p className="font-medium">{label}</p>
        <p className="mt-0.5 text-meta text-[var(--fg-quiet)]">{detail}</p>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <span className="w-10 shrink-0 text-right text-meta tabular-nums text-[var(--fg-quiet)]">
            {percent(before)}
          </span>
          <Meter value={before} tone="quiet" label={`${label} before`} />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-10 shrink-0 text-right text-meta tabular-nums text-[var(--fg)]">
            {percent(after)}
          </span>
          <Meter value={after} tone={after >= before ? "pass" : "warn"} label={`${label} after`} />
        </div>
      </div>

      <p
        className={clsx(
          "text-right font-medium tabular-nums",
          lift > 0 ? "text-[var(--pass)]" : lift < 0 ? "text-[var(--warn)]" : "text-[var(--fg-quiet)]",
        )}
      >
        {lift > 0 ? "+" : ""}
        {percent(lift)}
      </p>
    </div>
  );
}

function MemberRow({ member }: { member: CohortMember }) {
  const control = member.arm === "control";
  return (
    <tr
      className={clsx(
        "grid-row hairline-b last:border-b-0",
        control && "text-[var(--fg-tertiary)]",
      )}
    >
      {/*
        `th scope="row"`, not `td`. The SKU is what identifies the row, and a screen reader moving
        across it should hear which product each figure belongs to.
      */}
      <th scope="row" className="mono px-5 py-3.5 text-left font-normal">
        {member.sku}
      </th>
      <Td>
        <span className={clsx("pill", control ? "pill-warn" : "pill-pass")}>
          {control ? "Control" : "Treatment"}
        </span>
      </Td>
      <Td numeric>
        {percent(member.before.field_presence)} → {percent(member.after.field_presence)}
      </Td>
      <Td numeric>
        {percent(member.before.completeness)} → {percent(member.after.completeness)}
      </Td>
      <Td numeric>
        {percent(member.before.verifiability)} → {percent(member.after.verifiability)}
      </Td>
      <Td numeric>{percent(member.after.composite)}</Td>
      <Td numeric>
        <span
          className={clsx(
            member.delta.composite > 0
              ? "text-[var(--pass)]"
              : member.delta.composite < 0
                ? "text-[var(--fail)]"
                : "text-[var(--fg-quiet)]",
          )}
        >
          {member.delta.composite > 0 ? "+" : ""}
          {percent(member.delta.composite)}
        </span>
      </Td>
      <Td>
        {member.after.failed_rules.length > 0 ? (
          <span className="mono text-meta text-[var(--warn)]">
            {member.after.failed_rules.join(", ")}
          </span>
        ) : (
          <span className="text-meta text-[var(--fg-quiet)]">none</span>
        )}
      </Td>
    </tr>
  );
}

/**
 * Column header. Padding and alignment only — size, weight, case and colour come from
 * `.table-head th`, so this table's headers match every other table's without repeating them.
 */
function Th({ children, numeric }: { children: React.ReactNode; numeric?: boolean }) {
  return (
    <th scope="col" className={clsx("px-5 py-2.5", numeric && "text-right")}>
      {children}
    </th>
  );
}

function Td({ children, numeric }: { children: React.ReactNode; numeric?: boolean }) {
  return (
    <td className={clsx("px-5 py-3.5", numeric && "text-right tabular-nums")}>{children}</td>
  );
}
