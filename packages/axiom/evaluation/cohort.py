"""The before/after cohort study: what did enrichment actually change?

Tier 2, item 17. Every other measurement in this repository answers "is the extraction correct?".
This one answers the question a buyer actually asks, which is "what is my catalogue worth now that
you have finished with it?" — and it answers it against the state the catalogue was in before,
scored by exactly the same index.

**Both arms go through :func:`axiom.core.certificate.quality_index_for`.** That is the point of
splitting it out. A cohort whose two arms were scored by different code would be measuring the
code, and the temptation to score the "before" state with a stricter ruler is the single easiest
way to manufacture an impressive delta.

### The honest reading of the numbers

An item master row is scored as :attr:`DerivationMethod.LEGACY_RECORD`: present, unsourced, and
therefore **not publishable**. That produces a stark before/after on completeness, and stating it
without qualification would be a strawman — "0% complete" is not what a distributor believes about
their own data, and they would be right to object.

So this reports two completeness numbers side by side:

*   ``field_presence`` — the share of required fields that contain *anything*. This is what a
    conventional PIM completeness report shows, and it is the number the customer already has.
*   ``completeness`` — the share that is publishable under a provenance standard.

The interesting finding is the **gap between them in the before state**: a catalogue that reports
itself 46% complete and is 0% verifiable. That is a defensible, non-obvious claim about legacy
data, and it is far stronger than a headline delta that quietly redefines "complete".

### What the control group is actually for

The control arm is a holdout of SKUs that were never enriched. Since nothing happened to them,
their after-score equals their before-score *by construction*, and the difference-in-differences
therefore equals the treatment delta exactly. Presenting that as a randomised trial would be
dishonest, so this does not.

Its real job is to detect **scorer drift**. If a control SKU's before and after scores differ, the
measurement changed between the two scorings — someone edited the required-attribute set, the
index weights, or a validation rule — and the treatment delta is not trustworthy. A cohort study
with no such guard silently attributes a schema edit to the pipeline. That is a narrower claim
than "control group" usually implies, and it is the one this design can actually support.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from axiom.core.certificate import QualityIndex, quality_index_for
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.normalize import normalize_all
from axiom.schema import SchemaRegistry
from axiom.validate import Validator


class Arm(str, Enum):
    TREATMENT = "treatment"
    """Enriched by the pipeline."""

    CONTROL = "control"
    """Left in its original state, to detect a change in the measurement itself."""


@dataclass(frozen=True)
class CohortScore:
    """One SKU's quality at one point in time."""

    quality: QualityIndex
    field_presence: float
    """Share of required fields holding any value at all, publishable or not.

    Reported alongside :attr:`QualityIndex.completeness` rather than instead of it. Omitting it
    would let "unsourced" read as "empty", which is not the same claim and is easy to rebut.
    """

    values_present: int
    values_publishable: int
    values_with_evidence: int
    validation_failures: int
    required_total: int

    checks_run: int = 0
    """How many validation checks actually evaluated.

    Carried because it is the denominator behind ``consistency``, and it is *not* constant across
    the two arms: a rule only evaluates when the values it needs are present and canonical, so a
    sparse item master of unparsed strings gets a different set of checks than an enriched record
    does. Reporting consistency without it invites reading a ratio change as a data change.
    """

    failed_rules: tuple[str, ...] = ()
    """Which rules produced a blocking failure.

    Named rather than counted, because a consistency drop is only interpretable if you can see
    what failed. "Consistency fell 7 points" is a worrying number; "case_weight contradicts
    each_weight x case_quantity" is a work item.
    """

    def to_dict(self) -> dict[str, object]:
        return {
            **self.quality.to_dict(),
            "field_presence": round(self.field_presence, 4),
            "values_present": self.values_present,
            "values_publishable": self.values_publishable,
            "values_with_evidence": self.values_with_evidence,
            "validation_failures": self.validation_failures,
            "required_total": self.required_total,
            "checks_run": self.checks_run,
            "failed_rules": list(self.failed_rules),
        }


DIMENSIONS = ("completeness", "verifiability", "consistency", "composite")
"""The scored dimensions compared across arms.

``richness`` is absent by design. It is observed from channel pre-flight results and generated
copy, and neither arm of a cohort has them: the before-state is an item master with no exports,
and the after-state is reconstructed from a bundle rather than re-run. So richness is left
unmeasured on both sides and the composite renormalises over the three dimensions that were
measured — which is why the composite here is comparable between arms but not directly comparable
to a certificate's, where richness usually *is* observed.
"""


@dataclass(frozen=True)
class CohortMember:
    """One SKU, scored before and after."""

    sku: str
    arm: Arm
    before: CohortScore
    after: CohortScore

    def deltas(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for dimension in DIMENSIONS:
            before = _dimension(self.before, dimension)
            after = _dimension(self.after, dimension)
            out[dimension] = round(after - before, 4)
        out["field_presence"] = round(
            self.after.field_presence - self.before.field_presence, 4
        )
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "arm": self.arm.value,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "delta": self.deltas(),
        }


def _dimension(score: CohortScore, name: str) -> float:
    if name == "composite":
        return score.quality.composite
    value = getattr(score.quality, name)
    # An unmeasured dimension reads as 0.0 here only so the arithmetic has something to work with.
    # It is kept out of DIMENSIONS precisely so that never reaches a report.
    return 0.0 if value is None else float(value)


@dataclass
class CohortStudy:
    """Both arms, with the deltas and the drift guard."""

    members: list[CohortMember] = field(default_factory=list)
    schema_version: str | None = None
    notes: list[str] = field(default_factory=list)

    def arm(self, arm: Arm) -> list[CohortMember]:
        return [m for m in self.members if m.arm is arm]

    @property
    def treatment(self) -> list[CohortMember]:
        return self.arm(Arm.TREATMENT)

    @property
    def control(self) -> list[CohortMember]:
        return self.arm(Arm.CONTROL)

    def mean(self, arm: Arm, point: str, dimension: str) -> float:
        """Mean of one dimension across an arm at ``before`` or ``after``."""
        members = self.arm(arm)
        if not members:
            return 0.0
        if dimension == "field_presence":
            values = [getattr(m, point).field_presence for m in members]
        else:
            values = [_dimension(getattr(m, point), dimension) for m in members]
        return round(sum(values) / len(members), 4)

    def lift(self, dimension: str) -> float:
        """Treatment's mean improvement in one dimension."""
        return round(
            self.mean(Arm.TREATMENT, "after", dimension)
            - self.mean(Arm.TREATMENT, "before", dimension),
            4,
        )

    def control_drift(self, dimension: str) -> float:
        """How much the *unchanged* arm appears to have moved.

        Should be exactly zero. Anything else means the scorer changed between the two
        measurements, and every treatment number in this study is suspect.
        """
        return round(
            self.mean(Arm.CONTROL, "after", dimension)
            - self.mean(Arm.CONTROL, "before", dimension),
            4,
        )

    @property
    def drifted(self) -> list[str]:
        """Dimensions where the control arm moved. Non-empty invalidates the study."""
        if not self.control:
            return []
        return [d for d in DIMENSIONS if abs(self.control_drift(d)) > 1e-9]

    @property
    def trustworthy(self) -> bool:
        """Whether the deltas can be attributed to enrichment.

        Requires a control arm. Without one there is nothing separating "the pipeline improved
        the data" from "somebody adjusted the index weights", so a study with no control reports
        its deltas and declines to vouch for them.
        """
        return bool(self.control) and not self.drifted

    def summary(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "treatment_skus": len(self.treatment),
            "control_skus": len(self.control),
            "trustworthy": self.trustworthy,
            "control_drift": {d: self.control_drift(d) for d in DIMENSIONS}
            if self.control
            else None,
            "drifted_dimensions": self.drifted,
            "lift": {d: self.lift(d) for d in DIMENSIONS},
            "field_presence": {
                "before": self.mean(Arm.TREATMENT, "before", "field_presence"),
                "after": self.mean(Arm.TREATMENT, "after", "field_presence"),
            },
            "treatment_before": {
                d: self.mean(Arm.TREATMENT, "before", d) for d in DIMENSIONS
            },
            "treatment_after": {
                d: self.mean(Arm.TREATMENT, "after", d) for d in DIMENSIONS
            },
            "notes": list(self.notes),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.summary(), "members": [m.to_dict() for m in self.members]}


# --------------------------------------------------------------------------- scoring


def legacy_record(
    sku: str,
    attributes: Mapping[str, str],
    registry: SchemaRegistry,
    *,
    class_code: str,
    tenant_id: str = "demo",
    brand: str | None = None,
) -> ProductRecord:
    """Build a record representing an item master row, before any enrichment.

    Values are normalised through the *same* engine the pipeline uses, then validated by the
    *same* validator. Skipping either would flatter the after-state: half of what enrichment
    delivers is canonical units and caught contradictions, and an unnormalised before-state would
    be credited to extraction instead.

    Unparseable values are still recorded, carrying whatever the item master literally held. They
    are what a legacy catalogue actually contains, and dropping them would quietly raise the
    before-state's consistency by discarding its worst rows.
    """
    record = ProductRecord(
        tenant_id=tenant_id,
        sku=sku,
        mpn=sku,
        brand=brand,
        class_code=class_code,
        schema_version=registry.product_class(class_code).schema_version,
    )

    known = set(registry.attribute_codes)
    values = [
        AttributeValue(
            attribute_code=code,
            value_raw=str(raw),
            value_display=str(raw),
            method=DerivationMethod.LEGACY_RECORD,
            # Not AUTO_ACCEPTED: nothing accepted it. The item master simply asserts it, which
            # is exactly the state being measured.
            status=ValueStatus.CANDIDATE,
            # Zero, not a default like 0.5. There is no basis on which to be confident in a value
            # of unknown origin, and inventing one would put a number on the before-state that the
            # before-state did not earn.
            confidence=0.0,
        )
        for code, raw in attributes.items()
        if code in known and str(raw).strip()
    ]

    # `normalize_all` is the batch entry point the pipeline itself calls. Using the same one means
    # the before-state gets the same unit registry, the same enum snapping and the same fraction
    # handling — so a delta cannot be an artefact of one arm being normalised more carefully.
    normalised, _issues = normalize_all(values, registry)
    for value in normalised:
        record.add_value(value)

    return record


def score(
    record: ProductRecord,
    registry: SchemaRegistry,
    *,
    class_code: str | None = None,
    validate: bool = True,
) -> CohortScore:
    """Score a record, running validation first so consistency means something.

    Consistency counts values with no blocking failure, so scoring before validating would report
    every arm as perfectly consistent — including an item master whose case weights contradict
    its each-weights.
    """
    code = class_code or record.class_code
    required = list(registry.required_codes(code)) if code else []

    checks_run = 0
    if validate:
        report = Validator(registry).validate(record)
        checks_run = int(report.summary().get("checks", 0) or 0)
        for value in record.current_values():
            findings = report.per_attribute.get(value.attribute_code, [])
            if findings:
                value.validations = [*value.validations, *findings]

    current = record.current_values()
    present = {v.attribute_code for v in current}

    return CohortScore(
        quality=quality_index_for(record, required),
        field_presence=(
            sum(1 for code_ in required if code_ in present) / len(required)
            if required
            else 0.0
        ),
        values_present=len(current),
        values_publishable=len(record.publishable_values()),
        values_with_evidence=sum(1 for v in current if v.has_verified_evidence),
        validation_failures=sum(len(v.failed_validations()) for v in current),
        required_total=len(required),
        checks_run=checks_run,
        failed_rules=tuple(
            sorted({f.rule_id for v in current for f in v.failed_validations()})
        ),
    )


def build_study(
    *,
    before: Mapping[str, ProductRecord],
    after: Mapping[str, ProductRecord],
    registry: SchemaRegistry,
    control_skus: Iterable[str] = (),
    schema_version: str | None = None,
) -> CohortStudy:
    """Assemble a study from before- and after-state records, keyed by SKU.

    A control SKU is scored from its *before* record on both sides, which is what makes the drift
    guard meaningful: identical input through the same scorer twice must produce an identical
    number, so any difference is the scorer's, not the data's.
    """
    control = set(control_skus)
    study = CohortStudy(schema_version=schema_version)
    unenriched: list[str] = []

    for sku in sorted(before):
        base = before[sku]
        arm = Arm.CONTROL if sku in control else Arm.TREATMENT

        enriched = after.get(sku)
        if arm is Arm.CONTROL:
            # Deliberately the before record again, not the enriched one. A control SKU is one
            # nobody touched.
            enriched = base
        elif enriched is None:
            unenriched.append(sku)
            continue

        # Both arms are scored against the class the *pipeline* determined, not against whatever
        # the item master's category column said. The class is a property of the product;
        # enrichment discovers it rather than changing it. Scoring the before-state against a
        # different class would compare it to a different required-attribute set — and since
        # mis-assigned categories are part of what the before-state gets wrong, that is exactly
        # the SKU where the two arms would silently diverge.
        scoring_class = enriched.class_code or base.class_code

        study.members.append(
            CohortMember(
                sku=sku,
                arm=arm,
                before=score(base, registry, class_code=scoring_class),
                after=score(enriched, registry, class_code=scoring_class),
            )
        )

    # One aggregated note rather than one per SKU. On a real catalogue the unenriched set is the
    # large majority, and a per-SKU line would bury every other finding in the report.
    if unenriched:
        study.notes.append(
            f"{len(unenriched)} item-master row(s) had no enriched counterpart and were excluded "
            f"rather than counted as zero-improvement treatments — a run that never happened is "
            f"not a null result. Excluded: {_abbreviate(unenriched)}"
        )

    unmatched = sorted(set(after) - set(before))
    if unmatched:
        study.notes.append(
            "enriched SKUs with no item-master row, excluded from the cohort because they have "
            f"no before-state to improve on: {_abbreviate(unmatched)}"
        )

    return study


def _abbreviate(items: Sequence[str], limit: int = 6) -> str:
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])} and {len(items) - limit} more"


def format_study(study: CohortStudy) -> str:
    """Render the study for a terminal."""
    lines = ["=" * 78, "QUALITY INDEX — BEFORE / AFTER COHORT", "=" * 78]
    summary = study.summary()

    lines.append(
        f"  treatment {summary['treatment_skus']} SKUs | control {summary['control_skus']} SKUs"
    )
    if study.schema_version:
        lines.append(f"  schema    {study.schema_version}")

    if not study.control:
        lines += [
            "",
            "  NO CONTROL ARM: the deltas below are reported but not vouched for. Without a",
            "  holdout there is nothing separating an improvement in the data from a change in",
            "  the way it is scored.",
        ]
    elif study.drifted:
        lines += [
            "",
            "  CONTROL DRIFTED — THIS STUDY IS NOT VALID:",
            f"    dimensions that moved on untouched SKUs: {', '.join(study.drifted)}",
            "    The scorer changed between the two measurements, so the treatment deltas below",
            "    cannot be attributed to enrichment. Re-run both arms against one schema.",
        ]
    else:
        lines += [
            "",
            "  control held at zero on every dimension, so the scorer did not move between the",
            "  two measurements and the deltas are attributable to enrichment",
        ]

    lines += [
        "",
        "  The two completeness numbers, which answer different questions:",
        f"    field presence   {summary['field_presence']['before']:>7.1%} -> "
        f"{summary['field_presence']['after']:>7.1%}   "
        f"(fields holding anything — a conventional PIM report)",
        f"    publishable      {summary['treatment_before']['completeness']:>7.1%} -> "
        f"{summary['treatment_after']['completeness']:>7.1%}   "
        f"(fields holding something citable)",
        "",
        f"  {'dimension':<18} {'before':>9} {'after':>9} {'lift':>9}",
        "  " + "-" * 50,
    ]
    for dimension in DIMENSIONS:
        before = summary["treatment_before"][dimension]
        after = summary["treatment_after"][dimension]
        lines.append(
            f"  {dimension:<18} {before:>8.1%} {after:>8.1%} {study.lift(dimension):>+8.1%}"
        )

    # Consistency falling while completeness rises is the one line in this table that reads as a
    # regression and probably is not. What follows is deliberately the *facts* — the denominators
    # and the rule ids — rather than a causal story: the mechanism varies by corpus, and asserting
    # a tidy explanation that the numbers do not support would be worse than reporting none.
    if study.lift("consistency") < 0 < study.lift("completeness"):
        checks_before = sum(m.before.checks_run for m in study.treatment)
        checks_after = sum(m.after.checks_run for m in study.treatment)
        newly_failing = sorted(
            {rule for m in study.treatment for rule in m.after.failed_rules}
            - {rule for m in study.treatment for rule in m.before.failed_rules}
        )
        lines += [
            "",
            "  Consistency is a ratio, and its denominator is not the same on both sides:",
            f"    values scored      {sum(m.before.values_present for m in study.treatment)}"
            f" before -> {sum(m.after.values_present for m in study.treatment)} after",
            f"    checks evaluated   {checks_before} before -> {checks_after} after",
            "  A rule only evaluates where the values it references are present and canonical, so",
            "  an item master of unparsed strings is scored against a different set of checks than",
            "  an enriched record. The before-state's 100% is 100% of what could be checked, which",
            "  is not the same claim as being consistent.",
        ]
        if newly_failing:
            lines += [
                "",
                "  Rules failing after enrichment that did not fail before — these are findings,",
                "  and each is a real contradiction in the source data rather than damage done to"
                " it:",
                *(f"    {rule}" for rule in newly_failing),
            ]

    lines += [
        "",
        "  richness is not scored here. It is observed from channel pre-flight and generated copy,",
        "  and neither arm has them — so the composite above is a weighted mean over the three",
        "  dimensions that were measured, renormalised. Comparable between arms, but not directly",
        "  comparable to a certificate's composite, where richness usually is observed.",
    ]

    worst = sorted(study.treatment, key=lambda m: m.deltas()["composite"])[:3]
    if worst:
        lines += ["", "  least improved SKUs — where the remaining work is:"]
        for member in worst:
            delta = member.deltas()
            lines.append(
                f"    {member.sku:<14} composite {delta['composite']:+.1%}  "
                f"(completeness {delta['completeness']:+.1%}, "
                f"{member.after.validation_failures} validation failure(s) remaining)"
            )

    for note in study.notes:
        lines += ["", f"  NOTE: {note}"]

    lines.append("")
    return "\n".join(lines)


JOIN_KEYS = ("mpn", "sku")
"""Fields that can link an item-master row to an enriched record.

``mpn`` first because that is what the pipeline is driven by: ``run_pipeline.py --sku BA-100-075``
names the *manufacturer* part number, while an item master's own ``sku`` column is usually the
distributor's internal id (``MIL-BA100-075``). Joining on the wrong one yields an empty cohort,
which is why the key is a parameter rather than a guess.
"""


def load_records(
    rows: Sequence[Mapping[str, object]],
    registry: SchemaRegistry,
    *,
    class_code: str,
    key: str = "mpn",
    default_brand: str | None = None,
) -> dict[str, ProductRecord]:
    """Build before-state records from mapped supplier rows, keyed for the join.

    Takes the output of ``scripts/ingest_supplier_file.py --out``, which is the item master in
    canonical field names. Rows sharing a key keep the first, because a duplicate row with a
    variant spelling is a defect in the *source*, and silently merging them would repair the
    before-state for free — the duplicates are part of what the study is measuring.
    """
    records: dict[str, ProductRecord] = {}
    fallback = next((k for k in JOIN_KEYS if k != key), "sku")

    for row in rows:
        identity = str(row.get(key) or row.get(fallback) or "").strip()
        if not identity or identity in records:
            continue
        attributes = row.get("attributes") or {}
        if not isinstance(attributes, Mapping):
            continue
        records[identity] = legacy_record(
            identity,
            {str(k): str(v) for k, v in attributes.items()},
            registry,
            class_code=class_code,
            brand=str(row.get("brand") or default_brand or "") or None,
        )
    return records
