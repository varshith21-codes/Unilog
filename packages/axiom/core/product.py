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

    def add_gap(self, gap: Gap) -> None:
        self.gaps.append(gap)
        self.updated_at = datetime.now(UTC)

    # ------------------------------------------------------------------ metrics

    def fill_rate(self, required_codes: list[str]) -> float:
        """Share of required attributes with a publishable value."""
        if not required_codes:
            return 1.0
        populated = sum(1 for code in required_codes if self._has_publishable(code))
        return populated / len(required_codes)

    def verifiability(self) -> float:
        """Share of publishable values backed by verified evidence.

        This is the dimension nobody else reports and the one that matters most.
        Derivation- and human-family values count as verified: a unit conversion inherits
        the provenance of the value it was computed from, and a human entry is itself
        an accountable source.
        """
        values = self.publishable_values()
        if not values:
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
        v = self.get(attribute_code)
        return v is not None and v.is_publishable


_EVIDENCE_EXPECTED = frozenset(
    m for m in DerivationMethod if m.requires_evidence or m.is_inference
)
