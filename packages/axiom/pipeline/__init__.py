"""The enrichment pipeline, as a callable function rather than a script.

``scripts/run_pipeline.py`` held this orchestration inline for as long as the CLI was the only
caller. It is not any more: the single-SKU enrichment endpoint runs the same ten stages over a
document that arrived from a typed part number instead of a path on disk.

The argument for extracting it is the one ``axiom.delivery.batch`` already makes for the
deterministic path — a second implementation would be the worst kind of duplicate, one that scores
differently from the one CI gates, for reasons nobody would find quickly. So the CLI and the API
share :func:`~axiom.pipeline.stages.run_stages` and differ only in where the document came from and
how the result is presented.

The split is deliberate about where the seam falls:

*   :mod:`axiom.pipeline.stages` is stages 3 to 10 — classify, extract, normalize, validate, score,
    decide, generate, certify, syndicate. It takes a parsed document and returns everything the run
    produced. It prints nothing and writes nothing.
*   :mod:`axiom.pipeline.source` is how a submission becomes a document at all: a fetched URL, or
    the typed fields themselves hashed and stored.
*   :mod:`axiom.pipeline.single` is the single-SKU orchestrator, which is the only thing here that
    knows a part number can arrive without a file.
*   :mod:`axiom.pipeline.persist` writes the artifacts the console and the review workspace read.

Argument parsing, console reporting and file-path handling stay in the script. Everything that
decides what a value is worth lives here.
"""

from __future__ import annotations

from axiom.pipeline.delivery import EnrichmentDelivery, build_delivery
from axiom.pipeline.persist import PersistedPaths, persist_run
from axiom.pipeline.progress import (
    NO_PROGRESS,
    STAGE_PLAN,
    PlannedStage,
    ProgressRegistry,
    RunProgress,
    plan_stages,
)
from axiom.pipeline.retrieval import RetrievalAttempt, retrieve_documents
from axiom.pipeline.single import (
    EnrichmentRequest,
    EnrichmentResult,
    InsufficientInputError,
    enrich_one,
)
from axiom.pipeline.source import ResolvedSource, resolve_source, submission_text
from axiom.pipeline.stages import PipelineRun, load_calibration, run_stages

__all__ = [
    "NO_PROGRESS",
    "STAGE_PLAN",
    "EnrichmentDelivery",
    "EnrichmentRequest",
    "EnrichmentResult",
    "InsufficientInputError",
    "PersistedPaths",
    "PipelineRun",
    "PlannedStage",
    "ProgressRegistry",
    "ResolvedSource",
    "RetrievalAttempt",
    "RunProgress",
    "build_delivery",
    "enrich_one",
    "load_calibration",
    "persist_run",
    "plan_stages",
    "resolve_source",
    "retrieve_documents",
    "run_stages",
    "submission_text",
]
