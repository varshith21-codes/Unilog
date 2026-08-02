"""Raw-to-canonical normalization, driven by the schema datatype.

Extraction stores exactly what the source said. This stage turns that into something
comparable: a magnitude in a canonical unit, an enum snapped to a permitted value, a typed
range. Without it, faceted filtering and any accuracy measurement are impossible, because
``3/4"`` and ``19.05 mm`` and ``DN20`` are the same product and three different strings.

Three representations are kept, deliberately:

* ``value_raw``       — untouched, so the audit trail survives
* ``value_canonical`` — machine-comparable
* ``value_display``   — what a human should read, in the locale's preferred form

Normalization **failures become validation results, not silent nulls**. A value the pipeline
cannot interpret is a review item with a reason attached; discarding it would hide a data
problem, and guessing at it would invent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from axiom.core.validation import Severity, ValidationLayer, ValidationResult
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.normalize.parsers import ParsedValue, parse_dimension, parse_quantity, parse_range
from axiom.normalize.units import QuantityKind, registry
from axiom.schema.models import AttributeDefinition, Datatype

_TRUE_TOKENS = frozenset({"true", "yes", "y", "1", "compliant", "certified", "included", "t"})
_FALSE_TOKENS = frozenset({"false", "no", "n", "0", "non-compliant", "not compliant", "none", "f"})

# Denominator used when rendering millimetres back to imperial fractions. Sixteenths cover
# every nominal pipe size a distributor sells; finer denominators produce noise like 47/64".
IMPERIAL_DENOMINATOR = 16


@dataclass
class NormalizationOutcome:
    """The normalized value plus anything that went wrong doing it."""

    value: AttributeValue
    issues: list[ValidationResult] = field(default_factory=list)
    normalized: bool = True

    @property
    def failed(self) -> bool:
        return any(issue.is_blocking for issue in self.issues)


def normalize_value(
    value: AttributeValue, definition: AttributeDefinition
) -> NormalizationOutcome:
    """Fill ``value_canonical`` and ``value_display`` for one attribute value."""
    raw = value.value_raw
    if raw is None or not str(raw).strip():
        return NormalizationOutcome(
            value=value,
            issues=[
                ValidationResult.failed(
                    ValidationLayer.L0_TYPE_FORMAT,
                    "empty_raw_value",
                    f"attribute '{definition.code}' has no raw value to normalize",
                )
            ],
            normalized=False,
        )

    handler = _HANDLERS.get(definition.datatype, _normalize_string)
    canonical, display, issues = handler(str(raw), definition)

    if canonical is None:
        return NormalizationOutcome(value=value, issues=issues, normalized=False)

    updated = value.model_copy(
        update={"value_canonical": canonical, "value_display": display}
    )
    if issues:
        updated.validations = [*updated.validations, *issues]
    return NormalizationOutcome(value=updated, issues=issues, normalized=True)


def normalize_all(
    values: list[AttributeValue], registry_, *, class_code: str | None = None
) -> tuple[list[AttributeValue], list[ValidationResult]]:
    """Normalize a batch, skipping attributes the schema does not define.

    An unknown attribute code is reported rather than dropped: it usually means a prompt or
    schema version drifted, which is worth knowing about.
    """
    out: list[AttributeValue] = []
    all_issues: list[ValidationResult] = []
    del class_code  # reserved for future per-class display preferences

    for value in values:
        try:
            definition = registry_.attribute(value.attribute_code)
        except KeyError:
            all_issues.append(
                ValidationResult.failed(
                    ValidationLayer.L0_TYPE_FORMAT,
                    "unknown_attribute",
                    f"'{value.attribute_code}' is not defined in the active schema",
                )
            )
            out.append(value)
            continue

        outcome = normalize_value(value, definition)
        out.append(outcome.value)
        all_issues.extend(outcome.issues)

    return out, all_issues


# --------------------------------------------------------------------- per-datatype


def _fail(code: str, message: str, *, rule: str) -> list[ValidationResult]:
    return [
        ValidationResult.failed(
            ValidationLayer.L0_TYPE_FORMAT, rule, f"attribute '{code}': {message}"
        )
    ]


def _normalize_quantity(raw: str, definition: AttributeDefinition):
    parsed = parse_quantity(raw, unit_hint=definition.unit_hint)
    if parsed.magnitude is None:
        return None, None, _fail(
            definition.code, f"no numeric magnitude in {raw!r}", rule="quantity_unparseable"
        )

    issues: list[ValidationResult] = []
    if parsed.unit is None:
        return None, None, _fail(
            definition.code,
            f"no unit resolved from {raw!r} and no unit_hint declared",
            rule="unit_missing",
        )

    # L1: the unit's quantity kind must match what the attribute declares. Catching this here
    # is why a pressure value cannot end up in a length field.
    if (
        definition.quantity_kind
        and parsed.kind
        and parsed.kind.value != definition.quantity_kind
    ):
        return None, None, [
            ValidationResult.failed(
                ValidationLayer.L1_DIMENSION,
                "quantity_kind_mismatch",
                f"attribute '{definition.code}' expects {definition.quantity_kind} but "
                f"{raw!r} parsed as {parsed.kind.value}",
                counterexample=f"unit {parsed.unit!r} is {parsed.kind.value}",
            )
        ]

    canonical_unit = definition.canonical_unit or parsed.canonical_unit or parsed.unit
    magnitude = registry.convert(parsed.magnitude, parsed.unit, canonical_unit)
    quantity = Quantity(magnitude=round(magnitude, 6), unit=canonical_unit)

    if parsed.rating_class or parsed.reference_condition or parsed.conditional_note:
        issues.append(
            ValidationResult.passed(
                ValidationLayer.L0_TYPE_FORMAT,
                "qualifiers_retained",
                "value carries qualifiers preserved from the source",
                detail="; ".join(
                    part
                    for part in (
                        f"rating={parsed.rating_class}" if parsed.rating_class else "",
                        f"at={parsed.reference_condition}" if parsed.reference_condition else "",
                        f"note={parsed.conditional_note}" if parsed.conditional_note else "",
                    )
                    if part
                ),
            )
        )

    return quantity, _display_for(parsed, definition, quantity), issues


def _normalize_dimension(raw: str, definition: AttributeDefinition):
    return _normalize_quantity(raw, definition)


def _normalize_range(raw: str, definition: AttributeDefinition):
    parsed = parse_range(raw, unit_hint=definition.unit_hint)
    if not parsed.is_range:
        # A single value where a range is expected is usable but incomplete: "600 PSI max"
        # bounds one end only. Recorded as a warning so a reviewer decides.
        if parsed.magnitude is not None and parsed.unit:
            canonical_unit = definition.canonical_unit or parsed.unit
            magnitude = registry.convert(parsed.magnitude, parsed.unit, canonical_unit)
            value_range = ValueRange(
                minimum=round(magnitude, 6), maximum=round(magnitude, 6), unit=canonical_unit
            )
            return (
                value_range,
                parsed.display,
                [
                    ValidationResult.warned(
                        ValidationLayer.L0_TYPE_FORMAT,
                        "range_collapsed",
                        f"attribute '{definition.code}' expects a range but {raw!r} states a "
                        f"single value; stored with equal bounds",
                    )
                ],
            )
        return None, None, _fail(
            definition.code, f"could not parse a range from {raw!r}", rule="range_unparseable"
        )

    unit = parsed.unit
    if unit is None:
        return None, None, _fail(
            definition.code, f"no unit resolved from {raw!r}", rule="unit_missing"
        )

    if definition.quantity_kind and parsed.kind and parsed.kind.value != definition.quantity_kind:
        return None, None, [
            ValidationResult.failed(
                ValidationLayer.L1_DIMENSION,
                "quantity_kind_mismatch",
                f"attribute '{definition.code}' expects {definition.quantity_kind} but "
                f"{raw!r} parsed as {parsed.kind.value}",
            )
        ]

    canonical_unit = definition.canonical_unit or unit
    low = registry.convert(parsed.minimum, unit, canonical_unit)
    high = registry.convert(parsed.maximum, unit, canonical_unit)
    # Temperature conversion is affine, so ordering can invert across scales in principle;
    # sorting keeps ValueRange constructible either way.
    low, high = sorted((round(low, 6), round(high, 6)))
    value_range = ValueRange(minimum=low, maximum=high, unit=canonical_unit)

    issues = [
        ValidationResult.warned(
            ValidationLayer.L0_TYPE_FORMAT, "range_reordered", warning
        )
        for warning in parsed.warnings
        if "reversed" in warning
    ]
    return value_range, parsed.display, issues


def _normalize_dimension_set(raw: str, definition: AttributeDefinition):
    parts = parse_dimension(raw, unit_hint=definition.unit_hint)
    magnitudes: list[float] = []
    for part in parts:
        if part.magnitude is None or not part.unit:
            return None, None, _fail(
                definition.code,
                f"component {part.raw!r} of {raw!r} is not a usable measurement",
                rule="dimension_set_unparseable",
            )
        canonical_unit = definition.canonical_unit or part.unit
        magnitudes.append(round(registry.convert(part.magnitude, part.unit, canonical_unit), 6))

    unit = definition.canonical_unit or parts[-1].unit or ""
    display = " x ".join(f"{m:g}" for m in magnitudes) + (f" {unit}" if unit else "")
    return [f"{m:g}" for m in magnitudes], display, []


def _fold_enum(text: str) -> str:
    import re as _re

    return _re.sub(r"[^a-z0-9]", "", text.lower())


MIN_CONTAINMENT_CHARS = 4
"""Aliases shorter than this are not used for containment matching.

A two- or three-character alias appears inside unrelated words often enough that containment
on it would produce confident wrong answers, which is the one outcome worse than abstaining.
"""


def _snap_enum(raw: str, definition: AttributeDefinition) -> tuple[str | None, str | None]:
    """Resolve a raw string to a permitted value. Returns (value, dropped_text).

    Exact alias matching first. Then containment, because extraction is instructed to quote
    the source verbatim and datasheets qualify their values: the source says
    ``"NPT threaded, female both ends"`` where the permitted value is ``"NPT Threaded"``. The
    extracted text is correct and rejecting it would discard a good value over phrasing.

    Containment prefers the *longest* matching alias, so a more specific value always wins
    over a less specific one that happens to be a prefix of it.
    """
    if exact := definition.resolve_allowed(raw):
        return exact, None

    folded_raw = _fold_enum(raw)
    if not folded_raw:
        return None, None

    candidates: list[tuple[int, str, str]] = []
    for allowed in definition.allowed_values:
        for spelling in (allowed.value, *allowed.aliases):
            folded = _fold_enum(spelling)
            if len(folded) >= MIN_CONTAINMENT_CHARS and folded in folded_raw:
                candidates.append((len(folded), allowed.value, spelling))

    if not candidates:
        return None, None

    _, value, matched_spelling = max(candidates, key=lambda c: c[0])
    # Report what the source said beyond the matched portion — "female both ends" is real
    # information and silently discarding it would be a quiet data loss.
    return value, f"matched on {matched_spelling!r} within {raw!r}"


def _normalize_enum(raw: str, definition: AttributeDefinition):
    snapped, note = _snap_enum(raw, definition)
    if snapped is None:
        # Abstain rather than pick the closest value. A wrong enum is invisible in review and
        # corrupts a facet for every product in the class.
        permitted = ", ".join(a.value for a in definition.allowed_values[:6])
        return None, None, _fail(
            definition.code,
            f"{raw!r} does not match any permitted value (allowed: {permitted}…)",
            rule="enum_unmatched",
        )

    issues: list[ValidationResult] = []
    if note:
        issues.append(
            ValidationResult.warned(
                ValidationLayer.L0_TYPE_FORMAT,
                "enum_partial_match",
                f"attribute '{definition.code}' snapped to {snapped!r}; the source carried "
                f"additional text that is not represented in the canonical value",
                detail=note,
            )
        )
    return snapped, snapped, issues


def _normalize_multi_enum(raw: str, definition: AttributeDefinition):
    tokens = [t.strip() for t in raw.replace(";", ",").split(",")]
    tokens = [t for t in tokens if t]
    resolved: list[str] = []
    unmatched: list[str] = []

    for token in tokens:
        snapped, _ = _snap_enum(token, definition)
        if snapped is None:
            unmatched.append(token)
        elif snapped not in resolved:
            resolved.append(snapped)

    if not resolved:
        return None, None, _fail(
            definition.code,
            f"none of {tokens!r} matched a permitted value",
            rule="multi_enum_unmatched",
        )

    issues: list[ValidationResult] = []
    if unmatched:
        # Partial resolution is genuinely useful — "UL, CSA, FooCert" should keep UL and CSA
        # — but the dropped tokens must be visible, because an unrecognised certification
        # might be the one that matters.
        issues.append(
            ValidationResult.warned(
                ValidationLayer.L0_TYPE_FORMAT,
                "multi_enum_partial",
                f"attribute '{definition.code}': kept {resolved}, could not match "
                f"{unmatched}",
            )
        )
    return resolved, ", ".join(resolved), issues


def _normalize_boolean(raw: str, definition: AttributeDefinition):
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True, "Yes", []
    if token in _FALSE_TOKENS:
        return False, "No", []
    # Anything else is ambiguous. For a compliance flag in particular, guessing is the one
    # thing that must not happen.
    severity = Severity.ERROR if definition.compliance_claim else Severity.WARNING
    return None, None, [
        ValidationResult.failed(
            ValidationLayer.L0_TYPE_FORMAT,
            "boolean_ambiguous",
            f"attribute '{definition.code}': {raw!r} is not a recognisable true/false value",
            severity=severity,
            suggested_fix="restate as true or false, keeping the citation as evidence",
        )
    ]


def _normalize_integer(raw: str, definition: AttributeDefinition):
    parsed = parse_quantity(raw)
    if parsed.magnitude is None:
        return None, None, _fail(
            definition.code, f"no number in {raw!r}", rule="integer_unparseable"
        )
    if abs(parsed.magnitude - round(parsed.magnitude)) > 1e-9:
        return None, None, _fail(
            definition.code,
            f"{raw!r} is not a whole number",
            rule="integer_not_whole",
        )
    value = int(round(parsed.magnitude))
    return value, str(value), []


def _normalize_number(raw: str, definition: AttributeDefinition):
    parsed = parse_quantity(raw)
    if parsed.magnitude is None:
        return None, None, _fail(
            definition.code, f"no number in {raw!r}", rule="number_unparseable"
        )
    return parsed.magnitude, f"{parsed.magnitude:g}", []


def _normalize_string(raw: str, definition: AttributeDefinition):
    cleaned = " ".join(raw.split())
    if definition.pattern:
        import re

        if not re.fullmatch(definition.pattern, cleaned):
            return None, None, _fail(
                definition.code,
                f"{cleaned!r} does not match the required pattern {definition.pattern}",
                rule="pattern_mismatch",
            )
    return cleaned, cleaned, []


_HANDLERS = {
    Datatype.QUANTITY: _normalize_quantity,
    Datatype.DIMENSION: _normalize_dimension,
    Datatype.RANGE: _normalize_range,
    Datatype.DIMENSION_SET: _normalize_dimension_set,
    Datatype.ENUM: _normalize_enum,
    Datatype.MULTI_ENUM: _normalize_multi_enum,
    Datatype.BOOLEAN: _normalize_boolean,
    Datatype.INTEGER: _normalize_integer,
    Datatype.NUMBER: _normalize_number,
    Datatype.STRING: _normalize_string,
}


# --------------------------------------------------------------------- display


def _display_for(
    parsed: ParsedValue, definition: AttributeDefinition, quantity: Quantity
) -> str:
    """Render a value the way a buyer expects to read it.

    Nominal pipe sizes are the case that matters: a plumbing buyer searches for ``3/4"`` and
    will not recognise ``19.05 mm``, even though millimetres are the right storage unit.
    """
    if (
        definition.display_preference == "imperial_fraction"
        and quantity.unit == "mm"
        and definition.quantity_kind == QuantityKind.LENGTH.value
    ):
        return to_imperial_fraction(quantity.magnitude)
    return parsed.display


def to_imperial_fraction(millimetres: float, *, denominator: int = IMPERIAL_DENOMINATOR) -> str:
    """Render millimetres as an imperial fraction, e.g. 19.05 -> ``3/4"``.

    Snapped to sixteenths, which covers every nominal size a distributor sells. Finer
    denominators produce noise like ``47/64"`` that no catalogue would ever print.
    """
    inches = millimetres / 25.4
    snapped = Fraction(round(inches * denominator), denominator)
    whole, remainder = divmod(snapped, 1)
    whole = int(whole)

    if remainder == 0:
        return f'{whole}"'
    fraction = Fraction(remainder).limit_denominator(denominator)
    if whole == 0:
        return f'{fraction.numerator}/{fraction.denominator}"'
    return f'{whole}-{fraction.numerator}/{fraction.denominator}"'
