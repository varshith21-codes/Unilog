"""Projection of pipeline output for the console app.

Presentation only. Shared by the batch fixture exporter and the HTTP API so the two cannot
serve different shapes for the same data.
"""

from axiom.console.projection import (
    PUBLISHABLE_STATUSES,
    build_bundle,
    build_dataset,
    dataset_stats,
    jsonable,
    overlay_review_decisions,
    serialise_class,
    serialise_cost,
    serialise_document,
    serialise_pages,
    serialise_values,
)

__all__ = [
    "PUBLISHABLE_STATUSES",
    "build_bundle",
    "build_dataset",
    "dataset_stats",
    "jsonable",
    "overlay_review_decisions",
    "serialise_class",
    "serialise_cost",
    "serialise_document",
    "serialise_pages",
    "serialise_values",
]
