"""Parsing the evidence contract.

The contract is the JSON array the model must return: one element per requested attribute,
each either a value with a verbatim quote or an explicit abstention with a reason.

Parsing is **liberal about formatting and strict about substance**. A model that wraps valid
JSON in a markdown fence is a formatting nuisance and the pipeline should absorb it; a model
that returns a value with no quote is a correctness problem and must not be absorbed. Those
are different failures and conflating them either breaks working extractions or lets
unsourced values through.
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
        """A placeholder score, not a calibrated one.

        Self-reported certainty is weakly correlated with correctness at best. These values
        exist so the pipeline has something to sort by before the calibrated estimator is
        trained on real reviewer outcomes; that estimator replaces them entirely.
        """
        return {Certainty.HIGH: 0.90, Certainty.MEDIUM: 0.70, Certainty.LOW: 0.50}[self]


@dataclass(frozen=True)
class ContractItem:
    """One element of the evidence contract."""

    attribute_code: str
    found: bool
    value_raw: str | None = None
    evidence_quote: str | None = None
    evidence_page: int | None = None
    certainty: Certainty = Certainty.MEDIUM
    reason: str | None = None

    @property
    def is_well_formed(self) -> bool:
        """A found value must carry both a value and a quote. No quote, no value."""
        if not self.found:
            return True
        return bool(self.value_raw and self.value_raw.strip()) and bool(
            self.evidence_quote and self.evidence_quote.strip()
        )


# Phrases indicating the source deferred the value rather than omitting it. Worth
# distinguishing: "consult factory" means the value exists and we must ask, whereas silence
# means nobody has it. Those drive different follow-up actions.
_DEFERRAL = re.compile(
    r"consult"
    r"|contact\s+(?:the\s+)?(?:factory|manufacturer|supplier)"
    # allow qualifiers between the verb and the noun: "see derating chart", "see table 3"
    r"|see\s+(?:\w+\s+){0,2}(?:chart|table|page|drawing|catalog|catalogue|appendix|figure)"
    r"|available\s+on\s+request|upon\s+request|refer\s+to",
    re.IGNORECASE,
)

# Phrases indicating the value was present but scoped to a different variant. This is the
# applicability case; the value is real and belongs to another SKU.
_APPLICABILITY = re.compile(
    r"different\s+(?:size|variant|model)|only\s+for|applies\s+to|not\s+for\s+this"
    r"|other\s+size|1/2\"|mismatch|does\s+not\s+match",
    re.IGNORECASE,
)


def classify_abstention(reason: str | None) -> str:
    """Bucket an abstention reason: 'deferred', 'applicability' or 'absent'."""
    if not reason:
        return "absent"
    if _DEFERRAL.search(reason):
        return "deferred"
    if _APPLICABILITY.search(reason):
        return "applicability"
    return "absent"


def parse_contract(
    text: str, *, expected_codes: tuple[str, ...] | None = None
) -> list[ContractItem]:
    """Parse a model response into contract items.

    Raises :class:`ContractError` when the response is unusable, which the cascade treats as
    grounds to escalate to a stronger tier.
    """
    payload = _extract_json(text)

    if isinstance(payload, dict):
        # Some models wrap the array in an object despite instructions.
        for key in ("attributes", "results", "data", "items", "extractions"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            raise ContractError("response was a JSON object with no recognisable array field")

    if not isinstance(payload, list):
        raise ContractError(f"expected a JSON array, got {type(payload).__name__}")

    allowed = set(expected_codes) if expected_codes else None
    items: list[ContractItem] = []
    seen: set[str] = set()

    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            continue  # a stray non-object element is noise, not a fatal error
        code = raw.get("attribute_code") or raw.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        code = code.strip()
        if allowed is not None and code not in allowed:
            continue  # hallucinated attribute codes are dropped silently
        if code in seen:
            continue  # first response for an attribute wins
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
        del index

    if not items:
        raise ContractError("response contained no usable contract items")
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

    # Fall back to the outermost bracketed region, which handles a model that prefixed
    # commentary despite being told not to.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start >= 0 and end > start:
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
        # multi_enum attributes legitimately come back as a list
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
