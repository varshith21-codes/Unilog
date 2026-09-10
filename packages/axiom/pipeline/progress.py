"""Live progress for one enrichment run, so a caller can watch it happen.

Everything else in this package is deliberately silent: :mod:`axiom.pipeline.stages` says so in its
own docstring — *nothing here prints, and nothing here writes to disk* — which is what lets the same
function serve a CLI that reports to a terminal and an HTTP handler that returns JSON. That property
is worth keeping, and it is also why a run was previously unobservable while it was in flight. The
console's submit button flipped to "Running the pipeline…" and then said nothing for the thirty to
ninety seconds a real run takes.

This module is the seam that fixes it without breaking the silence rule. The stages do not print and
do not know where their progress goes; they call ``progress.begin(...)`` and
``progress.complete(...)`` on an object the *caller* supplied. A caller that supplies nothing gets
:data:`NO_PROGRESS`, whose methods do nothing, so the injected-dependency shape stays the same as
``client`` and ``fetcher``.

Three properties this has to have, because it is read from a different thread than the one writing
it:

1.  **Thread-safe.** ``POST /api/enrich`` runs in a FastAPI threadpool worker and
    ``GET /api/enrich/progress`` is served by another one while the first is still going. Every
    mutation and every read is under one lock, and :meth:`RunProgress.snapshot` returns plain data
    rather than a view onto live objects.
2.  **Unable to fail the run.** A progress side channel that can raise is a liability: an unknown
    stage id, a double ``begin``, a ``complete`` for something never begun. All of those are
    absorbed rather than raised. Losing a progress row is a cosmetic problem; losing a run that made
    two paid model calls is not.
3.  **Honest about what it does not know.** ``elapsed_ms`` is measured from a monotonic clock, so it
    is real. There is no percentage estimate and no ETA, because the two model stages dominate and
    their duration is not predictable — an escalation to a larger model can triple ``extract``. What
    is reported is which stage is running, how many are left, and how long the current one has
    actually been going.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

StageState = Literal["pending", "running", "done", "skipped", "failed"]
RunState = Literal["running", "complete", "failed"]


@dataclass(frozen=True)
class PlannedStage:
    """One step of the run, described before it starts.

    ``narration`` is present tense and is the point of the whole module: a stage name alone
    ("Classify") tells a viewer nothing they could not guess, and the question somebody staring at a
    spinner actually has is *what is it doing right now, and why does that step exist*.

    ``model`` travels with the stage rather than being inferred later, because it is the one fact
    that changes how a reader should treat the output: a deterministic stage produced its number by
    rule, a model stage produced it by reading. It is also what makes the cost legible while the
    money is being spent rather than afterwards.
    """

    id: str
    name: str
    narration: str
    model: bool = False


# The canonical order, matching the `# --- stage N` markers in `stages.py` and the two stages that
# happen before it (`retrieve`/`ingest`/`parse`) plus the two after (`certify`/`persist`).
#
# This is a *superset*. A run without retrieval never reaches `retrieve`, a run without
# `--generate-copy` never reaches `copy`, and `plan_stages` below drops what cannot happen so a
# viewer is not shown a step that was never going to run. Dropping is done up front rather than by
# marking rows skipped mid-run, because a checklist that shrinks while you read it is worse than one
# that was accurate to begin with.
STAGE_PLAN: tuple[PlannedStage, ...] = (
    PlannedStage(
        id="retrieve",
        name="Find the document",
        narration=(
            "Looking for the manufacturer's own document — the stored library first, then their "
            "own site. No model call."
        ),
    ),
    PlannedStage(
        id="ingest",
        name="Ingest",
        narration=(
            "Hashing the bytes and storing them under their own SHA-256, so a citation written "
            "today still resolves to these bytes later."
        ),
    ),
    PlannedStage(
        id="parse",
        name="Parse",
        narration=(
            "Reading the document into lines and addressable table cells, so a value can cite "
            "t1:r5:c1 rather than a page number."
        ),
    ),
    PlannedStage(
        id="classify",
        name="Classify",
        narration=(
            "Asking the model which product class this is. The class decides which attributes "
            "get requested at all, so it runs before extraction."
        ),
        model=True,
    ),
    PlannedStage(
        id="extract",
        name="Extract",
        narration=(
            "Reading values out of the source. Each one has to carry a verbatim quote, and a "
            "value whose quote cannot be found in the document is discarded rather than published."
        ),
        model=True,
    ),
    PlannedStage(
        id="merge",
        name="Read the other sources",
        narration=(
            "Reading the remaining retrieved documents on their own authority and merging them "
            "as candidates, so a disagreement stays visible instead of being settled by arrival "
            "order."
        ),
        model=True,
    ),
    PlannedStage(
        id="normalize",
        name="Normalize",
        narration=(
            "Parsing every value into a canonical unit, which is what makes two suppliers' "
            "figures comparable and what a cross-field rule needs before it can do arithmetic."
        ),
    ),
    PlannedStage(
        id="validate",
        name="Validate",
        narration=(
            "Running the deterministic checks, layers L0 to L3. A rule that could not run is "
            "counted as skipped rather than passed."
        ),
    ),
    PlannedStage(
        id="decide",
        name="Score and decide",
        narration=(
            "Scoring each value against the calibrated threshold. Anything below it becomes a "
            "review task rather than a publication."
        ),
    ),
    PlannedStage(
        id="export",
        name="Syndicate",
        narration=(
            "Projecting the record onto each channel's required fields. A channel missing a field "
            "it requires is held, not published with a gap."
        ),
    ),
    PlannedStage(
        id="copy",
        name="Generate copy",
        narration=(
            "Generating copy and claim-checking every sentence against the extracted facts. A "
            "fluent sentence with an unsupported number is discarded, not shipped."
        ),
        model=True,
    ),
    PlannedStage(
        id="certify",
        name="Certify",
        narration=(
            "Computing the Quality Index and signing the certificate — the artifact a buyer can "
            "audit without trusting this console."
        ),
    ),
    PlannedStage(
        id="persist",
        name="File the result",
        narration=(
            "Writing the review session, the console bundle and the delivery file, so the SKU "
            "joins the corpus and appears in Resolve and Audit."
        ),
    ),
)

_BY_ID: dict[str, PlannedStage] = {stage.id: stage for stage in STAGE_PLAN}


def plan_stages(
    *,
    retrieve: bool = True,
    generate_copy: bool = False,
    merge_sources: bool = True,
    persist: bool = True,
) -> tuple[PlannedStage, ...]:
    """The stages this particular run can reach, in order.

    Conditional stages are dropped rather than shown and later marked skipped. A viewer reading a
    twelve-row checklist that silently becomes ten has been told something false about the run;
    a ten-row checklist that was right from the start has not.

    ``merge`` is the one genuinely uncertain row and it is kept when merging is enabled, because
    whether a second document exists is only known after retrieval returns. If none does, it
    resolves to a stated "no second source" rather than vanishing — which is a finding worth
    reading, not an absence.
    """
    dropped: set[str] = set()
    if not retrieve:
        dropped.add("retrieve")
    if not generate_copy:
        dropped.add("copy")
    if not merge_sources:
        dropped.add("merge")
    if not persist:
        dropped.add("persist")
    return tuple(stage for stage in STAGE_PLAN if stage.id not in dropped)


@dataclass
class _StageProgress:
    """Mutable state for one row of the checklist."""

    stage: PlannedStage
    state: StageState = "pending"
    detail: str | None = None
    started: float | None = None
    ended: float | None = None

    def elapsed_ms(self, now: float) -> int | None:
        if self.started is None:
            return None
        end = self.ended if self.ended is not None else now
        return int((end - self.started) * 1000)

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "id": self.stage.id,
            "name": self.stage.name,
            "narration": self.stage.narration,
            "model": self.stage.model,
            "state": self.state,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms(now),
        }


class RunProgress:
    """One run's progress, written by the pipeline and read by whoever is watching.

    Not a queue and not an event log: the interesting question is "where is it now", and a watcher
    that polls every second must not have to replay a history to answer it. So this is current state
    plus per-stage timings, and :meth:`snapshot` is idempotent.
    """

    def __init__(
        self,
        *,
        sku: str,
        plan: tuple[PlannedStage, ...] = STAGE_PLAN,
        run_id: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._sku = sku
        self._stages: list[_StageProgress] = [_StageProgress(stage=stage) for stage in plan]
        self._state: RunState = "running"
        self._started = time.monotonic()
        self._started_at = datetime.now(UTC).isoformat()
        self._ended: float | None = None
        self._message: str | None = None
        self._notes: list[str] = []

    # ------------------------------------------------------------------ writing

    def begin(self, stage_id: str, detail: str | None = None) -> None:
        """Mark a stage running. Anything still running before it is closed as done.

        Auto-closing matters because the stages do not all have a natural completion callback —
        ``normalize`` is one function call inside a larger block — and a checklist with two rows
        spinning at once reports something that is not happening. The pipeline is strictly
        sequential, so "the next one started" is sound evidence the previous one finished.
        """
        with self._lock:
            now = time.monotonic()
            for entry in self._stages:
                if entry.state == "running":
                    entry.state = "done"
                    entry.ended = now
            entry = self._find(stage_id)
            if entry is None:
                return
            entry.state = "running"
            entry.started = now
            entry.ended = None
            if detail is not None:
                entry.detail = detail

    def complete(self, stage_id: str, detail: str | None = None) -> None:
        """Mark a stage done, with the one number worth reading off it."""
        with self._lock:
            entry = self._find(stage_id)
            if entry is None:
                return
            now = time.monotonic()
            if entry.started is None:
                entry.started = now
            entry.state = "done"
            entry.ended = now
            if detail is not None:
                entry.detail = detail

    def skip(self, stage_id: str, reason: str) -> None:
        """Mark a stage as not run, with why.

        A reason is required rather than optional. "Skipped" with no explanation is the single least
        useful thing this can report — the whole argument the validator makes about skipped rules is
        that a check which did not execute has established nothing, and the same applies here.
        """
        with self._lock:
            entry = self._find(stage_id)
            if entry is None:
                return
            entry.state = "skipped"
            entry.detail = reason
            entry.ended = time.monotonic()

    def detail(self, stage_id: str, detail: str) -> None:
        """Replace a stage's detail line without changing its state.

        For the long stages. ``extract`` can spend forty seconds in one model call, and "still
        extracting" is worth less than "escalated to the larger model".
        """
        with self._lock:
            entry = self._find(stage_id)
            if entry is not None:
                entry.detail = detail

    def note(self, message: str) -> None:
        """Something worth saying that is not a stage transition."""
        with self._lock:
            self._notes.append(message)

    def finish(self, message: str | None = None) -> None:
        """The run completed. Any stage still open is closed as done."""
        with self._lock:
            now = time.monotonic()
            for entry in self._stages:
                if entry.state == "running":
                    entry.state = "done"
                    entry.ended = now
                elif entry.state == "pending":
                    # Reached the end without running: the run took a path that did not need it.
                    entry.state = "skipped"
                    entry.detail = entry.detail or "not needed on this run"
            self._state = "complete"
            self._ended = now
            self._message = message

    def fail(self, message: str) -> None:
        """The run stopped. The stage that was running is the one that failed."""
        with self._lock:
            now = time.monotonic()
            for entry in self._stages:
                if entry.state == "running":
                    entry.state = "failed"
                    entry.ended = now
                    entry.detail = message
            self._state = "failed"
            self._ended = now
            self._message = message

    # ------------------------------------------------------------------ reading

    def snapshot(self) -> dict[str, Any]:
        """The whole state as plain JSON-able data.

        Plain data rather than the live objects, because the reader is on another thread and a
        response serialised from a structure still being mutated is how a watcher ends up rendering
        two stages as running at once.
        """
        with self._lock:
            now = self._ended if self._ended is not None else time.monotonic()
            stages = [entry.payload(now) for entry in self._stages]
            settled = sum(1 for entry in self._stages if entry.state in {"done", "skipped"})
            running = next(
                (entry.stage.id for entry in self._stages if entry.state == "running"), None
            )
            return {
                "run_id": self._run_id,
                "sku": self._sku,
                "state": self._state,
                "started_at": self._started_at,
                "elapsed_ms": int((now - self._started) * 1000),
                "current": running,
                "completed": settled,
                "total": len(self._stages),
                "message": self._message,
                "notes": list(self._notes),
                "stages": stages,
            }

    # ------------------------------------------------------------------ internal

    def _find(self, stage_id: str) -> _StageProgress | None:
        """The row for this id, or ``None``.

        ``None`` rather than a raise, and rather than appending an unplanned row. A stage id that is
        not in the plan is a bug in the emitting code, and the correct behaviour for a progress
        channel is to lose the row — appending would let a typo reorder the checklist, and raising
        would let it kill a paid run.
        """
        for entry in self._stages:
            if entry.stage.id == stage_id:
                return entry
        return None


class _NullProgress:
    """What a caller that did not ask for progress gets.

    A no-op object rather than ``None`` so every emit site in the pipeline is an unguarded
    ``progress.begin("classify")``. Threading ``if progress is not None`` through thirteen call
    sites would be thirteen chances to forget one, and the stage markers in ``stages.py`` are
    load-bearing enough already.
    """

    __slots__ = ()

    def begin(self, stage_id: str, detail: str | None = None) -> None:  # noqa: D102
        return None

    def complete(self, stage_id: str, detail: str | None = None) -> None:  # noqa: D102
        return None

    def skip(self, stage_id: str, reason: str) -> None:  # noqa: D102
        return None

    def detail(self, stage_id: str, detail: str) -> None:  # noqa: D102
        return None

    def note(self, message: str) -> None:  # noqa: D102
        return None

    def finish(self, message: str | None = None) -> None:  # noqa: D102
        return None

    def fail(self, message: str) -> None:  # noqa: D102
        return None


NO_PROGRESS = _NullProgress()
"""The default. Runs exactly as the pipeline did before this module existed."""


@dataclass
class ProgressRegistry:
    """The most recent run's progress, for an API with one run in flight at a time.

    ``apps.api.main`` holds a process-wide mutex on ``POST /api/enrich`` precisely so that two runs
    cannot both be spending money, which means a single slot is not a simplification here — it is
    the same invariant stated twice. A run id is still issued, so a watcher that started polling
    during the previous run can tell that what it is now reading is a different run rather than
    silently reading the wrong one's stages.

    The finished run is kept rather than cleared. The console polls on an interval, so the last poll
    almost always lands after the response has already come back; clearing on completion would make
    the final state a 404 and the last thing a viewer saw would be the second-to-last stage.
    """

    _current: RunProgress | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self, *, sku: str, plan: tuple[PlannedStage, ...] = STAGE_PLAN) -> RunProgress:
        progress = RunProgress(sku=sku, plan=plan)
        with self._lock:
            self._current = progress
        return progress

    def current(self) -> RunProgress | None:
        with self._lock:
            return self._current

    def snapshot(self) -> dict[str, Any] | None:
        progress = self.current()
        return progress.snapshot() if progress is not None else None


__all__ = [
    "NO_PROGRESS",
    "STAGE_PLAN",
    "PlannedStage",
    "ProgressRegistry",
    "RunProgress",
    "RunState",
    "StageState",
    "plan_stages",
]
