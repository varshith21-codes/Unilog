"""Layer L0 and L3 checks that do not need the expression evaluator.

L0 is format-level: things that are wrong on their face, independent of any other field. The
GTIN check digit is the standout example — a transposed digit is detectable with arithmetic
alone, no catalogue lookup and no model call.

L3 is plausibility: a value that parses, has the right unit, and is still almost certainly
wrong. A 3.5 kg drill bit is the canonical example. L3 *flags*, it does not reject, because
outliers are sometimes correct and the cost of blocking a real value is higher than the cost
of a review.
"""

from __future__ import annotations

from axiom.core.validation import ValidationLayer, ValidationResult
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.schema.models import AttributeDefinition

GTIN_LENGTHS = frozenset({8, 12, 13, 14})


def gtin_check_digit(digits: str) -> int | None:
    """Compute the GS1 mod-10 check digit for the payload (everything but the last digit)."""
    if not digits.isdigit():
        return None
    # Weights alternate 3 and 1, applied right-to-left from the check-digit position.
    total = 0
    for index, char in enumerate(reversed(digits)):
        weight = 3 if index % 2 == 0 else 1
        total += int(char) * weight
    return (10 - (total % 10)) % 10


def validate_gtin(value: str) -> ValidationResult:
    """Verify a GTIN's length and check digit.

    Worth doing because it is free and definitive: a mistyped or transposed digit is caught
    by arithmetic, with no external data and no ambiguity about whether it is wrong.
    """
    cleaned = "".join(c for c in value if c.isdigit())

    if not cleaned:
        return ValidationResult.failed(
            ValidationLayer.L0_TYPE_FORMAT, "gtin_not_numeric", f"{value!r} contains no digits"
        )
    if len(cleaned) not in GTIN_LENGTHS:
        return ValidationResult.failed(
            ValidationLayer.L0_TYPE_FORMAT,
            "gtin_length",
            f"{value!r} has {len(cleaned)} digits; a GTIN has 8, 12, 13 or 14",
        )

    payload, stated = cleaned[:-1], int(cleaned[-1])
    expected = gtin_check_digit(payload)
    if expected != stated:
        return ValidationResult.failed(
            ValidationLayer.L0_TYPE_FORMAT,
            "gtin_check_digit",
            f"GTIN {cleaned} fails its check digit",
            counterexample=f"stated check digit {stated}, computed {expected}",
            suggested_fix=f"{payload}{expected} would be valid; verify against the source",
        )
    return ValidationResult.passed(
        ValidationLayer.L0_TYPE_FORMAT, "gtin_check_digit", f"GTIN {cleaned} is well formed"
    )


def check_plausible_range(
    value: AttributeValue, definition: AttributeDefinition
) -> ValidationResult | None:
    """L3: is the magnitude physically plausible for this attribute?

    Uses the range declared in the schema. Returns a warning rather than a failure — a valve
    rated above the usual ceiling may be a genuine specialty item, and blocking it would be
    worse than queueing it.
    """
    if definition.plausible_range is None:
        return None

    magnitudes = _magnitudes(value.value_canonical)
    if not magnitudes:
        return None

    low, high = definition.plausible_range
    outliers = [m for m in magnitudes if m < low or m > high]
    if not outliers:
        return ValidationResult.passed(
            ValidationLayer.L3_STATISTICAL,
            "plausible_range",
            f"within the expected range [{low:g}, {high:g}]",
        )

    unit = getattr(value.value_canonical, "unit", "") or ""
    return ValidationResult.warned(
        ValidationLayer.L3_STATISTICAL,
        "plausible_range",
        f"attribute '{definition.code}' value {outliers[0]:g} {unit} is outside the expected "
        f"range [{low:g}, {high:g}] for this attribute",
        detail=f"raw source text: {value.value_raw!r}",
    )


def _magnitudes(canonical) -> list[float]:
    """Pull comparable numbers out of whatever shape the canonical value has."""
    if isinstance(canonical, Quantity):
        return [canonical.magnitude]
    if isinstance(canonical, ValueRange):
        return [canonical.minimum, canonical.maximum]
    if isinstance(canonical, bool):
        return []  # bool is an int subclass; a boolean has no magnitude
    if isinstance(canonical, int | float):
        return [float(canonical)]
    return []
