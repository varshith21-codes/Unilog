"""Loading and integrity-checking the schema.

The registry's job is to fail loudly at load time rather than quietly at runtime. A typo in
a unit code, an enum with no values, a rule referencing an attribute that does not exist, a
title template with a placeholder nothing populates — every one of those is a latent bug
that would otherwise surface as a wrong value in a customer's catalog weeks later.

Integrity problems are **collected, not raised on the first failure**. Fixing schema errors
one exception at a time is miserable, and a schema author wants the whole list.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from axiom.schema.models import (
    AttributeDefinition,
    ChannelProfile,
    ClassDefinition,
    Datatype,
    EvidenceRequirement,
    Requirement,
)

# Tokens a title template may reference that are not attribute codes.
COMPUTED_TEMPLATE_TOKENS = frozenset(
    {"brand", "mpn", "sku", "gtin", "class_name", "supplier", "body_material_short"}
)


class SchemaIntegrityError(Exception):
    """Raised when the declarative schema is internally inconsistent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        bullets = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{len(problems)} schema integrity problem(s):\n{bullets}")


class SchemaRegistry:
    """In-memory view of the attribute dictionary and class definitions."""

    def __init__(
        self,
        attributes: dict[str, AttributeDefinition],
        classes: dict[str, ClassDefinition],
    ) -> None:
        self._attributes = attributes
        self._classes = classes

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, root: Path | str, *, strict: bool = True) -> SchemaRegistry:
        """Load every YAML file under ``root/attributes`` and ``root/classes``."""
        root = Path(root)
        attr_dir, class_dir = root / "attributes", root / "classes"
        if not attr_dir.is_dir():
            raise FileNotFoundError(f"attribute directory not found: {attr_dir}")
        if not class_dir.is_dir():
            raise FileNotFoundError(f"class directory not found: {class_dir}")

        problems: list[str] = []
        attributes: dict[str, AttributeDefinition] = {}
        origin: dict[str, str] = {}

        for path in sorted(attr_dir.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in payload.get("attributes", []):
                code = raw.get("code", "<missing code>")
                try:
                    definition = AttributeDefinition.model_validate(raw)
                except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                    problems.append(f"{path.name}: attribute '{code}' invalid: {_describe(exc)}")
                    continue
                if definition.code in attributes:
                    problems.append(
                        f"{path.name}: attribute '{definition.code}' already defined in "
                        f"{origin[definition.code]} — duplicate definitions drift apart"
                    )
                    continue
                attributes[definition.code] = definition
                origin[definition.code] = path.name

        classes: dict[str, ClassDefinition] = {}
        for path in sorted(class_dir.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = payload.get("class", payload)
            code = raw.get("code", "<missing code>")
            try:
                definition = ClassDefinition.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{path.name}: class '{code}' invalid: {_describe(exc)}")
                continue
            if definition.code in classes:
                problems.append(f"{path.name}: class '{definition.code}' defined more than once")
                continue
            classes[definition.code] = definition

        problems.extend(_check_integrity(attributes, classes))
        if problems and strict:
            raise SchemaIntegrityError(problems)
        return cls(attributes, classes)

    # ------------------------------------------------------------------ accessors

    def attribute(self, code: str) -> AttributeDefinition:
        try:
            return self._attributes[code]
        except KeyError as exc:
            raise KeyError(f"unknown attribute '{code}'") from exc

    def product_class(self, code: str) -> ClassDefinition:
        try:
            return self._classes[code]
        except KeyError as exc:
            raise KeyError(f"unknown product class '{code}'") from exc

    @property
    def attribute_codes(self) -> list[str]:
        return sorted(self._attributes)

    @property
    def class_codes(self) -> list[str]:
        return sorted(self._classes)

    def attributes_for(
        self, class_code: str, *requirements: Requirement
    ) -> list[AttributeDefinition]:
        """Attribute definitions bound to a class, in the class's declared order."""
        definition = self.product_class(class_code)
        return [self.attribute(code) for code in definition.codes(*requirements)]

    def required_codes(self, class_code: str) -> list[str]:
        """Required attribute codes — the denominator for completeness scoring."""
        return self.product_class(class_code).required_codes

    def compliance_attributes(self, class_code: str) -> list[AttributeDefinition]:
        """Attributes carrying legal weight. These may never be satisfied by inference."""
        return [a for a in self.attributes_for(class_code) if a.compliance_claim]

    def completeness(self, class_code: str, populated_codes: Iterable[str]) -> float:
        """Weighted completeness. Weighting is the point: a missing pressure rating is not
        equivalent to a missing handle colour."""
        definition = self.product_class(class_code)
        bindings = [b for b in definition.attributes if b.requirement is Requirement.REQUIRED]
        if not bindings:
            return 1.0
        have = set(populated_codes)
        total = sum(b.weight for b in bindings)
        got = sum(b.weight for b in bindings if b.code in have)
        return got / total


def _describe(exc: Exception) -> str:
    """Render a validation failure as something a schema author can act on.

    Pydantic's ``str(ValidationError)`` opens with "N validation errors for Model", which is
    the least useful line in the message. Collecting problems is pointless if the collected
    text does not say what is actually wrong, so pull out the per-field messages instead.
    """
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors():
            location = ".".join(str(p) for p in error.get("loc", ())) or "<root>"
            parts.append(f"{location}: {error.get('msg', '')}")
        if parts:
            return "; ".join(parts)
    return str(exc).strip().splitlines()[0]


def _check_integrity(
    attributes: dict[str, AttributeDefinition],
    classes: dict[str, ClassDefinition],
) -> list[str]:
    """Cross-file consistency checks. Everything here would otherwise fail at runtime."""
    problems: list[str] = []

    for code, attr in attributes.items():
        problems.extend(_check_units(code, attr))

    for class_code, definition in classes.items():
        bound = set()
        for binding in definition.attributes:
            if binding.code not in attributes:
                problems.append(
                    f"class '{class_code}' binds unknown attribute '{binding.code}'"
                )
                continue
            bound.add(binding.code)

        for rule in definition.cross_field_rules:
            if not rule.references:
                problems.append(
                    f"class '{class_code}' rule '{rule.id}' declares no references; list the "
                    f"attribute codes it depends on so typos fail at load"
                )
            for ref in rule.references:
                if ref not in attributes:
                    problems.append(
                        f"class '{class_code}' rule '{rule.id}' references unknown "
                        f"attribute '{ref}'"
                    )
                elif ref not in bound:
                    problems.append(
                        f"class '{class_code}' rule '{rule.id}' references '{ref}', which the "
                        f"class does not bind — the rule can never evaluate"
                    )

        for profile in definition.channel_profiles:
            problems.extend(_check_channel(class_code, profile, attributes, bound))

    return problems


def _check_units(code: str, attr: AttributeDefinition) -> list[str]:
    """Validate unit declarations against the real unit registry.

    This is the check that earns its keep: a mistyped canonical unit is invisible in YAML
    review and corrupts every value of that attribute.

    The unit registry is imported here rather than at module scope on purpose. ``axiom.schema``
    sits *below* the schema-aware normalization pipeline in the dependency order, but it needs
    the pure unit tables for this one check. A function-local import breaks the cycle without
    inverting the layering or duplicating the unit definitions, and it costs nothing after the
    first call since the module is then cached.
    """
    from axiom.normalize.units import registry as unit_registry

    problems: list[str] = []
    if attr.canonical_unit is not None:
        resolved = unit_registry.resolve(attr.canonical_unit)
        if resolved is None:
            problems.append(
                f"attribute '{code}' declares canonical_unit '{attr.canonical_unit}', which the "
                f"unit registry does not recognise"
            )
        elif attr.quantity_kind and resolved.kind.value != attr.quantity_kind:
            problems.append(
                f"attribute '{code}' declares quantity_kind '{attr.quantity_kind}' but its "
                f"canonical_unit '{attr.canonical_unit}' is {resolved.kind.value}"
            )
    if attr.datatype.needs_unit and not attr.quantity_kind:
        problems.append(
            f"attribute '{code}' is {attr.datatype.value} and must declare a quantity_kind "
            f"so L1 dimensional validation can run"
        )
    if attr.unit_hint is not None and unit_registry.resolve(attr.unit_hint) is None:
        problems.append(
            f"attribute '{code}' declares unit_hint '{attr.unit_hint}', which the unit "
            f"registry does not recognise"
        )
    if attr.plausible_range is not None:
        low, high = attr.plausible_range
        if low >= high:
            problems.append(
                f"attribute '{code}' has plausible_range [{low}, {high}] with min >= max"
            )
    return problems


def _check_channel(
    class_code: str,
    profile: ChannelProfile,
    attributes: dict[str, AttributeDefinition],
    bound: set[str],
) -> list[str]:
    problems: list[str] = []
    for code in profile.required:
        if code in COMPUTED_TEMPLATE_TOKENS:
            continue  # supplied from the product record, not the attribute set
        if code not in attributes:
            problems.append(
                f"class '{class_code}' channel '{profile.name}' requires unknown "
                f"attribute '{code}'"
            )
        elif code not in bound:
            problems.append(
                f"class '{class_code}' channel '{profile.name}' requires '{code}', which the "
                f"class does not bind — publication to this channel can never succeed"
            )
    for token in profile.template_tokens():
        if token in COMPUTED_TEMPLATE_TOKENS or token in bound:
            continue
        problems.append(
            f"class '{class_code}' channel '{profile.name}' title_template references "
            f"'{{{token}}}', which is neither a bound attribute nor a computed token"
        )
    # A compliance attribute must not be silently required by a channel that cannot
    # guarantee verified data; surface it so the decision is explicit.
    for code in profile.required:
        attr = attributes.get(code)
        if attr and attr.compliance_claim and attr.evidence_requirement is not (
            EvidenceRequirement.STRICT
        ):
            problems.append(
                f"class '{class_code}' channel '{profile.name}' requires compliance attribute "
                f"'{code}' without strict evidence"
            )
    return problems


def default_schema_root() -> Path:
    """Repository ``schema/`` directory, resolved relative to this file."""
    return Path(__file__).resolve().parents[3] / "schema"


def load_default() -> SchemaRegistry:
    return SchemaRegistry.load(default_schema_root())


__all__ = [
    "COMPUTED_TEMPLATE_TOKENS",
    "Datatype",
    "SchemaIntegrityError",
    "SchemaRegistry",
    "default_schema_root",
    "load_default",
]
