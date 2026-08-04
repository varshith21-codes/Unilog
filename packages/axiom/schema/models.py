"""Declarative schema model: the attribute dictionary and class bindings.

This is the governed definition of *what a complete product looks like*. It is the
unglamorous module that determines whether everything else works, and it is deliberately
**data, not code** — the YAML under ``schema/`` is the source of truth, and adding an
attribute requires no Python change and no prompt change.

Two concepts, kept separate on purpose (blueprint M3):

* :class:`AttributeDefinition` — a reusable definition of one attribute: its datatype, its
  unit expectations, its allowed values, and the natural-language description and example
  values that get rendered into the extraction prompt.
* :class:`ClassDefinition` — a product class binding attributes as required or recommended,
  plus the cross-field rules and channel profiles that apply to that class.

The separation matters because ``body_material`` means the same thing for a ball valve and
a gate valve. Defining it twice is how the two definitions drift apart.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Datatype(str, Enum):
    """Value shapes. Chosen to cover what industrial specs actually contain."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    MULTI_ENUM = "multi_enum"
    QUANTITY = "quantity"
    """A magnitude with a unit, e.g. 600 psi."""

    RANGE = "range"
    """An inclusive interval, e.g. -20 to 60 degC. A scalar float loses this."""

    DIMENSION = "dimension"
    """A length-like measurement, rendered in the locale's preferred form."""

    DIMENSION_SET = "dimension_set"
    """Ordered components, e.g. 2 x 4 x 6 in."""

    @property
    def is_numeric(self) -> bool:
        return self in {
            Datatype.INTEGER,
            Datatype.NUMBER,
            Datatype.QUANTITY,
            Datatype.RANGE,
            Datatype.DIMENSION,
            Datatype.DIMENSION_SET,
        }

    @property
    def needs_unit(self) -> bool:
        return self in {
            Datatype.QUANTITY,
            Datatype.RANGE,
            Datatype.DIMENSION,
            Datatype.DIMENSION_SET,
        }

    @property
    def is_enumerated(self) -> bool:
        return self in {Datatype.ENUM, Datatype.MULTI_ENUM}


class EvidenceRequirement(str, Enum):
    """How strictly a value must be sourced."""

    STANDARD = "standard"
    """Extraction requires evidence; inference is permitted but marked."""

    STRICT = "strict"
    """Inference is forbidden outright. Used for compliance claims — a lead-free flag may
    never be derived from a product family, only from a certificate or declaration."""


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Requirement(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class AllowedValue(BaseModel):
    """One permitted enum value, with the source spellings that map onto it.

    Aliases are what make enum snapping deterministic. ``st. steel``, ``SS304`` and
    ``304 SS`` all mean the same thing on a datasheet, and resolving them by lookup is
    strictly better than asking a model to guess.
    """

    model_config = ConfigDict(frozen=True)

    value: str
    aliases: tuple[str, ...] = ()
    note: str | None = None

    def matches(self, candidate: str) -> bool:
        target = _fold(candidate)
        return target == _fold(self.value) or any(target == _fold(a) for a in self.aliases)


def _fold(text: str) -> str:
    """Casefold and strip punctuation/whitespace for alias comparison."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Datatype-specific output guidance, injected into the prompt per attribute.
#
# This exists because of an observed failure: for a boolean compliance attribute, the model
# returned the supporting text ("NSF/ANSI 61") as the value instead of a boolean. That is a
# reasonable thing for a model to do given no instruction, and it would have broken every
# boolean attribute in the catalogue — including the compliance flags, which are the
# highest-stakes fields we have. Telling it where the citation goes fixes it.
_REPORTING_GUIDANCE: dict[Datatype, str] = {
    Datatype.BOOLEAN: (
        'exactly "true" or "false" in value_raw. Put the supporting text in '
        "evidence_quote, not in value_raw."
    ),
    Datatype.MULTI_ENUM: (
        "a comma-separated list of every applicable value in value_raw. Report all that "
        "the source states, not just the first."
    ),
    Datatype.RANGE: (
        'the complete range including both bounds and the unit, e.g. "-20 degF to 366 degF". '
        "Do not report only one bound."
    ),
    Datatype.DIMENSION_SET: (
        'all components in order with the unit, e.g. "2 x 4 x 6 in".'
    ),
    Datatype.INTEGER: "a whole number only, with no unit and no thousands separator.",
}


class AttributeDefinition(BaseModel):
    """A reusable attribute definition. Renders directly into the extraction prompt."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    datatype: Datatype
    description: str = Field(
        min_length=10,
        description="Natural-language definition. This is prompt input, not documentation — "
        "supplying attribute descriptions and example values measurably improves "
        "extraction quality, so a vague description is a real defect.",
    )
    example_values: tuple[str, ...] = ()
    extraction_hints: tuple[str, ...] = Field(
        default=(),
        description="Where this typically appears, e.g. 'first column of an ordering table'",
    )
    table_headers: tuple[str, ...] = Field(
        default=(),
        description="Column headings that identify this attribute in an ordering table, e.g. "
        "'Size' or 'Carton Qty'. Used by variant explosion to bind a column to an attribute "
        "deterministically. Declarative on purpose: matching headers by guessing at the "
        "attribute name works until a supplier writes 'Ctn' and then fails silently.",
    )

    quantity_kind: str | None = None
    canonical_unit: str | None = None
    display_preference: str | None = None
    allowed_values: tuple[AllowedValue, ...] = ()
    pattern: str | None = Field(default=None, description="Regex the raw value must satisfy")
    tolerance: float | None = Field(
        default=None,
        ge=0.0,
        description="Relative tolerance for numeric comparison. Defines what 'equal' means "
        "for this attribute, so accuracy scoring is not left to assumption.",
    )
    plausible_range: tuple[float, float] | None = Field(
        default=None, description="Feeds L3 statistical plausibility checking"
    )
    multivalued: bool = False
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.STANDARD
    compliance_claim: bool = Field(
        default=False,
        description="Legally consequential. Never satisfiable by inference, and excluded "
        "from any channel requiring verified data.",
    )
    unit_hint: str | None = Field(
        default=None, description="Unit to assume when the source states a bare number"
    )

    @field_validator("example_values", "extraction_hints", "table_headers", mode="before")
    @classmethod
    def _coerce_tuple(cls, v):
        if v is None:
            return ()
        return tuple(v) if isinstance(v, list | tuple) else (v,)

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> AttributeDefinition:
        if self.datatype.is_enumerated and not self.allowed_values:
            raise ValueError(
                f"attribute '{self.code}' is {self.datatype.value} but declares no "
                f"allowed_values; enum snapping has nothing to snap to"
            )
        if not self.datatype.is_enumerated and self.allowed_values:
            raise ValueError(
                f"attribute '{self.code}' declares allowed_values but is "
                f"{self.datatype.value}, not an enum"
            )
        if self.datatype.needs_unit and not self.canonical_unit:
            raise ValueError(
                f"attribute '{self.code}' is {self.datatype.value} and must declare a "
                f"canonical_unit, otherwise stored values are not comparable"
            )
        if self.tolerance is not None and not self.datatype.is_numeric:
            raise ValueError(
                f"attribute '{self.code}' declares a tolerance but is not numeric"
            )
        if self.compliance_claim and self.evidence_requirement is not EvidenceRequirement.STRICT:
            raise ValueError(
                f"attribute '{self.code}' is a compliance_claim and must therefore set "
                f"evidence_requirement: strict — a legal claim cannot rest on inference"
            )
        if self.multivalued and self.datatype is Datatype.ENUM:
            raise ValueError(
                f"attribute '{self.code}' is multivalued; use datatype multi_enum"
            )
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"attribute '{self.code}' has an invalid regex: {exc}") from exc
        return self

    def resolve_allowed(self, candidate: str) -> str | None:
        """Snap a raw value onto a permitted enum value, or None if nothing matches."""
        for allowed in self.allowed_values:
            if allowed.matches(candidate):
                return allowed.value
        return None

    def prompt_block(self) -> str:
        """Render this attribute for the extraction prompt.

        Generated from the definition rather than hand-written, so adding an attribute to
        the YAML changes the prompt with no code edit.
        """
        parts = [f"  {self.code} — {self.name} — {self._datatype_phrase()}"]
        parts.append(f"      {self.description.strip()}")
        if self.allowed_values:
            values = ", ".join(a.value for a in self.allowed_values)
            parts.append(f"      Permitted values: {values}")
        if self.example_values:
            parts.append(f"      Example values: {', '.join(self.example_values)}")
        for hint in self.extraction_hints:
            parts.append(f"      Hint: {hint}")
        if reporting := _REPORTING_GUIDANCE.get(self.datatype):
            parts.append(f"      Report as: {reporting}")
        if self.evidence_requirement is EvidenceRequirement.STRICT:
            parts.append(
                "      STRICT: report only if explicitly certified or declared in the "
                "source. Never infer from the product family."
            )
        return "\n".join(parts)

    def _datatype_phrase(self) -> str:
        if self.datatype.needs_unit:
            kind = f" {self.quantity_kind}" if self.quantity_kind else ""
            return f"{self.datatype.value} ({kind.strip()}, expected unit {self.canonical_unit})"
        return self.datatype.value


class CrossFieldRule(BaseModel):
    """A deterministic constraint spanning several attributes.

    Executed by validation layer L2. The registry validates that the referenced attribute
    codes exist; evaluation lives in the validate package.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    expr: str = Field(description="Boolean expression over attribute codes")
    severity: Severity = Severity.ERROR
    message: str
    references: tuple[str, ...] = Field(
        default=(),
        description="Attribute codes the rule depends on. Declared rather than parsed out "
        "of the expression, so a typo in the expression is caught at load time.",
    )

    @field_validator("references", mode="before")
    @classmethod
    def _coerce(cls, v):
        return tuple(v) if v else ()


class ChannelProfile(BaseModel):
    """Per-destination output requirements. A PIM is judged by what comes out of it."""

    model_config = ConfigDict(frozen=True)

    name: str
    title_template: str | None = None
    max_title_chars: int | None = Field(default=None, gt=0)
    required: tuple[str, ...] = ()
    unit_system: str | None = None

    @field_validator("required", mode="before")
    @classmethod
    def _coerce(cls, v):
        return tuple(v) if v else ()

    def template_tokens(self) -> set[str]:
        """Placeholder names referenced by the title template."""
        if not self.title_template:
            return set()
        return set(re.findall(r"\{([a-z][a-z0-9_]*)\}", self.title_template))


class AttributeBinding(BaseModel):
    """A class's use of a dictionary attribute, with the requirement level."""

    model_config = ConfigDict(frozen=True)

    code: str
    requirement: Requirement = Requirement.REQUIRED
    weight: float = Field(
        default=1.0,
        gt=0.0,
        description="Importance for completeness scoring. A missing pressure rating matters "
        "more than a missing handle colour, and a flat count hides that.",
    )


class ClassDefinition(BaseModel):
    """A product class: which attributes apply, which rules hold, where it publishes."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9.]*$")
    name: str
    version: str
    browse_path: tuple[str, ...] = ()
    mappings: dict[str, str] = Field(
        default_factory=dict,
        description="External scheme codes: ETIM, UNSPSC, eCl@ss. Multi-target by design — "
        "the browse tree and the technical classification are different things.",
    )
    attributes: tuple[AttributeBinding, ...]
    cross_field_rules: tuple[CrossFieldRule, ...] = ()
    channel_profiles: tuple[ChannelProfile, ...] = ()

    @field_validator("browse_path", mode="before")
    @classmethod
    def _coerce(cls, v):
        return tuple(v) if v else ()

    @model_validator(mode="after")
    def _no_duplicate_bindings(self) -> ClassDefinition:
        codes = [b.code for b in self.attributes]
        dupes = {c for c in codes if codes.count(c) > 1}
        if dupes:
            raise ValueError(f"class '{self.code}' binds duplicate attributes: {sorted(dupes)}")
        return self

    @property
    def schema_version(self) -> str:
        return f"{self.code}@{self.version}"

    def codes(self, *requirements: Requirement) -> list[str]:
        wanted = set(requirements) or set(Requirement)
        return [b.code for b in self.attributes if b.requirement in wanted]

    @property
    def required_codes(self) -> list[str]:
        return self.codes(Requirement.REQUIRED)

    def binding(self, code: str) -> AttributeBinding | None:
        for b in self.attributes:
            if b.code == code:
                return b
        return None

    def channel(self, name: str) -> ChannelProfile | None:
        for profile in self.channel_profiles:
            if profile.name == name:
                return profile
        return None
