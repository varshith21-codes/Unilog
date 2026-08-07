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
from pathlib import Path
from typing import Literal

from axiom.confidence import Priors, select_threshold
from axiom.console import (
    build_dataset,
    dataset_stats,
    normalise_quality_index,
    overlay_cross_source,
    overlay_review_decisions,
)
from axiom.review import ACCEPT, CORRECT, REJECT, ReviewSession, queue_summary, record_decision
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO_ROOT / "data" / "sessions"
CONSOLE_DIR = REPO_ROOT / "data" / "console"
CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
CROSS_SOURCE_DIR = REPO_ROOT / "data" / "cross-source"
ARTIFACT_DIR = REPO_ROOT / "data" / "cache" / "artifacts"
COHORT_PATH = REPO_ROOT / "evals" / "cohort.json"

_SHA256 = re.compile(r"[0-9a-f]{64}")

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

app = FastAPI(
    title="AXIOM Review Workspace",
    description="Evidence-beside-value review for extracted product data",
    version="0.1.0",
)


class DecisionRequest(BaseModel):
    action: Literal["accept", "reject", "correct"]
    reviewer: str = Field(default="reviewer@local")
    corrected_value: str | None = None


def _session_path(sku: str) -> Path:
    # Reject anything that could escape the session directory. The SKU arrives from a URL and
    # is used to build a filesystem path, which is exactly the shape of a traversal bug.
    if not sku or "/" in sku or "\\" in sku or ".." in sku:
        raise HTTPException(status_code=400, detail="invalid sku")
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

        # A bundle records what the pipeline produced; the session records what a reviewer
        # decided since. Joining them here means a decision shows up on the dashboards without
        # the pipeline's own output being rewritten underneath it.
        session_path = SESSION_DIR / f"{bundle['sku']}.json"
        if session_path.is_file():
            try:
                bundle = overlay_review_decisions(bundle, ReviewSession.load(session_path))
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(session_path.name)

        # L4's findings arrive the same way, and for the same reason: the bundle is what a
        # single-source run produced, and a multi-source analysis is a later, separate reading of
        # the same SKU. Written by scripts/cross_validate.py --save.
        cross_path = CROSS_SOURCE_DIR / f"{bundle['sku']}.json"
        if cross_path.is_file():
            try:
                bundle = overlay_cross_source(
                    bundle, json.loads(cross_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, KeyError, TypeError):
                unreadable.append(cross_path.name)

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


@app.get("/")
def console() -> FileResponse:
    if not CONSOLE.is_file():
        raise HTTPException(status_code=404, detail="console not found")
    return FileResponse(CONSOLE)
