"""The claim-check pass: verify generated prose against the fact sheet, deterministically.

A prompt that says "state only these facts" is a request. This is the gate. Models comply with
such prompts most of the time, and for a pressure rating or a certification number, most of the
time is not a compliance posture.

Checking free text against structured data sounds like a job for a second model, and it is not.
An LLM judge shares the failure mode of the LLM writer — both are fluent, neither is checkable,
and a disagreement between them is unresolvable. Instead this extracts the *checkable* assertions
from the copy and verifies each one arithmetically or by lookup:

*   **Quantities** — every number-with-unit must match a verified value after unit conversion,
    inside a tight relative tolerance.
*   **Standards references** — every ``UL``, ``NSF/ANSI 61``, ``MSS SP-110`` style token must
    appear verbatim in a verified value. An invented standard number is the most damaging thing
    copy can produce: it reads as authoritative and a buyer can disprove it in one search.
*   **Material designations** — a specific alloy is a specification, so an invented one is a
    specification error dressed as prose.
*   **Regulated phrases** — "lead-free" requires the lead-free attribute to be present, verified
    and true. Not merely present.
*   **Banned phrases** — comparatives and guarantees cannot be substantiated by any product
    datum, so they fail on sight rather than being checked.

What this deliberately does not attempt: judging whether the prose is *good*, or whether an
adjective like "durable" is fair. Those are editorial questions with no ground truth here, and
pretending to check them would dilute the claims that genuinely are checkable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from axiom.generate.facts import FactSheet
from axiom.generate.policy import CopyPolicy
from axiom.normalize import registry as unit_registry


class ClaimKind(str, Enum):
    QUANTITY = "quantity"
    STANDARD = "standard"
    DESIGNATION = "designation"
    REGULATED = "regulated"
    BANNED = "banned"


class ClaimVerdict(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    BANNED = "banned"

    @property
    def blocks_publication(self) -> bool:
        return self is not ClaimVerdict.SUPPORTED


@dataclass(frozen=True)
class Claim:
    """One checkable assertion found in generated copy."""

    kind: ClaimKind
    text: str
    verdict: ClaimVerdict
    reason: str
    field_name: str = ""
    supported_by: str | None = None
    """Attribute code that substantiates the claim, when one does."""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "field": self.field_name,
            "supported_by": self.supported_by,
        }


@dataclass
class ClaimReport:
    """Every claim found across every field of a piece of copy."""

    claims: list[Claim] = field(default_factory=list)

    @property
    def unsupported(self) -> list[Claim]:
        return [c for c in self.claims if c.verdict is ClaimVerdict.UNSUPPORTED]

    @property
    def banned(self) -> list[Claim]:
        return [c for c in self.claims if c.verdict is ClaimVerdict.BANNED]

    @property
    def supported(self) -> list[Claim]:
        return [c for c in self.claims if c.verdict is ClaimVerdict.SUPPORTED]

    @property
    def passed(self) -> bool:
        """Copy publishes only with zero unsupported and zero banned claims.

        Not a score and not a threshold. One invented pressure rating is enough to make a
        description wrong, and averaging it against nine correct sentences would hide it.
        """
        return not any(claim.verdict.blocks_publication for claim in self.claims)

    def summary(self) -> dict[str, object]:
        return {
            "claims": len(self.claims),
            "supported": len(self.supported),
            "unsupported": len(self.unsupported),
            "banned": len(self.banned),
            "passed": self.passed,
        }


# A number followed by a unit. The unit alternatives are ordered longest-first so `ft-lb` is not
# read as `ft`, and the inch mark is matched explicitly because copy writes 3/4" not 3/4 in.
_QUANTITY = re.compile(
    r"""
    (?P<value>
        # A leading sign, but only where it is not a range dash. "-20degF to 366degF" opens
        # with a genuine minus; "18-22 ft-lb" does not. Requiring the sign to be preceded by a
        # non-digit distinguishes them, and getting this wrong silently turns every sub-zero
        # temperature into its positive twin — which then fails to match a real, negative,
        # verified bound.
        (?<![\d.])[-\u2212\u2013]?\s*
        (?:
            \d+\s*-\s*\d+/\d+      # 1-1/4
            | \d+/\d+              # 3/4
            | \d[\d,]*\.?\d*       # 600, 1,200, 18.5
        )
    )
    \s*
    (?P<unit>
        ft-lbs? | ft\.lbs? | foot-pounds?
        | in-lbs? | inch-pounds?
        | N[.\u00b7]m | newton-met(?:er|re)s?
        | psig? | bar | kpa | mpa
        | deg\s?[CF] | \u00b0\s?[CF] | degrees?\s+(?:C|F|celsius|fahrenheit)
        | mm | cm | millimet(?:er|re)s? | centimet(?:er|re)s?
        | inch(?:es)? | in\b | "
        | lbs? | pounds? | kg | kilograms?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Ranges like "-20°F to 366°F" produce two quantity matches, which is what we want: both bounds
# must be supported independently.

_UNIT_ALIASES = {
    "psig": "psi",
    "ft-lb": "N.m",
    "ft-lbs": "N.m",
    "ft.lb": "N.m",
    "ft.lbs": "N.m",
    "foot-pound": "N.m",
    "foot-pounds": "N.m",
    "n.m": "N.m",
    "n\u00b7m": "N.m",
    "newton-meter": "N.m",
    "newton-meters": "N.m",
    "newton-metre": "N.m",
    "newton-metres": "N.m",
    '"': "in",
    "inch": "in",
    "inches": "in",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "kilogram": "kg",
    "kilograms": "kg",
    "degc": "degC",
    "deg c": "degC",
    "\u00b0c": "degC",
    "\u00b0 c": "degC",
    "degrees c": "degC",
    "degrees celsius": "degC",
    "degf": "degF",
    "deg f": "degF",
    "\u00b0f": "degF",
    "\u00b0 f": "degF",
    "degrees f": "degF",
    "degrees fahrenheit": "degF",
}


def _normalise_unit(raw: str) -> str:
    folded = raw.strip().casefold().replace("  ", " ")
    return _UNIT_ALIASES.get(folded, folded)


def _parse_number(raw: str) -> float | None:
    text = raw.replace(",", "").replace("\u2212", "-").replace("\u2013", "-").strip()

    sign = 1.0
    if text.startswith("-"):
        sign = -1.0
        text = text[1:].strip()

    # Mixed fraction: 1-1/4
    mixed = re.fullmatch(r"(\d+)\s*-\s*(\d+)/(\d+)", text)
    if mixed:
        whole, num, den = (int(g) for g in mixed.groups())
        return sign * (whole + num / den) if den else None
    simple = re.fullmatch(r"(\d+)/(\d+)", text)
    if simple:
        num, den = (int(g) for g in simple.groups())
        return sign * (num / den) if den else None
    try:
        return sign * float(text)
    except ValueError:
        return None


def check_copy(
    fields: dict[str, str], sheet: FactSheet, policy: CopyPolicy
) -> ClaimReport:
    """Check every field of a piece of generated copy against the fact sheet."""
    report = ClaimReport()
    for field_name, text in fields.items():
        if not text:
            continue
        report.claims.extend(check_text(text, sheet, policy, field_name=field_name))
    return report


def check_text(
    text: str, sheet: FactSheet, policy: CopyPolicy, *, field_name: str = ""
) -> list[Claim]:
    """Extract and verify every checkable assertion in one string."""
    claims: list[Claim] = []
    claims.extend(_banned_claims(text, policy, field_name))
    claims.extend(_regulated_claims(text, sheet, policy, field_name))
    claims.extend(_quantity_claims(text, sheet, policy, field_name))
    claims.extend(_standard_claims(text, sheet, policy, field_name))
    claims.extend(_designation_claims(text, sheet, policy, field_name))
    return claims


def _banned_claims(text: str, policy: CopyPolicy, field_name: str) -> list[Claim]:
    folded = text.casefold()
    found = []
    for phrase in policy.banned_phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", folded):
            found.append(
                Claim(
                    kind=ClaimKind.BANNED,
                    text=phrase,
                    verdict=ClaimVerdict.BANNED,
                    reason=(
                        "comparative, absolute or promissory language cannot be substantiated "
                        "by any product attribute"
                    ),
                    field_name=field_name,
                )
            )
    return found


def _regulated_claims(
    text: str, sheet: FactSheet, policy: CopyPolicy, field_name: str
) -> list[Claim]:
    folded = text.casefold()
    claims: list[Claim] = []

    for rule in policy.regulated_claims:
        matched = next(
            (
                phrase
                for phrase in rule.all_phrases
                if re.search(rf"\b{re.escape(phrase)}\b", folded)
            ),
            None,
        )
        if matched is None:
            continue

        # `requires_true` is stricter than presence: a lead_free_compliant value of False must
        # not licence a lead-free claim, and neither must a queued one.
        satisfied_by = next((code for code in rule.requires_true if code in sheet.true_flags), None)
        if satisfied_by is None:
            satisfied_by = next(
                (code for code in rule.requires_any if code in sheet.present_codes), None
            )

        if satisfied_by is not None:
            claims.append(
                Claim(
                    kind=ClaimKind.REGULATED,
                    text=matched,
                    verdict=ClaimVerdict.SUPPORTED,
                    reason=f"substantiated by verified attribute '{satisfied_by}'",
                    field_name=field_name,
                    supported_by=satisfied_by,
                )
            )
            continue

        required = list(rule.requires_true) + list(rule.requires_any)
        detail = f"requires a verified {' or '.join(required)}"
        if rule.requires_true:
            detail += " that is true"
        claims.append(
            Claim(
                kind=ClaimKind.REGULATED,
                text=matched,
                verdict=ClaimVerdict.UNSUPPORTED,
                reason=f"regulated claim without substantiation: {detail}",
                field_name=field_name,
            )
        )
    return claims


def _quantity_claims(
    text: str, sheet: FactSheet, policy: CopyPolicy, field_name: str
) -> list[Claim]:
    claims: list[Claim] = []
    for match in _QUANTITY.finditer(text):
        raw_value, raw_unit = match.group("value"), match.group("unit")
        magnitude = _parse_number(raw_value)
        if magnitude is None:
            continue

        unit = _normalise_unit(raw_unit)
        phrase = match.group(0).strip()
        support = _find_quantity_support(magnitude, unit, sheet, policy.quantity_tolerance)

        if support is not None:
            claims.append(
                Claim(
                    kind=ClaimKind.QUANTITY,
                    text=phrase,
                    verdict=ClaimVerdict.SUPPORTED,
                    reason=f"matches verified '{support}'",
                    field_name=field_name,
                    supported_by=support,
                )
            )
        else:
            claims.append(
                Claim(
                    kind=ClaimKind.QUANTITY,
                    text=phrase,
                    verdict=ClaimVerdict.UNSUPPORTED,
                    reason="no verified attribute states this figure",
                    field_name=field_name,
                )
            )
    return claims


def _find_quantity_support(
    magnitude: float, unit: str, sheet: FactSheet, tolerance: float
) -> str | None:
    for fact in sheet.quantities:
        if not _within(magnitude, fact.magnitude, tolerance):
            converted = _convert(magnitude, unit, fact.unit)
            if converted is None or not _within(converted, fact.magnitude, tolerance):
                continue
        elif unit and fact.unit and not _compatible(unit, fact.unit):
            # Same number, incompatible dimension: 600 psi does not support 600 mm.
            continue
        return fact.attribute_code
    return None


def _within(a: float, b: float, tolerance: float) -> bool:
    if b == 0:
        return abs(a) <= tolerance
    return abs(a - b) / abs(b) <= tolerance


def _convert(magnitude: float, from_unit: str, to_unit: str) -> float | None:
    if not from_unit or not to_unit:
        return None
    try:
        return unit_registry.convert(magnitude, from_unit, to_unit)
    except Exception:  # noqa: BLE001 - incompatible or unknown units simply do not support it
        return None


def _compatible(a: str, b: str) -> bool:
    try:
        return unit_registry.are_compatible(a, b)
    except Exception:  # noqa: BLE001
        return False


def _corpus_support(token: str, sheet: FactSheet) -> str | None:
    """The attribute whose text contains this token, if any.

    Substring rather than equality, because a multi-valued attribute like ``approvals`` arrives
    as several entries rendered into one string.
    """
    folded = token.casefold()
    return next((code for code, entry in sheet.corpus if folded in entry), None)


def _standard_claims(
    text: str, sheet: FactSheet, policy: CopyPolicy, field_name: str
) -> list[Claim]:
    claims: list[Claim] = []
    for token in policy.find_standards(text):
        support = _corpus_support(token, sheet)
        if support is not None:
            claims.append(
                Claim(
                    kind=ClaimKind.STANDARD,
                    text=token,
                    verdict=ClaimVerdict.SUPPORTED,
                    reason=f"appears in verified attribute '{support}'",
                    field_name=field_name,
                    supported_by=support,
                )
            )
        else:
            claims.append(
                Claim(
                    kind=ClaimKind.STANDARD,
                    text=token,
                    verdict=ClaimVerdict.UNSUPPORTED,
                    reason=(
                        "no verified attribute references this standard; an invented "
                        "certification reads as authoritative and is trivially disprovable"
                    ),
                    field_name=field_name,
                )
            )
    return claims


def _designation_claims(
    text: str, sheet: FactSheet, policy: CopyPolicy, field_name: str
) -> list[Claim]:
    claims: list[Claim] = []
    for token in policy.find_designations(text):
        support = _corpus_support(token, sheet)
        if support is not None:
            claims.append(
                Claim(
                    kind=ClaimKind.DESIGNATION,
                    text=token,
                    verdict=ClaimVerdict.SUPPORTED,
                    reason=f"appears in verified attribute '{support}'",
                    field_name=field_name,
                    supported_by=support,
                )
            )
        else:
            claims.append(
                Claim(
                    kind=ClaimKind.DESIGNATION,
                    text=token,
                    verdict=ClaimVerdict.UNSUPPORTED,
                    reason="no verified attribute states this material or grade",
                    field_name=field_name,
                )
            )
    return claims
