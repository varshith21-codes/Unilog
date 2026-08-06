import clsx from "clsx";

import {
  AlertIcon,
  CheckIcon,
  Meter,
  Overline,
  Panel,
  SectionHeading,
  Stat,
} from "@/components/primitives";
import { percent } from "@/lib/format";
import type { CohortDimension, CohortMember, CohortStudy } from "@/lib/types";

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
  {
    key: "richness",
    label: "Richness",
    detail: "Not implemented — reads zero on both arms, so it cannot bias the lift",
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
    <div className="flex flex-col gap-10">
      <TrustBanner study={study} />

      <section>
        <SectionHeading
          title="The two completeness numbers"
          detail="They answer different questions, and the gap between them is the finding."
        />
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <Panel className="p-7">
            <Stat
              label="Field presence"
              value={`${percent(study.field_presence.before)} → ${percent(
                study.field_presence.after,
              )}`}
              hint="Required fields holding anything at all. What a conventional PIM completeness report shows."
            />
          </Panel>
          <Panel className="p-7">
            <Stat
              label="Publishable"
              value={`${percent(before.completeness)} → ${percent(after.completeness)}`}
              hint="Required fields holding something citable. A value nobody can source is a gap wearing a value's clothing."
              tone="pass"
            />
          </Panel>
        </div>
        <p className="mt-4 max-w-[76ch] text-sm text-[var(--fg-secondary)]">
          The item master reports itself{" "}
          <span className="tabular-nums text-[var(--fg)]">
            {percent(study.field_presence.before)}
          </span>{" "}
          complete and is{" "}
          <span className="tabular-nums text-[var(--fail)]">{percent(before.verifiability)}</span>{" "}
          verifiable. That gap, not the headline lift, is what legacy catalogue data actually looks
          like: the fields are full, and none of it can be traced to a source.
        </p>
      </section>

      <section>
        <SectionHeading
          title="Quality dimensions"
          detail={`${study.treatment_skus} enriched ${
            study.treatment_skus === 1 ? "SKU" : "SKUs"
          }, scored before and after against the same required-attribute set.`}
        />
        <Panel className="mt-6 divide-y divide-[var(--hairline)]">
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

        <div className="mt-6 grid gap-6 sm:grid-cols-3">
          <Panel className="p-7">
            <Stat
              label="Composite before"
              value={percent(before.composite)}
              hint="Weighted: completeness 35%, verifiability 30%, consistency 25%, richness 10%"
            />
          </Panel>
          <Panel className="p-7">
            <Stat label="Composite after" value={percent(after.composite)} tone="pass" />
          </Panel>
          <Panel className="p-7">
            <Stat
              label="Lift"
              value={`${study.lift.composite >= 0 ? "+" : ""}${percent(study.lift.composite)}`}
              hint="Richness is unimplemented and contributes zero on both sides, so every composite here is understated by up to 10 points."
              tone={study.lift.composite > 0 ? "pass" : "warn"}
            />
          </Panel>
        </div>
      </section>

      <ConsistencyNote study={study} />

      <section>
        <SectionHeading
          title="Per SKU"
          detail="Sorted by composite lift, least improved first — that is where the remaining work is."
        />
        <Panel className="mt-6 overflow-hidden">
          <div className="scroll-x">
            <table className="w-full min-w-[54rem] text-sm">
              <thead>
                <tr className="hairline-b text-left">
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
              <tbody className="divide-y divide-[var(--hairline)]">
                {[...study.members]
                  .sort((a, b) => a.delta.composite - b.delta.composite)
                  .map((member) => (
                    <MemberRow key={member.sku} member={member} />
                  ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>

      {study.notes.length > 0 ? (
        <section>
          <Overline>Method notes</Overline>
          <ul className="mt-3 flex flex-col gap-2">
            {study.notes.map((note) => (
              <li key={note} className="max-w-[86ch] text-meta text-[var(--fg-secondary)]">
                {note}
              </li>
            ))}
          </ul>
        </section>
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
  if (!(study.lift.consistency < 0 && study.lift.completeness > 0)) return null;

  const treatment = study.members.filter((m) => m.arm === "treatment");
  const checksBefore = treatment.reduce((sum, m) => sum + m.before.checks_run, 0);
  const checksAfter = treatment.reduce((sum, m) => sum + m.after.checks_run, 0);
  const beforeRules = new Set(treatment.flatMap((m) => m.before.failed_rules));
  const newlyFailing = [
    ...new Set(treatment.flatMap((m) => m.after.failed_rules).filter((r) => !beforeRules.has(r))),
  ].sort();

  return (
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
      <dl className="mt-5 grid gap-x-8 gap-y-2 sm:grid-cols-2">
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
          <p className="text-meta text-[var(--fg-secondary)]">
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
    <tr className={clsx(control && "text-[var(--fg-tertiary)]")}>
      <Td>
        <span className="mono">{member.sku}</span>
      </Td>
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

function Th({ children, numeric }: { children: React.ReactNode; numeric?: boolean }) {
  return (
    <th
      scope="col"
      className={clsx(
        "px-5 py-3 text-meta font-medium text-[var(--fg-tertiary)]",
        numeric && "text-right",
      )}
    >
      {children}
    </th>
  );
}

function Td({ children, numeric }: { children: React.ReactNode; numeric?: boolean }) {
  return (
    <td className={clsx("px-5 py-3.5", numeric && "text-right tabular-nums")}>{children}</td>
  );
}
