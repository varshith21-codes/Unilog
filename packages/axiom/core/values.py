"""Attribute values, and the structural enforcement of "evidence or null".

The key class here is `AttributeValue`. It stores three representations of every value:

* ``value_raw``       — exactly as it appeared in the source, never touched
* ``value_canonical`` — normalized and machine-comparable (canonical unit, resolved enum)
* ``value_display``   — what a human should read

Most systems store one of these and lose either auditability or usability. Keeping all
three is cheap and it is what makes both the evidence viewer and faceted search possible
from the same record.

`AttributeValue` also enforces the central invariant of the system: a value derived by an
extraction-family method **must** carry at least one evidence span. This is checked at
construction, so it is not possible for a caller to forget.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from axiom.core.evidence import EvidenceSpan
from axiom.core.validation import ValidationResult


class DerivationMethod(str, Enum):
    """How a value came to exist. Determines the evidence requirement."""

    # --- extraction family: evidence is mandatory ---
    DOCUMENT_EXTRACTION = "document_extraction"
    TABLE_EXTRACTION = "table_extraction"
    IMAGE_EXTRACTION = "image_extraction"
    WEB_EXTRACTION = "web_extraction"
    SUPPLIER_FEED = "supplier_feed"

    # --- derivation family: traceable to another value, evidence optional ---
    UNIT_CONVERSION = "unit_conversion"
    ENUM_RESOLUTION = "enum_resolution"
    COMPUTED = "computed"

    # --- inference family: no evidence, must never satisfy a compliance claim ---
    PART_NUMBER_GRAMMAR = "part_number_grammar"
    FAMILY_INFERENCE = "family_inference"
    STATISTICAL_DEFAULT = "statistical_default"

    # --- human ---
    HUMAN_ENTRY = "human_entry"
    HUMAN_CORRECTION = "human_correction"

    # --- legacy: present, but of unknown origin ---
    LEGACY_RECORD = "legacy_record"
    """A value that was already in the item master when AXIOM arrived.

    Every customer's starting state, and the one provenance case the other families cannot
    express. It is not an extraction, because nobody knows what document it came from. It is not
    an inference, because nothing derived it. And it is emphatically not a human entry: that
    family counts as verified on the grounds that a named person is an accountable source, and
    the defining property of a legacy row is that the person is not named.

    So it requires no evidence — there is none to be had — and is **never publishable** without
    one. That is not a technicality, it is the thesis: a value nobody can source is a gap wearing
    a value's clothing, and the before/after cohort exists to put a number on how many of them a
    catalogue contains.
    """

    @property
    def requires_evidence(self) -> bool:
        """Whether construction must be refused without an evidence span.

        False for legacy values even though they are unsourced, because refusing to *model* the
        state a catalogue is already in would leave it unmeasurable.
        """
        return self in _EXTRACTION_FAMILY

    @property
    def is_inference(self) -> bool:
        return self in _INFERENCE_FAMILY

    @property
    def is_human(self) -> bool:
        return self in {DerivationMethod.HUMAN_ENTRY, DerivationMethod.HUMAN_CORRECTION}

    @property
    def is_unsourced(self) -> bool:
        """Constructible without evidence, but not publishable without it."""
        return self is DerivationMethod.LEGACY_RECORD


_EXTRACTION_FAMILY = frozenset(
    {
        DerivationMethod.DOCUMENT_EXTRACTION,
        DerivationMethod.TABLE_EXTRACTION,
        DerivationMethod.IMAGE_EXTRACTION,
        DerivationMethod.WEB_EXTRACTION,
        DerivationMethod.SUPPLIER_FEED,
    }
)

_INFERENCE_FAMILY = frozenset(
    {
        DerivationMethod.PART_NUMBER_GRAMMAR,
        DerivationMethod.FAMILY_INFERENCE,
        DerivationMethod.STATISTICAL_DEFAULT,
    }
)


class ValueStatus(str, Enum):
    """Publication state of a single value."""

    CANDIDATE = "candidate"
    """Extracted but not yet validated."""

    AUTO_ACCEPTED = "auto_accepted"
    """Confidence exceeded the risk-controlled threshold. Publishable."""

    QUEUED_FOR_REVIEW = "queued_for_review"
    """Below threshold, or failed a validation layer. Not publishable."""

    HUMAN_APPROVED = "human_approved"
    """A reviewer confirmed it. Publishable."""

    REJECTED = "rejected"
    """Failed hard validation, or a reviewer rejected it. Never publishable."""

    SUPERSEDED = "superseded"
    """Replaced by a newer version of this attribute."""

    @property
    def is_publishable(self) -> bool:
        return self in {ValueStatus.AUTO_ACCEPTED, ValueStatus.HUMAN_APPROVED}


class UnitMismatchError(TypeError):
    """Raised when two quantities with different units are combined or compared.

    A ``TypeError`` subclass so an expression evaluator treating it as a type problem behaves
    sensibly, while callers who care can catch it specifically.
    """


class Quantity(BaseModel):
    """A magnitude with a unit. Canonical unit is enforced by the normalize package.

    Arithmetic and comparison are unit-checked. Cross-field rules need to write
    ``steam_pressure_rating <= pressure_rating_wog`` and
    ``case_weight - each_weight * case_quantity`` without unwrapping magnitudes by hand, and
    doing that on bare floats would compare pounds against kilograms without complaint. The
    unit check makes that specific mistake impossible rather than merely unlikely.
    """

    model_config = ConfigDict(frozen=True)

    magnitude: float
    unit: str = Field(description="UCUM-style unit code, e.g. 'psi', 'mm', 'N.m'")

    def __str__(self) -> str:
        return f"{self.magnitude:g} {self.unit}"

    def _require_same_unit(self, other: Quantity) -> None:
        if self.unit != other.unit:
            raise UnitMismatchError(
                f"cannot combine {self.unit!r} with {other.unit!r}; convert to a common unit "
                f"first"
            )

    # --- comparison ---
    def __lt__(self, other: Quantity) -> bool:
        self._require_same_unit(other)
        return self.magnitude < other.magnitude

    def __le__(self, other: Quantity) -> bool:
        self._require_same_unit(other)
        return self.magnitude <= other.magnitude

    def __gt__(self, other: Quantity) -> bool:
        self._require_same_unit(other)
        return self.magnitude > other.magnitude

    def __ge__(self, other: Quantity) -> bool:
        self._require_same_unit(other)
        return self.magnitude >= other.magnitude

    # --- arithmetic ---
    def __add__(self, other: Quantity) -> Quantity:
        self._require_same_unit(other)
        return Quantity(magnitude=self.magnitude + other.magnitude, unit=self.unit)

    def __sub__(self, other: Quantity) -> Quantity:
        self._require_same_unit(other)
        return Quantity(magnitude=self.magnitude - other.magnitude, unit=self.unit)

    def __mul__(self, factor: float | int) -> Quantity:
        if isinstance(factor, Quantity):
            raise UnitMismatchError(
                "multiplying two quantities would change the quantity kind, which this "
                "model does not represent"
            )
        return Quantity(magnitude=self.magnitude * factor, unit=self.unit)

    __rmul__ = __mul__

    def __truediv__(self, other: float | int | Quantity) -> Quantity | float:
        """Dividing by a scalar scales; dividing by a like quantity yields a ratio.

        The ratio case is what lets a packaging-consistency rule express a tolerance as a
        fraction rather than as an absolute weight.
        """
        if isinstance(other, Quantity):
            self._require_same_unit(other)
            if other.magnitude == 0:
                raise ZeroDivisionError("division by a zero quantity")
            return self.magnitude / other.magnitude
        if other == 0:
            raise ZeroDivisionError("division by zero")
        return Quantity(magnitude=self.magnitude / other, unit=self.unit)

    def __abs__(self) -> Quantity:
        return Quantity(magnitude=abs(self.magnitude), unit=self.unit)

    def __neg__(self) -> Quantity:
        return Quantity(magnitude=-self.magnitude, unit=self.unit)

    def __float__(self) -> float:
        return float(self.magnitude)


class ValueRange(BaseModel):
    """An inclusive range. Industrial specs are full of these and a scalar float loses them."""

    model_config = ConfigDict(frozen=True)

    minimum: float
    maximum: float
    unit: str

    @model_validator(mode="after")
    def _ordered(self) -> ValueRange:
        if self.minimum > self.maximum:
            raise ValueError(
                f"range minimum ({self.minimum}) must not exceed maximum ({self.maximum})"
            )
        return self

    def __str__(self) -> str:
        return f"{self.minimum:g} to {self.maximum:g} {self.unit}"


CanonicalValue = float | int | str | bool | Quantity | ValueRange | list[str]


class AttributeValue(BaseModel):
    """A single attribute value with full provenance, confidence and validation state.

    Append-only by convention: to change a value, insert a new one with an incremented
    ``version`` and mark the previous one :attr:`ValueStatus.SUPERSEDED`. This gives free
    history, safe rollback, honest diffs on datasheet revision, and an audit trail that
    survives a compliance question two years later.
    """

    model_config = ConfigDict(validate_assignment=True)

    attribute_code: str
    value_raw: str | None = Field(
        default=None, description="Exactly as it appeared in the source, untouched"
    )
    value_canonical: CanonicalValue | None = None
    value_display: str | None = None

    method: DerivationMethod
    confidence: float = Field(ge=0.0, le=1.0)
    status: ValueStatus = ValueStatus.CANDIDATE

    evidence: list[EvidenceSpan] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)

    # --- reproducibility: these are what let you attribute a regression to a change ---
    model_id: str | None = None
    model_tier: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None

    # --- versioning ---
    version: int = Field(default=1, ge=1)
    superseded_by: int | None = None
    derived_from: str | None = Field(
        default=None,
        description="For derivation-family methods, the attribute_code this was computed from",
    )

    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _enforce_evidence_requirement(self) -> AttributeValue:
        """The central invariant: extraction implies evidence.

        A value claiming to have been read out of a document must say where. Inference
        methods are permitted without evidence, but they are marked as such and are
        excluded from compliance claims elsewhere in the pipeline.
        """
        if self.method.requires_evidence and not self.evidence:
            raise ValueError(
                f"attribute '{self.attribute_code}' uses method '{self.method.value}', which "
                f"requires at least one evidence span. If no source supports this value, "
                f"record a Gap instead of an AttributeValue."
            )
        return self

    @model_validator(mode="after")
    def _inference_cannot_be_auto_accepted_silently(self) -> AttributeValue:
        """Inferred values may be published, but never without being marked."""
        if self.method.is_inference and self.status == ValueStatus.AUTO_ACCEPTED:
            raise ValueError(
                f"attribute '{self.attribute_code}' was inferred via "
                f"'{self.method.value}' and cannot be AUTO_ACCEPTED. Inferred values must be "
                f"reviewed (HUMAN_APPROVED) or remain QUEUED_FOR_REVIEW."
            )
        return self

    @property
    def has_verified_evidence(self) -> bool:
        """True when at least one span had its quote matched back to the source."""
        return any(span.quote_verified for span in self.evidence)

    @property
    def is_publishable(self) -> bool:
        """Publishable requires accepted status *and*, for extractions, verified evidence."""
        if not self.status.is_publishable:
            return False
        if self.method.requires_evidence and not self.has_verified_evidence:
            return False
        # A legacy row is constructible without evidence, so that the state a catalogue starts in
        # can be measured, but it must never publish on that basis. If it cannot be sourced it is
        # a gap, whatever it looks like in the item master.
        if self.method.is_unsourced and not self.has_verified_evidence:
            return False
        return not self.failed_validations()

    def failed_validations(self) -> list[ValidationResult]:
        """All blocking validation failures. Warnings are excluded."""
        return [v for v in self.validations if v.is_blocking]

    def supersede(self, replacement_version: int) -> None:
        self.superseded_by = replacement_version
        self.status = ValueStatus.SUPERSEDED

    def citation_summary(self) -> list[str]:
        return [span.locator() for span in self.evidence]

    def to_certificate_entry(self) -> dict[str, Any]:
        """Render this value for inclusion in an Enrichment Certificate."""
        canonical: Any = self.value_canonical
        if isinstance(canonical, BaseModel):
            canonical = canonical.model_dump()
        return {
            "code": self.attribute_code,
            "value_canonical": canonical,
            "value_display": self.value_display,
            "value_raw": self.value_raw,
            "confidence": round(self.confidence, 4),
            "method": self.method.value,
            "model_tier": self.model_tier,
            "status": self.status.value,
            "evidence": [
                {
                    "document": span.document_id,
                    "sha256": span.document_sha256[:8] + "…",
                    "page": span.page,
                    "bbox": span.bbox.as_list() if span.bbox else None,
                    "quote": span.quote,
                    "verified": span.quote_verified,
                }
                for span in self.evidence
            ],
            "validations": [v.to_certificate_entry() for v in self.validations],
        }
