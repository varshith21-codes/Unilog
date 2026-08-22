"""Deterministic attribute extraction from a cryptic supplier description.

The guide frames the whole problem around this string: *"descriptions are cryptic ('3/8 CPLG BRS
150#')"*, and the task is to turn one into a standardised record. So the description is not merely
an input to be classified — it is a **source document**, and a substring of it is a citable,
verifiable evidence span.

That last point is what makes this module compatible with the evidence rule rather than an
exception to it. A value extracted here cites a quote we can prove is present, in a document the
client themselves supplied. It is *better* evidenced than a model extraction, because verification
is a substring check rather than a fuzzy match.

No model is involved. Two rule families, and neither is hardcoded per attribute:

**Unit-derived patterns.** An attribute that declares ``quantity_kind: voltage`` and
``canonical_unit: V`` implies the pattern "a number followed by a volt spelling". The pattern is
*generated* from the unit registry's own alias table, so adding a quantity attribute to the schema
gives it a description-extraction rule with no code change — the same property the extraction prompt
has.

**Declared abbreviations.** ``schema/abbreviations.yaml`` maps (attribute, abbreviation) to a
canonical value. Scoped per attribute on purpose: "SS" is stainless steel on an appliance and a
socket set on a fastener.

The guard that makes both safe is the same one that fixed classification precision: **only
attributes the class actually binds are looked for.** A dishwasher class binds no wattage attribute,
so "24 in W" cannot be read as 24 watts. Without class scoping, unit patterns would be as
promiscuous as the retrieval vocabulary was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from axiom.core.evidence import EvidenceSpan
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueStatus,
)
from axiom.normalize.units import registry as unit_registry
from axiom.schema import SchemaRegistry
from axiom.schema.models import AttributeDefinition

PROMPT_VERSION = "description@v1"
"""Not a prompt, but the same field the model path fills. A value's provenance has to name the
thing that produced it, and "which version of the rules" is the reproducibility question here."""

QUANTITY_CONFIDENCE = 0.88
"""A number beside its unit in the client's own string. High, but not certain: the string is
abbreviated and a supplier can transpose a figure. Deliberately below the 0.95 an extraction with
a verified datasheet quote earns."""

ABBREVIATION_CONFIDENCE = 0.82
"""A declared expansion of a code in the client's own string. Lower than a quantity because the
expansion adds information the string did not literally contain."""

MIN_TOKEN_LENGTH = 2


@dataclass(frozen=True)
class AbbreviationTable:
    """(attribute, abbreviation) -> canonical value, as declared in schema/abbreviations.yaml."""

    terms: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None) -> AbbreviationTable:
        path = Path(path) if path else default_abbreviation_path()
        if not path.is_file():
            return cls()
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        terms: dict[str, dict[str, str]] = {}
        notes: dict[str, str] = {}
        for entry in payload.get("abbreviations", []):
            code = entry.get("attribute")
            if not code:
                continue
            # Folded on the way in so lookup is case-insensitive without folding at every call.
            terms[code] = {
                str(k).strip().casefold(): str(v) for k, v in (entry.get("terms") or {}).items()
            }
            if note := entry.get("note"):
                notes[code] = " ".join(str(note).split())
        return cls(terms=terms, notes=notes)

    def for_attribute(self, code: str) -> dict[str, str]:
        return self.terms.get(code, {})

    def attributes(self) -> tuple[str, ...]:
        return tuple(sorted(self.terms))


@dataclass(frozen=True)
class DescriptionMatch:
    """One attribute read out of a description, with the exact span that evidences it."""

    attribute_code: str
    value_raw: str
    start: int
    end: int
    rule: str
    confidence: float
    canonical: object | None = None
    display: str | None = None

    @property
    def quote(self) -> str:
        return self.value_raw

    def summary(self) -> dict[str, object]:
        return {
            "attribute": self.attribute_code,
            "raw": self.value_raw,
            "span": [self.start, self.end],
            "rule": self.rule,
            "confidence": round(self.confidence, 3),
            "display": self.display,
        }


@dataclass
class DescriptionExtraction:
    """Everything one description yielded, plus what was seen and refused."""

    description: str
    class_code: str | None
    matches: list[DescriptionMatch] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    """(attribute_code, reason) for a recognised token that was not allowed to become a value.
    Reported rather than dropped: a compliance code appearing in a description is exactly the
    thing a reviewer should see, and silently ignoring it would hide it."""

    def codes(self) -> tuple[str, ...]:
        return tuple(m.attribute_code for m in self.matches)

    def summary(self) -> dict[str, object]:
        return {
            "class_code": self.class_code,
            "matched": [m.summary() for m in self.matches],
            "refused": [{"attribute": c, "reason": r} for c, r in self.refused],
        }


def default_abbreviation_path() -> Path:
    return Path(__file__).resolve().parents[3] / "schema" / "abbreviations.yaml"


# ------------------------------------------------------------------ unit pattern generation


SAFE_BARE_UNITS = frozenset({"V", "A", "W"})
"""Single-character unit spellings permitted when reading an abbreviated description.

A one-letter unit is fine on a datasheet and dangerous in a 35-character part description, because
a digit followed by a letter is overwhelmingly more likely to be a part number than a measurement.
The registry lists ``c`` as an alias for Celsius, which turned ``Apollo 77C Series`` into
``temperature_range = 77 degC`` — a plausible number inside a plausible range, cited to a real
substring.

``V``, ``A`` and ``W`` stay because suppliers genuinely write them bare and unspaced in exactly this
context: ``120V``, ``15A``, ``60W``. ``C``, ``F``, ``K`` and ``L`` do not survive, so a temperature
in a description must spell its unit out (``degF``, ``°C``) to be read.
"""


def _unit_spellings(unit_code: str) -> list[str]:
    """Every spelling the registry accepts for a unit, longest first.

    Longest-first matters: "VAC" must be tried before "V", or the alternation matches the "V" and
    leaves "AC" stranded in the description.

    Single-character spellings are filtered against :data:`SAFE_BARE_UNITS` — see the note there
    for the part-number collision that made it necessary. A single-character spelling that is not
    alphanumeric is exempt from that filter, because the collision the filter exists to prevent is
    specifically a DIGIT FOLLOWED BY A LETTER being read as a measurement. ``"`` and ``'`` are the
    prime marks for inch and foot, they cannot occur inside a part-number token, and excluding them
    made every imperial dimension in an abbreviated description unreadable: a 52" fan span, a 12'
    board, a 5" cut-off wheel and a 30" range all state their unit and only that way.
    """
    resolved = unit_registry.resolve(unit_code)
    if resolved is None:
        return []
    spellings = {resolved.code, *resolved.aliases}
    if resolved.display:
        spellings.add(resolved.display)
    usable = {
        spelling
        for spelling in spellings
        if len(spelling) > 1
        or spelling.upper() in SAFE_BARE_UNITS
        or not spelling.isalnum()
    }
    return sorted(usable, key=len, reverse=True)


def _units_of_kind(quantity_kind: str) -> list[str]:
    """Every unit code the registry holds for one quantity kind.

    Used so a dimension can be read in whatever unit the supplier wrote rather than only in the one
    the schema guessed. `nominal_length` declares `unit_hint: in` because lighting states tube
    lengths in inches, and the same attribute carries decking board lengths in FEET — patterning on
    the hint alone read the lamps and missed 167 boards. The kind is already declared on every
    unit-bearing attribute and conversion to canonical happens per match, so this costs nothing in
    correctness and removes a guess.
    """
    out = []
    for code in unit_registry.known_units():
        resolved = unit_registry.resolve(code)
        if resolved is not None and resolved.kind.value == quantity_kind:
            out.append(code)
    return out


def _quantity_pattern(*unit_codes: str) -> re.Pattern[str] | None:
    """A pattern for "number immediately followed by any of these units".

    ``\\s?`` rather than ``\\s*`` is the guard that keeps this honest on real data. The client's own
    ground truth contains "24 in W x 24-1/4 in D", where W is an axis label. Allowing arbitrary
    whitespace between number and unit would read that as 24 watts. One optional space matches how
    a unit is actually written ("120V", "120 V") and refuses a token two words away.

    Several unit codes are accepted because an attribute's canonical unit is frequently NOT the unit
    the source writes. Every dimension in this schema is canonically millimetres and every
    description in this catalogue is in inches or feet, so a pattern built from the canonical unit
    alone matched nothing at all. The caller passes the canonical unit and the declared
    ``unit_hint`` together; the matched spelling is resolved on its own terms and converted, so a
    value read as inches still stores as millimetres.
    """
    spellings: list[str] = []
    for unit_code in unit_codes:
        for spelling in _unit_spellings(unit_code):
            if spelling not in spellings:
                spellings.append(spelling)
    if not spellings:
        return None
    # Longest first across the merged set, for the same reason as within one unit.
    spellings.sort(key=len, reverse=True)
    alternation = "|".join(re.escape(s) for s in spellings)
    # The trailing guard permits an `x` separator, and that alternative is not a convenience — it is
    # a correctness fix. `(?![\w])` alone rejects the FIRST figure of a dimension chain, because
    # `5"x.045"x7/8"` puts an `x` immediately after the unit mark. The match then slides down the
    # string and finds `8"` — the tail of the 7/8" arbor — which is inside a cut-off wheel's
    # plausible diameter range and publishes as a confidently wrong, correctly cited 8" diameter on
    # a 5" wheel. Accepting `x` followed by a digit or a decimal point reads the 5" instead.
    #
    # The separator must be followed by a digit or a point to qualify, so this does not open the
    # guard to a letter: `18VDC` is still refused by the first alternative, as it must be.
    #
    # The AXIS-LABEL guard is the other half, and it is what keeps the imperial spellings honest.
    # "24 in W x 24-1/4 in D" states a width and a depth, and a single-dimension attribute has no
    # claim on either: the figure belongs to the axis its label names. Refusing a match followed by
    # a bare W, D, H or L leaves those to `overall_size`, which stores the pair as written.
    #
    # The trailing `(?![\w])` on the axis letter is load-bearing: this supplier writes "Wh" for
    # white, and "12' Wh Heritage Post" must still read its length. A bare W is an axis; a W that
    # starts a word is not.
    return re.compile(
        rf"(?<![\w.])(\d+(?:\.\d+)?(?:-\d+/\d+)?)\s?({alternation})"
        rf"(?:(?![\w])|(?=[xX][\d.]))"
        rf"(?!\s?[WDHL](?![\w]))",
        re.IGNORECASE,
    )


def _abbreviation_pattern(abbreviation: str) -> re.Pattern[str]:
    """Whole-token match, so "LF" does not fire inside "SHELF"."""
    return re.compile(rf"(?<![\w/]){re.escape(abbreviation)}(?![\w/])", re.IGNORECASE)


# ------------------------------------------------------------------ extraction


def extract_from_description(
    description: str,
    *,
    registry: SchemaRegistry,
    class_code: str | None,
    abbreviations: AbbreviationTable | None = None,
) -> DescriptionExtraction:
    """Read every attribute the class declares that the description evidences.

    Class-scoped by design. Looking for every attribute in the dictionary would make unit patterns
    as promiscuous as the retrieval vocabulary was before `identity_terms` — "24 in W" would become
    24 watts on any class that happened to have a wattage attribute somewhere in the schema.
    """
    result = DescriptionExtraction(description=description, class_code=class_code)
    if not description or not class_code:
        return result

    try:
        definition = registry.product_class(class_code)
    except KeyError:
        return result

    table = abbreviations if abbreviations is not None else AbbreviationTable.load()
    claimed: set[int] = set()

    # Quantities first. They are the more specific rule — an abbreviation table could plausibly
    # contain a token that also appears inside a quantity match, and the quantity is the better
    # reading because it carries a magnitude.
    for binding in definition.attributes:
        try:
            attribute = registry.attribute(binding.code)
        except KeyError:
            continue
        if not attribute.datatype.needs_unit or not attribute.canonical_unit:
            continue
        _match_quantity(description, attribute, result, claimed)

    for binding in definition.attributes:
        try:
            attribute = registry.attribute(binding.code)
        except KeyError:
            continue
        terms = table.for_attribute(binding.code)
        if terms:
            _match_abbreviations(description, attribute, terms, result, claimed)

    result.matches.sort(key=lambda m: m.start)
    return result


def _match_quantity(
    description: str,
    attribute: AttributeDefinition,
    result: DescriptionExtraction,
    claimed: set[int],
) -> None:
    # Every unit of the attribute's own quantity kind, so the figure can be read in whatever the
    # supplier wrote and converted afterwards. The canonical unit alone matched no imperial
    # dimension at all, and the declared hint is a single guess that is right for one cohort and
    # wrong for the next — inches for a lamp tube, feet for a deck board, same attribute.
    units = (
        _units_of_kind(attribute.quantity_kind)
        if attribute.quantity_kind
        else [attribute.canonical_unit or "", attribute.unit_hint or ""]
    )
    pattern = _quantity_pattern(*units)
    if pattern is None:
        return
    for found in pattern.finditer(description):
        span = range(found.start(), found.end())
        if claimed & set(span):
            continue

        magnitude_text, unit_text = found.group(1), found.group(2)
        magnitude = _to_float(magnitude_text)
        if magnitude is None:
            continue

        resolved = unit_registry.resolve(unit_text)
        if resolved is None:
            continue

        if attribute.plausible_range:
            low, high = attribute.plausible_range
            canonical_magnitude = resolved.to_canonical(magnitude)
            if not low <= canonical_magnitude <= high:
                # Outside what this attribute can physically be. Almost always a coincidental
                # match rather than a real value, so it is refused with the reason attached.
                result.refused.append(
                    (
                        attribute.code,
                        f"{found.group(0)!r} is outside the plausible range "
                        f"[{low}, {high}] {attribute.canonical_unit}",
                    )
                )
                continue

        canonical = Quantity(
            magnitude=resolved.to_canonical(magnitude),
            unit=attribute.canonical_unit or resolved.code,
        )
        result.matches.append(
            DescriptionMatch(
                attribute_code=attribute.code,
                value_raw=found.group(0),
                start=found.start(),
                end=found.end(),
                rule=f"unit:{attribute.canonical_unit}",
                confidence=QUANTITY_CONFIDENCE,
                canonical=canonical,
                display=f"{magnitude_text} {resolved.code}",
            )
        )
        claimed.update(span)
        if not attribute.multivalued:
            # One value per single-valued attribute. The loop continues past claimed spans and
            # implausible figures to FIND a usable match, not to collect every one of them:
            # "5\"x.045\"x7/8\" Metal Cut Off Disc" otherwise recorded the wheel diameter twice, as
            # 5" and again as the 8" tail of the arbor fraction. Two cited values for one dimension
            # is worse than either alone, because both look verified.
            return


def _match_abbreviations(
    description: str,
    attribute: AttributeDefinition,
    terms: dict[str, str],
    result: DescriptionExtraction,
    claimed: set[int],
) -> None:
    # Longest abbreviation first: "BSS" must win over "SS".
    for abbreviation in sorted(terms, key=len, reverse=True):
        found = _abbreviation_pattern(abbreviation).search(description)
        if found is None:
            continue
        span = range(found.start(), found.end())
        if claimed & set(span):
            continue

        if attribute.compliance_claim:
            # A three-letter code in a description is not a certification. The attribute declares
            # strict evidence, so this is reported for review rather than published.
            result.refused.append(
                (
                    attribute.code,
                    f"{found.group(0)!r} suggests this compliance claim, but a code in a "
                    f"description is not a certification; needs a document",
                )
            )
            claimed.update(span)
            return

        canonical = terms[abbreviation]
        if attribute.datatype.is_enumerated:
            snapped = attribute.resolve_allowed(canonical)
            if snapped is None:
                result.refused.append(
                    (
                        attribute.code,
                        f"{canonical!r} is not a permitted value for this enum",
                    )
                )
                claimed.update(span)
                return
            canonical = snapped

        result.matches.append(
            DescriptionMatch(
                attribute_code=attribute.code,
                value_raw=found.group(0),
                start=found.start(),
                end=found.end(),
                rule=f"abbreviation:{abbreviation}",
                confidence=ABBREVIATION_CONFIDENCE,
                canonical=canonical,
                display=canonical,
            )
        )
        claimed.update(span)
        return  # one value per attribute from this family


def _to_float(text: str) -> float | None:
    """Parse a magnitude that may carry an imperial fraction, e.g. "50-1/4"."""
    from axiom.normalize.parsers import parse_fraction

    if "/" in text:
        return parse_fraction(text)
    try:
        return float(text)
    except ValueError:
        return None


# ------------------------------------------------------------------ to attribute values


def to_attribute_values(
    extraction: DescriptionExtraction,
    *,
    document_id: str,
    document_sha256: str,
    schema_version: str | None = None,
    accept: bool = True,
) -> list[AttributeValue]:
    """Turn matches into record values, each citing its own substring.

    ``quote_verified=True`` is asserted here rather than hoped for, and it is the one place this
    module is *stronger* than the model path: verification is ``description[start:end] == quote``,
    a substring check on a string we hold, not a fuzzy match against a parsed PDF. The assertion is
    re-derived below rather than trusted from the match, so a bug in span arithmetic surfaces as a
    failure instead of as a false citation.

    ``accept`` controls whether these publish. It defaults to True, which is a real decision worth
    defending: the evidence is a verified substring of a document the client themselves sent, the
    derivation is deterministic and declared in version-controlled YAML, and the attribute is
    class-scoped. That is a stronger provenance chain than an auto-accepted model extraction has.
    A caller running with a calibrated policy should pass ``accept=False`` and let the policy
    decide, which is what the full pipeline does.
    """
    values: list[AttributeValue] = []
    for match in extraction.matches:
        quote = extraction.description[match.start : match.end]
        if quote != match.value_raw:
            # Span arithmetic disagrees with the captured text. Refuse rather than cite wrongly.
            extraction.refused.append(
                (
                    match.attribute_code,
                    f"span [{match.start}:{match.end}] holds {quote!r}, not {match.value_raw!r}",
                )
            )
            continue

        span = EvidenceSpan(
            span_id=f"desc-{match.attribute_code}-{match.start}",
            document_id=document_id,
            document_sha256=document_sha256,
            quote=quote,
            page=None,
            quote_verified=True,
            match_score=1.0,
        )
        values.append(
            AttributeValue(
                attribute_code=match.attribute_code,
                value_raw=match.value_raw,
                value_canonical=match.canonical,
                value_display=match.display,
                # SUPPLIER_FEED is in the extraction family, so the type system *requires* the
                # evidence span above. That is the right family: the value came off a document the
                # supplier sent, not from inference and not from a nameless legacy row.
                method=DerivationMethod.SUPPLIER_FEED,
                confidence=match.confidence,
                status=ValueStatus.AUTO_ACCEPTED if accept else ValueStatus.CANDIDATE,
                evidence=[span],
                prompt_version=PROMPT_VERSION,
                schema_version=schema_version,
            )
        )
    return values


__all__ = [
    "ABBREVIATION_CONFIDENCE",
    "MIN_TOKEN_LENGTH",
    "SAFE_BARE_UNITS",
    "PROMPT_VERSION",
    "QUANTITY_CONFIDENCE",
    "AbbreviationTable",
    "DescriptionExtraction",
    "DescriptionMatch",
    "default_abbreviation_path",
    "extract_from_description",
    "to_attribute_values",
]
