"""Gap records — the honest output when no source supports a value.

A gap is a first-class deliverable, not a failure. It records what was looked for, where
the system looked, and why it came back empty. Two consequences worth noting:

1. It makes `null` auditable. "We don't have a Cv value" and "we never checked for a Cv
   value" are very different statements, and only one of them is acceptable.
2. It drives the prioritised enrichment backlog: a gap with a revenue-exposure estimate is
   a work item, and it is what a supplier data request is generated from.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class GapReason(str, Enum):
    NOT_PRESENT_IN_ANY_SOURCE = "not_present_in_any_source"
    """Searched every available source; the value genuinely is not stated."""

    NO_SOURCE_AVAILABLE = "no_source_available"
    """No datasheet, no page, nothing to read. Private label and legacy items."""

    REFERRED_ELSEWHERE = "referred_elsewhere"
    """Source says 'consult factory' or points at a document we do not have."""

    EXTRACTED_BUT_UNVERIFIABLE = "extracted_but_unverifiable"
    """A candidate was produced but its quote could not be matched back to the source."""

    FAILED_VALIDATION = "failed_validation"
    """A value was found but rejected by a blocking validation layer."""

    CONFLICTING_SOURCES = "conflicting_sources"
    """Multiple sources disagree and precedence could not resolve it."""

    AWAITING_REVIEW = "awaiting_review"
    """Candidate exists but confidence was below the auto-accept threshold."""

    @property
    def is_actionable_by_supplier(self) -> bool:
        """Gaps a supplier data request could actually close."""
        return self in {
            GapReason.NOT_PRESENT_IN_ANY_SOURCE,
            GapReason.NO_SOURCE_AVAILABLE,
            GapReason.REFERRED_ELSEWHERE,
            GapReason.CONFLICTING_SOURCES,
        }


class RecommendedAction(str, Enum):
    REQUEST_FROM_SUPPLIER = "request_from_supplier"
    HUMAN_RESEARCH = "human_research"
    HUMAN_REVIEW = "human_review"
    RETRY_WITH_BETTER_SOURCE = "retry_with_better_source"
    ACCEPT_AS_NOT_APPLICABLE = "accept_as_not_applicable"


class Gap(BaseModel):
    """A required attribute that could not be established, with the audit trail of the attempt."""

    model_config = ConfigDict(frozen=True)

    attribute_code: str
    reason: GapReason
    sources_searched: list[str] = Field(
        default_factory=list,
        description="Every source consulted, so the negative result is itself auditable",
    )
    detail: str | None = None
    revenue_exposure_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated annual revenue at risk. Drives backlog prioritisation.",
    )
    recommended_action: RecommendedAction | None = None
    is_required: bool = Field(
        default=True, description="Required for the product's class, vs merely recommended"
    )
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_certificate_entry(self) -> dict:
        entry: dict = {
            "code": self.attribute_code,
            "reason": self.reason.value,
            "sources_searched": self.sources_searched,
            "required": self.is_required,
        }
        if self.detail:
            entry["detail"] = self.detail
        if self.revenue_exposure_usd is not None:
            entry["revenue_exposure_usd"] = round(self.revenue_exposure_usd, 2)
        if self.recommended_action:
            entry["recommended_action"] = self.recommended_action.value
        return entry
