"""Source-native product specifications that do not depend on the attribute schema.

A schema attribute is a normalized catalogue fact with a datatype, validation rules and publication
policy. A manufacturer specification is a different claim: it is the label and value the source
actually printed. Keeping the two separate lets extraction retain new or manufacturer-specific data
without inventing attribute codes or bypassing typed validation.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from axiom.core.evidence import EvidenceSpan
from axiom.core.values import DerivationMethod

_SPACE = re.compile(r"\s+")


def _fold(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()


def _phrase_pattern(text: str) -> str:
    """Match source text exactly while tolerating harmless whitespace variation."""
    return r"\s+".join(re.escape(part) for part in text.strip().split())


def quote_supports_specification(label: str, value: str, quote: str) -> bool:
    """Require one source line to state the complete label and value cells.

    The label must begin the row (apart from table/bullet punctuation), a visible column delimiter
    must separate it from the value, and the value must end at the row or a table-cell boundary.
    This preserves tables, colon pairs and dot-leader lists while rejecting substrings such as
    ``Material`` / ``Bronze`` inside ``Body Material`` / ``Bronze C84400``.
    """
    if not _fold(label) or not _fold(value):
        return False

    label_pattern = _phrase_pattern(label)
    value_pattern = _phrase_pattern(value)
    separator = r"(?:\s*[:=]\s*|\s*\|\s*|\s+\.{2,}\s+|\s+[-–—]\s+|\t+|\s{2,})"
    pattern = re.compile(
        rf"^\s*(?:(?:[|•*]\s*)|(?:-\s+))?{label_pattern}{separator}"
        rf"{value_pattern}[.,;]?(?=\s*(?:\||$))",
        re.IGNORECASE,
    )
    return any(pattern.search(line) is not None for line in quote.splitlines() or (quote,))


def specification_id(document_sha256: str, label_raw: str, value_raw: str) -> str:
    """Stable identity for one source-stated label/value pair.

    Page and coordinates are deliberately excluded. A specification repeated in a summary block
    and an ordering table is still one source claim; its first verified citation wins during
    deterministic deduplication.
    """
    material = "\x1f".join((document_sha256.lower(), _fold(label_raw), _fold(value_raw)))
    return f"ms_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


class ManufacturerSpecification(BaseModel):
    """One verbatim manufacturer label/value pair with reproducible source evidence.

    ``mapped_attribute_code`` is metadata only. It says the source label corresponds to a typed
    class attribute; it never promotes this raw observation into an ``AttributeValue``. Typed values
    still travel through normalization, validation and confidence policy independently.
    """

    model_config = ConfigDict(frozen=True)

    specification_id: str
    label_raw: str = Field(min_length=1)
    value_raw: str = Field(min_length=1)
    evidence: list[EvidenceSpan] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    method: DerivationMethod
    mapped_attribute_code: str | None = None
    citable_as_manufacturer: bool = False
    """True only when source policy established that the publisher is the manufacturer."""
    model_id: str | None = None
    model_tier: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None

    @field_validator("mapped_attribute_code", mode="before")
    @classmethod
    def _normalise_mapped_attribute_code(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def _requires_verified_source_evidence(self) -> ManufacturerSpecification:
        if not self.method.requires_evidence:
            raise ValueError(
                "manufacturer specifications must use an evidence-requiring extraction method"
            )
        if not any(span.quote_verified for span in self.evidence):
            raise ValueError("manufacturer specifications require at least one verified quote")
        if not self.has_verified_support:
            raise ValueError(
                "manufacturer specifications require one verified quote that states the "
                "label/value pair"
            )
        return self

    @property
    def normalized_label(self) -> str:
        return _fold(self.label_raw)

    @property
    def normalized_value(self) -> str:
        return _fold(self.value_raw)

    @property
    def deduplication_key(self) -> tuple[str, str, str]:
        span = self.evidence[0]
        return (span.document_sha256, self.normalized_label, self.normalized_value)

    @property
    def projection_key(self) -> tuple[str, str, str, str]:
        """Stable delivery order, independent of model response order."""
        span = self.evidence[0]
        return (
            self.normalized_label,
            self.normalized_value,
            span.document_sha256,
            self.specification_id,
        )

    @property
    def has_verified_evidence(self) -> bool:
        return any(span.quote_verified for span in self.evidence)

    @property
    def has_verified_support(self) -> bool:
        return any(
            span.quote_verified
            and quote_supports_specification(self.label_raw, self.value_raw, span.quote)
            for span in self.evidence
        )

    def citation_summary(self) -> list[str]:
        return [span.locator() for span in self.evidence]


__all__ = ["ManufacturerSpecification", "quote_supports_specification", "specification_id"]
