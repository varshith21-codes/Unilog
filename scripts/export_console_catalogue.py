"""Item master in, one console bundle per row out — the whole catalogue, not one SKU.

The console dashboards read ``data/console/*.bundle.json``. Until now the only writer of those
files was ``run_pipeline.py``, which takes **one datasheet plus one --sku** and makes real model
calls. So the catalogue the console showed was exactly "the number of times somebody ran that
script": two valve SKUs cut from ``data/samples/``. The client's 1,000-row item master had no path
into the console at all — it only ever reached ``data/delivery/`` through ``export_delivery.py``.

This script is that missing path. It walks the row file and writes a bundle (and a review session)
per row, so every SKU the client sent is visible, reviewable and counted.

    python scripts/export_console_catalogue.py "Unihack_ Sample Dataset - Input.csv"
    python scripts/export_console_catalogue.py "Unihack_ Sample Dataset - Input.csv" --limit 50
    python scripts/export_console_catalogue.py "Unihack_ Sample Dataset - Input.csv" `
      --classified-only

**Entirely offline. No model calls, no credentials.** It runs the same deterministic stages the
batch delivery path runs — retrieval classification, description extraction, normalisation,
validation, calibrated scoring, the acceptance policy, channel pre-flight, certificate — and then
projects the result through the same ``build_bundle`` the live pipeline uses. Nothing here invents a
value, and nothing here is seeded from an answer sheet.

What that means for the numbers, said plainly so they are not a surprise:

*   **A six-column row is a thin source.** There is no datasheet attached, so the only thing to read
    is the ~40-character ``Part_Desc``. Expect a handful of values per row and a large, honest gap
    list. The gaps carry ``no_source_available`` and recommend retrieval, which is the truth: the
    fix is to fetch the manufacturer document and run ``run_pipeline.py``, not to squeeze the
    description harder.
*   **Most rows do not classify.** The schema in ``schema/classes/`` covers four classes; the item
    master spans abrasives, lumber, power tools, PPE, wire and more. Retrieval abstains rather than
    guessing, so those rows land with ``class_code: null`` — present in the catalogue, with nothing
    scored against them. That is a schema-coverage result, and hiding those rows would hide it.
    ``--classified-only`` skips them when you want the narrower view.

One deliberate departure from ``run_pipeline.py``, because 1,000 rows share one file: each row is
carried as **its own excerpt** of the item master. The excerpt's ``document_id`` is
``<stem>#<part-number>``, its ``sha256`` is the hash of the whole item master, and its single page
holds that row's six fields. The console keys documents by id and renders one page view per SKU, so
this is what lets a reviewer see the exact line a value was read from instead of the first row of
the file — and embedding the full 1,000-line parse in every bundle would cost about a gigabyte.
Citations are verified by substring against the description either way (see
``axiom.extract.description.to_attribute_values``), never by page coordinate.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

import axiom  # noqa: E402
from axiom.classify import Classifier  # noqa: E402
from axiom.confidence import (  # noqa: E402
    DEFAULT_EPSILON,
    Calibrator,
    Priors,
    apply_policy,
    extract_features,
    select_threshold,
)
from axiom.console import (  # noqa: E402
    build_bundle,
    jsonable,
    serialise_class,
    serialise_document,
    serialise_pages,
)
from axiom.core.certificate import build_certificate  # noqa: E402
from axiom.core.gaps import Gap, GapReason, RecommendedAction  # noqa: E402
from axiom.core.naming import sku_slug  # noqa: E402
from axiom.core.product import ProductRecord  # noqa: E402
from axiom.delivery.batch import (  # noqa: E402
    InputColumnsError,
    classify_row,
    select_rows,
    validate_input_columns,
)
from axiom.delivery.source import INPUT_COLUMNS, SupplierRow  # noqa: E402
from axiom.docintel import parse_artifact  # noqa: E402
from axiom.extract import ExtractionResult  # noqa: E402
from axiom.extract.description import (  # noqa: E402
    AbbreviationTable,
    extract_from_description,
)
from axiom.extract.description import to_attribute_values as description_values  # noqa: E402
from axiom.ingest import LocalArtifactStore, ingest_file  # noqa: E402
from axiom.normalize import BrandMaster, clean_mpn, normalize_all  # noqa: E402
from axiom.review import build_session  # noqa: E402
from axiom.schema import load_default  # noqa: E402
from axiom.syndicate import export_all  # noqa: E402
from axiom.validate import Validator  # noqa: E402

DEFAULT_CONSOLE_DIR = REPO_ROOT / "data" / "console"
DEFAULT_SESSION_DIR = REPO_ROOT / "data" / "sessions"
DEFAULT_CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
ARTIFACT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"

TENANT = "unilog"

# The gap detail every unread required attribute carries. One sentence, and it names both the
# limitation and the action — a reviewer reading a wall of gaps needs to know instantly that this
# is "no document was attached", not "the document was read and came back empty".
NO_DOCUMENT_DETAIL = (
    "the only source for this SKU is its item-master row: a part number and a short "
    "description. No manufacturer document was attached, so this attribute was never stated. "
    "Attach a datasheet and run scripts/run_pipeline.py to close it."
)

PROMPT_VERSION = "item-master-description-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="supplier CSV (the Unilog item master)")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_CONSOLE_DIR,
        help=f"bundle directory (default: {DEFAULT_CONSOLE_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--session-out",
        type=Path,
        default=DEFAULT_SESSION_DIR,
        help=(
            "review session directory. Sessions are what the resolve workspace posts decisions "
            f"against (default: {DEFAULT_SESSION_DIR.relative_to(REPO_ROOT)})"
        ),
    )
    parser.add_argument("--limit", type=int, help="process only the first N rows")
    parser.add_argument(
        "--mpn",
        action="append",
        default=[],
        help="process only these part numbers; repeatable",
    )
    parser.add_argument(
        "--class-code",
        help="force this class on every row, bypassing classification",
    )
    parser.add_argument(
        "--classified-only",
        action="store_true",
        help=(
            "skip rows retrieval could not classify, rather than writing a bundle that records "
            "the abstention. Narrower, and it hides how much of the file the schema does not cover"
        ),
    )
    parser.add_argument(
        "--no-sessions",
        action="store_true",
        help="write bundles only. The dashboards render, but the resolve workspace has nothing "
        "to post a decision against",
    )
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        default=DEFAULT_CALIBRATION_DIR,
        help="directory holding calibrator.json, priors.json and calibration_set.json",
    )
    parser.add_argument(
        "--risk-budget",
        type=float,
        default=DEFAULT_EPSILON,
        help=(
            "maximum acceptable error rate on auto-published values. Must match the budget the "
            "other bundles in --out were produced under, or the console cannot report one policy"
        ),
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "delete bundles and sessions in the output directories whose SKU is not in this run. "
            "Off by default: it would remove the pipeline output of any SKU enriched from a real "
            "datasheet, which is the most expensive data in the repository"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the per-row log")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 1

    registry = load_default()
    if args.class_code and args.class_code not in registry.class_codes:
        print(
            f"unknown class {args.class_code!r}; known: {', '.join(registry.class_codes)}",
            file=sys.stderr,
        )
        return 1

    rows = _read(args.source)
    if not rows:
        print(f"{args.source} contains no data rows", file=sys.stderr)
        return 1

    try:
        validate_input_columns(rows[0].keys())
    except InputColumnsError as exc:
        print(exc, file=sys.stderr)
        return 1

    selected = select_rows(rows, mpns=args.mpn, limit=args.limit)
    if not selected:
        print("nothing selected: --mpn/--limit matched no rows", file=sys.stderr)
        return 1

    # The item master is itself the source document every description-derived value is cited
    # against, so it is ingested and content-hashed like any other arrival. That is also what puts
    # the original bytes behind /api/artifact/{sha256}, so a reviewer can open the file the
    # citation names rather than taking it on trust.
    store = LocalArtifactStore(ARTIFACT_STORE)
    artifact = ingest_file(args.source, store, supplier_id=TENANT)
    stem = args.source.stem.replace(" ", "_")

    calibrator, priors, policy = _load_calibration(args.calibration_dir, args.risk_budget)
    classifier = Classifier(registry)
    abbreviations = AbbreviationTable.load()
    brands = BrandMaster.load()
    validator = Validator(registry)

    if not args.quiet:
        print(f"read {len(rows)} rows from {args.source.name}; processing {len(selected)}")
        print(f"item master {artifact.document.document_id}  sha256 {artifact.document.sha256}")
        summary = policy.summary()
        threshold = summary.get("threshold")
        print(
            f"policy      {summary['epsilon']:.0%} error budget, threshold "
            f"{threshold if threshold is None else f'{threshold:.4f}'}, "
            f"calibrator {'trained' if calibrator.is_trained else 'untrained-heuristic'}\n"
        )

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.no_sessions:
        args.session_out.mkdir(parents=True, exist_ok=True)

    methods: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    written: list[str] = []
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    skipped_no_mpn = 0
    skipped_unclassified = 0
    values_total = 0
    gaps_total = 0
    accepted_total = 0

    for index, raw in enumerate(selected, start=1):
        source = SupplierRow.parse(dict(raw))
        if not source.identified:
            skipped_no_mpn += 1
            if not args.quiet:
                print(f"  {index:>5}  SKIP  (no part number)")
            continue

        class_code, method = classify_row(classifier, source, args.class_code)
        methods[method] += 1
        classes[class_code or "-"] += 1

        if class_code is None and args.classified_only:
            skipped_unclassified += 1
            if not args.quiet:
                print(f"  {index:>5}  SKIP  {source.mpn:24s} unclassified ({method})")
            continue

        sku = source.mpn or ""
        if sku in seen:
            # Two rows, one part number. The bundle path is keyed by SKU, so the second would
            # silently overwrite the first. Reported instead.
            duplicates.append(sku)
            if not args.quiet:
                print(
                    f"  {index:>5}  DUPE  {sku:24s} already written from row {seen[sku]}; "
                    f"overwriting"
                )
        seen[sku] = index

        bundle_path, session_path, stats = _process_row(
            source,
            class_code=class_code,
            registry=registry,
            classifier=classifier,
            abbreviations=abbreviations,
            brands=brands,
            validator=validator,
            calibrator=calibrator,
            priors=priors,
            policy=policy,
            artifact=artifact,
            stem=stem,
            out=args.out,
            session_out=None if args.no_sessions else args.session_out,
        )
        written.append(sku)
        values_total += stats["values"]
        gaps_total += stats["gaps"]
        accepted_total += stats["accepted"]

        if not args.quiet:
            print(
                f"  {index:>5}  {sku:24s} {class_code or '-':28s} "
                f"{stats['values']:2d} values ({stats['accepted']} auto-accepted), "
                f"{stats['gaps']:2d} gaps  {method}"
            )
        del bundle_path, session_path

    if not written:
        print("nothing written: every row was skipped", file=sys.stderr)
        return 1

    pruned = _prune(args, keep=set(written)) if args.prune else []

    _report(
        args,
        written=written,
        methods=methods,
        classes=classes,
        duplicates=duplicates,
        skipped_no_mpn=skipped_no_mpn,
        skipped_unclassified=skipped_unclassified,
        values_total=values_total,
        gaps_total=gaps_total,
        accepted_total=accepted_total,
        pruned=pruned,
        policy=policy,
    )
    return 0


def _process_row(
    source: SupplierRow,
    *,
    class_code: str | None,
    registry,
    classifier: Classifier,
    abbreviations: AbbreviationTable,
    brands: BrandMaster,
    validator: Validator,
    calibrator: Calibrator,
    priors: Priors,
    policy,
    artifact,
    stem: str,
    out: Path,
    session_out: Path | None,
) -> tuple[Path, Path | None, dict[str, int]]:
    """Run every offline stage for one row and write its bundle (and session).

    The stage order is the pipeline's order and it is load-bearing: validation runs before
    scoring so the confidence features can see the verdicts, and the certificate is built last so
    its richness dimension can observe channel readiness. Departing from that here would make the
    console's explanation of a score disagree with the score.
    """
    # --- the row as its own document -------------------------------------------
    row_artifact, parsed = _row_document(source, artifact=artifact, stem=stem)
    document_id = row_artifact.document.document_id

    # --- classification, captured rather than just its verdict ------------------
    # `classify_row` returned the code; `build_bundle` wants the candidate ranking behind it, so
    # the console can show what retrieval considered and how decisively it chose.
    classification = (
        classifier.classify(source.description, sku=source.mpn)
        if source.description
        else None
    )

    # --- extraction: the description, deterministically -------------------------
    extraction = extract_from_description(
        source.description or "",
        registry=registry,
        class_code=class_code,
        abbreviations=abbreviations,
    )
    # accept=False: these are candidates until the calibrated policy rules on them, exactly as a
    # model extraction is. Letting the description pass auto-accept itself would bypass the risk
    # budget this console reports against.
    raw_values = description_values(
        extraction,
        document_id=document_id,
        document_sha256=row_artifact.document.sha256,
        schema_version=(
            registry.product_class(class_code).schema_version if class_code else None
        ),
        accept=False,
    )
    normalized, norm_issues = normalize_all(raw_values, registry, class_code=class_code)

    # --- the record -------------------------------------------------------------
    brand = brands.resolve(source.brand.brand) if source.brand.resolved else None
    record = ProductRecord(
        tenant_id=TENANT,
        sku=source.mpn or "",
        mpn=source.mpn,
        mpn_normalized=clean_mpn(
            source.mpn or "", brand=brand.brand if brand and brand.resolved else None
        ),
        brand=(
            brand.brand.name
            if brand and brand.resolved
            else (source.brand.brand if source.brand.resolved else None)
        ),
        brand_id=brand.brand.brand_id if brand and brand.resolved else None,
        # The supplier code, not the manufacturer name. `Part_Manuf` mixes manufacturers with
        # distributors and buying co-ops, so `axiom.delivery.source` refuses to publish it as a
        # manufacturer; recording the code it parsed keeps the row traceable without asserting it.
        supplier_id=source.manufacturer.supplier_code,
        class_code=class_code,
        schema_version=(
            registry.product_class(class_code).schema_version if class_code else None
        ),
        source_document_ids=[document_id],
    )
    if classification is not None:
        record.classifications.extend(classification.classifications)
    for value in normalized:
        record.add_value(value)

    gaps = _gaps(record, registry, class_code, document_id=document_id)
    for gap in gaps:
        record.add_gap(gap)

    # --- validation, then scoring -----------------------------------------------
    report = validator.validate(record)
    for value in record.current_values():
        findings = report.per_attribute.get(value.attribute_code, [])
        if findings:
            value.validations = [*value.validations, *findings]

    scores: dict[str, float] = {}
    feature_map: dict[str, dict[str, float]] = {}
    for value in record.current_values():
        features = extract_features(value, priors=priors, supplier_id=record.supplier_id)
        feature_map[value.attribute_code] = features.explain()
        scores[value.attribute_code] = calibrator.predict(features)

    decisions = apply_policy(record.current_values(), scores, policy)

    # --- channels, then the certificate ----------------------------------------
    exports = export_all(record, registry)
    certificate = build_certificate(
        record,
        required_attribute_codes=(
            registry.required_codes(class_code) if class_code else []
        ),
        pipeline_version=f"axiom-{axiom.__version__}",
        # No cost, deliberately. Nothing here called a model, so a dollar figure would be a real
        # number describing work that never happened. The console renders "not recorded".
        cost_usd=None,
        wall_clock_seconds=0.0,
        exports=exports,
    )

    bundle = build_bundle(
        registry=registry,
        record=record,
        artifact=row_artifact,
        classification=classification or _NoClassification(),
        extraction=_extraction_result(extraction, normalized, gaps, registry, class_code),
        normalization_issues=norm_issues,
        validation=report,
        scores=scores,
        features=feature_map,
        decisions=decisions,
        certificate=certificate,
        exports=exports,
    )

    payload = {
        "bundle": bundle,
        "document": serialise_document(row_artifact, parsed),
        "pages": serialise_pages(parsed),
        "class_definition": serialise_class(registry, class_code) if class_code else None,
        "policy": jsonable(policy.summary()),
        "calibrator": "trained" if calibrator.is_trained else "untrained-heuristic",
    }
    # Slugged, not raw. `52C3-5/8-UPC` is a real part number in this file and a raw f-string would
    # write it into a directory that does not exist. See axiom.core.naming.
    slug = sku_slug(record.sku)
    bundle_path = out / f"{slug}.bundle.json"
    bundle_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    session_path = None
    if session_out is not None:
        session = build_session(
            record,
            parsed,
            registry,
            decisions,
            scores,
            policy,
            quality=certificate.summary.quality_index.to_dict(),
        )
        session_path = session.save(session_out / f"{slug}.json")

    return (
        bundle_path,
        session_path,
        {
            "values": len(record.current_values()),
            "gaps": len(record.gaps),
            "accepted": sum(1 for d in decisions if d.accepted),
        },
    )


def _row_document(source: SupplierRow, *, artifact, stem: str):
    """Present one row as its own excerpt of the item master.

    The bytes are the row's six fields, one per line, so the real text parser produces real line
    geometry and the resolve workspace can show a reviewer the line a value came from. The hash
    stays the **whole file's** hash: that is what the citation has to pin, and it is what
    ``/api/artifact`` will serve back.
    """
    text = "\n".join(f"{column}: {source.raw.get(column, '')}" for column in INPUT_COLUMNS)
    document = artifact.document.model_copy(
        update={
            "document_id": f"{stem}#{source.mpn}",
            "page_count": 1,
        }
    )
    row_artifact = replace(artifact, document=document)
    return row_artifact, parse_artifact(f"{text}\n".encode(), document)


def _gaps(record: ProductRecord, registry, class_code: str | None, *, document_id: str):
    """A gap per required attribute the row could not establish.

    ``NO_SOURCE_AVAILABLE`` rather than ``NOT_PRESENT_IN_ANY_SOURCE``, and the distinction is the
    whole point of having gap reasons: nothing here searched a datasheet and came back empty,
    because no datasheet was attached. Recording the stronger reason would claim a negative result
    that was never established, and it would send someone to chase a supplier for a value that is
    very likely printed on a document nobody has fetched yet.
    """
    if not class_code:
        return []

    have = {value.attribute_code for value in record.current_values()}
    return [
        Gap(
            attribute_code=code,
            reason=GapReason.NO_SOURCE_AVAILABLE,
            sources_searched=[document_id],
            detail=NO_DOCUMENT_DETAIL,
            recommended_action=RecommendedAction.RETRY_WITH_BETTER_SOURCE,
            is_required=True,
        )
        for code in registry.required_codes(class_code)
        if code not in have
    ]


def _extraction_result(extraction, values, gaps, registry, class_code: str | None):
    """Wrap the description pass in the shape the console's extraction summary expects.

    ``ExtractionResult`` is the pipeline's own type and the console renders its ``summary()``.
    Handing over ``DescriptionExtraction.summary()`` instead would put a different set of keys in
    the bundle for the same field, which is exactly how two producers of one contract drift apart.

    The token counts stay zero because no model was called, and ``requested_codes`` is the set the
    class actually asks for — so "5 of 21 requested" reads as coverage rather than as a mystery.
    """
    requested = tuple(registry.product_class(class_code).codes()) if class_code else ()
    return ExtractionResult(
        values=list(values),
        gaps=list(gaps),
        rejected=[],
        # Every refusal the description pass recorded, surfaced where the console already looks
        # for candidates that did not survive, instead of being dropped on the floor.
        corrections=[f"refused {code}: {reason}" for code, reason in extraction.refused],
        prompt_version=PROMPT_VERSION,
        schema_version=(
            registry.product_class(class_code).schema_version if class_code else ""
        ),
        requested_codes=requested,
    )


class _NoClassification:
    """Stands in for a classification that never ran, because the row has no description.

    ``build_bundle`` asks a classification for its summary and its candidates. A row with nothing
    to classify has both — they are empty — and the honest shape is an abstention with a reason,
    not a missing key the console has to guess about.
    """

    class_code = None
    method = "no_description"
    candidates: tuple = ()
    classifications: tuple = ()

    def summary(self) -> dict[str, object]:
        return {
            "class_code": None,
            "method": self.method,
            "confidence": 0.0,
            "candidates": 0,
            "reason": "the row carries no part description, so there was nothing to classify",
        }


def _load_calibration(directory: Path, epsilon: float):
    """The trained calibrator, learned priors and validated policy, if they exist.

    Same loader as ``run_pipeline.py``, and it must stay the same: bundles scored under a
    different threshold than the ones already in ``data/console/`` make the console report two
    policies for one dashboard, which it correctly refuses to do silently.
    """
    calibrator = Calibrator()
    priors = Priors()
    policy = select_threshold([], [], epsilon=epsilon)

    if (path := directory / "calibrator.json").exists():
        calibrator = Calibrator.load(path)
    if (path := directory / "priors.json").exists():
        priors = Priors.load(path)
    if (path := directory / "calibration_set.json").exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = select_threshold(payload["scores"], payload["labels"], epsilon=epsilon)
    return calibrator, priors, policy


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _prune(args, *, keep: set[str]) -> list[str]:
    """Remove bundles and sessions for SKUs outside this run.

    Opt-in, and it reports what it removed rather than doing it quietly.
    """
    slugs = {sku_slug(sku) for sku in keep}
    removed: list[str] = []
    for path in sorted(args.out.glob("*.bundle.json")):
        slug = path.name.removesuffix(".bundle.json")
        if slug not in slugs:
            path.unlink()
            removed.append(slug)
            session = args.session_out / f"{slug}.json"
            if session.is_file():
                session.unlink()
    return removed


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _report(
    args,
    *,
    written: list[str],
    methods: Counter,
    classes: Counter,
    duplicates: list[str],
    skipped_no_mpn: int,
    skipped_unclassified: int,
    values_total: int,
    gaps_total: int,
    accepted_total: int,
    pruned: list[str],
    policy,
) -> None:
    print()
    print("=" * 78)
    print(f"wrote {len(written)} bundle(s) to {_display(args.out)}")
    if not args.no_sessions:
        print(f"      {len(written)} session(s) to {_display(args.session_out)}")
    on_disk = len(list(args.out.glob("*.bundle.json")))
    print(f"      {on_disk} bundle(s) now on disk: that is what the console will show")
    print()

    print(f"values          {values_total} ({accepted_total} auto-accepted at the threshold)")
    print(f"required gaps   {gaps_total}")
    print(f"skipped         {skipped_no_mpn} without a part number", end="")
    print(f", {skipped_unclassified} unclassified" if skipped_unclassified else "")
    if duplicates:
        print(
            f"duplicate MPNs  {len(duplicates)} row(s) reused a part number and overwrote an "
            f"earlier bundle: {', '.join(sorted(set(duplicates))[:5])}"
        )
    if pruned:
        print(f"pruned          {len(pruned)} bundle(s) not in this run")

    print("\nclassification")
    for code, count in classes.most_common():
        label = code if code != "-" else "(abstained)"
        print(f"  {label:<30} {count:>5}")
    print("\nmethod")
    for method, count in methods.most_common():
        print(f"  {method:<30} {count:>5}")

    if classes.get("-"):
        print(
            f"\n{classes['-']} row(s) matched no class in schema/classes/. Retrieval abstained "
            f"rather than\nguessing, so those SKUs carry no scored attributes. Closing that gap "
            f"means adding\nclass definitions, not loosening the classifier."
        )

    if not policy.achievable:
        print(f"\nNO VALIDATED POLICY: {policy.summary()['reason']}")

    print("\nserve them:     python -m uvicorn apps.api.main:app --port 8000")
    print("console:        cd apps/console; npm run dev")


if __name__ == "__main__":
    raise SystemExit(main())
