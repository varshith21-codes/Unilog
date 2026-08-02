"""Human-in-the-loop review.

The point of this package is speed with accountability: put the evidence beside the value so a
decision takes seconds, and record every decision as training signal so the next batch needs
less review.
"""

from axiom.review.session import (
    ACCEPT,
    CORRECT,
    REJECT,
    EvidenceView,
    ReviewItem,
    ReviewOutcome,
    ReviewSession,
    SourcePage,
    ValidationView,
    build_session,
    queue_summary,
    record_decision,
)

__all__ = [
    "ACCEPT",
    "CORRECT",
    "REJECT",
    "EvidenceView",
    "ReviewItem",
    "ReviewOutcome",
    "ReviewSession",
    "SourcePage",
    "ValidationView",
    "build_session",
    "queue_summary",
    "record_decision",
]
