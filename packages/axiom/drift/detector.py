"""Spec drift: what changed when the manufacturer reissued the datasheet.

Blueprint Tier 3, item 24. The question every distributor discovers too late: *the supplier
published Rev D last month — which of the twelve thousand records we built from Rev C are now
wrong?*

Nothing else in this system can answer it. L0–L3 reason about one observation against the rules;
L4 compares two sources at one moment in time. Drift compares **one source against its own past**,
and that is a different question with a different consequence: the old value was not a mistake, it
was correct until the day it stopped being correct.

### Direction is the whole point

A naive differ reports "pressure_rating_wog changed" and leaves a human to work out whether that
matters. It always matters, but it matters in opposite directions:

*   The rating went **down** from 600 to 400 psi. Every product page built on Rev C now advertises
    a pressure the manufacturer no longer supports. That is not stale data, it is an **overclaim**,
    and it is the one drift outcome with liability attached.
*   The rating went **up**. The published value is merely conservative. Nothing unsafe ships, the
    catalogue just undersells the part.

Same attribute, same magnitude of change, opposite urgency. A drift report that cannot tell them
apart is a list of things to check, which is what a distributor already has.

### Where the direction comes from

It is already declared. :class:`~axiom.schema.models.SubstitutionRule` exists so the equivalence
engine knows that a 600 psi valve substitutes for a 400 psi one but not the reverse — that is
``substitution: at_least`` on the attribute, written by whoever owns the merchandising judgement.

"Is this value better or worse than that one" across *time* is the same question the rule already
answers across *parts*. So drift reads the existing declaration rather than introducing a parallel
one. The alternative — a second `drift_direction` key in the YAML — would let the two drift apart
until a valve could be a safe substitute and an unsafe revision simultaneously, which is not a
state anyone could resolve.

This is the same reasoning that put :mod:`axiom.core.compare` in the lowest layer: one question,
one implementation, however many callers.

### Refusing to guess which document is newer

Direction is meaningless without knowing which revision came first, and that is read off the page
by :func:`~axiom.docintel.revision.find_revision` — never from a file timestamp, which records when
a copy was obtained rather than when the specification was written.

When the two documents cannot be ordered, every difference is reported as an unordered revision
rather than a direction. That is deliberately unhelpful, because the alternative is telling an
operator a rating was tightened when it may have been relaxed, and a confident wrong direction
here sends exactly the wrong records to the wrong queue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from axiom.core import compare
from axiom.core.evidence import SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.schema.models import AttributeDefinition, Interchange, SubstitutionRule
from axiom.schema.registry import SchemaRegistry
from axiom.validate.cross_source import revision_rank


class DriftKind(str, Enum):
    """What happened to one attribute between two revisions."""

    UNCHANGED = "unchanged"
    """Both revisions state the same value, inside the attribute's declared tolerance."""

    TIGHTENED = "tightened"
    """The new revision is more restrictive: a lower rating, a narrower service window, a
    certification dropped. A value published from the old revision is now an overclaim."""

    RELAXED = "relaxed"
    """The new revision is less restrictive. The published value is conservative rather than
    wrong — nothing unsafe ships, the catalogue just undersells the part."""

    REVISED = "revised"
    """Changed, but the attribute declares no direction, so which revision is "better" is not a
    question the schema can answer. A material changing from RPTFE to PTFE is a real change and
    ranking the two is a materials judgement, not a string comparison."""

    ADDED = "added"
    """The new revision states an attribute the old one did not. Usually a gap that can now be
    filled — a Cv printed as a number where it used to say "consult factory"."""

    WITHDRAWN = "withdrawn"
    """The old revision stated it and the new one does not.

    The subtle one, and the one a differ built on "compare the values" misses entirely. It does
    **not** mean the value became false. It means the value lost its source, and under this
    system's central invariant a value with no evidence cannot publish however true it may be.
    """

    @property
    def is_change(self) -> bool:
        return self is not DriftKind.UNCHANGED

    @property
    def is_directional(self) -> bool:
        return self in {DriftKind.TIGHTENED, DriftKind.RELAXED}


class Consequence(str, Enum):
    """What has to happen to a record already built from the old revision.

    Separate from :class:`DriftKind` because the same kind of change carries different
    consequences depending on what the attribute is for. A tightened rating on a cosmetic
    attribute is a refresh; a tightened rating on a compliance claim is a withdrawal.
    """

    NONE = "none"
    """Nothing to do."""

    REFRESH = "refresh"
    """Re-extract when convenient. The published value is stale but not misleading."""

    REVIEW = "review"
    """A human has to decide. The change is real and its direction is not derivable."""

    RE_VERIFY = "re_verify"
    """The value lost its citation. It must be re-sourced or dropped — publishing it now would
    breach the evidence invariant even though nothing contradicted it."""

    WITHDRAW = "withdraw"
    """Take it down now. The published value claims more than the current source supports."""

    @property
    def is_urgent(self) -> bool:
        """Whether something already published is currently wrong.

        The property the console sorts on, and the reason drift is worth running: it separates
        the handful of records that are actively misleading a buyer from the thousands that are
        merely out of date.
        """
        return self in {Consequence.WITHDRAW, Consequence.RE_VERIFY}


@dataclass(frozen=True)
class AttributeDrift:
    """One attribute's change between two revisions, and what it costs."""

    attribute_code: str
    kind: DriftKind
    consequence: Consequence
    reason: str
    before_display: str | None = None
    after_display: str | None = None
    interchange: Interchange | None = None
    compliance_claim: bool = False
    was_published: bool = False
    """Whether the old revision's value was publishable. An unpublished value that drifted costs
    nothing — nobody ever saw it."""

    @property
    def is_change(self) -> bool:
        return self.kind.is_change

    def to_dict(self) -> dict[str, object]:
        return {
            "attribute_code": self.attribute_code,
            "kind": self.kind.value,
            "consequence": self.consequence.value,
            "urgent": self.consequence.is_urgent,
            "reason": self.reason,
            "before": self.before_display,
            "after": self.after_display,
            "interchange": self.interchange.value if self.interchange else None,
            "compliance_claim": self.compliance_claim,
            "was_published": self.was_published,
        }


@dataclass
class DriftReport:
    """What changed for one SKU between two revisions of its source."""

    sku: str
    before_document: str
    after_document: str
    before_revision: str | None = None
    after_revision: str | None = None
    ordered: bool = True
    """Whether the two documents could be placed in revision order. False disables every
    direction verdict in the report."""

    order_note: str = ""
    identical_source: bool = False
    """The two documents are the same bytes. No drift is possible and none is reported."""

    drifts: list[AttributeDrift] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    """Attributes the schema does not define, so no direction could be applied."""

    def changes(self) -> list[AttributeDrift]:
        return [d for d in self.drifts if d.is_change]

    def urgent(self) -> list[AttributeDrift]:
        """Changes that make an already-published value wrong. The queue that matters."""
        return [d for d in self.changes() if d.consequence.is_urgent and d.was_published]

    def by_kind(self, kind: DriftKind) -> list[AttributeDrift]:
        return [d for d in self.drifts if d.kind is kind]

    def compliance_impacts(self) -> list[AttributeDrift]:
        return [d for d in self.changes() if d.compliance_claim]

    @property
    def has_drift(self) -> bool:
        return bool(self.changes())

    @property
    def requires_republication(self) -> bool:
        return any(d.consequence is not Consequence.NONE for d in self.changes())

    def summary(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "ordered": self.ordered,
            "identical_source": self.identical_source,
            "before_revision": self.before_revision,
            "after_revision": self.after_revision,
            "attributes_compared": len(self.drifts),
            "changed": len(self.changes()),
            "tightened": len(self.by_kind(DriftKind.TIGHTENED)),
            "relaxed": len(self.by_kind(DriftKind.RELAXED)),
            "revised": len(self.by_kind(DriftKind.REVISED)),
            "added": len(self.by_kind(DriftKind.ADDED)),
            "withdrawn": len(self.by_kind(DriftKind.WITHDRAWN)),
            "urgent": len(self.urgent()),
            "compliance_impacts": len(self.compliance_impacts()),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "before_document": self.before_document,
            "after_document": self.after_document,
            "order_note": self.order_note,
            "unclassified_attributes": list(self.unclassified),
            "drifts": [d.to_dict() for d in self.drifts],
        }


def detect_drift(
    sku: str,
    before: ProductRecord,
    before_document: SourceDocument,
    after: ProductRecord,
    after_document: SourceDocument,
    registry: SchemaRegistry,
) -> DriftReport:
    """Compare one SKU's record across two revisions of its source document.

    ``before`` and ``after`` are named for intent, not trusted for order. Which document is
    actually newer is established from the revision markers, and if the caller has them the wrong
    way round the report says so instead of inverting every direction.
    """
    ordered, order_note, inverted = _establish_order(before_document, after_document)

    if inverted:
        before, after = after, before
        before_document, after_document = after_document, before_document

    report = DriftReport(
        sku=sku,
        before_document=before_document.document_id,
        after_document=after_document.document_id,
        before_revision=before_document.revision_label,
        after_revision=after_document.revision_label,
        ordered=ordered,
        order_note=order_note,
        identical_source=before_document.sha256 == after_document.sha256,
    )

    if report.identical_source:
        report.order_note = (
            "both revisions resolve to the same content hash, so there is nothing to compare"
        )
        return report

    old_values = {v.attribute_code: v for v in before.current_values()}
    new_values = {v.attribute_code: v for v in after.current_values()}

    for code in sorted(set(old_values) | set(new_values)):
        definition = _definition_or_none(registry, code)
        if definition is None:
            report.unclassified.append(code)

        report.drifts.append(
            _classify(
                code,
                old_values.get(code),
                new_values.get(code),
                definition,
                ordered=ordered,
            )
        )

    return report


def detect_drift_across(
    before: Mapping[str, ProductRecord],
    before_document: SourceDocument,
    after: Mapping[str, ProductRecord],
    after_document: SourceDocument,
    registry: SchemaRegistry,
) -> list[DriftReport]:
    """Run drift for every SKU present in both revisions.

    A SKU present in only one revision is *not* reported as drift. Its appearance or
    disappearance is a lifecycle event — a new part, or a discontinued one — and folding that
    into an attribute-drift report would file "this product no longer exists" under the same
    heading as "its seat material changed".
    """
    return [
        detect_drift(
            sku,
            before[sku],
            before_document,
            after[sku],
            after_document,
            registry,
        )
        for sku in sorted(set(before) & set(after))
    ]


# --------------------------------------------------------------------------- internals


def _establish_order(
    first: SourceDocument, second: SourceDocument
) -> tuple[bool, str, bool]:
    """Decide which document is newer.

    Returns ``(ordered, note, inverted)``. ``inverted`` is True when the caller's ``before`` is
    in fact the newer document, which is a caller mistake worth correcting silently rather than
    reporting every attribute backwards.
    """
    first_rank = revision_rank(first)
    second_rank = revision_rank(second)

    if first_rank is None or second_rank is None:
        missing = ", ".join(
            document.document_id
            for document, rank in ((first, first_rank), (second, second_rank))
            if rank is None
        )
        return (
            False,
            f"no revision marker on {missing}, so the two documents cannot be placed in order "
            f"and no direction is claimed for any change",
            False,
        )

    if first_rank == second_rank:
        return (
            False,
            f"both documents carry the same revision marker "
            f"({first.revision_label!r}), so neither can be shown to supersede the other",
            False,
        )

    if first_rank > second_rank:
        return (
            True,
            f"{first.document_id} ({first.revision_label}) is the newer revision, so the "
            f"comparison was run in the opposite order to the one requested",
            True,
        )

    return (
        True,
        f"{second.document_id} ({second.revision_label}) supersedes "
        f"{first.document_id} ({first.revision_label})",
        False,
    )


def _classify(
    code: str,
    old: AttributeValue | None,
    new: AttributeValue | None,
    definition: AttributeDefinition | None,
    *,
    ordered: bool,
) -> AttributeDrift:
    compliance = bool(definition and definition.compliance_claim)
    interchange = definition.interchange if definition else None
    was_published = bool(old and old.is_publishable)

    if old is None and new is not None:
        return AttributeDrift(
            attribute_code=code,
            kind=DriftKind.ADDED,
            consequence=Consequence.REFRESH,
            reason=(
                "the new revision states a value the previous one did not; a gap recorded "
                "against the old source may now be fillable"
            ),
            after_display=_render(new),
            interchange=interchange,
            compliance_claim=compliance,
        )

    if old is not None and new is None:
        return AttributeDrift(
            attribute_code=code,
            kind=DriftKind.WITHDRAWN,
            consequence=Consequence.WITHDRAW if compliance else Consequence.RE_VERIFY,
            reason=(
                "the current revision no longer states this value. It has not been "
                "contradicted, it has lost its source — and a compliance claim with no source "
                "must come down immediately rather than wait for a re-extraction"
                if compliance
                else "the current revision no longer states this value, so the published figure "
                "no longer has a citation in the live source and cannot publish from it"
            ),
            before_display=_render(old),
            interchange=interchange,
            compliance_claim=compliance,
            was_published=was_published,
        )

    assert old is not None and new is not None  # both-None is impossible by construction

    tolerance = definition.tolerance or 0.0 if definition else 0.0
    if compare.agree(_canonical(old), _canonical(new), tolerance=tolerance):
        return AttributeDrift(
            attribute_code=code,
            kind=DriftKind.UNCHANGED,
            consequence=Consequence.NONE,
            reason="both revisions state the same value",
            before_display=_render(old),
            after_display=_render(new),
            interchange=interchange,
            compliance_claim=compliance,
            was_published=was_published,
        )

    if not ordered:
        return AttributeDrift(
            attribute_code=code,
            kind=DriftKind.REVISED,
            consequence=Consequence.REVIEW,
            reason=(
                "the value differs, but the two documents could not be placed in revision "
                "order, so it cannot be said whether this is a tightening or a relaxation"
            ),
            before_display=_render(old),
            after_display=_render(new),
            interchange=interchange,
            compliance_claim=compliance,
            was_published=was_published,
        )

    kind = _direction(_canonical(old), _canonical(new), definition)
    return AttributeDrift(
        attribute_code=code,
        kind=kind,
        consequence=_consequence(kind, interchange, compliance=compliance),
        reason=_direction_reason(kind, definition),
        before_display=_render(old),
        after_display=_render(new),
        interchange=interchange,
        compliance_claim=compliance,
        was_published=was_published,
    )


def _direction(
    old: object, new: object, definition: AttributeDefinition | None
) -> DriftKind:
    """Classify a changed value as more or less restrictive.

    Reads :class:`SubstitutionRule` off the attribute definition. An attribute at the default
    ``EQUAL`` has no declared direction and so cannot be ordered — which is correct rather than a
    shortfall: for a body alloy there is no "more".
    """
    if definition is None:
        return DriftKind.REVISED

    rule = definition.substitution

    if rule is SubstitutionRule.AT_LEAST:
        return _by_magnitude(old, new, more_is_better=True)
    if rule is SubstitutionRule.AT_MOST:
        return _by_magnitude(old, new, more_is_better=False)
    if rule is SubstitutionRule.ENCLOSES:
        return _by_enclosure(old, new)
    if rule is SubstitutionRule.SUPERSET:
        return _by_membership(old, new)
    return DriftKind.REVISED


def _by_magnitude(old: object, new: object, *, more_is_better: bool) -> DriftKind:
    old_magnitude = _magnitude(old)
    new_magnitude = _magnitude(new)
    if old_magnitude is None or new_magnitude is None:
        return DriftKind.REVISED
    if new_magnitude == old_magnitude:
        return DriftKind.REVISED
    increased = new_magnitude > old_magnitude
    improved = increased if more_is_better else not increased
    return DriftKind.RELAXED if improved else DriftKind.TIGHTENED


def _by_enclosure(old: object, new: object) -> DriftKind:
    """A narrower service window is a tightening even when both bounds look reasonable."""
    if not isinstance(old, ValueRange) or not isinstance(new, ValueRange):
        return DriftKind.REVISED
    if old.unit != new.unit:
        return DriftKind.REVISED

    if new.minimum >= old.minimum and new.maximum <= old.maximum:
        return DriftKind.TIGHTENED
    if new.minimum <= old.minimum and new.maximum >= old.maximum:
        return DriftKind.RELAXED
    # The window moved rather than growing or shrinking: it gained at one end and gave up at the
    # other. Calling that either name would hide half of it.
    return DriftKind.REVISED


def _by_membership(old: object, new: object) -> DriftKind:
    """Losing a listing is a tightening; gaining one is a relaxation."""
    if not isinstance(old, list) or not isinstance(new, list):
        return DriftKind.REVISED
    before, after = {str(v) for v in old}, {str(v) for v in new}
    if after < before:
        return DriftKind.TIGHTENED
    if after > before:
        return DriftKind.RELAXED
    return DriftKind.REVISED


def _magnitude(value: object) -> float | None:
    if isinstance(value, bool):
        # A compliance flag is orderable: holding a certification beats not holding it, which is
        # what `at_least` means on a boolean.
        return 1.0 if value else 0.0
    if isinstance(value, Quantity):
        return float(value.magnitude)
    if isinstance(value, int | float):
        return float(value)
    return None


def _consequence(
    kind: DriftKind, interchange: Interchange | None, *, compliance: bool
) -> Consequence:
    """Translate a change into an action on the records already published.

    Compliance attributes escalate unconditionally. Everywhere else in this system a compliance
    claim is held to a stricter standard than a specification — strict evidence, never satisfied
    by inference — and it would be incoherent to relax that here, at the one moment when the
    claim is known to have moved.
    """
    if kind is DriftKind.UNCHANGED:
        return Consequence.NONE

    if kind is DriftKind.TIGHTENED:
        if compliance:
            return Consequence.WITHDRAW
        if interchange is Interchange.COSMETIC:
            return Consequence.REFRESH
        return Consequence.WITHDRAW

    if kind is DriftKind.WITHDRAWN:
        return Consequence.WITHDRAW if compliance else Consequence.RE_VERIFY

    if kind is DriftKind.RELAXED:
        # Nothing published is wrong: the catalogue is understating the part. Worth re-extracting,
        # never worth an incident.
        return Consequence.REFRESH

    if kind is DriftKind.ADDED:
        return Consequence.REFRESH

    if interchange is Interchange.COSMETIC:
        return Consequence.REFRESH
    return Consequence.REVIEW


def _direction_reason(kind: DriftKind, definition: AttributeDefinition | None) -> str:
    rule = definition.substitution.value if definition else "unknown"
    if kind is DriftKind.TIGHTENED:
        return (
            f"the new revision is more restrictive on an attribute declared "
            f"'{rule}', so a value published from the old revision now claims more than the "
            f"current source supports"
        )
    if kind is DriftKind.RELAXED:
        return (
            f"the new revision is less restrictive on an attribute declared "
            f"'{rule}', so the published value is conservative rather than wrong"
        )
    if definition is None:
        return (
            "the value changed and the schema does not define this attribute, so no direction "
            "could be applied"
        )
    return (
        f"the value changed on an attribute declared '{rule}', which states no direction — "
        f"ranking these two is a domain judgement the schema deliberately does not make"
    )


def _definition_or_none(registry: SchemaRegistry, code: str) -> AttributeDefinition | None:
    try:
        return registry.attribute(code)
    except KeyError:
        return None


def _canonical(value: AttributeValue) -> object:
    return value.value_canonical if value.value_canonical is not None else value.value_raw


def _render(value: AttributeValue) -> str:
    return value.value_display or value.value_raw or str(_canonical(value))
