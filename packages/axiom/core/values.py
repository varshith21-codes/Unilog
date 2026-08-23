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

    # --- self-declared: the client's own input, parsed. Evidence-bearing, never publishable ---
    ITEM_MASTER_PARSE = "item_master_parse"
    """A value read out of the client's own item-master row — almost always ``Part_Desc``.

    This is the method that keeps the system honest about its own input, and it exists because
    ``SUPPLIER_FEED`` was quietly doing two incompatible jobs. A supplier feed proper is a
    structured file a *manufacturer* published; the item master is the 1,000-row CSV the client
    asked us to enrich. Filing the second under the first put the question being asked into the
    answer: parse ``230V`` out of ``MILW 230V ANGLE GRINDER``, cite the row it came from, and the
    SKU scores as evidenced — on the strength of the string we were handed.

    The span is real and worth keeping. ``description[start:end] == quote`` is a genuine substring
    check, the derivation is declared in version-controlled YAML, and a reviewer should be able to
    see what the description implied. So this method **requires** evidence like any extraction.

    What it must not do is publish. ``Part_Desc`` is unsourced free text written to move stock: it
    abbreviates, it rounds, it inherits a family's figure for a specific variant, and nobody can be
    asked to correct it. A specification traceable only to it is a **hypothesis about the product,
    not a fact about it** — the same reasoning ``LEGACY_RECORD`` already applies to an untraceable
    legacy row, and the same verdict ``evaluation/cohort.py`` already reaches when it scores the
    client's starting state at zero completeness.

    So it is a candidate, and it is a good one: it tells retrieval which attribute to go and confirm
    on the manufacturer's page. Once a manufacturer document states the same value, that document
    supplies its own ``DOCUMENT_EXTRACTION``/``TABLE_EXTRACTION`` value and *that* one publishes.
    Until then the attribute is a gap, which is the truth.
    """

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

    @property
    def is_self_declared(self) -> bool:
        """Cited against the client's own item master, so never publishable however well cited.

        Deliberately narrower than :attr:`is_independent`. This one gates *publication*, and it
        names only ``ITEM_MASTER_PARSE`` so that ``LEGACY_RECORD`` keeps the behaviour it has: a
        legacy value that someone has since attached real evidence to may publish on the strength of
        that evidence. The item-master parse cannot, because its evidence is the input itself and no
        amount of it will ever be independent.
        """
        return self is DerivationMethod.ITEM_MASTER_PARSE

    @property
    def is_independent(self) -> bool:
        """Whether this method can rest on a source outside the file we were asked to enrich.

        The predicate the quality metrics are computed over, and it is about the *method's*
        capability, not about a particular value: whether a given value truly stands on an
        independent source also depends on the spans it carries. Both self-declared methods are
        excluded here even though only one of them is barred from publishing, because a metric that
        counted either would be measuring the input.
        """
        return self not in _SELF_DECLARED_FAMILY


_EXTRACTION_FAMILY = frozenset(
    {
        DerivationMethod.DOCUMENT_EXTRACTION,
        DerivationMethod.TABLE_EXTRACTION,
        DerivationMethod.IMAGE_EXTRACTION,
        DerivationMethod.WEB_EXTRACTION,
        DerivationMethod.SUPPLIER_FEED,
        # Requires a span for the same reason the others do: it read a document and must cite the
        # place it read. That the document is the client's own file is a question of *authority*,
        # settled by `_SELF_DECLARED_FAMILY`, not of whether a citation is owed.
        DerivationMethod.ITEM_MASTER_PARSE,
    }
)

_SELF_DECLARED_FAMILY = frozenset(
    {
        DerivationMethod.ITEM_MASTER_PARSE,
        # A legacy row is self-declared too, and more weakly than the item master: at least the
        # item master names the file it arrived in.
        DerivationMethod.LEGACY_RECORD,
    }
)
"""Methods whose only possible source is the catalogue we were asked to enrich.

Excluded from completeness and verifiability by construction. Crediting them would let a catalogue
report itself as enriched by restating its own input, which is the specific failure this system
exists to make impossible.
"""

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
        # A bare number is the commoner mistake, and it used to be the worse one. Reaching for
        # `other.unit` on a float raised `AttributeError`, which is not a `TypeError`, so the
        # expression evaluator's `except TypeError` did not catch it and a single malformed rule in
        # one class aborted an entire batch — found exactly that way, on a real 1,000-row run, from
        # `wheel_thickness <= 6.0`.
        #
        # Reported as a unit mismatch because that is what it is: `6.0` is not six millimetres, it
        # is
        # six of nothing. Refusing it is the same protection that stops pounds being compared with
        # kilograms, and `UnitMismatchError` subclasses `TypeError` so the evaluator now degrades to
        # "this rule is malformed" and the other rules still run.
        if not isinstance(other, Quantity):
            raise UnitMismatchError(
                f"cannot compare {self.unit!r} with a unitless {type(other).__name__}: a rule "
                f"comparing a quantity to a bare number is ambiguous. Compare against another "
                f"quantity, or against '.magnitude' if the constant is in canonical units."
            )
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
        # Self-declared values never publish, however well cited. The span proves the client's file
        # says this; it does not make the claim true, and the citation would read to a downstream
        # consumer exactly like one that terminates in the manufacturer's own drawing. An
        # independent source has to state it before it can be published — at which point that
        # source contributes its own value and this one is superseded or stays a corroborating
        # candidate.
        if self.method.is_self_declared:
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
