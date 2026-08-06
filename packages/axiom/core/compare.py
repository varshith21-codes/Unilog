"""When are two canonical values the same value?

One question, asked in two places that must never answer it differently:

*   **Backtesting** compares an extracted value against ground truth.
*   **Validation layer L4** compares two independent sources against each other.

Those look like different problems and are the same problem. Keeping two implementations would
let a tolerance drift apart until a value could be simultaneously "correct" against the golden set
and "in conflict" between two sources that both stated it — a contradiction with no possible
resolution, and one that would surface as an unreproducible bug months later.

So the primitive lives here, in the lowest layer, and both callers import it.

The three match kinds are returned separately rather than collapsed into a boolean because
strictness is information. A figure that only agrees inside a tolerance band is a weaker
agreement than an identical one, and the backtest's exact-match rate exists precisely to stop a
loosely-toleranced attribute inflating a headline accuracy number.
"""

from __future__ import annotations

from enum import Enum

from axiom.core.values import Quantity, ValueRange


class MatchKind(str, Enum):
    EXACT = "exact"
    """Identical after type-strict comparison."""

    TOLERANCE = "tolerance"
    """Numerically equal inside the attribute's declared relative tolerance."""

    NORMALIZED = "normalized"
    """Equal ignoring case and whitespace. Text only."""

    @property
    def is_exact(self) -> bool:
        return self is MatchKind.EXACT


def match(
    expected: object, actual: object, *, tolerance: float = 0.0
) -> MatchKind | None:
    """Strongest match between two canonical values, or None if they differ.

    Tried in decreasing strictness so a caller can distinguish an identical value from one that
    merely passed inside a band.
    """
    if exact(expected, actual):
        return MatchKind.EXACT
    if tolerance > 0 and within_tolerance(expected, actual, tolerance):
        return MatchKind.TOLERANCE
    if normalized_equal(expected, actual):
        return MatchKind.NORMALIZED
    return None


def agree(expected: object, actual: object, *, tolerance: float = 0.0) -> bool:
    """Whether two values may be treated as the same. The boolean form of :func:`match`."""
    return match(expected, actual, tolerance=tolerance) is not None


def exact(expected: object, actual: object) -> bool:
    # Booleans are compared type-strictly. `True == 1.0` in Python, so without this a compliance
    # flag that normalised to a float instead of a bool would be scored correct — hiding a type
    # confusion in exactly the field where type confusion matters most.
    if isinstance(expected, bool) != isinstance(actual, bool):
        return False

    if isinstance(expected, Quantity) and isinstance(actual, Quantity):
        return expected.unit == actual.unit and expected.magnitude == actual.magnitude
    if isinstance(expected, ValueRange) and isinstance(actual, ValueRange):
        return (
            expected.unit == actual.unit
            and expected.minimum == actual.minimum
            and expected.maximum == actual.maximum
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return sorted(map(str, expected)) == sorted(map(str, actual))
    return expected == actual


def normalized_equal(expected: object, actual: object) -> bool:
    """Case- and whitespace-insensitive comparison for text values."""
    if isinstance(expected, str) and isinstance(actual, str):
        return " ".join(expected.split()).casefold() == " ".join(actual.split()).casefold()
    return False


def within_tolerance(expected: object, actual: object, tolerance: float) -> bool:
    if isinstance(expected, Quantity) and isinstance(actual, Quantity):
        if expected.unit != actual.unit:
            return False
        return close(expected.magnitude, actual.magnitude, tolerance)
    if isinstance(expected, ValueRange) and isinstance(actual, ValueRange):
        if expected.unit != actual.unit:
            return False
        return close(expected.minimum, actual.minimum, tolerance) and close(
            expected.maximum, actual.maximum, tolerance
        )
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if isinstance(expected, bool) or isinstance(actual, bool):
            return False
        return close(float(expected), float(actual), tolerance)
    return False


def close(a: float, b: float, tolerance: float) -> bool:
    """Relative closeness, scaled by the larger magnitude."""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    if scale == 0:
        return True
    return abs(a - b) / scale <= tolerance
