"""The fact sheet: the only thing generated copy is allowed to know.

Generation is a separate subsystem from extraction, and this is the boundary between them. A
copy generator never sees a product record. It sees this — a flat list of attributes that are
already **publishable**, meaning verified evidence, no blocking validation failure, and either
auto-accepted above the risk threshold or approved by a human.

Everything excluded is excluded on purpose:

*   **Queued values.** A value awaiting review must not appear in a product description, because
    the description would go live before the review did. Copy is a publication channel like any
    other and gets the same gate.
*   **Inferred values.** Anything derived by part-number grammar or statistical default is a
    guess. A guess restated as prose reads exactly like a measured fact.
*   **Gaps.** What is unknown is not mentioned. The alternative — letting a model see the list of
    attributes it *could not* establish — invites it to fill them in, which is the failure this
    whole system exists to prevent.

The fact sheet also carries the indexes the claim checker needs, because both halves have to
agree on what "supported" means. Building them here rather than in the checker keeps a single
definition of the ground truth for one product.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.normalize import registry as unit_registry
from axiom.schema import SchemaRegistry


@dataclass(frozen=True)
class Fact:
    """One publishable attribute, in the form copy may refer to it."""

    attribute_code: str
    name: str
    display: str
    canonical: object
    unit: str | None = None
    is_compliance_claim: bool = False
    is_true_flag: bool = False
    """True when this is a boolean compliance attribute whose value is True. Used to decide
    whether a regulated phrase like 'lead-free' is substantiated."""

    def as_prompt_line(self) -> str:
        return f"- {self.name} ({self.attribute_code}): {self.display}"


@dataclass(frozen=True)
class QuantityFact:
    """A numeric fact, in canonical units, for tolerance-aware comparison."""

    attribute_code: str
    magnitude: float
    unit: str


@dataclass
class FactSheet:
    """Everything a generator may use, and everything a claim may be checked against."""

    sku: str
    brand: str | None
    class_name: str
    category_path: tuple[str, ...] = ()
    facts: list[Fact] = field(default_factory=list)
    quantities: list[QuantityFact] = field(default_factory=list)
    corpus: tuple[tuple[str, str], ...] = ()
    """``(attribute_code, folded_text)`` for every publishable value.

    Paired with its attribute rather than flattened into a set of strings, so a supported token
    can name *which* attribute substantiates it. "UL is supported" is a weaker statement than
    "UL is supported by `approvals`", and a reviewer auditing a claim needs the second one.
    """

    true_flags: frozenset[str] = frozenset()
    """Attribute codes of boolean compliance values that are present, verified and True."""

    present_codes: frozenset[str] = frozenset()
    withheld_codes: tuple[str, ...] = ()
    """Values that exist on the record but were not publishable. Surfaced for reporting, never
    given to the generator."""

    def __len__(self) -> int:
        return len(self.facts)

    def to_prompt(self) -> str:
        lines = [f"SKU: {self.sku}"]
        if self.brand:
            lines.append(f"Brand: {self.brand}")
        lines.append(f"Product class: {self.class_name}")
        if self.category_path:
            lines.append(f"Category: {' > '.join(self.category_path)}")
        lines.append("")
        lines.append("VERIFIED ATTRIBUTES — the only facts you may state:")
        lines.extend(fact.as_prompt_line() for fact in self.facts)
        return "\n".join(lines)

    def summary(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "facts": len(self.facts),
            "quantities": len(self.quantities),
            "true_flags": sorted(self.true_flags),
            "withheld": list(self.withheld_codes),
        }


def build_fact_sheet(record: ProductRecord, registry: SchemaRegistry) -> FactSheet:
    """Project a record into the constrained fact sheet.

    ``publishable_values`` is the same gate the channel exporters use. Copy deliberately shares
    it rather than defining its own: a fact good enough for a product feed and not good enough
    for a description, or the reverse, would be an inconsistency nobody could defend.
    """
    publishable = record.publishable_values()
    publishable_codes = {value.attribute_code for value in publishable}

    facts: list[Fact] = []
    quantities: list[QuantityFact] = []
    corpus: list[tuple[str, str]] = []
    true_flags: set[str] = set()

    for value in sorted(publishable, key=lambda v: v.attribute_code):
        definition = _definition(registry, value.attribute_code)
        display = value.value_display or _render(value.value_canonical) or value.value_raw or ""
        if not display:
            continue

        is_flag = value.value_canonical is True
        facts.append(
            Fact(
                attribute_code=value.attribute_code,
                name=definition.name if definition else value.attribute_code,
                display=display,
                canonical=value.value_canonical,
                unit=getattr(value.value_canonical, "unit", None),
                is_compliance_claim=bool(definition and definition.compliance_claim),
                is_true_flag=is_flag,
            )
        )
        if is_flag:
            true_flags.add(value.attribute_code)

        quantities.extend(_quantities_of(value))
        corpus.extend(
            (value.attribute_code, text) for text in _corpus_of(value, display)
        )

    class_name = ""
    category: tuple[str, ...] = ()
    if record.class_code:
        product_class = registry.product_class(record.class_code)
        class_name = product_class.name
        category = tuple(product_class.browse_path)

    withheld = tuple(
        sorted(
            value.attribute_code
            for value in record.current_values()
            if value.attribute_code not in publishable_codes
        )
    )

    return FactSheet(
        sku=record.sku,
        brand=record.brand,
        class_name=class_name,
        category_path=category,
        facts=facts,
        quantities=quantities,
        corpus=tuple(dict.fromkeys(corpus)),
        true_flags=frozenset(true_flags),
        present_codes=frozenset(publishable_codes),
        withheld_codes=withheld,
    )


def _definition(registry: SchemaRegistry, code: str):
    try:
        return registry.attribute(code)
    except KeyError:
        return None


def _render(canonical: object) -> str:
    if canonical is None:
        return ""
    if isinstance(canonical, Quantity):
        return f"{canonical.magnitude} {canonical.unit}"
    if isinstance(canonical, ValueRange):
        return f"{canonical.minimum} to {canonical.maximum} {canonical.unit}"
    if isinstance(canonical, bool):
        return "Yes" if canonical else "No"
    if isinstance(canonical, list | tuple):
        return ", ".join(str(item) for item in canonical)
    return str(canonical)


def _quantities_of(value: AttributeValue) -> list[QuantityFact]:
    """Canonical numeric facts, plus the imperial form the display string used.

    Both are needed. Copy written from a display value says ``600 psi`` and copy written from a
    range says ``-20°F to 366°F``, while the canonical values are psi and degC respectively.
    Indexing only the canonical form would flag the correct imperial figure as unsupported.
    """
    canonical = value.value_canonical
    out: list[QuantityFact] = []

    if isinstance(canonical, Quantity):
        out.append(QuantityFact(value.attribute_code, canonical.magnitude, canonical.unit))
        out.extend(_alternates(value.attribute_code, canonical.magnitude, canonical.unit))
    elif isinstance(canonical, ValueRange):
        for bound in (canonical.minimum, canonical.maximum):
            out.append(QuantityFact(value.attribute_code, bound, canonical.unit))
            out.extend(_alternates(value.attribute_code, bound, canonical.unit))
    elif isinstance(canonical, int | float) and not isinstance(canonical, bool):
        out.append(QuantityFact(value.attribute_code, float(canonical), ""))

    return out


# Units copy is likely to use when the canonical form is metric. Kept small and explicit
# rather than enumerating every convertible unit, which would let a wrong figure find some
# unit that happens to make it true.
_ALTERNATE_UNITS = {
    "mm": ("in",),
    "degC": ("degF",),
    "kg": ("lb",),
    "N.m": ("ft-lb",),
    "psi": ("bar", "kPa"),
}


def _alternates(code: str, magnitude: float, unit: str) -> list[QuantityFact]:
    out: list[QuantityFact] = []
    for target in _ALTERNATE_UNITS.get(unit, ()):
        try:
            converted = unit_registry.convert(magnitude, unit, target)
        except Exception:  # noqa: BLE001 - an unconvertible pair is simply not indexed
            continue
        out.append(QuantityFact(code, converted, target))
    return out


def _corpus_of(value: AttributeValue, display: str) -> set[str]:
    """Every textual form of a value, folded, for token support checks."""
    parts = {display, value.value_raw or "", _render(value.value_canonical)}
    canonical = value.value_canonical
    if isinstance(canonical, list | tuple):
        parts.update(str(item) for item in canonical)
    return {part.casefold() for part in parts if part}
