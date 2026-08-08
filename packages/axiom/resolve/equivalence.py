"""Attribute-compatibility equivalence: is this part an acceptable substitute for that one?

Blueprint Tier 3, item 20. The question a distributor asks constantly and almost no catalogue can
answer: *the part the customer wanted is out of stock — what else will do?*

The usual implementation compares product descriptions for text similarity, which is worse than
useless here. "Bronze ball valve 3/4 NPT 600WOG" and "Bronze ball valve 3/4 NPT 400WOG" are nearly
identical strings, and one of them will fail at 500 psi. Similarity is not compatibility, and the
gap between them is the whole point.

So this compares **normalised specification values under declared interchange semantics**. The
comparison primitive is :func:`axiom.core.compare.match`, the same one the backtest and validation
layer L4 use, with each attribute's own declared tolerance — because "these two values agree" has
to mean one thing across this system, or a value could be simultaneously correct against ground
truth and incompatible with itself.

### Four properties that make the verdict trustworthy

**Substitution is directional.** A 600 psi valve substitutes for a 400 psi one; the reverse is a
downgrade that could fail in service. So :func:`equivalence` takes a named ``reference`` and
``candidate`` rather than two interchangeable arguments, and the answers genuinely differ. An
engine built on symmetric equality would either refuse every safe upgrade or approve every unsafe
downgrade.

**An unestablished attribute is not a match.** Evidence-or-null, one layer up. If the candidate's
end connection was never established then it is *unknown* whether it threads into the same pipe,
and reporting "no difference found, therefore compatible" would be the most dangerous thing this
module could do. Unknowns downgrade the verdict to :attr:`Verdict.INDETERMINATE` and are listed by
name, so the report says "I cannot tell you" instead of "yes". A cross-reference that treats
silence as agreement is how a wrong valve ships.

**Only publishable values are compared.** A queued value is a machine's unreviewed guess.
Letting one support a substitution would launder an unverified extraction into a purchasing
decision, so a non-publishable value is treated exactly like an absent one.

**A class difference caps the verdict.** A bronze gate valve at the same size, rating and alloy as
a bronze ball valve is not a drop-in for it: the class implies throttling behaviour, flow
characteristic and service position that this attribute dictionary does not model. Claiming
drop-in across classes would rest on the *absence* of attributes rather than on their agreement,
so the ladder is capped at :attr:`Verdict.FUNCTIONAL_EQUIVALENT` and the reason says why.

### The verdict ladder

The rung that earns this feature its place is the middle one. *Functional equivalent but not
drop-in* is the answer a buyer actually needs, and it is exactly what a binary
compatible/incompatible flag cannot express. A solder-end valve does the same job as a threaded
one and will not thread into the same pipe. Telling someone "not compatible" when the truth is
"compatible, order different fittings" loses a sale for no reason. Telling them "compatible"
loses a customer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

from axiom.core import compare
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.schema.models import AttributeDefinition, Interchange, SubstitutionRule
from axiom.schema.registry import SchemaRegistry


class Verdict(str, Enum):
    """How far a candidate goes toward replacing a reference part."""

    IDENTICAL = "identical"
    """Every interchange-relevant attribute agreed exactly, and nothing cosmetic differed either.
    Two part numbers for the same thing."""

    DROP_IN = "drop_in"
    """Form, fit and function all satisfied. Install it without changing anything else."""

    FUNCTIONAL_EQUIVALENT = "functional_equivalent"
    """Performs the job, but a form-or-fit attribute differs, so installation changes.

    The rung a binary compatible/incompatible flag cannot express, and the one buyers need most.
    """

    NOT_EQUIVALENT = "not_equivalent"
    """A defining attribute differs, or a performance requirement is not met."""

    INDETERMINATE = "indeterminate"
    """Too little was established to reach a verdict.

    Deliberately not a synonym for incompatible. "I cannot tell" and "no" are different answers,
    and collapsing them hides the fact that the remedy is to enrich a record rather than to
    reject a part.
    """

    @property
    def is_substitutable(self) -> bool:
        """Whether this verdict supports offering the candidate at all."""
        return self in {Verdict.IDENTICAL, Verdict.DROP_IN, Verdict.FUNCTIONAL_EQUIVALENT}

    @property
    def needs_enrichment(self) -> bool:
        """An indeterminate verdict is a data problem, not a product problem."""
        return self is Verdict.INDETERMINATE


class Compatibility(str, Enum):
    """The outcome of comparing one attribute across two records."""

    AGREES = "agrees"
    """Equal under the attribute's declared tolerance."""

    SATISFIES = "satisfies"
    """Differs, but in the permitted direction — the candidate meets or exceeds the reference."""

    DIFFERS = "differs"
    """Differs in a way the declared substitution rule does not permit."""

    UNKNOWN_REFERENCE = "unknown_reference"
    """The reference has no publishable value, so there is no requirement to compare against."""

    UNKNOWN_CANDIDATE = "unknown_candidate"
    """The candidate has no publishable value. Never treated as agreement."""

    UNKNOWN_BOTH = "unknown_both"
    """Neither side established it. Not a match, and not a difference either."""

    NOT_COMPARABLE = "not_comparable"
    """Both values exist but could not be compared — mismatched units, or an unorderable type."""

    INAPPLICABLE = "inapplicable"
    """The attribute is not bound to both product classes, so there is nothing to compare.

    Distinct from unknown, and the distinction matters: a gate valve has no port type in the
    ball-valve sense, and recording that as *unestablished* would make every cross-class
    comparison indeterminate for a reason that has nothing to do with data quality.
    """

    @property
    def is_satisfied(self) -> bool:
        return self in {Compatibility.AGREES, Compatibility.SATISFIES}

    @property
    def is_unknown(self) -> bool:
        return self in {
            Compatibility.UNKNOWN_REFERENCE,
            Compatibility.UNKNOWN_CANDIDATE,
            Compatibility.UNKNOWN_BOTH,
        }

    @property
    def blocks(self) -> bool:
        """Whether this outcome is a positive finding of difference."""
        return self in {Compatibility.DIFFERS, Compatibility.NOT_COMPARABLE}


@dataclass(frozen=True)
class AttributeComparison:
    """One attribute, compared across the two records."""

    attribute_code: str
    name: str
    interchange: Interchange | None
    substitution: SubstitutionRule
    compatibility: Compatibility
    reference_value: Any = None
    candidate_value: Any = None
    reference_display: str | None = None
    candidate_display: str | None = None
    match_kind: str | None = None
    detail: str | None = None

    @property
    def decides(self) -> bool:
        """Whether this comparison can affect the verdict.

        False for a cosmetic attribute, for an inapplicable one, and for an *unclassified* one.
        An unclassified attribute is excluded from the verdict **and reported** rather than
        quietly folded in under a default — see :meth:`EquivalenceReport.unclassified`.
        """
        if self.compatibility is Compatibility.INAPPLICABLE:
            return False
        return self.interchange is not None and self.interchange.decides_verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_code": self.attribute_code,
            "name": self.name,
            "interchange": self.interchange.value if self.interchange else None,
            "substitution": self.substitution.value,
            "compatibility": self.compatibility.value,
            "decides": self.decides,
            "reference_value": _render(self.reference_value),
            "candidate_value": _render(self.candidate_value),
            "reference_display": self.reference_display,
            "candidate_display": self.candidate_display,
            "match_kind": self.match_kind,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EquivalenceReport:
    """Whether one part may be offered in place of another, and exactly why."""

    reference_sku: str
    candidate_sku: str
    verdict: Verdict
    reason: str
    comparisons: tuple[AttributeComparison, ...] = ()
    reference_class: str | None = None
    candidate_class: str | None = None
    reference_brand: str | None = None
    candidate_brand: str | None = None

    @property
    def same_class(self) -> bool:
        return self.reference_class == self.candidate_class

    @property
    def cross_brand(self) -> bool:
        """Whether this substitution crosses manufacturers, which is the interesting case."""
        return (
            self.reference_brand is not None
            and self.candidate_brand is not None
            and self.reference_brand != self.candidate_brand
        )

    # ---------------------------------------------------------------- slices

    def deciding(self) -> tuple[AttributeComparison, ...]:
        return tuple(c for c in self.comparisons if c.decides)

    def agreed(self) -> tuple[AttributeComparison, ...]:
        return tuple(c for c in self.deciding() if c.compatibility is Compatibility.AGREES)

    def satisfied(self) -> tuple[AttributeComparison, ...]:
        """Differs in the permitted direction. An upgrade, not a match."""
        return tuple(c for c in self.deciding() if c.compatibility is Compatibility.SATISFIES)

    def blocking(self) -> tuple[AttributeComparison, ...]:
        return tuple(c for c in self.deciding() if c.compatibility.blocks)

    def unknown(self) -> tuple[AttributeComparison, ...]:
        """Deciding attributes not established on one side or the other."""
        return tuple(c for c in self.deciding() if c.compatibility.is_unknown)

    def cosmetic_differences(self) -> tuple[AttributeComparison, ...]:
        """Differences that cannot change the verdict but a buyer should still see.

        A handle style or a country of origin must not block a substitution, and hiding it would
        still be wrong: the installer expecting a lever, and the buyer weighing a tariff, are both
        entitled to know before the pallet arrives.
        """
        return tuple(
            c
            for c in self.comparisons
            if c.interchange is Interchange.COSMETIC and c.compatibility.blocks
        )

    def inapplicable(self) -> tuple[str, ...]:
        """Attributes not bound to both classes, so never compared."""
        return tuple(
            sorted(
                c.attribute_code
                for c in self.comparisons
                if c.compatibility is Compatibility.INAPPLICABLE
            )
        )

    def unclassified(self) -> tuple[str, ...]:
        """Attributes with no declared interchange level.

        Reported rather than dropped, on the same principle as variant explosion surfacing an
        ordering-table column no attribute claimed: an attribute nobody has classified is a schema
        gap, and silence would let it stay one indefinitely.
        """
        return tuple(
            sorted(
                c.attribute_code
                for c in self.comparisons
                if c.interchange is None and c.compatibility is not Compatibility.INAPPLICABLE
            )
        )

    # ---------------------------------------------------------------- summary

    @property
    def compared(self) -> int:
        """Deciding attributes where both sides held a publishable value."""
        return sum(1 for c in self.deciding() if not c.compatibility.is_unknown)

    @property
    def coverage_note(self) -> str:
        """One sentence on how much of the verdict rested on established values."""
        deciding = len(self.deciding())
        if not deciding:
            return "no interchange-relevant attribute was established on either record"
        return (
            f"{self.compared} of {deciding} interchange-relevant attributes were established on "
            f"both records"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "reference_sku": self.reference_sku,
            "candidate_sku": self.candidate_sku,
            "verdict": self.verdict.value,
            "substitutable": self.verdict.is_substitutable,
            "needs_enrichment": self.verdict.needs_enrichment,
            "reason": self.reason,
            "reference_class": self.reference_class,
            "candidate_class": self.candidate_class,
            "same_class": self.same_class,
            "reference_brand": self.reference_brand,
            "candidate_brand": self.candidate_brand,
            "cross_brand": self.cross_brand,
            "deciding": len(self.deciding()),
            "compared": self.compared,
            "agreed": len(self.agreed()),
            "satisfied": len(self.satisfied()),
            "blocking": len(self.blocking()),
            "unknown": len(self.unknown()),
            "cosmetic_differences": len(self.cosmetic_differences()),
            "inapplicable": list(self.inapplicable()),
            "unclassified": list(self.unclassified()),
            "coverage_note": self.coverage_note,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "blocking_detail": [c.to_dict() for c in self.blocking()],
            "unknown_detail": [c.to_dict() for c in self.unknown()],
            "satisfied_detail": [c.to_dict() for c in self.satisfied()],
            "cosmetic_detail": [c.to_dict() for c in self.cosmetic_differences()],
            "agreed_attributes": [c.attribute_code for c in self.agreed()],
            "comparisons": [c.to_dict() for c in self.comparisons],
        }


def equivalence(
    reference: ProductRecord,
    candidate: ProductRecord,
    registry: SchemaRegistry,
    *,
    only_codes: Iterable[str] | None = None,
) -> EquivalenceReport:
    """Can ``candidate`` be offered in place of ``reference``?

    Directional. ``equivalence(a, b)`` asks whether b replaces a, which is a different question
    from whether a replaces b, and the two routinely have different answers.
    """
    reference_values = {v.attribute_code: v for v in reference.publishable_values()}
    candidate_values = {v.attribute_code: v for v in candidate.publishable_values()}

    reference_bound = _bound_codes(reference, registry)
    candidate_bound = _bound_codes(candidate, registry)

    codes = sorted(
        set(only_codes)
        if only_codes is not None
        else (reference_bound | candidate_bound | set(reference_values) | set(candidate_values))
    )

    comparisons: list[AttributeComparison] = []
    for code in codes:
        try:
            definition = registry.attribute(code)
        except KeyError:
            continue
        # An attribute only one class binds is inapplicable, not unestablished. A gate valve has
        # no port type in the ball-valve sense, and calling that a gap would make every
        # cross-class comparison indeterminate for a reason unrelated to data quality.
        applicable = not (
            reference_bound
            and candidate_bound
            and (code not in reference_bound or code not in candidate_bound)
        )
        comparisons.append(
            _compare_attribute(
                definition,
                reference_values.get(code),
                candidate_values.get(code),
                applicable=applicable,
            )
        )

    frozen = tuple(comparisons)
    verdict, reason = _decide(
        frozen,
        same_class=reference.class_code == candidate.class_code,
        reference_class=reference.class_code,
        candidate_class=candidate.class_code,
    )

    return EquivalenceReport(
        reference_sku=reference.sku,
        candidate_sku=candidate.sku,
        verdict=verdict,
        reason=reason,
        comparisons=frozen,
        reference_class=reference.class_code,
        candidate_class=candidate.class_code,
        reference_brand=reference.brand,
        candidate_brand=candidate.brand,
    )


def rank(
    reference: ProductRecord,
    candidates: Sequence[ProductRecord],
    registry: SchemaRegistry,
    *,
    include_unsubstitutable: bool = False,
    limit: int | None = None,
) -> list[EquivalenceReport]:
    """Every candidate scored against one reference, best substitute first.

    Ordered by verdict, then by how much was actually established. The second key matters: two
    candidates can both come back ``drop_in`` while one was judged on eleven attributes and the
    other on three, and the report should not present those as equally good news.

    ``include_unsubstitutable`` keeps the rejections. They are worth showing — "not equivalent
    because the pressure rating is lower" is a more useful answer than an empty list, and an
    indeterminate result is a work item rather than a dead end.
    """
    reports = [
        equivalence(reference, candidate, registry)
        for candidate in candidates
        if candidate.sku != reference.sku
    ]
    if not include_unsubstitutable:
        reports = [r for r in reports if r.verdict.is_substitutable]

    reports.sort(key=_rank_key)
    return reports[:limit] if limit else reports


_VERDICT_ORDER = {
    Verdict.IDENTICAL: 0,
    Verdict.DROP_IN: 1,
    Verdict.FUNCTIONAL_EQUIVALENT: 2,
    Verdict.INDETERMINATE: 3,
    Verdict.NOT_EQUIVALENT: 4,
}


def _rank_key(report: EquivalenceReport) -> tuple[int, int, int, str]:
    return (
        _VERDICT_ORDER[report.verdict],
        -report.compared,
        len(report.cosmetic_differences()),
        report.candidate_sku,
    )


def _bound_codes(record: ProductRecord, registry: SchemaRegistry) -> set[str]:
    """Attribute codes the record's class binds, or an empty set if the class is unknown."""
    if not record.class_code:
        return set()
    try:
        return {a.code for a in registry.attributes_for(record.class_code)}
    except KeyError:
        return set()


def _compare_attribute(
    definition: AttributeDefinition,
    reference: AttributeValue | None,
    candidate: AttributeValue | None,
    *,
    applicable: bool = True,
) -> AttributeComparison:
    """Compare one attribute across two records under its declared substitution rule."""
    left = reference.value_canonical if reference is not None else None
    right = candidate.value_canonical if candidate is not None else None

    def build(
        compatibility: Compatibility,
        *,
        match_kind: str | None = None,
        detail: str | None = None,
    ) -> AttributeComparison:
        return AttributeComparison(
            attribute_code=definition.code,
            name=definition.name,
            interchange=definition.interchange,
            substitution=definition.substitution,
            compatibility=compatibility,
            reference_value=left,
            candidate_value=right,
            reference_display=_display(reference),
            candidate_display=_display(candidate),
            match_kind=match_kind,
            detail=detail,
        )

    if not applicable:
        return build(
            Compatibility.INAPPLICABLE,
            detail="not bound to both product classes, so there is nothing to compare",
        )

    if left is None and right is None:
        return build(
            Compatibility.UNKNOWN_BOTH,
            detail="neither record establishes this, so it is neither a match nor a difference",
        )
    if left is None:
        return build(
            Compatibility.UNKNOWN_REFERENCE,
            detail="the reference does not establish this, so there is no requirement to meet",
        )
    if right is None:
        return build(
            Compatibility.UNKNOWN_CANDIDATE,
            detail=(
                "the candidate does not establish this, so compatibility cannot be confirmed. "
                "Silence is not agreement"
            ),
        )

    kind = compare.match(left, right, tolerance=definition.tolerance or 0.0)
    if kind is not None:
        return build(Compatibility.AGREES, match_kind=kind.value)

    satisfied = _satisfies(definition.substitution, left, right)
    if satisfied is None:
        return build(
            Compatibility.NOT_COMPARABLE,
            detail=(
                f"both values are present but cannot be ordered under "
                f"substitution: {definition.substitution.value}"
            ),
        )
    if satisfied:
        return build(
            Compatibility.SATISFIES,
            detail=f"differs, and satisfies substitution: {definition.substitution.value}",
        )
    return build(
        Compatibility.DIFFERS,
        detail=f"does not satisfy substitution: {definition.substitution.value}",
    )


def _satisfies(rule: SubstitutionRule, reference: Any, candidate: Any) -> bool | None:
    """Whether an unequal candidate is nonetheless acceptable. None means not comparable.

    Only reached once :func:`axiom.core.compare.match` has already declined, so equality is
    never in question here — this decides whether the *inequality* runs the permitted way.
    """
    if rule is SubstitutionRule.EQUAL:
        return False

    if rule is SubstitutionRule.SUPERSET:
        if isinstance(reference, list) and isinstance(candidate, list):
            return set(map(str, reference)) <= set(map(str, candidate))
        return None

    if rule is SubstitutionRule.ENCLOSES:
        if isinstance(reference, ValueRange) and isinstance(candidate, ValueRange):
            if reference.unit != candidate.unit:
                return None
            return (
                candidate.minimum <= reference.minimum and candidate.maximum >= reference.maximum
            )
        return None

    # AT_LEAST / AT_MOST. Booleans are ordered on purpose: holding a certification the reference
    # lacks is an upgrade, and `True > False` expresses exactly that.
    if isinstance(reference, bool) and isinstance(candidate, bool):
        left, right = float(reference), float(candidate)
    elif isinstance(reference, Quantity) and isinstance(candidate, Quantity):
        if reference.unit != candidate.unit:
            return None
        left, right = reference.magnitude, candidate.magnitude
    elif isinstance(reference, int | float) and isinstance(candidate, int | float):
        left, right = float(reference), float(candidate)
    else:
        return None

    return right >= left if rule is SubstitutionRule.AT_LEAST else right <= left


def _decide(
    comparisons: Sequence[AttributeComparison],
    *,
    same_class: bool,
    reference_class: str | None,
    candidate_class: str | None,
) -> tuple[Verdict, str]:
    """Walk the ladder.

    Order matters and is not arbitrary. A *definite* negative outranks an unknown, because
    knowing the size differs settles the question whatever else is missing. An unknown then
    outranks a *positive* claim, because an unconfirmed requirement must never be reported as met.
    """
    deciding = [c for c in comparisons if c.decides]
    if not deciding:
        return (
            Verdict.INDETERMINATE,
            "no interchange-relevant attribute is established on both records, so there is "
            "nothing to base a verdict on",
        )

    def blocks(comparison: AttributeComparison) -> bool:
        return comparison.compatibility.blocks

    def unknown(comparison: AttributeComparison) -> bool:
        return comparison.compatibility.is_unknown

    def at(level: Interchange, predicate) -> list[AttributeComparison]:
        return [c for c in deciding if c.interchange is level and predicate(c)]

    # 1. A defining difference ends it.
    if defining := at(Interchange.DEFINING, blocks):
        return (
            Verdict.NOT_EQUIVALENT,
            f"{_names(defining)} differ, and a difference there makes this a different product "
            f"rather than a substitute",
        )

    # 2. An unmet performance requirement ends it.
    if functional := at(Interchange.FUNCTIONAL, blocks):
        return (
            Verdict.NOT_EQUIVALENT,
            f"the candidate does not meet the reference on {_names(functional)}",
        )

    # 3. An unknown on a defining or functional attribute means the question is open. Resolving
    #    it could flip the verdict to not-equivalent, so it cannot be reported as a pass.
    open_questions = at(Interchange.DEFINING, unknown) + at(Interchange.FUNCTIONAL, unknown)
    if open_questions:
        return (
            Verdict.INDETERMINATE,
            f"{_names(open_questions)} could not be established on both records, so "
            f"compatibility cannot be confirmed. Enrich these rather than reject the part",
        )

    # 4. A known form-or-fit difference. Unknown criticals do not change this — the verdict is
    #    already capped at functional equivalence, and resolving them could not raise it.
    critical = at(Interchange.CRITICAL, blocks)
    if critical:
        return (
            Verdict.FUNCTIONAL_EQUIVALENT,
            f"performs the same function, but differs on {_names(critical)}, so it is not a "
            f"drop-in and installation changes",
        )

    # 5. Everything performs, but a fit attribute is unconfirmed. Not a drop-in claim, and not a
    #    fit difference either, so it stays open.
    if fit_unknown := at(Interchange.CRITICAL, unknown):
        return (
            Verdict.INDETERMINATE,
            f"every performance requirement is met, but {_names(fit_unknown)} could not be "
            f"established on both records, so a drop-in claim cannot be made",
        )

    # 6. A class difference caps the ladder. See the module docstring: the class carries
    #    behaviour this attribute dictionary does not model.
    if not same_class:
        return (
            Verdict.FUNCTIONAL_EQUIVALENT,
            f"every compared attribute is satisfied, but {reference_class} and {candidate_class} "
            f"are different product classes, and the behaviour that distinguishes them is not "
            f"modelled by any attribute here — so this is not claimed as a drop-in",
        )

    upgrades = [c for c in deciding if c.compatibility is Compatibility.SATISFIES]
    cosmetic = [
        c
        for c in comparisons
        if c.interchange is Interchange.COSMETIC and c.compatibility.blocks
    ]

    if not upgrades and not cosmetic:
        return (
            Verdict.IDENTICAL,
            "every interchange-relevant attribute agrees exactly and nothing cosmetic differs; "
            "these are two part numbers for the same specification",
        )

    if upgrades:
        return (
            Verdict.DROP_IN,
            f"form and fit match, and the candidate exceeds the reference on {_names(upgrades)}",
        )

    return (
        Verdict.DROP_IN,
        f"form, fit and function all match; differs only on {_names(cosmetic)}, which does not "
        f"affect interchangeability",
    )


def _names(comparisons: Sequence[AttributeComparison]) -> str:
    return ", ".join(c.name.lower() for c in sorted(comparisons, key=lambda c: c.attribute_code))


def _display(value: AttributeValue | None) -> str | None:
    if value is None:
        return None
    return value.value_display or value.value_raw


def _render(value: Any) -> Any:
    """Plain-JSON rendering of a canonical value."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_render(v) for v in value]
    return value
