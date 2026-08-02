"""Parsers for the value forms that actually appear on industrial datasheets.

Real spec values look like this, and a naive ``float()`` handles none of them::

    3/4"                    fractional inch
    1-1/4                   mixed number, hyphen as an implicit "and"
    600 PSI WOG @ 73°F      value with a rating class and a reference condition
    18–22 ft-lb             range with an en dash
    -20 °C to +60 °C        signed range with a word separator
    IP66 (cover closed)     value with a conditional qualifier
    2 x 4 x 6 in            dimension set

The tricky one is ``1-1/4``. In industrial usage the hyphen means "one and a quarter", but
in ``18-22`` the same character means a range. Disambiguation is by shape: a hyphen
followed by a fraction is a mixed number; a hyphen between two plain numbers is a range.
Getting this wrong turns a 1.25 inch fitting into a range of 1 to 4 inches, which is the
kind of silent corruption this whole package exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

from axiom.normalize.units import QuantityKind, registry


class Qualifier(str, Enum):
    """Modifiers that change what a number means. Dropping these loses the spec."""

    EXACT = "exact"
    MAXIMUM = "maximum"
    MINIMUM = "minimum"
    NOMINAL = "nominal"
    APPROXIMATE = "approximate"
    TYPICAL = "typical"


_QUALIFIER_TOKENS: dict[str, Qualifier] = {
    "max": Qualifier.MAXIMUM,
    "maximum": Qualifier.MAXIMUM,
    "up to": Qualifier.MAXIMUM,
    "not to exceed": Qualifier.MAXIMUM,
    "min": Qualifier.MINIMUM,
    "minimum": Qualifier.MINIMUM,
    "at least": Qualifier.MINIMUM,
    "nom": Qualifier.NOMINAL,
    "nominal": Qualifier.NOMINAL,
    "approx": Qualifier.APPROXIMATE,
    "approximately": Qualifier.APPROXIMATE,
    "~": Qualifier.APPROXIMATE,
    "typ": Qualifier.TYPICAL,
    "typical": Qualifier.TYPICAL,
}

# Dash characters datasheets use interchangeably: hyphen, en dash, em dash, minus sign.
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_RANGE_WORDS = r"(?:to|through|thru|\.\.\.|\.\.)"

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_FRACTION = r"\d+\s*/\s*\d+"
_MIXED = rf"\d+\s*[{_DASHES}\s]\s*{_FRACTION}"

# Order matters: mixed number before bare fraction before plain number.
_MAGNITUDE_RE = re.compile(rf"(?P<mag>{_MIXED}|{_FRACTION}|{_NUMBER})")


@dataclass(frozen=True)
class ParsedValue:
    """A parsed scalar or range, normalized to the canonical unit of its kind."""

    magnitude: float | None
    unit: str | None
    kind: QuantityKind | None
    raw: str
    qualifier: Qualifier = Qualifier.EXACT
    minimum: float | None = None
    maximum: float | None = None
    reference_condition: str | None = None
    """e.g. '73°F' from '600 PSI WOG @ 73°F' — derating depends on this."""

    conditional_note: str | None = None
    """e.g. 'cover closed' from 'IP66 (cover closed)'."""

    rating_class: str | None = None
    """e.g. 'WOG', 'WSP' — a rating class, not a unit."""

    canonical_magnitude: float | None = None
    canonical_unit: str | None = None
    unparsed_remainder: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_range(self) -> bool:
        return self.minimum is not None and self.maximum is not None

    @property
    def display(self) -> str:
        if self.is_range:
            return f"{_fmt(self.minimum)} to {_fmt(self.maximum)}{_space(self.unit)}"
        if self.magnitude is None:
            return self.raw.strip()
        prefix = {
            Qualifier.MAXIMUM: "max ",
            Qualifier.MINIMUM: "min ",
            Qualifier.APPROXIMATE: "~",
        }.get(self.qualifier, "")
        return f"{prefix}{_fmt(self.magnitude)}{_space(self.unit)}"


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _space(unit: str | None) -> str:
    if not unit or unit == "1":
        return ""
    return f" {unit}"


def parse_fraction(text: str) -> float | None:
    """Parse a plain, fractional or mixed number to a float.

    >>> parse_fraction("3/4")
    0.75
    >>> parse_fraction("1-1/4")
    1.25
    >>> parse_fraction("1 1/4")
    1.25
    >>> parse_fraction("2.5")
    2.5
    """
    token = text.strip()
    if not token:
        return None
    for dash in _DASHES:
        token = token.replace(dash, "-")

    # mixed number: whole part, then a fraction, separated by hyphen or space
    mixed = re.fullmatch(r"([+-]?\d+)\s*[-\s]\s*(\d+)\s*/\s*(\d+)", token)
    if mixed:
        whole, num, den = (int(g) for g in mixed.groups())
        if den == 0:
            return None
        sign = -1 if whole < 0 else 1
        return float(abs(whole) + Fraction(num, den)) * sign

    bare = re.fullmatch(r"([+-]?\d+)\s*/\s*(\d+)", token)
    if bare:
        num, den = int(bare.group(1)), int(bare.group(2))
        return None if den == 0 else float(Fraction(num, den))

    try:
        return float(token)
    except ValueError:
        return None


def _extract_qualifier(text: str) -> tuple[Qualifier, str]:
    lowered = text.lower()
    # longest token first, so "up to" wins over "to"
    for token in sorted(_QUALIFIER_TOKENS, key=len, reverse=True):
        if token in lowered:
            idx = lowered.index(token)
            stripped = text[:idx] + text[idx + len(token) :]
            return _QUALIFIER_TOKENS[token], stripped
    return Qualifier.EXACT, text


def _extract_parenthetical(text: str) -> tuple[str | None, str]:
    match = re.search(r"\(([^)]*)\)", text)
    if not match:
        return None, text
    return match.group(1).strip(), (text[: match.start()] + text[match.end() :])


def _extract_reference_condition(text: str) -> tuple[str | None, str]:
    match = re.search(r"@\s*([^,;()]+)", text)
    if not match:
        return None, text
    return match.group(1).strip(), (text[: match.start()] + text[match.end() :])


_RATING_CLASSES = ("WOG", "WSP", "SWP", "CWP")


def _extract_rating_class(text: str) -> tuple[str | None, str]:
    for token in _RATING_CLASSES:
        match = re.search(rf"\b{token}\b", text, flags=re.IGNORECASE)
        if match:
            return token, (text[: match.start()] + text[match.end() :])
    return None, text


def _resolve_unit(remainder: str, unit_hint: str | None) -> tuple[str | None, str]:
    """Find a known unit in the leftover text. Falls back to the hint."""
    cleaned = remainder.strip(" \t.,;:")
    if cleaned:
        # try progressively shorter leading token sequences, longest first
        tokens = cleaned.split()
        for length in range(len(tokens), 0, -1):
            candidate = " ".join(tokens[:length])
            unit = registry.resolve(candidate)
            if unit is not None:
                leftover = " ".join(tokens[length:])
                return unit.code, leftover
        # unit may be glued to the number, e.g. '600psi' already split, or a symbol
        for symbol in ('"', "'", "°", "″"):
            if symbol in cleaned:
                unit = registry.resolve(symbol)
                if unit is not None:
                    return unit.code, cleaned.replace(symbol, "").strip()
    if unit_hint:
        unit = registry.resolve(unit_hint)
        if unit is not None:
            return unit.code, cleaned
    return None, cleaned


def parse_quantity(text: str, *, unit_hint: str | None = None) -> ParsedValue:
    """Parse a single value with an optional unit, qualifier and reference condition.

    >>> p = parse_quantity("600 PSI WOG @ 73°F")
    >>> p.magnitude, p.unit, p.rating_class, p.reference_condition
    (600.0, 'psi', 'WOG', '73°F')
    >>> parse_quantity('3/4"').canonical_magnitude
    19.05
    """
    raw = text
    warnings: list[str] = []

    reference, working = _extract_reference_condition(text)
    note, working = _extract_parenthetical(working)
    rating, working = _extract_rating_class(working)
    qualifier, working = _extract_qualifier(working)

    match = _MAGNITUDE_RE.search(working)
    if match is None:
        return ParsedValue(
            magnitude=None,
            unit=None,
            kind=None,
            raw=raw,
            qualifier=qualifier,
            reference_condition=reference,
            conditional_note=note,
            rating_class=rating,
            unparsed_remainder=working.strip(),
            warnings=("no numeric magnitude found",),
        )

    magnitude = parse_fraction(match.group("mag"))
    remainder = working[match.end() :]
    unit_code, leftover = _resolve_unit(remainder, unit_hint)

    kind = registry.kind_of(unit_code) if unit_code else None
    canonical_mag: float | None = None
    canonical_unit: str | None = None
    if magnitude is not None and unit_code:
        canonical_mag, canonical_unit = registry.to_canonical(magnitude, unit_code)
        canonical_mag = round(canonical_mag, 6)

    if unit_code is None and magnitude is not None:
        warnings.append("no unit resolved; value stored as dimensionless")

    return ParsedValue(
        magnitude=magnitude,
        unit=unit_code,
        kind=kind,
        raw=raw,
        qualifier=qualifier,
        reference_condition=reference,
        conditional_note=note,
        rating_class=rating,
        canonical_magnitude=canonical_mag,
        canonical_unit=canonical_unit,
        unparsed_remainder=leftover.strip(),
        warnings=tuple(warnings),
    )


def parse_range(text: str, *, unit_hint: str | None = None) -> ParsedValue:
    """Parse a range. Falls back to :func:`parse_quantity` when there is only one value.

    >>> r = parse_range("18-22 ft-lb")
    >>> r.minimum, r.maximum, r.unit
    (18.0, 22.0, 'ft.lbf')
    >>> parse_range("-20 °C to +60 °C").minimum
    -20.0
    >>> parse_range("1-1/4 in").magnitude          # mixed number, not a range
    1.25
    """
    raw = text
    reference, working = _extract_reference_condition(text)
    note, working = _extract_parenthetical(working)
    rating, working = _extract_rating_class(working)

    left, right = _split_range(working)
    if left is None or right is None:
        return parse_quantity(raw, unit_hint=unit_hint)

    # The right-hand side normally carries the unit; the left may repeat it.
    right_parsed = parse_quantity(right, unit_hint=unit_hint)
    left_parsed = parse_quantity(left, unit_hint=right_parsed.unit or unit_hint)

    if left_parsed.magnitude is None or right_parsed.magnitude is None:
        return parse_quantity(raw, unit_hint=unit_hint)

    unit_code = right_parsed.unit or left_parsed.unit
    lo, hi = left_parsed.magnitude, right_parsed.magnitude
    warnings: list[str] = []
    if lo > hi:
        lo, hi = hi, lo
        warnings.append("range bounds were reversed in the source and have been ordered")

    canonical_lo = canonical_hi = None
    canonical_unit = None
    if unit_code:
        canonical_lo, canonical_unit = registry.to_canonical(lo, unit_code)
        canonical_hi, _ = registry.to_canonical(hi, unit_code)
        canonical_lo, canonical_hi = round(canonical_lo, 6), round(canonical_hi, 6)

    return ParsedValue(
        magnitude=None,
        unit=unit_code,
        kind=registry.kind_of(unit_code) if unit_code else None,
        raw=raw,
        minimum=lo,
        maximum=hi,
        reference_condition=reference,
        conditional_note=note,
        rating_class=rating,
        canonical_magnitude=canonical_lo,
        canonical_unit=canonical_unit,
        warnings=tuple(warnings),
    )


def _split_range(text: str) -> tuple[str | None, str | None]:
    """Split on a range separator, without mistaking a mixed number for a range."""
    word_split = re.split(_RANGE_WORDS, text, maxsplit=1, flags=re.IGNORECASE)
    if len(word_split) == 2:
        return word_split[0], word_split[1]

    # A dash is a range only between two plain numbers. If a fraction follows the dash,
    # it is a mixed number like 1-1/4.
    dash_pattern = re.compile(
        rf"(?P<lo>{_NUMBER})\s*[{_DASHES}]\s*(?P<hi>{_NUMBER})(?!\s*/)"
    )
    match = dash_pattern.search(text)
    if match and not re.match(rf"^\s*{_MIXED}", text):
        return match.group("lo"), text[match.start("hi") :]
    return None, None


def parse_dimension(text: str, *, unit_hint: str | None = None) -> list[ParsedValue]:
    """Parse a dimension set such as ``2 x 4 x 6 in`` into ordered components.

    The unit usually appears once, at the end, and applies to every component.

    >>> dims = parse_dimension("2 x 4 x 6 in")
    >>> [d.canonical_magnitude for d in dims]
    [50.8, 101.6, 152.4]
    """
    parts = re.split(r"\s*[x×]\s*", text, flags=re.IGNORECASE)
    if len(parts) < 2:
        return [parse_quantity(text, unit_hint=unit_hint)]

    last = parse_quantity(parts[-1], unit_hint=unit_hint)
    shared_unit = last.unit or unit_hint
    return [parse_quantity(p, unit_hint=shared_unit) for p in parts]
