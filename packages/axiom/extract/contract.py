"""Parsing the model's evidence contract.

The live contract has two independent channels in one response: schema-bound ``attributes`` and
source-native ``manufacturer_specifications``. The first may become normalized catalogue values;
the second preserves every explicit manufacturer label/value pair without inventing schema codes.
Both require verbatim evidence.

Parsing is liberal about formatting and strict about substance. Legacy bare attribute arrays remain
accepted so saved fixtures and older model responses can still be replayed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum


class ContractError(ValueError):
    """Raised when the response is not a usable evidence contract. Triggers escalation."""


class Certainty(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def provisional_confidence(self) -> float:
        """A placeholder score, not a calibrated one."""
        return {Certainty.HIGH: 0.90, Certainty.MEDIUM: 0.70, Certainty.LOW: 0.50}[self]


@dataclass(frozen=True)
class ContractItem:
    """One schema-bound attribute element of the evidence contract."""

    attribute_code: str
    found: bool
    value_raw: str | None = None
    evidence_quote: str | None = None
    evidence_page: int | None = None
    certainty: Certainty = Certainty.MEDIUM
    reason: str | None = None

    @property
    def is_well_formed(self) -> bool:
        if not self.found:
            return True
        return bool(self.value_raw and self.value_raw.strip()) and bool(
            self.evidence_quote and self.evidence_quote.strip()
        )


@dataclass(frozen=True)
class ManufacturerSpecificationContractItem:
    """One arbitrary source-stated label/value pair before evidence verification."""

    label_raw: str | None
    value_raw: str | None
    evidence_quote: str | None
    evidence_page: int | None = None
    certainty: Certainty = Certainty.MEDIUM

    @property
    def is_well_formed(self) -> bool:
        return all(
            value is not None and bool(value.strip())
            for value in (self.label_raw, self.value_raw, self.evidence_quote)
        )


@dataclass(frozen=True)
class ExtractionContract:
    """The two channels returned by one extraction call."""

    attributes: tuple[ContractItem, ...] = ()
    manufacturer_specifications: tuple[ManufacturerSpecificationContractItem, ...] = ()


_DEFERRAL = re.compile(
    r"consult"
    r"|contact\s+(?:the\s+)?(?:factory|manufacturer|supplier)"
    r"|see\s+(?:\w+\s+){0,2}(?:chart|table|page|drawing|catalog|catalogue|appendix|figure)"
    r"|available\s+on\s+request|upon\s+request|refer\s+to",
    re.IGNORECASE,
)
_APPLICABILITY = re.compile(
    r"different\s+(?:size|variant|model)|only\s+for|applies\s+to|not\s+for\s+this"
    r"|other\s+size|1/2\"|mismatch|does\s+not\s+match",
    re.IGNORECASE,
)


def classify_abstention(reason: str | None) -> str:
    """Bucket an abstention reason: ``deferred``, ``applicability`` or ``absent``."""
    if not reason:
        return "absent"
    if _DEFERRAL.search(reason):
        return "deferred"
    if _APPLICABILITY.search(reason):
        return "applicability"
    return "absent"


def parse_extraction_contract(
    text: str,
    *,
    expected_codes: tuple[str, ...] | None = None,
    allow_specification_only: bool = False,
) -> ExtractionContract:
    """Parse the complete two-channel extraction response.

    A bare array is treated as a legacy attribute-only response. When typed codes are expected, at
    least one usable typed item is required so a specification-only answer cannot suppress cascade
    escalation. An explicitly empty ``expected_codes`` tuple denotes a specification-only pass,
    where even an empty two-channel object is a valid answer.

    ``allow_specification_only`` relaxes the typed-attribute requirement for a classified pass when
    the answer nonetheless carries usable manufacturer specifications. This is for a source read
    *for* its full attribute list — a manufacturer's own product page, whose "Technical details"
    are specifications rather than schema-typed values — where a spec-bearing response is a real
    answer, not the empty one the escalation guard is meant to reject. The guard still fires for a
    response that has neither a typed attribute nor a usable specification, so a genuinely empty
    reply still escalates.
    """
    payload = _extract_json(text)

    if isinstance(payload, list):
        attributes_raw, specifications_raw = payload, []
    elif isinstance(payload, dict):
        attributes_raw = payload.get("attributes")
        specifications_raw = (
            payload.get("manufacturer_specifications")
            or payload.get("source_specifications")
            or payload.get("specifications")
            or []
        )
        if not isinstance(attributes_raw, list):
            for key in ("results", "data", "items", "extractions"):
                if isinstance(payload.get(key), list):
                    attributes_raw = payload[key]
                    break
        if attributes_raw is None:
            attributes_raw = []
        if not isinstance(attributes_raw, list) or not isinstance(specifications_raw, list):
            raise ContractError("response arrays have an invalid shape")
    else:
        raise ContractError(f"expected a JSON object or array, got {type(payload).__name__}")

    attributes = _parse_attributes(attributes_raw, expected_codes=expected_codes)
    specifications = _parse_specifications(specifications_raw)
    # A classified pass with no typed attribute normally escalates: a weak model that found nothing
    # typed should hand off to a stronger one rather than have its silence accepted. But when the
    # caller is reading a source for its specification list and the response did carry usable
    # specifications, that silence is the answer, not a failure to try.
    lacks_typed_attribute = expected_codes and not any(
        item.is_well_formed for item in attributes
    )
    if lacks_typed_attribute and not (allow_specification_only and specifications):
        raise ContractError("response contained no usable typed attribute items")
    if expected_codes == () and specifications_raw and not specifications:
        raise ContractError(
            "specification-only response contained no usable specification items"
        )
    if expected_codes == () and attributes_raw and not specifications:
        raise ContractError(
            "specification-only response contained typed attributes but no specifications"
        )
    if expected_codes != () and not attributes and not specifications:
        raise ContractError("response contained no usable contract items")
    return ExtractionContract(tuple(attributes), tuple(specifications))


def parse_contract(
    text: str, *, expected_codes: tuple[str, ...] | None = None
) -> list[ContractItem]:
    """Backward-compatible attribute-only parser.

    New extraction code should call :func:`parse_extraction_contract`; cascade and contract tests
    still use this function to exercise the original single-array boundary.
    """
    parsed = parse_extraction_contract(text, expected_codes=expected_codes)
    if not parsed.attributes:
        raise ContractError("response contained no usable contract items")
    return list(parsed.attributes)


def _parse_attributes(
    payload: list, *, expected_codes: tuple[str, ...] | None
) -> list[ContractItem]:
    allowed = set(expected_codes) if expected_codes is not None else None
    items: list[ContractItem] = []
    seen: set[str] = set()

    for raw in payload:
        if not isinstance(raw, dict):
            continue
        code = raw.get("attribute_code") or raw.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        code = code.strip()
        if allowed is not None and code not in allowed:
            continue
        if code in seen:
            continue
        seen.add(code)
        items.append(
            ContractItem(
                attribute_code=code,
                found=_coerce_bool(raw.get("found")),
                value_raw=_coerce_text(raw.get("value_raw")),
                evidence_quote=_coerce_text(raw.get("evidence_quote")),
                evidence_page=_coerce_page(raw.get("evidence_page")),
                certainty=_coerce_certainty(raw.get("certainty")),
                reason=_coerce_text(raw.get("reason")),
            )
        )
    return items


def _parse_specifications(payload: list) -> list[ManufacturerSpecificationContractItem]:
    """Keep every candidate until source verification can choose supported evidence.

    Label/value deduplication belongs after quote location. Dropping duplicates here would let an
    invalid first citation suppress a later candidate whose quote actually supports the statement.
    """
    items: list[ManufacturerSpecificationContractItem] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        label = _coerce_text(raw.get("label_raw") or raw.get("label") or raw.get("attribute"))
        value = _coerce_text(raw.get("value_raw") or raw.get("value"))
        quote = _coerce_text(raw.get("evidence_quote") or raw.get("quote"))
        item = ManufacturerSpecificationContractItem(
            label_raw=label,
            value_raw=value,
            evidence_quote=quote,
            evidence_page=_coerce_page(raw.get("evidence_page") or raw.get("page")),
            certainty=_coerce_certainty(raw.get("certainty")),
        )
        if item.is_well_formed:
            items.append(item)
    return items


def _extract_json(text: str):
    """Strip fences and leading prose, then parse."""
    if not text or not text.strip():
        raise ContractError("empty response")

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    candidates = []
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        if start >= 0:
            candidates.append((start, opener, closer))
    for start, _opener, closer in sorted(candidates):
        end = cleaned.rfind(closer)
        if end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ContractError("response was not valid JSON")


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1"}
    return bool(value)


def _coerce_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        joined = ", ".join(str(v).strip() for v in value if str(v).strip())
        return joined or None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_page(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            page = int(match.group(0))
            return page if page >= 1 else None
    return None


def _coerce_certainty(value) -> Certainty:
    if isinstance(value, str):
        try:
            return Certainty(value.strip().lower())
        except ValueError:
            return Certainty.MEDIUM
    return Certainty.MEDIUM
