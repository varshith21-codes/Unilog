"""Re-run enrichment over SKUs already in the catalogue, and report what changed.

The corpus in ``data/console/`` was mostly written by ``export_console_catalogue.py``, which is the
deterministic offline path: it reads the client's six-column item master and produces a structurally
complete row per part with no model call and no manufacturer document. That is honest and thin —
most of those bundles hold values whose only provenance is ``Part_Desc``, and the gap list asks for
``retry_with_better_source`` on nearly two thousand attributes.

This script goes back over them with the *online* pipeline. Same ``enrich_one`` the console's run
screen calls, so a SKU refreshed here and a SKU refreshed there produce the same record — a second
implementation would be one CI does not gate, and it would drift.

    # what would run, against which rows, and what each already holds. No requests, no model calls.
    python scripts/reenrich_corpus.py --dry-run

    # the thin ones first: SKUs that never got a class or never got a document
    python scripts/reenrich_corpus.py --only-unclassified --limit 20 --yes

    # named parts
    python scripts/reenrich_corpus.py --mpn 8896700140 --mpn PDSH4816AF --yes

    # the whole corpus, resumable, with a JSON report
    python scripts/reenrich_corpus.py --yes --report data/reenrich-report.json --sleep 1.0

**This spends real money, per SKU.** Two Bedrock calls each, three with ``--generate-copy``, times
however many rows are selected. That is why ``--yes`` is required for anything but a dry run and why
the default selection is *nothing* rather than everything: a script that re-runs a thousand paid
enrichments because somebody pressed up-arrow and enter is a bill, not a tool.

**It also replaces what is on disk.** Each successful run overwrites
``data/console/<slug>.bundle.json`` and rewrites ``data/sessions/<slug>.json`` — which discards any
review decision recorded against the old session, exactly as ``POST /api/enrich`` with
``replace=true`` does. ``--skip-reviewed`` is the way to leave worked sessions alone.

Why the item master is read at all, when the corpus is what drives the selection: a bundle records
what a run *produced*, and for the offline corpus that does not include the manufacturer name.
``Part_Manuf`` lives in the client's input file, and the manufacturer is what makes retrieval
possible — it is the difference between searching ``mirka.com`` and searching the open web. So the
bundles decide *which* SKUs run and the input file supplies *what* is submitted for each one.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from axiom.core.naming import sku_slug  # noqa: E402
from axiom.delivery.source import BRAND_COLUMNS  # noqa: E402
from axiom.ingest import IngestError, LocalArtifactStore, UrlFetchError  # noqa: E402
from axiom.pipeline import (  # noqa: E402
    EnrichmentRequest,
    InsufficientInputError,
    RunProgress,
    build_delivery,
    enrich_one,
    persist_run,
    plan_stages,
)
from axiom.pipeline.persist import source_summaries  # noqa: E402

DEFAULT_INPUT = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"
CONSOLE_DIR = REPO_ROOT / "data" / "console"
SESSION_DIR = REPO_ROOT / "data" / "sessions"

# Placeholder values the item master uses where a column has nothing in it. Treated as absent rather
# than as a brand, which is the same reading `axiom.ingest.placeholders` applies: `-- Unbranded --`
# appears in 799 of 1,000 rows and is not a brand name.
_SENTINEL_MARKERS = ("--", "no brand", "unbranded", "n/a", "none")


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    # A value with no letter or digit in it is punctuation, not a name. The item master uses a bare
    # `-` in `Part_Manuf` for rows with no manufacturer, and submitting that as one would have
    # retrieval searching for a hyphen.
    if not any(character.isalnum() for character in text):
        return None
    folded = text.casefold()
    if any(marker in folded for marker in _SENTINEL_MARKERS):
        return None
    return text


@dataclass
class Existing:
    """What the bundle on disk already claims, so the re-run can be compared against it.

    Read *before* the run, because the run overwrites the file. Without this the script could report
    what it produced but not whether that was an improvement, which is the only question worth
    asking of a re-run that cost money.
    """

    slug: str
    class_code: str | None = None
    values: int = 0
    publishable: int = 0
    gaps: int = 0
    verifiability: float = 0.0
    has_document: bool = False
    composite: float | None = None
    reviewed: bool = False

    @classmethod
    def read(cls, slug: str) -> Existing | None:
        path = CONSOLE_DIR / f"{slug}.bundle.json"
        if not path.is_file():
            return None
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))["bundle"]
        except (OSError, ValueError, KeyError):
            # A corrupt bundle is still a SKU worth re-running; it just cannot be compared against.
            return cls(slug=slug)

        metrics = bundle.get("metrics") or {}
        sources = bundle.get("sources") or []
        quality = ((bundle.get("certificate") or {}).get("summary") or {}).get(
            "quality_index"
        ) or {}
        return cls(
            slug=slug,
            class_code=bundle.get("class_code"),
            values=int(metrics.get("values_total") or 0),
            publishable=int(metrics.get("values_publishable") or 0),
            gaps=int(metrics.get("gaps_total") or 0),
            verifiability=float(metrics.get("verifiability") or 0.0),
            # `item_master` is the client's own row. It is evidence and it is never a manufacturer
            # document, so a bundle carrying only that tier has never had a real source read for
            # it — which is precisely the population this script exists to fix.
            has_document=any(
                (source.get("tier") or "") not in {"", "item_master"} for source in sources
            ),
            composite=quality.get("composite"),
            reviewed=_has_review_decisions(slug),
        )


def _has_review_decisions(slug: str) -> bool:
    """Whether a human has recorded anything against this SKU's session.

    Checked so `--skip-reviewed` can protect work a re-run would silently discard. Read defensively:
    an unreadable session is reported as *reviewed*, because the safe failure mode for a question
    guarding somebody's work is "leave it alone".
    """
    path = SESSION_DIR / f"{slug}.json"
    if not path.is_file():
        return False
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    # `decisions` is the append-only log `axiom.review.record_decision` writes to. A session with an
    # empty list has been generated but never worked, which is the state every bundle in the offline
    # corpus is in — so this is a real filter, not a theoretical one.
    decisions = session.get("decisions")
    if not isinstance(decisions, list):
        return True
    return len(decisions) > 0


@dataclass
class Candidate:
    """One SKU already in the corpus, with the submission to re-run it from."""

    mpn: str
    manufacturer: str | None
    description: str | None
    brand: str | None
    existing: Existing
    origin: str
    """Where the submission fields came from: the item master, the last delivery row, or nowhere.

    Reported because it changes what the re-run can do. Without a manufacturer name retrieval has no
    domain to search and falls back to the open web, which is a materially weaker run — so a SKU in
    that state is worth seeing in the plan rather than discovering in the results.
    """


@dataclass
class Outcome:
    """What one re-run did, in the shape the summary table and the JSON report both read."""

    mpn: str
    slug: str
    status: str
    before: Existing
    values: int = 0
    publishable: int = 0
    gaps: int = 0
    verifiability: float = 0.0
    class_code: str | None = None
    composite: float | None = None
    from_document: bool = False
    source_uri: str | None = None
    calls: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "mpn": self.mpn,
            "slug": self.slug,
            "status": self.status,
            "before": {
                "class_code": self.before.class_code,
                "values": self.before.values,
                "publishable": self.before.publishable,
                "gaps": self.before.gaps,
                "verifiability": round(self.before.verifiability, 4),
                "had_manufacturer_document": self.before.has_document,
                "composite": self.before.composite,
            },
            "after": {
                "class_code": self.class_code,
                "values": self.values,
                "publishable": self.publishable,
                "gaps": self.gaps,
                "verifiability": round(self.verifiability, 4),
                "from_manufacturer_document": self.from_document,
                "source_uri": self.source_uri,
                "composite": self.composite,
            },
            "cost": {"calls": self.calls, "usd": self.cost_usd, "seconds": round(self.seconds, 2)},
            "error": self.error,
            "notes": self.notes,
        }


# ------------------------------------------------------------------ selection


def _read_master(source: Path) -> dict[str, dict[str, str]]:
    """The item master, indexed by part number, first row per part wins.

    First rather than last because the file repeats part numbers and the duplicates observed are
    identical apart from ordering; picking a side deterministically matters more than which side.
    """
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        mpn = _clean(row.get("Mfg_Part_Num"))
        if mpn is not None and mpn not in indexed:
            indexed[mpn] = row
    return indexed


def _read_delivery_row(slug: str) -> dict[str, str] | None:
    """The echo columns off this SKU's last delivery file.

    The recovery path for a SKU that is in the corpus but not in the item master — a part somebody
    enriched by hand through the console or the CLI. The delivery row exists precisely because those
    six columns let the client join our output back to their input, and it is written at run time,
    so it is a faithful record of what the last run was submitted with. Same column names as the
    item master by construction (see ``axiom.pipeline.delivery``), so one reader handles both.
    """
    path = REPO_ROOT / "data" / "enrich" / f"{slug}.delivery.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, ValueError):
        return None
    return rows[0] if rows else None


def _submission_from(row: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    """``Part_Manuf``, ``Part_Desc`` and the first populated brand column, cleaned.

    Brand precedence follows ``BRAND_COLUMNS`` in ``axiom.delivery.source`` rather than inventing a
    second ordering here — the leftmost column in the file is not the most trustworthy one, which is
    the whole reason that constant exists.
    """
    brand = next(
        (_clean(row.get(column)) for column in BRAND_COLUMNS if _clean(row.get(column))),
        None,
    )
    return _clean(row.get("Part_Manuf")), _clean(row.get("Part_Desc")), brand


def read_candidates(
    source: Path,
    *,
    mpns: list[str],
    only_unclassified: bool,
    only_undocumented: bool,
    only_identified: bool,
    skip_reviewed: bool,
    limit: int | None,
) -> tuple[list[Candidate], dict[str, int]]:
    """Every SKU in ``data/console/`` that can be re-submitted, filtered and truncated.

    Driven by the **corpus** rather than by the input file, which is the difference between "re-run
    the client's sample" and "re-run everything we hold". Seven bundles in this repo were produced
    by hand and are not in the item master; iterating the CSV would silently omit them, and "all
    existing SKUs" that quietly means "999 of 1,006" is a gap nobody notices until it matters.

    The part number comes from the bundle's own ``sku`` field, not from the filename. A slug is not
    a part number — ``52C3-5~2F8-UPC.bundle.json`` holds ``52C3-5/8-UPC`` — and submitting the
    filename would enrich a part that does not exist.

    Filter order mirrors ``axiom.delivery.batch.select_rows``: names, then state, then ``--limit``.
    So ``--mpn X --limit 1`` means "the first of the ones I named", not "X if it sorts first".
    """
    master = _read_master(source) if source.is_file() else {}
    wanted = {m.strip() for m in mpns if m.strip()}

    counts = {
        "bundles": 0,
        "unreadable": 0,
        "already_classified": 0,
        "already_documented": 0,
        "already_identified": 0,
        "reviewed": 0,
        "no_manufacturer": 0,
        "from_master": 0,
        "from_delivery": 0,
        "bundle_only": 0,
    }
    candidates: list[Candidate] = []

    for path in sorted(CONSOLE_DIR.glob("*.bundle.json")):
        counts["bundles"] += 1
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))["bundle"]
            mpn = str(bundle["sku"]).strip()
        except (OSError, ValueError, KeyError):
            # One corrupt bundle must not stop a sweep, and it cannot be re-run either: without the
            # part number there is nothing to submit.
            counts["unreadable"] += 1
            continue

        if not mpn or (wanted and mpn not in wanted):
            continue

        slug = sku_slug(mpn)
        existing = Existing.read(slug)
        if existing is None:
            counts["unreadable"] += 1
            continue

        if only_unclassified and existing.class_code is not None:
            counts["already_classified"] += 1
            continue
        if only_undocumented and existing.has_document:
            counts["already_documented"] += 1
            continue
        if skip_reviewed and existing.reviewed:
            counts["reviewed"] += 1
            continue

        # The client's own input first, because it is the authoritative statement of what this part
        # is and who makes it. The last delivery row second, because it is what a previous run was
        # actually submitted with. Nothing third — a runnable state, just a weaker one.
        row = master.get(mpn)
        origin = "item master"
        if row is None:
            row = _read_delivery_row(slug)
            origin = "last delivery row"
        if row is None:
            row, origin = {}, "bundle only"

        manufacturer, description, brand = _submission_from(row)
        if manufacturer is None and only_identified:
            counts["already_identified"] += 1
            continue
        # Counted after the filter, not before, so the figure describes what was *selected*.
        # Counting first made `--only-identified` report the same 41 rows as both skipped and
        # included, which is the kind of summary that makes somebody distrust the whole table.
        if manufacturer is None:
            counts["no_manufacturer"] += 1

        counts[
            {"item master": "from_master", "last delivery row": "from_delivery"}.get(
                origin, "bundle_only"
            )
        ] += 1

        candidates.append(
            Candidate(
                mpn=mpn,
                manufacturer=manufacturer,
                description=description,
                brand=brand,
                existing=existing,
                origin=origin,
            )
        )

    if limit is not None:
        candidates = candidates[:limit]
    return candidates, counts


# ------------------------------------------------------------------ one run


def reenrich(
    candidate: Candidate,
    *,
    main,
    registry,
    client,
    store: LocalArtifactStore,
    include_optional: bool,
    generate_copy: bool,
    refresh_sources: bool,
    quiet: bool,
) -> Outcome:
    """Re-run one SKU and persist it, overwriting what was there.

    Every failure is returned rather than raised. A sweep of a thousand parts will hit an
    unreachable datasheet and a throttled model call, and stopping the whole run on the first one
    would mean the money already spent bought nothing.
    """
    started = time.monotonic()
    outcome = Outcome(
        mpn=candidate.mpn,
        slug=candidate.existing.slug,
        status="failed",
        before=candidate.existing,
    )

    try:
        request = EnrichmentRequest(
            mpn=candidate.mpn,
            manufacturer=candidate.manufacturer,
            description=candidate.description,
            brand=candidate.brand,
            retrieve=True,
            # The whole point of a re-run: look past whatever is cached for this part and see
            # whether the manufacturer has published something better since. No model call; it
            # costs requests.
            refresh_sources=refresh_sources,
            include_optional=include_optional,
            generate_copy=generate_copy,
        )
    except InsufficientInputError as exc:
        outcome.status = "refused"
        outcome.error = str(exc)
        outcome.seconds = time.monotonic() - started
        return outcome

    # The same progress object the API publishes, pointed at a terminal instead of an HTTP endpoint.
    # Shared deliberately: a stage that reports one thing to the console's run screen and another
    # thing here would be two descriptions of one pipeline, and one of them would go stale.
    progress = RunProgress(
        sku=request.clean_mpn,
        plan=plan_stages(retrieve=True, generate_copy=generate_copy),
    )
    reporter = _TerminalProgress(progress, quiet=quiet)

    try:
        result = enrich_one(
            request,
            registry=registry,
            client=client,
            store=store,
            calibration_dir=main.CALIBRATION_DIR,
            library_path=main.LIBRARY_INDEX,
            fetcher=main.retrieval_fetcher(),
            search=main.search_provider(),
            renderer=main.browser_renderer(),
            progress=reporter,
        )
    except (UrlFetchError, IngestError) as exc:
        outcome.status = "unreachable"
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.seconds = time.monotonic() - started
        return outcome
    except Exception as exc:  # noqa: BLE001 - a sweep must survive one bad row
        outcome.status = "failed"
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.seconds = time.monotonic() - started
        return outcome

    sources = source_summaries(result.source, result.retrieval, mpn=request.clean_mpn)
    paths = persist_run(
        result.run,
        parsed=result.source.parsed,
        artifact=result.source.artifact,
        registry=registry,
        sessions_dir=SESSION_DIR,
        console_dir=CONSOLE_DIR,
        sources=sources,
    )

    # Written now rather than on download, for the same reason the endpoint does it: a download that
    # re-ran the pipeline would spend model calls to reproduce bytes we already have.
    build_delivery(
        result.record,
        registry=registry,
        fmt=main.load_delivery_format(),
        out_dir=main.ENRICH_DIR,
        mpn=request.clean_mpn,
        manufacturer=candidate.manufacturer,
        description=candidate.description,
        brand=candidate.brand,
        source_url=(
            result.source.artifact.document.uri
            if result.source.citable_as_manufacturer
            else None
        ),
    )

    record = result.record
    outcome.status = "enriched"
    outcome.slug = paths.slug
    outcome.class_code = result.run.class_code
    outcome.values = len(record.current_values())
    outcome.publishable = len(record.publishable_values())
    outcome.gaps = len(record.gaps)
    outcome.verifiability = record.verifiability()
    outcome.composite = result.run.certificate.summary.quality_index.composite
    outcome.from_document = result.source.from_url
    outcome.source_uri = result.source.artifact.document.uri
    outcome.calls = result.run.usage.calls
    outcome.cost_usd = result.run.cost_usd
    outcome.seconds = time.monotonic() - started
    outcome.notes = list(result.run.notes)
    return outcome


class _TerminalProgress:
    """Forwards stage transitions to stdout as they happen.

    A wrapper rather than a subclass so the real :class:`RunProgress` stays the single source of
    truth for state — this only decides what gets printed. It is the terminal counterpart of the
    console's stage checklist, and it exists for the same reason: a run that prints one line per SKU
    and then goes silent for ninety seconds is indistinguishable from a run that has hung.
    """

    def __init__(self, progress: RunProgress, *, quiet: bool) -> None:
        self._progress = progress
        self._quiet = quiet

    def begin(self, stage_id: str, detail: str | None = None) -> None:
        self._progress.begin(stage_id, detail)
        if self._quiet:
            return
        snapshot = self._progress.snapshot()
        stage = next((s for s in snapshot["stages"] if s["id"] == stage_id), None)
        if stage is None:
            return
        marker = "$" if stage["model"] else " "
        print(
            f"      [{snapshot['completed']:>2}/{snapshot['total']}]{marker} {stage['name']}",
            flush=True,
        )

    def complete(self, stage_id: str, detail: str | None = None) -> None:
        self._progress.complete(stage_id, detail)
        if not self._quiet and detail:
            print(f"           {detail}", flush=True)

    def skip(self, stage_id: str, reason: str) -> None:
        self._progress.skip(stage_id, reason)
        if not self._quiet:
            print(f"           skipped: {reason}", flush=True)

    def detail(self, stage_id: str, detail: str) -> None:
        self._progress.detail(stage_id, detail)

    def note(self, message: str) -> None:
        self._progress.note(message)

    def finish(self, message: str | None = None) -> None:
        self._progress.finish(message)

    def fail(self, message: str) -> None:
        self._progress.fail(message)


# ------------------------------------------------------------------ reporting


def _delta(before: int, after: int) -> str:
    change = after - before
    if change == 0:
        return f"{after} (=)"
    return f"{after} ({change:+d})"


def _print_plan(candidates: list[Candidate], counts: dict[str, int], *, verbose: bool) -> None:
    print(f"SKUs in data/console/:  {counts['bundles']}")
    print(f"selected for re-run:    {len(candidates)}")
    for key, label in (
        ("already_classified", "skipped, already classified"),
        ("already_documented", "skipped, already reads a manufacturer document"),
        ("reviewed", "skipped, has recorded review decisions"),
        ("already_identified", "skipped, no manufacturer name to search with"),
        ("unreadable", "skipped, bundle could not be read"),
    ):
        if counts[key]:
            print(f"  {counts[key]:>5}  {label}")
    print()
    print("submission fields resolved from:")
    for key, label in (
        ("from_master", "the item master"),
        ("from_delivery", "the last delivery row (enriched by hand, not in the item master)"),
        ("bundle_only", "nothing — no manufacturer or description recoverable"),
    ):
        if counts[key]:
            print(f"  {counts[key]:>5}  {label}")
    if counts["no_manufacturer"]:
        print(
            f"  {counts['no_manufacturer']:>5}  have no manufacturer name, so retrieval searches "
            f"the open web rather than a known domain"
        )
    print()
    if not candidates:
        return

    # The full table only on request. A thousand rows of it buries the summary above, which is the
    # part that decides whether to spend the money.
    shown = candidates if verbose else candidates[:15]
    print(f"{'part number':<24} {'manufacturer':<30} {'class':<22} vals  gaps  doc")
    print("-" * 100)
    for candidate in shown:
        before = candidate.existing
        print(
            f"{candidate.mpn[:24]:<24} "
            f"{(candidate.manufacturer or '(none)')[:30]:<30} "
            f"{(before.class_code or 'unclassified')[:22]:<22} "
            f"{before.values:>4}  {before.gaps:>4}  "
            f"{'yes' if before.has_document else 'no'}"
        )
    if len(shown) < len(candidates):
        print(f"... and {len(candidates) - len(shown)} more. --verbose lists every one.")


def _print_outcome(index: int, total: int, outcome: Outcome) -> None:
    before = outcome.before
    head = f"[{index}/{total}] {outcome.mpn}"
    if outcome.status != "enriched":
        print(f"{head}  {outcome.status.upper()}: {outcome.error}", flush=True)
        return

    print(
        f"{head}  {outcome.status}"
        f"  class={outcome.class_code or 'none'}"
        f"  values={_delta(before.values, outcome.values)}"
        f"  publishable={_delta(before.publishable, outcome.publishable)}"
        f"  gaps={_delta(before.gaps, outcome.gaps)}"
        f"  source={'manufacturer document' if outcome.from_document else 'submission'}"
        f"  {outcome.calls} calls"
        f"  {outcome.seconds:.1f}s",
        flush=True,
    )
    if outcome.from_document and not before.has_document:
        # The headline result of a sweep: a part that had never had a real source read for it now
        # has one. Called out because it is the change that moves a value from self-declared to
        # verified, which is the whole reason to spend the money.
        print(f"          new manufacturer document: {outcome.source_uri}", flush=True)


def _print_summary(outcomes: list[Outcome]) -> None:
    enriched = [o for o in outcomes if o.status == "enriched"]
    failed = [o for o in outcomes if o.status != "enriched"]
    calls = sum(o.calls for o in enriched)
    priced = [o.cost_usd for o in enriched if o.cost_usd is not None]
    gained_document = sum(1 for o in enriched if o.from_document and not o.before.has_document)
    gained_class = sum(1 for o in enriched if o.class_code and not o.before.class_code)
    values_before = sum(o.before.values for o in enriched)
    values_after = sum(o.values for o in enriched)

    print()
    print("=" * 72)
    print(f"re-ran            {len(enriched)} of {len(outcomes)} selected")
    if failed:
        print(f"failed            {len(failed)}")
        for outcome in failed[:10]:
            print(f"  {outcome.mpn}: {outcome.status} — {outcome.error}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more; see the report for all of them")
    print(f"new documents     {gained_document} SKUs now read a manufacturer document")
    print(f"newly classified  {gained_class}")
    gained = values_after - values_before
    print(f"values            {values_before} -> {values_after} ({gained:+d})")
    print(f"model calls       {calls}")
    # No total when a tier had no published price. A partial figure would understate the bill, which
    # is the one number nobody should have to discover later.
    print(
        f"cost              ${sum(priced):.4f}"
        if len(priced) == len(enriched)
        else f"cost              ${sum(priced):.4f} over {len(priced)} of {len(enriched)} runs; "
        f"the rest used an unpriced tier"
    )


# ------------------------------------------------------------------ entry point


def main_cli() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"the item master to read submissions from (default: {DEFAULT_INPUT.name})",
    )
    parser.add_argument(
        "--mpn",
        action="append",
        default=[],
        help="re-run only these part numbers; repeatable",
    )
    parser.add_argument("--limit", type=int, help="re-run at most N SKUs")
    parser.add_argument(
        "--only-unclassified",
        action="store_true",
        help="only SKUs whose stored bundle has no class code — the thinnest rows in the corpus",
    )
    parser.add_argument(
        "--only-undocumented",
        action="store_true",
        help=(
            "only SKUs whose stored bundle cites nothing but the client's own item-master row — "
            "those that have never had a manufacturer document read for them"
        ),
    )
    parser.add_argument(
        "--only-identified",
        action="store_true",
        help=(
            "only SKUs whose manufacturer name is recoverable. Without one, retrieval has no "
            "domain to search and falls back to the open web, which is a weaker and slower run."
        ),
    )
    parser.add_argument(
        "--skip-reviewed",
        action="store_true",
        help=(
            "leave SKUs whose review session carries a human decision alone. A re-run rewrites the "
            "session and those decisions are not recoverable."
        ),
    )
    parser.add_argument(
        "--no-refresh-sources",
        action="store_true",
        help=(
            "reuse stored coverage instead of looking for a newer manufacturer page or datasheet. "
            "Cheaper in requests, and defeats most of the point of a re-run."
        ),
    )
    parser.add_argument(
        "--no-optional",
        action="store_true",
        help="request only the class's required attributes, not its optional ones",
    )
    parser.add_argument(
        "--generate-copy",
        action="store_true",
        help="also generate and claim-check copy. One extra model call per SKU.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "wait between SKUs. Bedrock throttles on sustained per-account request rates, and a "
            "throttled call is a failed row that still consumed its retry budget."
        ),
    )
    parser.add_argument("--report", type=Path, help="write a per-SKU JSON report here")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would run and what each SKU already holds. No requests, no model calls.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="one line per SKU instead of a line per stage",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="list every selected SKU in the plan rather than the first fifteen",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm that this will spend real model calls and overwrite the stored runs",
    )
    args = parser.parse_args()

    if not CONSOLE_DIR.is_dir():
        print(f"error: no corpus to re-run: {CONSOLE_DIR} does not exist", file=sys.stderr)
        return 2
    if not args.input.is_file():
        # A warning rather than an error. The corpus drives the selection, so a missing item master
        # degrades the submission fields it would have supplied — it does not stop the sweep.
        print(
            f"warning: {args.input} not found, so submission fields come from each SKU's last "
            f"delivery row where one exists.",
            file=sys.stderr,
        )

    candidates, counts = read_candidates(
        args.input,
        mpns=args.mpn,
        only_unclassified=args.only_unclassified,
        only_undocumented=args.only_undocumented,
        only_identified=args.only_identified,
        skip_reviewed=args.skip_reviewed,
        limit=args.limit,
    )

    _print_plan(candidates, counts, verbose=args.verbose)

    if not candidates:
        print("nothing selected.")
        return 0

    if args.dry_run:
        print()
        print(
            f"dry run: nothing was fetched and no model was called. "
            f"{len(candidates)} SKUs would run."
        )
        return 0

    # The gate. Two model calls each, three with copy, and every success overwrites a stored bundle
    # and its session — so the confirmation is required rather than warned about. By the time a
    # warning is read the money is spent and the sessions are gone.
    if not args.yes:
        calls = len(candidates) * (3 if args.generate_copy else 2)
        print()
        print(
            f"refusing to run {len(candidates)} SKUs without --yes.\n"
            f"  That is roughly {calls} Bedrock calls billed to your AWS account, and it\n"
            f"  overwrites data/console/<slug>.bundle.json and data/sessions/<slug>.json for\n"
            f"  each one — discarding any review decisions recorded against them.\n"
            f"  Re-run with --yes to proceed, or --dry-run to see the selection without spending.",
            file=sys.stderr,
        )
        return 2

    from apps.api import main  # noqa: PLC0415 - imported late so --dry-run needs no credentials

    try:
        client = main.model_client()
    except Exception as exc:  # noqa: BLE001 - botocore raises several unrelated types
        print(f"error: no usable Bedrock client ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("       set AWS_PROFILE, or run with --dry-run.", file=sys.stderr)
        return 2

    registry = main.load_schema()
    store = LocalArtifactStore(main.ARTIFACT_DIR)

    print()
    print(f"re-running {len(candidates)} SKUs. Each one spends real model calls.")
    print()

    outcomes: list[Outcome] = []
    for index, candidate in enumerate(candidates, start=1):
        if not args.quiet:
            print(
                f"[{index}/{len(candidates)}] {candidate.mpn} "
                f"({candidate.manufacturer or 'no manufacturer'})",
                flush=True,
            )
        outcome = reenrich(
            candidate,
            main=main,
            registry=registry,
            client=client,
            store=store,
            include_optional=not args.no_optional,
            generate_copy=args.generate_copy,
            refresh_sources=not args.no_refresh_sources,
            quiet=args.quiet,
        )
        outcomes.append(outcome)
        _print_outcome(index, len(candidates), outcome)

        if args.report:
            # Written after every SKU, not at the end. A sweep of a thousand parts will be
            # interrupted, and a report that only exists on clean exit is a report that does not
            # exist for the run that needed it most.
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(
                    {
                        "input": str(args.input),
                        "selected": len(candidates),
                        "completed": len(outcomes),
                        "runs": [o.payload() for o in outcomes],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        if args.sleep > 0 and index < len(candidates):
            time.sleep(args.sleep)

    _print_summary(outcomes)
    if args.report:
        print(f"report            {args.report}")

    # Non-zero when nothing succeeded, so CI or a shell loop can tell a no-op from a sweep.
    return 0 if any(o.status == "enriched" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
