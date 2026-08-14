"""Spec drift detection: what a reissued datasheet did to the records built from the old one.

Blueprint Tier 3, item 24. Compares one source document against its own earlier revision and
classifies every attribute change by *direction*, because the direction is what decides urgency: a
rating that fell makes every page built on the old revision an overclaim, while a rating that rose
merely undersells the part.

Direction is not invented here. It is read from :class:`~axiom.schema.models.SubstitutionRule`,
already declared per attribute so the equivalence engine knows which way a substitution may go —
the same question asked across time instead of across parts.

What is deliberately not built: change detection over a *corpus* of documents, and the
re-enrichment scheduler that would consume these findings. Both are named in the blueprint's
incremental-re-enrichment argument and neither is needed to answer the question this module exists
for.
"""

from axiom.drift.corpus import (
    RevisionPair,
    RevisionPairError,
    golden_revision_pair,
    source_document,
)
from axiom.drift.detector import (
    AttributeDrift,
    Consequence,
    DriftKind,
    DriftReport,
    detect_drift,
    detect_drift_across,
)
from axiom.drift.report import (
    DriftSweep,
    format_drift,
    format_drift_sweep,
    sweep_drift,
)

__all__ = [
    "AttributeDrift",
    "Consequence",
    "DriftKind",
    "DriftReport",
    "DriftSweep",
    "RevisionPair",
    "RevisionPairError",
    "detect_drift",
    "detect_drift_across",
    "format_drift",
    "format_drift_sweep",
    "golden_revision_pair",
    "source_document",
    "sweep_drift",
]
