"""Review workspace API.

Serves review sessions produced by the pipeline and records human decisions against them.

Deliberately small. The interesting engineering is in the packages this exposes; an API that
grows its own logic ends up duplicating the pipeline badly. Every endpoint here either reads a
session from disk or applies a decision through ``axiom.review``.

Run it:

    $env:AWS_PROFILE = "axiom"
    python -m uvicorn apps.api.main:app --reload --port 8000

Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Annotated, Literal

from axiom.confidence import Priors, select_threshold
from axiom.console import (
    backfill_provenance,
    build_dataset,
    dataset_stats,
    normalise_quality_index,
    overlay_cross_source,
    overlay_equivalence,
    overlay_review_decisions,
)
from axiom.core.naming import is_sku_slug, sku_slug
from axiom.delivery import (
    WORKBOOK_MEDIA_TYPE,
    DeliveryFormatExporter,
    DeliveryWorkbookExporter,
)
from axiom.delivery import load_default as load_delivery_format
from axiom.delivery.batch import (
    BatchOptions,
    DocumentSource,
    InputColumnsError,
    parse_documents,
    run_batch,
    select_rows,
    validate_input_columns,
)
from axiom.delivery.source import INPUT_COLUMNS
from axiom.extract import BedrockModelClient, ModelCascade
from axiom.extract.client import ModelError
from axiom.ingest import (
    IngestError,
    LocalArtifactStore,
    UrlFetchError,
    check_url,
    profile_rows,
    read_flat_file,
    sha256_bytes,
)
from axiom.pipeline import (
    EnrichmentRequest,
    InsufficientInputError,
    build_delivery,
    enrich_one,
    persist_run,
)
from axiom.pipeline.persist import bundle_payload
from axiom.pipeline.source import SUBMISSION_MAX_BYTES
from axiom.review import ACCEPT, CORRECT, REJECT, ReviewSession, queue_summary, record_decision
from axiom.schema import load_default as load_schema
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO_ROOT / "data" / "sessions"
CONSOLE_DIR = REPO_ROOT / "data" / "console"
CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
CROSS_SOURCE_DIR = REPO_ROOT / "data" / "cross-source"
EQUIVALENCE_DIR = REPO_ROOT / "data" / "equivalence"
ARTIFACT_DIR = REPO_ROOT / "data" / "cache" / "artifacts"
ENRICH_DIR = REPO_ROOT / "data" / "enrich"
LIBRARY_INDEX = REPO_ROOT / "data" / "library" / "index.json"
"""The document library retrieval reads and writes.

Shared with ``scripts/retrieve_sources.py`` deliberately: a document fetched by a batch run is one
the form never has to fetch again, and vice versa. That reuse is the whole economic argument for the
library — retrieval is a one-time cost per document, not per part."""
COHORT_PATH = REPO_ROOT / "evals" / "cohort.json"

_SHA256 = re.compile(r"[0-9a-f]{64}")

# Botocore's own failure family, which `ModelError` does **not** cover.
#
# `BedrockModelClient.converse` wraps `ClientError` — a call that reached AWS and was
# refused. It does not wrap `BotoCoreError`, which is the family a call that never got
# that far raises: `NoCredentialsError`, `ProfileNotFound`, `EndpointConnectionError`.
# Those are exactly the errors an operator hits on first run, and unwrapped they reach a
# browser as a 500 traceback, which reads as a bug in the console rather than a missing
# `AWS_PROFILE`.
#
# An empty tuple when botocore is absent, because `except ()` never matches — which is the correct
# behaviour for an installation that cannot make the call at all.
try:  # pragma: no cover - exercised by whether the extra is installed, not by a test
    from botocore.exceptions import BotoCoreError as _BotoCoreError

    _CREDENTIAL_ERRORS: tuple[type[BaseException], ...] = (_BotoCoreError,)
except ImportError:  # pragma: no cover
    _CREDENTIAL_ERRORS = ()

# ---------------------------------------------------------------------- enrichment limits
#
# The upload endpoint below is bounded because it spends CPU on caller-supplied bytes. This one is
# bounded for a harder reason: **it spends money.** Every submission is two Bedrock calls,
# three with copy generation, on an endpoint with no authentication. The caps do not close
# that gap — nothing short of auth does — they bound what one caller can spend before
# somebody notices.
MAX_DESCRIPTION_CHARS = 4_000
"""Long enough for a real ERP description several times over. The client's own sample descriptions
run to about 40 characters, and the description is sent to the model verbatim, so this is the field
that decides the input token bill."""

MAX_URL_CHARS = 2_048
"""The ceiling browsers and proxies agree on. A longer URL is a mistake, not a datasheet."""

ENRICH_TIMEOUT = 20.0
ENRICH_MAX_BYTES = SUBMISSION_MAX_BYTES
"""Re-exported from the pipeline so ``/api/enrich/limits`` reports the number actually enforced
rather than a copy of it that can drift."""

# One run in flight per process.
#
# Not a queue and not a rate limit — a mutex. Concurrent runs would multiply the spend by however
# many requests arrive, and the second caller's honest answer is "wait" rather than a slower run and
# a doubled bill. A real deployment needs a work queue and auth; this is what bounds a laptop.
_ENRICH_LOCK = threading.Lock()

# ---------------------------------------------------------------------- upload limits
#
# Every other endpoint here reads a file off disk. This one accepts bytes from a caller and does
# real work on them — parse, classify, extract, project 252 columns — so it is the only place in
# this API where an unbounded request costs CPU and memory rather than a directory listing. The
# caps exist because there is no authentication in front of them (see the note on the endpoint),
# and an unauthenticated unbounded compute endpoint is a denial-of-service primitive.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
"""25 MiB. The client's own 1,000-row sample is 96 KB, so this is three orders of magnitude of
headroom and still bounded."""

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_DOCUMENTS = 10
MAX_ROWS = 5_000
"""Rows processed per request. Beyond this the answer is the CLI, not an HTTP request that will
outlive its own timeout."""

# Suffixes `read_flat_file` can route. An allowlist, on the same principle as `_MEDIA_TYPES`: the
# reader dispatches on the suffix, and handing it something it cannot route produces a confusing
# parse error rather than a clear rejection.
UPLOAD_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}

OUTPUT_FORMATS = ("xlsx", "csv", "json")

# Header values must be latin-1 encodable, so these carry numbers and hex only. They exist because
# the response body is a file: a caller that wants the run summary without parsing the workbook has
# nowhere else to read it from.
_SUMMARY_HEADERS = (
    "X-Axiom-Rows",
    "X-Axiom-Rows-Skipped",
    "X-Axiom-Columns-Populated",
    "X-Axiom-Columns-Total",
    "X-Axiom-Withheld",
    "X-Axiom-Compliant",
    "X-Axiom-Content-Hash",
)

# Suffixes the artifact endpoint will serve, and the type it declares for each. An allowlist rather
# than a lookup: the store holds whatever suppliers sent, and guessing a content type for an
# unexpected suffix is how a browser ends up executing something.
_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    # Deliberately *not* text/html. These are third-party bytes served inline, so declaring them
    # HTML would let a supplier page run script on this API's origin — and `nosniff` cannot help
    # when the declared type is itself the dangerous one. Nothing needs it rendered: the console
    # draws the *parsed* projection of a page, and the only reason to hand back the original file is
    # so a human can read it. text/plain does that and executes nothing.
    ".html": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
CONSOLE = Path(__file__).resolve().parent / "static" / "index.html"
DELIVERY_CONSOLE = Path(__file__).resolve().parent / "static" / "delivery.html"

app = FastAPI(
    title="AXIOM Review Workspace",
    description="Evidence-beside-value review for extracted product data",
    version="0.1.0",
)


class DecisionRequest(BaseModel):
    action: Literal["accept", "reject", "correct"]
    reviewer: str = Field(default="reviewer@local")
    corrected_value: str | None = None


class EnrichRequest(BaseModel):
    """One product to enrich, as somebody would type it.

    Two required fields and two optional ones, and the interesting rule is not expressible as
    "required": **at least one of ``description`` and ``source_url`` must be present.** A part
    number identifies a product but does not describe one, so with neither field the run would
    classify nothing, extract nothing, and return an identity-only row after paying for two
    model calls. That
    is checked in :class:`~axiom.pipeline.EnrichmentRequest` rather than here, so the CLI and the
    endpoint refuse the same submissions for the same stated reason.

    Lengths are capped because the description is sent to a model verbatim and this endpoint has no
    authentication in front of it. See the note on the endpoint.
    """

    mpn: str = Field(min_length=1, max_length=128, description="manufacturer part number")
    manufacturer: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    source_url: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    retrieve: bool = Field(
        default=True,
        description=(
            "look for the manufacturer's own document when no URL is supplied: the document "
            "library first, then the manufacturer's site read through its own search form. No "
            "model call. Off makes the run fully offline and then requires a description or a URL."
        ),
    )
    brand: str | None = Field(default=None, max_length=256)
    class_code: str | None = Field(
        default=None,
        description="force this class instead of classifying, and the fallback when it abstains",
    )
    include_optional: bool = Field(
        default=False, description="also request the class's optional attributes"
    )
    generate_copy: bool = Field(
        default=False,
        description=(
            "also generate marketing copy and claim-check it. One extra model call; copy that "
            "fails the check is reported, not published."
        ),
    )
    risk_budget: float = Field(
        default=0.05,
        gt=0.0,
        lt=1.0,
        description="max acceptable error rate on auto-published values. Tighter publishes less.",
    )
    replace: bool = Field(
        default=False,
        description=(
            "overwrite an existing run for this part number. Without it an existing SKU is a 409, "
            "because a re-run replaces the session a reviewer may have already worked."
        ),
    )
    supplier_id: str | None = Field(default=None, max_length=128)


def _session_path(sku: str) -> Path:
    """The session file for a SKU **slug**, which is what this endpoint's path segment carries.

    The SKU arrives from a URL and is used to build a filesystem path, which is exactly the shape
    of a traversal bug, so the value is whitelist-validated rather than pattern-rejected.

    It is the slug and not the raw part number because industrial part numbers contain separators
    — ``52C3-5/8-UPC`` is a fractional size, not an attack — and while the filename *was* the SKU
    there was nothing this endpoint could do with one but refuse it. See ``axiom.core.naming``.
    A part number needing no escaping is its own slug, so every existing caller is unaffected.
    """
    if not is_sku_slug(sku):
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid sku '{sku}': expected a SKU slug, i.e. letters, digits, '-', '_', '.' "
                f"and '~' escapes. A part number containing a separator is addressed by its "
                f"slug, e.g. '52C3-5/8-UPC' as '52C3-5~2F8-UPC'."
            ),
        )
    return SESSION_DIR / f"{sku}.json"


def _load(sku: str) -> ReviewSession:
    path = _session_path(sku)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no review session for '{sku}'. Generate one with: "
                f"python scripts/run_pipeline.py <source> --sku {sku} --save-session"
            ),
        )
    return ReviewSession.load(path)


def _priors() -> Priors:
    path = CALIBRATION_DIR / "priors.json"
    return Priors.load(path) if path.exists() else Priors()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "sessions": len(list(SESSION_DIR.glob("*.json"))) if SESSION_DIR.exists() else 0,
        "bundles": len(list(CONSOLE_DIR.glob("*.bundle.json"))) if CONSOLE_DIR.exists() else 0,
        "calibrated": (CALIBRATION_DIR / "calibration_set.json").exists(),
    }


@app.get("/api/sessions")
def list_sessions() -> dict:
    """Every session on disk, with just enough detail to render a picker."""
    if not SESSION_DIR.exists():
        return {"sessions": []}

    summaries = []
    for path in sorted(SESSION_DIR.glob("*.json")):
        try:
            session = ReviewSession.load(path)
        except Exception:  # noqa: BLE001 - a malformed file must not break the list
            continue
        summaries.append(
            {
                "sku": session.sku,
                "brand": session.brand,
                "class_name": session.class_name,
                "document_id": session.document_id,
                **queue_summary(session),
            }
        )
    return {"sessions": summaries}


@app.get("/api/session/{sku}")
def get_session(sku: str) -> dict:
    session = _load(sku)
    return {**session.to_dict(), "summary": queue_summary(session)}


@app.post("/api/session/{sku}/decision/{attribute_code}")
def post_decision(sku: str, attribute_code: str, request: DecisionRequest) -> dict:
    """Record a decision, then report what it changed about future automation.

    The response deliberately includes the prior movement and the new risk policy. Showing a
    reviewer that their correction shifted the threshold is what makes the flywheel visible
    rather than theoretical.
    """
    session = _load(sku)
    priors = _priors()

    action = {"accept": ACCEPT, "reject": REJECT, "correct": CORRECT}[request.action]
    try:
        outcome = record_decision(
            session,
            attribute_code,
            action,
            reviewer=request.reviewer,
            priors=priors,
            supplier_id=None,
            corrected_value=request.corrected_value,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    priors.save(CALIBRATION_DIR / "priors.json")
    session.save(_session_path(sku))

    return {
        "outcome": outcome.to_dict(),
        "summary": queue_summary(session),
        "item": next(
            (i for i in session.to_dict()["items"] if i["attribute_code"] == attribute_code),
            None,
        ),
    }


@app.get("/api/console/dataset")
def console_dataset() -> dict:
    """Everything the console dashboards render, assembled from persisted pipeline runs.

    Reads only. The bundles on disk were produced by ``run_pipeline.py --save-session``, which
    is where the model calls happened; re-running extraction here would make a page load cost
    money and return a different answer each time.

    The policy reported is the one the bundles were **actually decided under**, not a freshly
    computed one. Serving a different threshold than the one that produced these accept/queue
    decisions would make every score badge in the UI disagree with its own explanation.
    ``/api/policy`` is the separate what-if dial.
    """
    if not CONSOLE_DIR.exists():
        return _empty_dataset("data/console/ does not exist")

    paths = sorted(CONSOLE_DIR.glob("*.bundle.json"))
    if not paths:
        return _empty_dataset("no console bundles on disk")

    bundles: list[dict] = []
    documents: dict[str, dict] = {}
    class_definitions: dict[str, dict] = {}
    policies: list[dict] = []
    calibrators: set[str] = set()
    unreadable: list[str] = []

    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            bundle = payload["bundle"]
        except (OSError, ValueError, KeyError):
            # One corrupt file must not blank the whole console.
            unreadable.append(path.name)
            continue

        # A certificate written before richness was observable recorded a defaulted 0.0 and no
        # composite. Reinterpreted on read so the console renders one shape; the signed bytes on
        # disk are left alone, because the signature is the point of having them.
        bundle = normalise_quality_index(bundle)
        # Likewise for the provenance split and the source list. A bundle written before the item
        # master stopped counting as evidence has no self-declared values by construction, so the
        # backfill is a derivation rather than a default. Without it the console reads `undefined`
        # for a required metric and renders NaN.
        bundle = backfill_provenance(bundle)

        # A bundle records what the pipeline produced; the session records what a reviewer
        # decided since. Joining them here means a decision shows up on the dashboards without
        # the pipeline's own output being rewritten underneath it.
        # Slugged, because the bundle carries the true part number and the filename cannot. A SKU
        # needing no escaping is its own slug, so this resolves every artifact already on disk.
        slug = sku_slug(bundle["sku"])
        session_path = SESSION_DIR / f"{slug}.json"
        if session_path.is_file():
            try:
                bundle = overlay_review_decisions(bundle, ReviewSession.load(session_path))
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(session_path.name)

        # L4's findings arrive the same way, and for the same reason: the bundle is what a
        # single-source run produced, and a multi-source analysis is a later, separate reading of
        # the same SKU. Written by scripts/cross_validate.py --save.
        cross_path = CROSS_SOURCE_DIR / f"{slug}.json"
        if cross_path.is_file():
            try:
                bundle = overlay_cross_source(
                    bundle, json.loads(cross_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(cross_path.name)

        # The cross-reference joins the same way, and for the same reason: a bundle records what a
        # single-SKU run produced, and a comparison against the rest of the catalogue is a later
        # reading of it. Written by scripts/cross_reference.py --write.
        equivalence_path = EQUIVALENCE_DIR / f"{slug}.json"
        if equivalence_path.is_file():
            try:
                bundle = overlay_equivalence(
                    bundle, json.loads(equivalence_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(equivalence_path.name)

        bundles.append(bundle)
        documents.setdefault(
            bundle["document_id"],
            {"document": payload["document"], "pages": payload["pages"]},
        )
        definition = payload.get("class_definition")
        if definition and bundle.get("class_code"):
            class_definitions.setdefault(bundle["class_code"], definition)
        if payload.get("policy"):
            policies.append(payload["policy"])
        calibrators.add(payload.get("calibrator", "unknown"))

    if not bundles:
        return _empty_dataset(f"every bundle failed to parse: {', '.join(unreadable)}")

    # Bundles produced at different risk budgets cannot share one threshold badge. Rather than
    # silently showing whichever was read last, say so.
    thresholds = {p.get("threshold") for p in policies}
    warnings = []
    if len(thresholds) > 1:
        warnings.append(
            f"bundles were produced under {len(thresholds)} different acceptance thresholds "
            f"({sorted(t for t in thresholds if t is not None)}); re-run the pipeline with a "
            f"single --risk-budget so the dashboard reports one policy"
        )
    if unreadable:
        warnings.append(f"skipped unreadable bundles: {', '.join(unreadable)}")

    return build_dataset(
        sorted(bundles, key=lambda b: b["sku"]),
        documents=documents,
        class_definitions=class_definitions,
        policy=policies[0] if policies else {},
        meta={
            "generator": "apps/api",
            "live": True,
            "calibrator": ", ".join(sorted(calibrators)),
            "policy_source": "data/calibration (backtest)",
            "source_bundles": [p.name for p in paths],
            "warnings": warnings,
            "notes": (
                "Produced by real model calls through the Bedrock cascade and scored against "
                "the calibration set written by scripts/run_backtest.py."
            ),
        },
    )


def _empty_dataset(reason: str) -> dict:
    """A valid but empty dataset, so the console renders an explanation rather than crashing."""
    return build_dataset(
        [],
        documents={},
        class_definitions={},
        policy={},
        meta={
            "generator": "apps/api",
            "live": True,
            "calibrator": "none",
            "policy_source": "none",
            "source_bundles": [],
            "warnings": [reason],
            "notes": (
                "No pipeline output is available. Generate some with: python "
                "scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075 "
                "--include-optional --save-session"
            ),
        },
    )


@app.get("/api/console/stats")
def console_stats() -> dict:
    """Counts only — cheap enough for a nav badge or a poll."""
    return dataset_stats(console_dataset())


@app.get("/api/artifact/{sha256}")
def artifact(sha256: str) -> FileResponse:
    """Serve a stored source document by its own hash, so the evidence viewer can render it.

    **Addressed by hash, never by path.** The store is content-addressed, so the SHA-256 *is* the
    key — and a 64-hex-character validation is inherently traversal-proof in a way that sanitising a
    caller-supplied path is not. There is no input here that could name a file outside the store,
    because the only accepted input cannot contain a separator or a dot.

    The suffix is discovered by globbing rather than taken from the caller, for the same reason.

    **This endpoint serves raw supplier documents and has no authentication**, like the rest of this
    API. That is documented as a known gap, and it matters more here than elsewhere: the other
    endpoints return derived projections, while this one returns bytes a supplier gave you under
    licence terms. Anything beyond a laptop needs auth and a licence check in front of it.
    """
    if not _SHA256.fullmatch(sha256):
        raise HTTPException(
            status_code=400,
            detail="artifact id must be a 64-character lowercase hex SHA-256",
        )

    directory = ARTIFACT_DIR / sha256[:2] / sha256[2:4]
    matches = sorted(directory.glob(f"{sha256}.*")) if directory.is_dir() else []
    # A `.partial` file is a write that crashed mid-flight; serving one would hand out truncated
    # bytes under a hash that claims to describe complete content.
    matches = [path for path in matches if path.suffix != ".partial"]

    if not matches:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no stored artifact for {sha256[:12]}…. The bytes live in "
                f"data/cache/artifacts/, which is gitignored, so a fresh clone has to re-ingest "
                f"the source before its citations can be rendered."
            ),
        )

    target = matches[0]
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    if media_type is None:
        # Refuse rather than guess. An unexpected suffix in the store is not something to hand to a
        # browser with a content type invented for it.
        raise HTTPException(
            status_code=415, detail=f"stored artifact type {target.suffix!r} is not servable"
        )

    return FileResponse(
        target,
        media_type=media_type,
        # `inline` so a PDF renders in the evidence viewer instead of downloading. `nosniff` because
        # these are third-party bytes and the browser must not reinterpret their type.
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            # Immutable is safe to the letter here: the URL contains the content hash, so the bytes
            # at this address can never change.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@app.get("/api/cohort")
def cohort() -> dict:
    """The before/after quality cohort, as written by ``scripts/run_cohort.py --write``.

    Read from disk rather than recomputed, for the same reason the dataset is: the study joins an
    item master against persisted pipeline output, and recomputing it per request would make a
    page load depend on files that may be mid-rewrite.

    ``available: false`` rather than a 404 when no study exists. A missing cohort is a normal state
    — nobody has run it yet — and the console needs to explain that rather than render an error.
    """
    if not COHORT_PATH.is_file():
        return {
            "available": False,
            "reason": (
                "no cohort study on disk. Build the before state with "
                "scripts/ingest_supplier_file.py --out data/ingest, then run "
                "scripts/run_cohort.py --write"
            ),
        }
    try:
        payload = json.loads(COHORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"cohort study could not be read: {exc}"}

    if not isinstance(payload, dict):
        return {"available": False, "reason": "cohort study is not a JSON object"}
    return {"available": True, **payload}


@app.get("/api/policy")
def policy(epsilon: float = 0.05) -> dict:
    """The risk dial. Recomputes the threshold and coverage for a given error budget."""
    path = CALIBRATION_DIR / "calibration_set.json"
    if not path.exists():
        return {
            "achievable": False,
            "reason": "no calibration data; run scripts/run_backtest.py --write first",
            "epsilon": epsilon,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = select_threshold(payload["scores"], payload["labels"], epsilon=epsilon)
    return {
        **selected.summary(),
        "curve": [
            {
                "threshold": point.threshold,
                "coverage": point.coverage,
                "error_upper_bound": point.error_upper_bound,
            }
            for point in selected.curve
        ],
    }


async def _read_upload(upload: UploadFile, cap: int, *, label: str) -> bytes:
    """Read an upload into memory, refusing anything over ``cap``.

    Read in chunks and checked as it goes, rather than reading it all and measuring afterwards.
    ``UploadFile`` spools to a temp file past a threshold, so trusting a declared size or reading
    first and asking later would let a caller decide how much memory and disk this process uses.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{label} exceeds the {cap // (1024 * 1024)} MiB limit. "
                    f"For a file this size use scripts/export_delivery.py, which streams to disk "
                    f"and has no request timeout."
                ),
            )
        chunks.append(chunk)
    await upload.close()

    if total == 0:
        raise HTTPException(status_code=400, detail=f"{label} is empty")
    return b"".join(chunks)


async def _read_documents(uploads: list[UploadFile] | None):
    """Read and parse the optional manufacturer documents attached to an export.

    Parsing failures are fatal rather than skipped. A document that was attached and silently
    dropped would produce a thinner file with no explanation, and the caller would reasonably read
    the missing values as an extraction failure instead of a parse one.
    """
    # An empty multipart field arrives as one UploadFile with no filename, which is not a document.
    present = [u for u in (uploads or []) if u.filename]
    if not present:
        return []
    if len(present) > MAX_DOCUMENTS:
        raise HTTPException(
            status_code=413,
            detail=f"at most {MAX_DOCUMENTS} documents per request; got {len(present)}",
        )

    sources = []
    for upload in present:
        data = await _read_upload(
            upload, MAX_DOCUMENT_BYTES, label=f"document {upload.filename!r}"
        )
        sources.append(DocumentSource(data=data, name=upload.filename or "document"))

    try:
        return parse_documents(sources)
    except Exception as exc:  # noqa: BLE001 - the parser raises per-format errors
        raise HTTPException(
            status_code=400, detail=f"could not parse an attached document: {exc}"
        ) from exc


def _safe_stem(filename: str) -> str:
    """A filename stem safe to put in a header and on a filesystem.

    The upload's name is caller-controlled and ends up in ``Content-Disposition``, so anything that
    could terminate a header value or traverse a path is removed rather than escaped.
    """
    stem = Path(filename).stem.strip() or "delivery"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (cleaned or "delivery")[:96]


def _headers_for(export, fmt) -> dict[str, str]:
    """The run summary, as response headers.

    The body is a file, so a caller wanting the numbers has nowhere else to read them. Values are
    digits and hex only: header values must be latin-1 encodable and nothing here should ever be
    able to carry supplier text into a header.
    """
    return {
        "X-Axiom-Rows": str(export.row_count),
        "X-Axiom-Columns-Populated": str(len(export.populated_by_column)),
        "X-Axiom-Columns-Total": str(len(fmt)),
        "X-Axiom-Withheld": str(export.withheld_count),
        "X-Axiom-Compliant": "true" if export.compliant else "false",
        "X-Axiom-Content-Hash": export.content_hash,
    }


def _file_response(
    body: bytes, *, filename: str, media_type: str, summary: dict[str, str]
) -> Response:
    """A download, with the summary headers attached.

    ``filename*`` as well as ``filename`` so a non-ASCII name survives, though `_safe_stem` has
    already reduced it to ASCII; the pair is what browsers agree on.
    """
    quoted = urllib.parse.quote(filename)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quoted}'
            ),
            "X-Content-Type-Options": "nosniff",
            # Named explicitly so the headers survive a cross-origin fetch. Nothing needs that
            # today — the console proxies through its own server — but a header a caller cannot
            # read is a header that looks broken.
            "Access-Control-Expose-Headers": ", ".join(_SUMMARY_HEADERS),
            **summary,
        },
    )


def _run_summary(
    result,
    fmt,
    *,
    filename: str,
    documents,
    rows_in_file: int,
    selected: list[dict],
    sheet_name: str | None,
) -> dict:
    """What the run did, without the file.

    Exists so a UI can show the outcome before offering a download, and so the numbers it shows
    come from the same export the file would be built from rather than a second count of its own.
    """
    exporter = DeliveryFormatExporter(fmt)
    export = exporter.to_csv(result.rows)
    profiles = profile_rows(list(INPUT_COLUMNS), selected)

    populated = sorted(export.populated_by_column.items(), key=lambda kv: -kv[1])
    return {
        "source": {
            "filename": filename,
            "sheet": sheet_name,
            "rows_in_file": rows_in_file,
            "rows_selected": len(selected),
            "input_profile": [profiles[column].summary() for column in INPUT_COLUMNS],
            "dead_columns": [c for c in INPUT_COLUMNS if not profiles[c].carries_data],
        },
        "documents": [
            {
                "document_id": parsed.document.document_id,
                "pages": parsed.page_count,
                "tables": len(parsed.all_tables()),
                "parser": parsed.parser,
                "doc_type": parsed.document.doc_type.value
                if hasattr(parsed.document.doc_type, "value")
                else str(parsed.document.doc_type),
            }
            for parsed in documents
        ],
        "batch": result.summary(),
        "export": export.summary(),
        "columns_populated": [{"column": name, "rows": count} for name, count in populated],
        "rows": [outcome.summary() for outcome in result.outcomes],
        "preview": [
            {
                name: value
                for name, value in row.as_dict().items()
                if value and name in export.populated_by_column
            }
            for row in result.rows[:25]
        ],
        "notes": [
            "Blank cells are deliberate: a column is left empty when nothing in the input or the "
            "attached documents evidenced a value for it.",
            "No model calls were made. Classification is deterministic retrieval and abstains "
            "rather than guessing; extraction reads the description string and any attached "
            "documents by layout.",
        ]
        + (
            [
                "No manufacturer documents were attached, so the attribute grid carries its "
                "labels and few values. Attach datasheets to close that gap."
            ]
            if not documents
            else []
        ),
    }


@app.get("/api/delivery/format")
def delivery_format() -> dict:
    """The output contract, so a caller can see what an upload will produce before uploading.

    Served from the same ``schema/delivery/*.yaml`` the exporter builds against, rather than a
    hand-copied list. A UI that showed a stale header would be describing a file we do not produce.
    """
    fmt = load_delivery_format()
    return {
        "name": fmt.name,
        "version": fmt.version,
        "format": f"{fmt.name}@{fmt.version}",
        "columns": len(fmt),
        "join_key": fmt.join_key,
        "description": fmt.description,
        "populated_in_ground_truth": fmt.populated_in_ground_truth,
        "header": list(fmt.header),
        "sections": [
            {
                "group": section.group,
                "provenance": section.provenance.value,
                "columns": len(section.columns),
                "note": section.note,
            }
            for section in fmt.sections
        ],
        "unavailable_columns": list(fmt.unavailable_columns()),
        "input_columns": list(INPUT_COLUMNS),
        "upload": {
            "accepts": sorted(UPLOAD_SUFFIXES),
            "outputs": list(OUTPUT_FORMATS),
            "max_bytes": MAX_UPLOAD_BYTES,
            "max_rows": MAX_ROWS,
            "max_documents": MAX_DOCUMENTS,
            "max_document_bytes": MAX_DOCUMENT_BYTES,
        },
    }


@app.post("/api/delivery/export")
async def delivery_export(
    file: Annotated[UploadFile, File(description="supplier item master: CSV, TSV or XLSX")],
    documents: Annotated[
        list[UploadFile] | None,
        File(description="optional manufacturer documents to extract from"),
    ] = None,
    output: Annotated[str, Form(description="xlsx, csv or json")] = "xlsx",
    limit: Annotated[int | None, Form(description="process only the first N rows")] = None,
    mpns: Annotated[str, Form(description="comma-separated part numbers to process")] = "",
    class_code: Annotated[
        str | None, Form(description="force this class on every row")
    ] = None,
    classified_only: Annotated[
        bool, Form(description="drop rows retrieval could not classify")
    ] = False,
    read_descriptions: Annotated[
        bool, Form(description="read attributes out of Part_Desc")
    ] = True,
    include_audit: Annotated[
        bool, Form(description="include the provenance sheets in the workbook")
    ] = True,
) -> Response:
    """Upload an item master, get the delivery file back.

    The same projection ``scripts/export_delivery.py`` runs, over the same
    :func:`axiom.delivery.batch.run_batch`, so an upload and a CLI run of the same file produce the
    same cells. That shared path is the point: a second implementation here would be one CI does not
    gate, and it would drift.

    **Runs entirely offline and makes no model calls**, so it needs no credentials. Classification
    is the deterministic retrieval step and abstains rather than guessing; extraction reads the
    description string and any attached documents by layout. The honest consequence, stated because
    a caller seeing 28 of 252 columns filled will otherwise assume a bug: with no documents
    attached there is nothing to extract from, so the attribute grid emits its labels and few
    values. Attach datasheets to close that gap.

    ``output=xlsx`` (default) returns a workbook whose ``Delivery`` sheet is the 252-column
    contract and whose other sheets carry the provenance record. XLSX rather than CSV by default
    because Excel reinterprets a CSV on import — ``0123`` loses its zero and ``50-1/4`` becomes a
    date — and a part number that survives the pipeline and dies in the client's spreadsheet is
    still a failed delivery. ``output=csv`` returns exactly what the CLI writes. ``output=json``
    returns the summary and a preview without a file, for a UI that wants to show the result before
    offering the download.

    **No authentication**, like every other endpoint here, and that is a sharper gap on this one
    than on the read-only routes: it accepts bytes and spends CPU on them. The caps above are the
    only thing bounding that. Anything beyond a laptop needs auth in front of it.
    """
    if output not in OUTPUT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"output must be one of {', '.join(OUTPUT_FORMATS)}; got {output!r}",
        )

    filename = file.filename or "upload.csv"
    suffix = Path(filename).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"cannot read {suffix or 'a file with no extension'!r}; "
                f"expected one of {', '.join(sorted(UPLOAD_SUFFIXES))}"
            ),
        )

    payload = await _read_upload(file, MAX_UPLOAD_BYTES, label="item master")

    try:
        flat = read_flat_file(payload, filename=filename)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=f"could not read {filename}: {exc}") from exc

    if not flat.rows:
        raise HTTPException(status_code=400, detail=f"{filename} has a header but no data rows")

    try:
        validate_input_columns(flat.headers)
    except InputColumnsError as exc:
        # 422 rather than 400: the request was well-formed and the file was readable, it is the
        # wrong file. The lists travel structured so a UI can name the missing header instead of
        # asking someone to read a paragraph.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_input_columns",
                "message": "this does not look like a Unilog item master",
                "missing": list(exc.missing),
                "found": list(exc.found),
                "expected": list(INPUT_COLUMNS),
            },
        ) from exc

    fmt = load_delivery_format()
    registry = load_schema()
    if class_code and class_code not in registry.class_codes:
        raise HTTPException(
            status_code=400,
            detail=f"unknown class {class_code!r}; known: {', '.join(registry.class_codes)}",
        )

    requested = [m for m in (mpns or "").split(",") if m.strip()]
    effective_limit = min(limit, MAX_ROWS) if limit and limit > 0 else MAX_ROWS
    selected = select_rows(flat.rows, mpns=requested, limit=effective_limit)
    if not selected:
        raise HTTPException(
            status_code=422,
            detail=(
                f"no rows selected. {filename} has {len(flat.rows)} row(s) and the part-number "
                f"filter matched none of them."
                if requested
                else f"no rows selected from {filename}"
            ),
        )

    parsed_documents = await _read_documents(documents)

    # The item master is itself the document description-derived values are cited against, so it is
    # content-hashed like any other arrival. The id is the uploaded name pinned to its bytes: two
    # uploads of a corrected file are two different documents, and a citation has to say which.
    document_sha256 = sha256_bytes(payload)
    stem = _safe_stem(filename)

    result = run_batch(
        selected,
        fmt=fmt,
        registry=registry,
        document_id=f"{stem}@{document_sha256[:8]}",
        document_sha256=document_sha256,
        options=BatchOptions(
            class_code=class_code,
            classified_only=classified_only,
            read_descriptions=read_descriptions,
        ),
        documents=parsed_documents,
    )

    if not result.rows:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "every_row_skipped",
                "message": (
                    "nothing to write: every selected row was skipped. Rows without a part "
                    "number cannot be identified, and with classified_only set, rows that "
                    "retrieval could not classify are dropped."
                ),
                "rows_selected": len(selected),
                "skipped": result.skipped,
                "classification": dict(result.methods),
            },
        )

    if output == "json":
        return JSONResponse(
            _run_summary(
                result,
                fmt,
                filename=filename,
                documents=parsed_documents,
                rows_in_file=len(flat.rows),
                selected=selected,
                sheet_name=flat.sheet_name,
            )
        )

    if output == "csv":
        exporter = DeliveryFormatExporter(fmt)
        export = exporter.to_csv(result.rows)
        return _file_response(
            export.csv_text.encode("utf-8"),
            filename=f"{stem}.delivery.csv",
            media_type="text/csv; charset=utf-8",
            summary=_headers_for(export, fmt),
        )

    workbook = DeliveryWorkbookExporter(fmt).to_workbook(
        result.rows, source_name=filename, include_audit=include_audit
    )
    return _file_response(
        workbook.data,
        filename=f"{stem}.delivery.xlsx",
        media_type=WORKBOOK_MEDIA_TYPE,
        summary=_headers_for(workbook.export, fmt),
    )


# ------------------------------------------------------------------ single-SKU enrichment


def credential_profiles() -> dict[str, bool]:
    """Every named AWS profile, and whether it actually resolves credentials.

    Reported in the 503 detail, so a credentials failure diagnoses itself instead of sending
    somebody back to the documentation. The distinction it exposes is the one that caused real
    confusion here: a profile can be present in ``~/.aws/config`` — giving it a region, and making
    it look configured — while having no entry in ``~/.aws/credentials`` at all.
    """
    import boto3

    found: dict[str, bool] = {}
    for name in boto3.Session().available_profiles:
        try:
            found[name] = boto3.Session(profile_name=name).get_credentials() is not None
        except Exception:  # noqa: BLE001 - a malformed profile is "unusable", not fatal
            found[name] = False
    return found


def resolve_profile() -> str | None:
    """Which AWS profile to run as, without depending on the shell that launched the server.

    This exists because the obvious design — pass ``None`` and let boto3's default chain decide —
    fails in a way that is genuinely hard to read. With no ``AWS_PROFILE`` set, boto3 selects the
    profile named ``default``; if that profile exists in ``~/.aws/config`` but has no entry in
    ``~/.aws/credentials``, the result is ``NoCredentialsError`` on a machine where working
    credentials are sitting right there under another name. The error says "Unable to locate
    credentials", which reads as *none are configured* rather than *the wrong one was selected*.

    So the order is:

    1.  ``AXIOM_AWS_PROFILE``, then ``AWS_PROFILE``. An explicit instruction always wins, including
        when it is wrong — surfacing a bad profile name beats silently substituting a good one.
    2.  The ambient chain, if it resolves. This is the path that must keep working on EC2, ECS and
        Lambda, where there is no profile at all and credentials come from an instance role.
    3.  Failing both, the single named profile that *does* resolve credentials. Chosen only when
        there is exactly one candidate, because picking between two would be guessing at which
        account to bill.

    Step 3 is the one worth defending. It is not "try things until something works": it fires only
    when the ambient chain has already failed, so the alternative is not a different credential — it
    is a 503. And it is announced on stderr rather than applied quietly, because which account is
    being billed is not something to infer from a working screen.
    """
    import os

    explicit = os.environ.get("AXIOM_AWS_PROFILE") or os.environ.get("AWS_PROFILE")
    if explicit:
        return explicit

    import boto3

    try:
        if boto3.Session().get_credentials() is not None:
            return None
    except Exception:  # noqa: BLE001 - fall through to the named-profile search
        pass

    usable = [name for name, ok in credential_profiles().items() if ok]
    if len(usable) == 1:
        print(
            f"[axiom] no ambient AWS credentials; using the only profile that resolves any: "
            f"{usable[0]!r}. Set AXIOM_AWS_PROFILE to choose explicitly.",
            file=sys.stderr,
        )
        return usable[0]
    return None


def model_client(profile: str | None = None):
    """The Bedrock client an enrichment run will use.

    A module-level function rather than an inline constructor so a test can replace it with
    :class:`~axiom.extract.StubModelClient`. That is the same reason
    :func:`~axiom.pipeline.enrich_one` takes its client as an argument: the alternative is an
    endpoint whose only test needs credentials, which is an endpoint with no tests.
    """
    return BedrockModelClient(
        region=ModelCascade.load().region,
        profile=profile if profile is not None else resolve_profile(),
    )


def retrieval_fetcher():
    """The fetcher retrieval uses, or None for the real one.

    A seam, for the same reason :func:`model_client` is one — but with a sharper edge. Retrieval
    makes outbound HTTP requests to third-party websites, so a test that reached the default would
    not merely be slow and flaky: it would send traffic to a manufacturer that did not ask for it,
    from whatever machine ran the suite. Every test therefore replaces this, and the replacement is
    what keeps the retrieval path exercised offline instead of skipped.

    None rather than :func:`axiom.ingest.web.fetch_url` so the ingest layer keeps choosing its own
    default, including the resolver guard.
    """
    return None


def search_provider():
    """The open-web search arm. **On by default.**

    This used to return None on the grounds that search is an external service with a key and a
    bill, so defaulting it on would make the endpoint's reach depend on ambient credentials. That
    reasoning was right about a keyed provider and wrong about the outcome: it left the arm that
    reaches an *undeclared* manufacturer switched off, which is the arm that matters most — a
    manufacturer with a declared domain is already served by site discovery.

    :class:`~axiom.retrieve.DuckDuckGoSearch` needs no key, no account and no bill, so there is no
    ambient credential to depend on and nothing to leave unconfigured. Measured on this repository's
    own item master it returns the manufacturer's own product page as the first result for long-tail
    industrial part numbers.

    Set ``AXIOM_SEARCH=off`` to disable it — worth doing for a run that must make no third-party
    request at all. ``AXIOM_SEARCH=bedrock`` selects the Bedrock Web Search arm instead, which keeps
    the query inside the AWS boundary but needs OpenAI GPT model access on the account.
    """
    import os

    choice = (os.environ.get("AXIOM_SEARCH") or "duckduckgo").strip().lower()
    if choice in {"off", "none", "0", "false"}:
        return None
    if choice == "bedrock":
        from axiom.retrieve.search_bedrock import BedrockWebSearch, BedrockWebSearchError

        try:
            return BedrockWebSearch.from_env()
        except BedrockWebSearchError as exc:
            # Degraded rather than fatal: the run still has the library and site discovery, and a
            # missing search key should not take the endpoint down.
            print(
                f"[axiom] AXIOM_SEARCH=bedrock but that provider is unavailable: {exc}",
                file=sys.stderr,
            )
            return None

    from axiom.retrieve import DuckDuckGoSearch

    return DuckDuckGoSearch()


def _enrich_delivery_path(slug: str, output: str) -> Path:
    suffix = {"csv": ".delivery.csv", "xlsx": ".delivery.xlsx", "provenance": ".provenance.json"}
    return ENRICH_DIR / f"{slug}{suffix[output]}"


@app.get("/api/enrich/limits")
def enrich_limits() -> dict:
    """What a submission may contain, so the form can describe the real caps.

    Served rather than hard-coded in the console for the same reason ``/api/delivery/format`` is: a
    UI that advertised a limit this API does not enforce would be describing a different service.
    """
    return {
        "required": ["mpn", "manufacturer"],
        "optional": ["description", "source_url", "brand", "class_code"],
        "one_of": ["description", "source_url"],
        "max_description_chars": MAX_DESCRIPTION_CHARS,
        "max_url_chars": MAX_URL_CHARS,
        "max_document_bytes": ENRICH_MAX_BYTES,
        "fetch_timeout_seconds": ENRICH_TIMEOUT,
        "url_schemes": ["https"],
        "concurrent_runs": 1,
        "outputs": ["csv", "xlsx"],
        "model_calls_per_run": {"without_copy": 2, "with_copy": 3},
        "notes": [
            "Runs the online pipeline: classification and extraction are real Bedrock calls, so "
            "this endpoint needs credentials and costs money per submission.",
            "A description or a manufacturer URL is required. A part number identifies a product "
            "but does not describe one, so with neither field there is nothing to classify and "
            "nothing to extract from.",
            "With no URL the submission itself becomes the source document, hashed and citable. "
            "That is a real provenance claim and a weaker one than a datasheet.",
        ],
    }


@app.post("/api/enrich")
def enrich(request: EnrichRequest) -> JSONResponse:
    """Enrich one product from a typed part number. **Makes real model calls.**

    The same ten stages ``scripts/run_pipeline.py`` runs, through the same
    :func:`axiom.pipeline.run_stages`, so a submission here and a CLI run over the same document
    produce the same record. That shared path is the point: a second implementation would be one CI
    does not gate, and it would drift.

    What arrives is a part number, a manufacturer, and at least one of a description or an
    ``https://`` URL. With a URL the fetched document is the source and the description is *also*
    read, deterministically, with the document's values superseding it — a datasheet states a fact
    where a description only implies it. With no URL the submission itself is hashed and stored
    as the source, and every citation resolves to a field somebody typed. Both are provenance;
    they are not equal, and the response labels which one it was.

    The run is persisted, so the SKU joins the corpus: it appears in the Resolve queue, the Audit
    list and the dashboards, and its delivery file is written now rather than on download.

    **Security posture, stated rather than assumed.** Two things are materially worse here than on
    the read-only routes, and neither is closed by anything in this function:

    *   **No authentication**, like every endpoint in this API, on a route that *spends money*. The
        length caps and the single-run mutex bound the damage; they do not prevent it. Anything
        beyond an operator's laptop needs auth in front of this before anything else.
    *   **Server-side request forgery.** This fetches a caller-supplied URL from inside your
        network. The ingest layer refuses non-https schemes, loopback and private literals,
        resolves the hostname and re-checks every answer, and re-checks again at each
        redirect hop — but DNS is
        re-resolved when the socket is opened, so a short-TTL record can still win that race. This
        needs an egress policy, not just a library check. See
        :func:`axiom.ingest.web._check_resolved`.
    """
    # 429 rather than queueing. Two concurrent runs cost twice as much and the honest answer to the
    # second caller is "one at a time", not a slower run and a doubled bill.
    if not _ENRICH_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=(
                "an enrichment run is already in flight. This endpoint runs one at a time because "
                "each run costs real model calls; retry when it finishes."
            ),
        )
    try:
        return _run_enrichment(request)
    finally:
        _ENRICH_LOCK.release()


def _run_enrichment(request: EnrichRequest) -> JSONResponse:
    registry = load_schema()

    if request.class_code and request.class_code not in registry.class_codes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown class {request.class_code!r}; known: {', '.join(registry.class_codes)}"
            ),
        )

    url = (request.source_url or "").strip()
    if url and not url.lower().startswith("https://"):
        # Refused here as well as in the ingest layer, so the message names the field rather than
        # explaining a trade-off the form never offered. Plain http would hash bytes that arrived
        # with no integrity guarantee and call the result provenance.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insecure_url",
                "message": (
                    "the manufacturer URL must start with https://. Bytes fetched over plain http "
                    "carry no integrity guarantee, and hashing them as provenance would defeat the "
                    "point of the citation."
                ),
                "field": "source_url",
            },
        )

    if url:
        # The literal address guard, hoisted to run **before** the already-enriched check below.
        #
        # The ingest layer performs this too, and more thoroughly — it also resolves the
        # hostname. But that happens inside the run, which is after the conflict check, so a
        # caller pointing an already-enriched part number at `169.254.169.254` was told about
        # the conflict and never about the address. A security refusal must not be masked by a
        # bookkeeping one, so the free half of the check runs first.
        #
        # Only the literal half. Resolving here would put a DNS lookup in front of every submission
        # and would report an unresolvable host as a validation error, when the honest answer for
        # that is the 502 fetch path further down.
        try:
            check_url(url, allow_insecure_http=False)
        except UrlFetchError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_url",
                    "message": str(exc),
                    "field": "source_url",
                },
            ) from exc

    try:
        enrichment_request = EnrichmentRequest(
            mpn=request.mpn,
            manufacturer=request.manufacturer,
            description=request.description,
            source_url=url or None,
            brand=request.brand,
            class_code=request.class_code,
            supplier_id=request.supplier_id,
            include_optional=request.include_optional,
            generate_copy=request.generate_copy,
            risk_budget=request.risk_budget,
            retrieve=request.retrieve,
        )
    except InsufficientInputError as exc:
        # 422 rather than 400: the request was well-formed, it just cannot produce anything worth
        # paying for. The missing fields travel structured so a form can highlight them instead of
        # asking somebody to read a paragraph.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_input",
                "message": str(exc),
                "missing": list(exc.missing),
            },
        ) from exc

    slug = sku_slug(enrichment_request.clean_mpn)
    existing = SESSION_DIR / f"{slug}.json"
    if existing.is_file() and not request.replace:
        # A re-run replaces the session a reviewer may already have worked, and the decisions on it.
        # `run_pipeline` refuses a combination that would blank a saved session for the same reason:
        # by the time a warning is read the data is already gone.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_enriched",
                "message": (
                    f"{enrichment_request.clean_mpn} has already been enriched. Re-running "
                    f"replaces the saved session and any review decisions recorded against "
                    f"it. Send "
                    f"replace=true to do that deliberately."
                ),
                "sku": enrichment_request.clean_mpn,
                "slug": slug,
                "enriched_at": _session_timestamp(existing),
            },
        )

    try:
        client = model_client()
    except Exception as exc:  # noqa: BLE001 - botocore raises several unrelated types here
        raise HTTPException(status_code=503, detail=_credentials_detail(exc)) from exc

    try:
        result = enrich_one(
            enrichment_request,
            registry=registry,
            client=client,
            store=LocalArtifactStore(ARTIFACT_DIR),
            calibration_dir=CALIBRATION_DIR,
            # Retrieval on, which is what makes a part number plus a manufacturer name sufficient.
            # It makes HTTP requests to the manufacturer's own site, policy-gated and robots-aware,
            # and no model call. `search` is left None: the open-web arm needs a key and a bill, so
            # it is opt-in rather than ambient.
            library_path=LIBRARY_INDEX,
            fetcher=retrieval_fetcher(),
            search=search_provider(),
        )
    except (UrlFetchError, IngestError) as exc:
        # 502 rather than 400. The request was fine; the *upstream* document could not be retrieved,
        # and a caller staring at a link that works in their browser needs the fetch error verbatim.
        raise HTTPException(
            status_code=502,
            detail={
                "error": "source_unreachable",
                "message": str(exc),
                "field": "source_url",
                "url": url,
            },
        ) from exc
    except (ModelError, *_CREDENTIAL_ERRORS) as exc:
        # Both families, because they fail at different depths and a caller cannot tell them
        # apart. `ModelError` is a call AWS refused — throttling, or a model not enabled in
        # the region. `BotoCoreError` is a call that never left the process, which on a first
        # run is almost always a missing `AWS_PROFILE`. Unwrapped, either one reaches a
        # browser as a traceback and reads as a bug in the console rather than a
        # configuration problem.
        raise HTTPException(status_code=503, detail=_credentials_detail(exc)) from exc

    paths = persist_run(
        result.run,
        parsed=result.source.parsed,
        artifact=result.source.artifact,
        registry=registry,
        sessions_dir=SESSION_DIR,
        console_dir=CONSOLE_DIR,
    )

    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=load_delivery_format(),
        out_dir=ENRICH_DIR,
        mpn=enrichment_request.clean_mpn,
        manufacturer=request.manufacturer,
        description=request.description,
        brand=request.brand,
        source_url=url or None,
    )

    # The bundle the console already knows how to render. `pages` is deliberately dropped: the
    # stage cards read the document summary, and a multi-page datasheet's line geometry would
    # be most of the response for something nothing on this screen draws. The evidence viewer
    # reads it from /api/session/{slug}, which is now on disk.
    payload = bundle_payload(
        result.run,
        parsed=result.source.parsed,
        artifact=result.source.artifact,
        registry=registry,
    )

    return JSONResponse(
        {
            "sku": result.sku,
            "slug": paths.slug,
            "replaced": existing.is_file() and request.replace,
            "summary": result.summary(),
            "queue": result.queue(registry),
            "bundle": backfill_provenance(normalise_quality_index(payload["bundle"])),
            "document": payload["document"],
            "policy": payload["policy"],
            "calibrator": payload["calibrator"],
            "delivery": delivery.summary(),
            "persisted": {
                **paths.relative_to(REPO_ROOT),
                "delivery_csv": _display_path(delivery.csv_path),
                "delivery_xlsx": _display_path(delivery.xlsx_path),
                "provenance": _display_path(delivery.provenance_path),
            },
            "links": {
                "review": f"/api/session/{paths.slug}",
                "delivery_csv": f"/api/enrich/{paths.slug}/delivery?output=csv",
                "delivery_xlsx": f"/api/enrich/{paths.slug}/delivery?output=xlsx",
                "source_artifact": f"/api/artifact/{result.source.artifact.sha256}",
            },
        }
    )


def _display_path(path: Path) -> str:
    """A path relative to the repository, or absolute when it is not under it.

    The fallback is not defensive padding: the directory constants are module-level and a
    test points them at a temporary directory, so ``relative_to`` genuinely raises. Reporting
    the absolute path is more useful there than a 500 anyway.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _session_timestamp(path: Path) -> str | None:
    """When the existing run happened, read from the session rather than the filesystem.

    A file's mtime says when the bytes were last written, which is not the same claim — a decision
    recorded against the session rewrites it without a new run having happened.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("created_at")
    except (OSError, ValueError, AttributeError):
        return None


def _credentials_detail(exc: Exception) -> dict:
    """A model failure, explained as the thing it almost always is.

    ``ModelError`` reaching a browser as a traceback reads as a bug in the console. It is usually
    absent or expired credentials, or a model that is not enabled in the region, and both are fixed
    by the operator rather than by retrying.

    The profile inventory is included because the failure that actually happened here was not
    "no credentials configured" but "the wrong profile was selected" — ``default`` present in
    ``~/.aws/config`` with no entry in ``~/.aws/credentials``, while a working ``axiom`` profile sat
    beside it. A message that only said "Unable to locate credentials" sent somebody looking for a
    missing key that was never missing. Naming every profile and whether it resolves makes the next
    occurrence self-diagnosing.
    """
    try:
        profiles = credential_profiles()
        selected = resolve_profile()
    except Exception:  # noqa: BLE001 - never let diagnostics replace the original failure
        profiles, selected = {}, None

    usable = sorted(name for name, ok in profiles.items() if ok)
    unusable = sorted(name for name, ok in profiles.items() if not ok)

    hint = (
        "Set AXIOM_AWS_PROFILE to one of the profiles that resolve credentials, in the shell that "
        "starts the API — boto3 reads it when the session is built, so exporting it afterwards in "
        "another terminal has no effect."
    )
    if not usable and profiles:
        hint = (
            f"None of the configured profiles ({', '.join(unusable)}) resolve credentials. A "
            f"profile can appear in ~/.aws/config — which gives it a region and makes it look "
            f"configured — while having no entry in ~/.aws/credentials."
        )
    elif not profiles:
        hint = "No AWS profiles are configured. Run 'aws configure --profile axiom'."

    return {
        "error": "model_unavailable",
        "message": (
            "the model call could not be made. This endpoint runs the online pipeline, so it needs "
            "AWS credentials with Bedrock access and the cascade's models enabled in the "
            f"configured region. {hint} For a run that needs no credentials, use the Publish page, "
            "which is entirely deterministic."
        ),
        "detail": str(exc),
        "region": ModelCascade.load().region,
        "profile_used": selected,
        "profiles_with_credentials": usable,
        "profiles_without_credentials": unusable,
    }


@app.get("/api/enrich/{sku}/delivery")
def enrich_delivery(sku: str, output: str = "csv") -> Response:
    """Serve the delivery file a run already wrote.

    Reads bytes off disk. It does **not** re-run anything, which is the whole reason the files are
    written at run time: a download that re-ran the pipeline would spend two more model calls to
    produce a file we already had, and could hand back something different from what the screen
    reported.

    The path segment is a SKU **slug**, validated against the same whitelist ``/api/session/{sku}``
    uses. That is not decoration: the value arrives from a URL and is used to build a filesystem
    path, which is exactly the shape of a traversal bug.
    """
    if output not in {"csv", "xlsx"}:
        raise HTTPException(
            status_code=400, detail=f"output must be csv or xlsx; got {output!r}"
        )
    if not is_sku_slug(sku):
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid sku '{sku}': expected a SKU slug, i.e. letters, digits, '-', '_', '.' "
                f"and '~' escapes. A part number containing a separator is addressed by its slug, "
                f"e.g. '52C3-5/8-UPC' as '52C3-5~2F8-UPC'."
            ),
        )

    target = _enrich_delivery_path(sku, output)
    if not target.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no enrichment delivery file for '{sku}'. Files are written when a submission is "
                f"enriched through POST /api/enrich; a SKU produced by the CLI or the Publish "
                f"upload has its output elsewhere."
            ),
        )

    body = target.read_bytes()
    media_type = (
        WORKBOOK_MEDIA_TYPE if output == "xlsx" else "text/csv; charset=utf-8"
    )
    return _file_response(
        body,
        filename=target.name,
        media_type=media_type,
        # Read off the sidecar and the file itself, so these agree with the bytes being served
        # rather than with a second count of their own.
        summary=_enrich_headers(sku),
    )


def _enrich_headers(slug: str) -> dict[str, str]:
    """The run summary for a persisted delivery file, from its provenance sidecar.

    Digits and hex only, like every other header here: header values must be latin-1 encodable and
    nothing should be able to carry supplier text into one.

    The content hash is computed from the CSV on disk rather than read from the sidecar, and that is
    the stronger claim: it describes the bytes actually being served, not what a run recorded about
    bytes it wrote earlier. It matches ``DeliveryExport.content_hash`` by construction, because that
    is the SHA-256 of the same UTF-8 text.
    """
    counts: dict[str, str] = {}

    sidecar = _enrich_delivery_path(slug, "provenance")
    if sidecar.is_file():
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))["records"][0]
            counts = {
                "X-Axiom-Rows": "1",
                "X-Axiom-Columns-Populated": str(record.get("populated", 0)),
                "X-Axiom-Columns-Total": str(record.get("of_columns", 0)),
                "X-Axiom-Withheld": str(len(record.get("withheld", []))),
            }
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            counts = {}

    csv_path = _enrich_delivery_path(slug, "csv")
    if csv_path.is_file():
        counts["X-Axiom-Content-Hash"] = sha256_bytes(csv_path.read_bytes())
    return counts


@app.get("/")
def console() -> FileResponse:
    if not CONSOLE.is_file():
        raise HTTPException(status_code=404, detail="console not found")
    return FileResponse(CONSOLE)


@app.get("/delivery")
def delivery_console() -> FileResponse:
    """The upload page.

    Its own route rather than a panel in the review workspace: that layout is a three-pane
    keyboard-driven tool for one SKU, and this is a linear task over a whole file.
    """
    if not DELIVERY_CONSOLE.is_file():
        raise HTTPException(status_code=404, detail="delivery console not found")
    return FileResponse(DELIVERY_CONSOLE)
