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
from axiom.schema import Datatype, SchemaRegistry
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


def _unit_spellings(unit_code: str) -> list[str]:
    """Every spelling the registry accepts for a unit, longest first.

    Longest-first matters: "VAC" must be tried before "V", or the alternation matches the "V" and
    leaves "AC" stranded in the description.
    """
    resolved = unit_registry.resolve(unit_code)
    if resolved is None:
        return []
    spellings = {resolved.code, *resolved.aliases}
    if resolved.display:
        spellings.add(resolved.display)
    return sorted(spellings, key=len, reverse=True)


def _quantity_pattern(unit_code: str) -> re.Pattern[str] | None:
    """A pattern for "number immediately followed by this unit".

    ``\\s?`` rather than ``\\s*`` is the guard that keeps this honest on real data. The client's own
    ground truth contains "24 in W x 24-1/4 in D", where W is an axis label. Allowing arbitrary
    whitespace between number and unit would read that as 24 watts. One optional space matches how
    a unit is actually written ("120V", "120 V") and refuses a token two words away.
    """
    spellings = _unit_spellings(unit_code)
    if not spellings:
        return None
    alternation = "|".join(re.escape(s) for s in spellings)
    return re.compile(
        rf"(?<![\w.])(\d+(?:\.\d+)?(?:-\d+/\d+)?)\s?({alternation})(?![\w])",
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
    pattern = _quantity_pattern(attribute.canonical_unit or "")
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
    "PROMPT_VERSION",
    "QUANTITY_CONFIDENCE",
    "AbbreviationTable",
    "DescriptionExtraction",
    "DescriptionMatch",
    "Datatype",
    "default_abbreviation_path",
    "extract_from_description",
    "to_attribute_values",
]
