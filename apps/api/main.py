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
from pathlib import Path
from typing import Literal

from axiom.confidence import Priors, select_threshold
from axiom.review import ACCEPT, CORRECT, REJECT, ReviewSession, queue_summary, record_decision
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO_ROOT / "data" / "sessions"
CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
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
