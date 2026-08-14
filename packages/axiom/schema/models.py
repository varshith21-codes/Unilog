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


class Interchange(str, Enum):
    """What a difference in this attribute does to a substitution claim (blueprint M-resolve).

    Declared per attribute rather than hard-coded in the equivalence engine, for the same reason
    everything else here is declarative: whether a handle style blocks a substitution is a
    merchandising judgement, and the person who holds it should be able to change it without
    touching Python.

    The four levels are ordered by how much a difference costs, and they are genuinely different
    claims rather than a severity scale:
    """

    DEFINING = "defining"
    """Differs -> a different product. A 3/4" valve does not substitute for a 1" valve at any
    price, so no other agreement can rescue it."""

    CRITICAL = "critical"
    """Differs -> form or fit differs, so it is not a drop-in. It may still do the same job: a
    solder-end valve performs identically to a threaded one and needs different fittings."""

    FUNCTIONAL = "functional"
    """Must be met or exceeded, per :class:`SubstitutionRule`. A lower pressure rating is not a
    substitute; a higher one is."""

    COSMETIC = "cosmetic"
    """No bearing on interchangeability. Reported as a difference, never as a blocker."""

    @property
    def blocks_drop_in(self) -> bool:
        return self in {Interchange.DEFINING, Interchange.CRITICAL}

    @property
    def decides_verdict(self) -> bool:
        """Whether a difference here can change the verdict at all."""
        return self is not Interchange.COSMETIC


class SubstitutionRule(str, Enum):
    """The direction in which a candidate must relate to the reference to be acceptable.

    Substitution is **asymmetric**, and this enum is where that lives. A 600 psi valve
    substitutes for a 400 psi one; the reverse is a downgrade that could fail in service. An
    engine that compared values for equality would either reject every safe upgrade or accept
    every unsafe downgrade, and both are wrong in a way a distributor would notice.
    """

    EQUAL = "equal"
    """Must agree. The default, and the right answer for materials: ranking alloys is a
    metallurgical judgement, not a string comparison."""

    AT_LEAST = "at_least"
    """Candidate must be greater than or equal to the reference. Ratings, flow coefficients,
    and compliance flags — where holding a certification the reference lacks is never a fault."""

    AT_MOST = "at_most"
    """Candidate must be less than or equal to the reference. For attributes where more is
    worse, such as whether a Proposition 65 warning is required."""

    ENCLOSES = "encloses"
    """Candidate's range must contain the reference's. A narrower service window is a
    downgrade even when both bounds look reasonable in isolation."""

    SUPERSET = "superset"
    """Candidate must hold every value the reference holds, and may hold more."""

    @property
    def is_directional(self) -> bool:
        return self is not SubstitutionRule.EQUAL


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
        "attribute name works until a supplier writes 'Ctn' and then fails silently.\n\n"
        "Order is significant. When two columns of one table both resolve to this attribute — "
        "an ordering table carrying both 'DN' and 'Size' — the header appearing earlier in this "
        "tuple wins, so the schema author's preference is explicit rather than decided by which "
        "column the supplier happened to print first.",
    )
    spec_labels: tuple[str, ...] = Field(
        default=(),
        description="Row labels that identify this attribute in a SPECIFICATION BLOCK, e.g. "
        "'Pressure Rating' in 'Pressure Rating ....... 600 PSI WOG'.\n\n"
        "Deliberately separate from `table_headers`, because the same word means different "
        "things in the two layouts and conflating them produces confident wrong values. The case "
        "that forced the split: `handle_type` declares the table header 'Handwheel', which is "
        "correct for an ordering-table column whose cells read 'Lever' or 'Tee'. In a "
        "specification block, 'Handwheel ....... Malleable Iron' is the handwheel's *material*, "
        "and reading it as a handle type yields a cited, plausible, wrong value.\n\n"
        "The attribute's `name` is always matched in both layouts, so a label identical to the "
        "name needs no entry here.",
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
    interchange: Interchange | None = Field(
        default=None,
        description="What a difference in this attribute does to a substitution claim. "
        "Undeclared on purpose rather than defaulted: the equivalence engine reports an "
        "unclassified attribute instead of silently ignoring it, the same way variant "
        "explosion reports an ordering-table column no attribute claimed. Defaulting to "
        "cosmetic would make a newly added attribute vanish from every equivalence verdict "
        "with nothing to show it had been overlooked.",
    )
    substitution: SubstitutionRule = Field(
        default=SubstitutionRule.EQUAL,
        description="Direction the candidate must satisfy relative to the reference. Equality "
        "is the safe default: it can only ever refuse a substitution that a directional rule "
        "would have allowed.",
    )

    @field_validator(
        "example_values", "extraction_hints", "table_headers", "spec_labels", mode="before"
    )
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
        self._check_substitution()
        return self

    def _check_substitution(self) -> None:
        """Refuse a substitution rule the datatype cannot support.

        Every one of these would otherwise fail silently at comparison time by declining to
        compare, which reads in a report as "these two products agree" — the single most
        misleading thing an equivalence engine can say.
        """
        rule, datatype = self.substitution, self.datatype

        if rule is SubstitutionRule.ENCLOSES and datatype is not Datatype.RANGE:
            raise ValueError(
                f"attribute '{self.code}' declares substitution: encloses but is "
                f"{datatype.value}, not a range; there are no bounds to enclose"
            )
        if rule is SubstitutionRule.SUPERSET and datatype is not Datatype.MULTI_ENUM:
            raise ValueError(
                f"attribute '{self.code}' declares substitution: superset but is "
                f"{datatype.value}; only a multi_enum holds a set of values"
            )
        if rule in {SubstitutionRule.AT_LEAST, SubstitutionRule.AT_MOST}:
            comparable = datatype.is_numeric or datatype is Datatype.BOOLEAN
            if not comparable or datatype in {Datatype.RANGE, Datatype.DIMENSION_SET}:
                raise ValueError(
                    f"attribute '{self.code}' declares substitution: {rule.value} but is "
                    f"{datatype.value}, which has no single magnitude to order. Use encloses "
                    f"for a range"
                )
        if rule.is_directional and self.interchange is None:
            raise ValueError(
                f"attribute '{self.code}' declares substitution: {rule.value} without an "
                f"interchange level, so the direction can never be applied. Declare "
                f"interchange, or leave substitution at its default"
            )
        if rule.is_directional and self.interchange is Interchange.COSMETIC:
            raise ValueError(
                f"attribute '{self.code}' declares substitution: {rule.value} but is "
                f"interchange: cosmetic, which never affects a verdict — the direction is dead "
                f"configuration"
            )

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
    label: str | None = Field(
        default=None,
        description="Customer-facing label for this attribute *in this class*, overriding the "
        "dictionary name. Exists because the display label is genuinely per-class while the "
        "attribute is shared: `product_series` is named 'Product Series' in the dictionary and "
        "must print as 'Series' in the dishwasher grid. Unilog's own List of Values is keyed by "
        "(Classpath, Attribute Label) for exactly this reason. Renaming the dictionary entry "
        "instead would change the label for every other class that binds it.",
    )

    @property
    def display_label(self) -> str | None:
        """The override, if declared. Callers fall back to the definition's ``name``."""
        return self.label


class ClassDefinition(BaseModel):
    """A product class: which attributes apply, which rules hold, where it publishes."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9.]*$")
    name: str
    version: str
    item_type: str | None = Field(
        default=None,
        description="The bare noun a buyer would call this product — 'Dishwasher', not "
        "'Built-In Dishwasher'. Distinct from `name`, which is a class label and properly "
        "carries qualifiers. Needed wherever the product is named inside a sentence: the "
        "delivery format's `Product Name` column expects 'Dishwasher', and the long "
        "description reads 'FRIGIDAIRE(R) Dishwasher With CleanBoost(TM)...'. Depluralising "
        "the browse path to get it would be fragile and wrong as often as not. Falls back to "
        "`name` when undeclared.",
    )

    identity_terms: tuple[str, ...] = Field(
        default=(),
        description="Terms that must appear in the product text for this class to be a "
        "candidate at all. The classifier's own first rule is to decide on what the product IS "
        "rather than how it is described, and without this that rule is only enforced when a "
        "model is in the loop.\n\n"
        "It exists because retrieval scores attribute vocabulary as readily as identity "
        "vocabulary, and attribute vocabulary is promiscuous. Measured on the 1,000-row Unilog "
        "sample with three classes and no term guard: 45 rows classified and 35 of them wrongly, "
        "because 'Castle Gate' PVC decking matched the gate-valve class, a '2 Port Decor Plate' "
        "outscored every real dishwasher on the strength of 'Port Type', and cut-off discs "
        "matched ball valves on shared size fractions. A score floor cannot fix it: the best "
        "false positive scored 0.1854 against a true-positive floor of 0.1725.\n\n"
        "Include abbreviations a supplier might actually write, since that is the whole "
        "difficulty — 'dw' for dishwasher, 'cplg' for coupling. Leave empty to disable the "
        "guard for a class, which is the backward-compatible default.",
    )

    browse_path: tuple[str, ...] = ()
    """The customer-facing browse tree — how buyers shop. Rendered as the delivery format's
    ``Classpath``."""

    reporting_path: tuple[str, ...] = Field(
        default=(),
        description="The internal reporting hierarchy, distinct from browse_path. Distributors "
        "commonly run two trees: one the buyer navigates and one finance and merchandising "
        "report against. In the client's delivery format these are genuinely different strings "
        "for the same product — browse_path is 'Appliances & Consumer Electronics > Kitchen "
        "Appliances > Built-In Dishwashers' while reporting_path is 'Appliances / Large "
        "Appliances / Dishwashers'. Neither is derivable from the other, so both are declared.",
    )

    mappings: dict[str, str] = Field(
        default_factory=dict,
        description="External scheme codes: ETIM, UNSPSC, eCl@ss. Multi-target by design — "
        "the browse tree and the technical classification are different things.",
    )
    attributes: tuple[AttributeBinding, ...]
    cross_field_rules: tuple[CrossFieldRule, ...] = ()
    channel_profiles: tuple[ChannelProfile, ...] = ()

    @field_validator("browse_path", "reporting_path", "identity_terms", mode="before")
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

    @property
    def product_noun(self) -> str:
        """The bare item type, falling back to the class name."""
        return self.item_type or self.name

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

    def slot_bindings(self) -> tuple[AttributeBinding, ...]:
        """Bindings in declared order — the attribute-grid slot assignment.

        Declared order is load-bearing for the delivery format, where slot *n* of the
        ``ATTRIBUTE_LABEL/VALUE/UOM`` grid is binding *n*. Reordering this tuple silently
        reshuffles a published column layout, so it is exposed as an explicit method rather than
        left to callers iterating ``attributes`` and hoping the order is meaningful.
        """
        return self.attributes

    def channel(self, name: str) -> ChannelProfile | None:
        for profile in self.channel_profiles:
            if profile.name == name:
                return profile
        return None
