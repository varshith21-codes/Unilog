"""The canonical product record.

Two modelling decisions worth calling out, both from blueprint Part 7:

**Classification is multi-target.** One product carries N simultaneous classifications —
internal browse tree, ETIM, UNSPSC, marketplace tree, HS code — each with its own
confidence and its own review state. Systems that conflate the merchandising taxonomy with
the technical classification model become unfixable, so they are kept separate here by
construction.

**Attribute values are held as a list, not a dict.** A product can legitimately hold
multiple versions of the same attribute (current plus superseded) and multiple conflicting
candidates from different sources awaiting resolution. A dict keyed by attribute code
would silently destroy both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from axiom.core.gaps import Gap
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus


class ClassificationScheme(str, Enum):
    INTERNAL = "internal"
    """The distributor's customer-facing browse tree. Optimised for how buyers shop."""

    ETIM = "ETIM"
    """Technical class with typed features. Optimised for what the product is."""

    ECLASS = "eCl@ss"
    UNSPSC = "UNSPSC"
    GS1_GPC = "GS1_GPC"
    HTS = "HTS"
    """Tariff code. Never auto-published — see blueprint Part 3.3."""

    MARKETPLACE = "marketplace"

    @property
    def requires_human_confirmation(self) -> bool:
        """Schemes where published benchmarks do not support automation."""
        return self == ClassificationScheme.HTS


class LifecycleStatus(str, Enum):
    ACTIVE = "active"
    NEW = "new"
    DISCONTINUED = "discontinued"
    SUPERSEDED = "superseded"
    SPECIAL_ORDER = "special_order"


class Classification(BaseModel):
    """One product's placement in one classification scheme."""

    model_config = ConfigDict(validate_assignment=True)

    scheme: ClassificationScheme
    code: str
    path: list[str] = Field(
        default_factory=list, description="Human-readable ancestry, for UI breadcrumbs"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    level_confidences: list[float] = Field(
        default_factory=list,
        description="Per-level confidence. Enables 'confident to level 3, not level 4' "
        "rather than a confident wrong leaf.",
    )
    rationale: str | None = None
    method: str | None = None
    alternatives: list[dict] = Field(
        default_factory=list,
        description="Competing candidates with their scores. For HTS, includes duty deltas.",
    )
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @property
    def is_publishable(self) -> bool:
        return not (self.scheme.requires_human_confirmation and self.reviewed_by is None)


class ProductRecord(BaseModel):
    """The canonical record. Everything downstream — validation, generation, syndication,
    the certificate — reads from this."""

    model_config = ConfigDict(validate_assignment=True)

    tenant_id: str
    sku: str
    mpn: str | None = Field(default=None, description="Manufacturer part number, as supplied")
    mpn_normalized: str | None = Field(
        default=None, description="Cleansed for matching: prefixes stripped, casing folded"
    )
    gtin: str | None = None
    brand: str | None = None
    brand_id: str | None = None
    supplier_id: str | None = None

    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    parent_sku: str | None = Field(
        default=None, description="Set by variant table explosion to build parent-child groups"
    )

    class_code: str | None = Field(
        default=None, description="Primary internal class, drives the required attribute set"
    )
    schema_version: str | None = None

    classifications: list[Classification] = Field(default_factory=list)
    attribute_values: list[AttributeValue] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ accessors

    def current_values(self) -> list[AttributeValue]:
        """Values that have not been superseded."""
        return [
            v
            for v in self.attribute_values
            if v.superseded_by is None and v.status != ValueStatus.SUPERSEDED
        ]

    def get(self, attribute_code: str) -> AttributeValue | None:
        """Highest-version current value for an attribute, if any."""
        candidates = [v for v in self.current_values() if v.attribute_code == attribute_code]
        if not candidates:
            return None
        return max(candidates, key=lambda v: v.version)

    def publishable_values(self) -> list[AttributeValue]:
        return [v for v in self.current_values() if v.is_publishable]

    def values_needing_review(self) -> list[AttributeValue]:
        return [
            v
            for v in self.current_values()
            if v.status == ValueStatus.QUEUED_FOR_REVIEW or v.failed_validations()
        ]

    def conflicts(self) -> dict[str, list[AttributeValue]]:
        """Attributes with more than one unresolved candidate value."""
        grouped: dict[str, list[AttributeValue]] = {}
        for v in self.current_values():
            grouped.setdefault(v.attribute_code, []).append(v)
        return {code: vals for code, vals in grouped.items() if len(vals) > 1}

    def classification(self, scheme: ClassificationScheme) -> Classification | None:
        for c in self.classifications:
            if c.scheme == scheme:
                return c
        return None

    # ------------------------------------------------------------------ mutation

    def add_value(self, value: AttributeValue) -> None:
        """Append a value, superseding any existing current value for the same attribute.

        Append-only: the previous value is retained and marked superseded rather than
        overwritten, so history and rollback are always available.
        """
        existing = self.get(value.attribute_code)
        if existing is not None:
            value.version = existing.version + 1
            existing.supersede(value.version)
        self.attribute_values.append(value)
        self.updated_at = datetime.now(UTC)

    def add_candidate(self, value: AttributeValue) -> None:
        """Append a competing value **without** superseding what is already there.

        The counterpart to :meth:`add_value`, and the reason attribute values are held as a list.
        When two independent sources each state a value, superseding one with the other would
        resolve the disagreement by arrival order — the last document read would silently win, and
        :meth:`conflicts` would report nothing to resolve.

        So cross-source candidates accumulate, validation layer L4 compares them, and only then is
        one promoted. Which is the correct sequence: a conflict has to be *visible* before it can
        be adjudicated.
        """
        self.attribute_values.append(value)
        self.updated_at = datetime.now(UTC)

    def add_gap(self, gap: Gap) -> None:
        self.gaps.append(gap)
        self.updated_at = datetime.now(UTC)

    # ------------------------------------------------------------------ metrics

    def fill_rate(self, required_codes: list[str]) -> float:
        """Share of required attributes established by an independent source.

        "Publishable" carries the provenance rule rather than restating it here: a value cited only
        against the client's own item master is not publishable, so it does not count. That is the
        difference between reporting what a catalogue *contains* and what has been *established*
        about it, and it is why an un-retrieved SKU reads 0.0 no matter how descriptive its
        ``Part_Desc`` is. :meth:`self_declared_rate` is where that description's contribution shows
        up, unmixed with this.

        **An empty requirement set scores 0.0, not 1.0.** Vacuous truth is the wrong reading here.
        "Every one of no required attributes is populated" is defensible arithmetic and a false
        statement about the product: the only thing that produces an empty requirement set in this
        system is a record whose classification abstained, and an unclassified SKU is the least
        complete thing in the catalogue rather than the most. Returning 1.0 put the sample's twenty
        unclassified rows — the ones where not even the product type is known — at the top of the
        dashboard on 100% completeness. A caller that needs "was this measurable at all" should ask
        the class code or the requirement list, not read it out of the score.
        """
        if not required_codes:
            return 0.0
        populated = sum(1 for code in required_codes if self._has_publishable(code))
        return populated / len(required_codes)

    def self_declared_rate(self, required_codes: list[str]) -> float:
        """Share of required attributes for which the client's own input suggests a value.

        Reported *beside* :meth:`fill_rate`, never folded into it. Without this the honest
        completeness number would make the description parse invisible and the pipeline would look
        like it had done nothing on a row it had in fact read correctly; with it folded in, the
        input would masquerade as enrichment. Two numbers, because there are two facts.
        """
        if not required_codes:
            return 0.0
        return sum(1 for code in required_codes if code in self._self_declared_codes()) / len(
            required_codes
        )

    def corroborated_rate(self, required_codes: list[str]) -> float:
        """Share of required attributes an independent source *confirmed* the input's suggestion on.

        The number that says retrieval is working rather than merely running. A high
        :meth:`self_declared_rate` with a low value here means documents are being fetched that do
        not speak to what the descriptions claim, which is a retrieval-targeting problem and looks
        nothing like a coverage problem in the other two metrics.
        """
        if not required_codes:
            return 0.0
        suggested = self._self_declared_codes()
        confirmed = sum(
            1 for code in required_codes if code in suggested and self._has_publishable(code)
        )
        return confirmed / len(required_codes)

    def _self_declared_codes(self) -> set[str]:
        """Attribute codes the client's own input suggested, whether or not it was later confirmed.

        Scans every version rather than :meth:`current_values`, and that is load-bearing. A document
        that confirms a description-derived value supersedes it, so by the time these metrics run
        the input's suggestion is no longer *current* — reading only current values would report
        zero self-declared and zero corroborated on precisely the SKUs where retrieval did its job.
        """
        return {
            v.attribute_code for v in self.attribute_values if not v.method.is_independent
        }

    def verifiability(self) -> float:
        """Share of publishable values backed by verified, independent evidence.

        This is the dimension nobody else reports and the one that matters most.
        Derivation- and human-family values count as verified: a unit conversion inherits
        the provenance of the value it was computed from, and a human entry is itself
        an accountable source.

        Self-declared values are excluded twice over — they are not publishable, so they never
        reach the denominator, and :attr:`DerivationMethod.is_independent` would bar them if they
        did. Both guards are deliberate. This metric read 1.0 across nearly the whole sample
        catalogue when a verified substring of the input CSV satisfied it, which is the most
        flattering possible reading of having retrieved nothing.
        """
        values = [v for v in self.publishable_values() if v.method.is_independent]
        if not values:
            # No independent value, so nothing has been verified. Zero is the measurement, not a
            # missing one: the SKU may well be full of description-derived candidates, and none of
            # them has been confirmed by anybody.
            return 0.0
        verified = sum(
            1
            for v in values
            if v.has_verified_evidence
            or v.method.is_human
            or v.method not in _EVIDENCE_EXPECTED
        )
        return verified / len(values)

    def _has_publishable(self, attribute_code: str) -> bool:
        """Whether *any* current candidate for this attribute is publishable.

        Any, not the highest-version one. Cross-source candidates accumulate through
        :meth:`add_candidate` without superseding each other, so an attribute can hold a
        description-derived candidate and a datasheet-derived one at the same version. Asking
        :meth:`get` would pick between them by list order and could answer "not established" about
        an attribute a manufacturer document states outright.
        """
        return any(
            v.is_publishable
            for v in self.current_values()
            if v.attribute_code == attribute_code
        )


_EVIDENCE_EXPECTED = frozenset(
    m for m in DerivationMethod if m.requires_evidence or m.is_inference or m.is_unsourced
)
"""Methods for which absent evidence means unverified.

Legacy values belong here for the reason that makes the cohort study worth running: an item
master row is not verified merely because it exists, and crediting it would report a catalogue as
provenance-complete on the strength of data nobody can trace.
"""
