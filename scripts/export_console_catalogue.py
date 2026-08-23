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
*   **Most rows classify, and the ones that do not say why.** This paragraph used to read "most
    rows do not classify", and it was accurate: the schema held four classes — two valves, a
    dishwasher and a lamp — against an item master spanning decking, abrasives, power tools, PPE,
    appliances, wire and more, so 785 of 1,000 rows landed with ``class_code: null``. The console
    rendered them as "no class could be established", which was the honest answer to a question the
    schema could not answer.

    ``schema/classes/`` now covers thirty-two classes drawn from what the file actually contains
    (see ``scripts/profile_cohorts.py`` for the measurement that chose them), and coverage is 959 of
    1,000 rows. The remainder still abstain rather than guessing, and they abstain for one of two
    stated reasons: ``no_viable_candidate`` where nothing in the schema matches, and
    ``ambiguous_no_model`` where two classes were too close to separate without a model to
    adjudicate. Both are visible in the bundle. ``--classified-only`` skips them when you want the
    narrower view; ``scripts/score_classification.py`` reports the split.

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
    extract_from_documents,
    select_rows,
    validate_input_columns,
)
from axiom.delivery.source import INPUT_COLUMNS, SupplierRow  # noqa: E402
from axiom.docintel import parse_artifact, title_block  # noqa: E402
from axiom.extract import ExtractionResult  # noqa: E402
from axiom.extract.description import (  # noqa: E402
    AbbreviationTable,
    extract_from_description,
)
from axiom.extract.description import to_attribute_values as description_values  # noqa: E402
from axiom.ingest import LocalArtifactStore, ingest_file  # noqa: E402
from axiom.normalize import BrandMaster, clean_mpn, normalize_all  # noqa: E402
from axiom.retrieve import DocumentLibrary, SourceTier  # noqa: E402
from axiom.review import build_session  # noqa: E402
from axiom.schema import load_default  # noqa: E402
from axiom.syndicate import export_all  # noqa: E402
from axiom.validate import Validator  # noqa: E402

DEFAULT_CONSOLE_DIR = REPO_ROOT / "data" / "console"
DEFAULT_SESSION_DIR = REPO_ROOT / "data" / "sessions"
DEFAULT_CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
DEFAULT_LIBRARY_INDEX = REPO_ROOT / "data" / "library" / "index.json"
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
        "--library",
        type=Path,
        default=DEFAULT_LIBRARY_INDEX,
        help=(
            "document library index written by scripts/retrieve_sources.py. Any retrieved document "
            "that covers a row is extracted from, deterministically and with citations. Absent or "
            "empty, the run falls back to the description alone."
        ),
    )
    parser.add_argument(
        "--no-library",
        action="store_true",
        help=(
            "ignore the document library and read descriptions only. The measurement arm: run with "
            "and without, and the difference is what retrieval contributed."
        ),
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

    # Loaded once for the whole run, so a document covering two hundred rows is parsed once rather
    # than two hundred times. That memoisation lives on the library instance, which is why it is
    # built here and passed down rather than constructed per row.
    library = None
    if not args.no_library:
        library = DocumentLibrary.load(LocalArtifactStore(ARTIFACT_STORE), args.library)

    if not args.quiet:
        print(f"read {len(rows)} rows from {args.source.name}; processing {len(selected)}")
        print(f"item master {artifact.document.document_id}  sha256 {artifact.document.sha256}")
        summary = policy.summary()
        threshold = summary.get("threshold")
        print(
            f"policy      {summary['epsilon']:.0%} error budget, threshold "
            f"{threshold if threshold is None else f'{threshold:.4f}'}, "
            f"calibrator {'trained' if calibrator.is_trained else 'untrained-heuristic'}"
        )
        if library is None:
            print("library     ignored (--no-library): descriptions only")
        else:
            covered = library.stats()["skus_covered"]
            print(
                f"library     {len(library)} document(s), covering {covered} part number(s) "
                f"so far"
            )
        print()

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
    from_documents_total = 0
    rows_with_documents = 0
    # Counted across the run, because frequency is what turns a diagnostic into a decision: a column
    # header the schema does not know, seen on two hundred rows, is one line of YAML worth more than
    # the rest of the tail put together.
    unmapped: dict[str, Counter[str]] = {
        "unmapped_headers": Counter(),
        "unmapped_labels": Counter(),
        "refused": Counter(),
    }

    for index, raw in enumerate(selected, start=1):
        source = SupplierRow.parse(dict(raw))
        if not source.identified:
            skipped_no_mpn += 1
            if not args.quiet:
                print(f"  {index:>5}  SKIP  (no part number)")
            continue

        # Coverage is looked up *before* classification, because it can feed it. A row whose
        # description is blank is unclassifiable from the row alone — and a retrieved datasheet is a
        # far better classification input than a forty-character description anyway. Without this
        # ordering, a SKU with a perfectly good covering document still abstained, and then no
        # attributes were asked for and the document went unread.
        coverage = _coverage_for(library, source.mpn or "")
        class_code, method = _classify(
            classifier,
            source,
            forced=args.class_code,
            coverage=coverage,
            library=library,
        )
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
            coverage=coverage,
        )
        written.append(sku)
        values_total += stats["values"]
        gaps_total += stats["gaps"]
        accepted_total += stats["accepted"]
        from_documents_total += stats["from_documents"]
        if stats["documents"]:
            rows_with_documents += 1
        for kind, entries in stats["diagnostics"].items():
            unmapped[kind].update(entries)

        if not args.quiet:
            retrieved = (
                f"  +{stats['from_documents']} from {stats['documents']} doc"
                if stats["documents"]
                else ""
            )
            print(
                f"  {index:>5}  {sku:24s} {class_code or '-':28s} "
                f"{stats['values']:2d} values ({stats['accepted']} auto-accepted), "
                f"{stats['gaps']:2d} gaps  {method}{retrieved}"
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
        library=library,
        from_documents_total=from_documents_total,
        rows_with_documents=rows_with_documents,
        unmapped=unmapped,
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
    coverage: list = (),
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
    # `_classify` returned the code; `build_bundle` wants the candidate ranking behind it, so the
    # console can show what retrieval considered and how decisively it chose. Re-run against the
    # same text that decided the class, or the bundle would display candidates for a different
    # input than the one the verdict came from.
    classification = None
    if source.description:
        classification = classifier.classify(source.description, sku=source.mpn)
    if (classification is None or not classification.class_code) and coverage:
        _entry, best = coverage[0]
        classification = classifier.classify(title_block(best), sku=source.mpn)

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

    # --- retrieved documents covering this part ----------------------------------
    # Resolved by the caller before classification, because a covering document can decide the class
    # when the row has no description. Written by scripts/retrieve_sources.py; the reuse point is
    # `DocumentLibrary.coverage_for`, where one accessory catalogue answers for every part listed in
    # it and a document already checked and found not to mention this part is skipped unparsed.
    documents = list(coverage) if class_code else []

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

    # --- extraction from the retrieved documents, deterministically --------------
    # After the description pass and before the gap sweep, which is the same ordering the batch
    # delivery path uses and for the same reason: both are evidenced, but a datasheet *states* a
    # fact where a description only implies it, so the document supersedes rather than collides.
    # Still no model call — `extract_structured` reads specification lines and table cells by
    # layout and cites the line or cell it read.
    from_documents = 0
    document_refused = 0
    # What the reader saw in the document and could not place. Collected per row and aggregated by
    # the caller, because an unmapped column that shows up on two hundred rows is one schema line
    # worth adding and a column that shows up once is noise.
    diagnostics: dict[str, list[str]] = {}
    if documents and class_code:
        parsed_documents = [found for _coverage, found in documents]
        from_documents, document_refused = extract_from_documents(
            record,
            parsed_documents,
            registry,
            class_code,
            source.mpn or "",
            diagnostics=diagnostics,
        )

    gaps = _gaps(
        record,
        registry,
        class_code,
        document_id=document_id,
        # The gap *reason* turns on this. With a covering document searched, an attribute that is
        # still missing was genuinely looked for and not stated — `not_present_in_any_source`, which
        # a supplier request can close. With no document, nothing was read at all, and claiming the
        # stronger reason would assert a negative result that was never established.
        documents_searched=[coverage.entry for coverage, _ in documents],
    )
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
        # Where the data came from, on the record, in the form a person can check: the URL, the
        # host, and what tier that host was judged to be. Only a manufacturer-tier URL is citable as
        # the manufacturer's own page, so the tier travels with the link rather than being implied
        # by it — otherwise every source would read as equally authoritative.
        #
        # Passed into the projection rather than assigned onto the result afterwards, which is how
        # every bundle already on disk came to be missing the key.
        sources=_sources(row_artifact, coverage),
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
            "documents": len(documents),
            "from_documents": from_documents,
            "document_refused": document_refused,
            "diagnostics": diagnostics,
        },
    )


def _sources(row_artifact, coverage: list) -> list[dict]:
    """Every source behind this record, with its URL and the tier it was judged at.

    The item master comes first because it is always there and is the client's own file. Retrieved
    documents follow, each carrying the URL that was actually fetched — post-redirect, which is the
    address the bytes came from rather than the one we asked for.

    ``citable_as_manufacturer`` is the field that matters and it is stated rather than inferred. A
    URL on the manufacturer's own domain may be written into the delivery format's ``MFR URL``
    column; an unknown-tier source is real evidence and must not be. Leaving a reader to work that
    out from the host would put the distinction one mistake away.
    """
    sources = [
        {
            "document_id": row_artifact.document.document_id,
            "url": row_artifact.document.uri,
            "host": "",
            "tier": "item_master",
            "doc_type": row_artifact.document.doc_type.value,
            "sha256": row_artifact.document.sha256,
            "covers_this_sku": "row",
            "citable_as_manufacturer": False,
            "license_note": row_artifact.document.license_note,
            "revision_label": row_artifact.document.revision_label,
        }
    ]
    for entry, _parsed in coverage:
        document = entry.entry
        sources.append(
            {
                "document_id": document.document_id,
                "url": document.source_uri,
                "host": document.host,
                "tier": document.tier,
                "doc_type": document.doc_type,
                "sha256": document.sha256,
                # `table` means the document lists this part in an ordering row, which is the
                # strongest claim available: it offers the part rather than mentioning it.
                "covers_this_sku": entry.how,
                "citable_as_manufacturer": document.tier == SourceTier.MANUFACTURER.value,
                "license_note": document.license_note,
                "revision_label": document.revision_label,
            }
        )
    return sources


def _coverage_for(library: DocumentLibrary | None, mpn: str) -> list:
    """Every retrieved document covering this part, with its parsed form.

    Empty without a library. Resolved once per row and threaded through, because ``coverage_for``
    may have to parse a document to answer and the result is needed by both classification and
    extraction.
    """
    if library is None or not mpn:
        return []
    found = []
    for entry in library.coverage_for(mpn):
        if (parsed := library.parsed(entry.entry)) is not None:
            found.append((entry, parsed))
    return found


def _classify(
    classifier: Classifier,
    source: SupplierRow,
    *,
    forced: str | None,
    coverage: list,
    library: DocumentLibrary | None,
) -> tuple[str | None, str]:
    """The class for one row, from the best text available.

    The description first, because it is the client's own words about this specific part number.
    Then, if that produced nothing, the covering document's full text — which is what
    ``run_pipeline.py`` has always classified from.

    The fallback is what makes *part number plus manufacturer* a usable input. With no description
    there is nothing in the row to classify, so the row abstains, so no class is established, so no
    attributes are requested and the retrieved datasheet is never read. One blank field silently
    disabled the entire retrieval chain. Reported as ``document`` rather than folded into
    ``retrieval_only`` so the console can show which text decided the class.
    """
    if forced:
        return forced, "forced"

    if source.description:
        result = classifier.classify(source.description, sku=source.mpn)
        if result.class_code:
            return result.class_code, result.method

    if coverage:
        # The strongest covering document, which `coverage_for` already sorted to the front.
        _entry, parsed = coverage[0]
        # The **title block**, not the full text, and the difference is decisive rather than
        # cosmetic. Retrieval classification abstains unless one candidate dominates the next, and a
        # whole datasheet mentions the vocabulary of several neighbouring classes — a bronze ball
        # valve's spec sheet scores 0.53 for ball valves and 0.47 for gate valves, which is a real
        # ambiguity and it correctly refuses to guess (`ambiguous_no_model`). The same document's
        # opening lines score 0.76 and decide it, because a title block names the product and
        # nothing else. That is also the same *kind* of text the description path classifies from,
        # so the two arms stay comparable.
        #
        # `run_pipeline.py` classifies from the full text instead, and should: it has a model to
        # resolve the ambiguity this path has to avoid.
        result = classifier.classify(title_block(parsed), sku=source.mpn)
        if result.class_code:
            return result.class_code, f"document:{result.method}"
        return None, "document_ambiguous"

    del library
    return None, "no_description" if not source.description else "no_viable_candidate"


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


def _gaps(
    record: ProductRecord,
    registry,
    class_code: str | None,
    *,
    document_id: str,
    documents_searched: list = (),
):
    """A gap per required attribute the row could not establish, with the *reason* it could not.

    Which reason is the whole point of having several, and it turns on one question: was a real
    source read?

    **No covering document** gives ``NO_SOURCE_AVAILABLE``. Nothing searched a datasheet and came
    back empty, because no datasheet was attached — the only source was a forty-character
    description. Recording the stronger reason here would claim a negative result that was never
    established, and it would send someone to chase a supplier for a value that is very likely
    printed on a document nobody has fetched yet. The action is therefore
    ``RETRY_WITH_BETTER_SOURCE``: go and get it. ``scripts/retrieve_sources.py`` is how.

    **A covering document was read** gives ``NOT_PRESENT_IN_ANY_SOURCE``, and the action becomes
    ``REQUEST_FROM_SUPPLIER``. This is a genuine negative: the manufacturer's own document was
    parsed, the part number was located in it, and the attribute is not stated. Nobody should go
    looking for a better document, because this is the document. Somebody should ask the supplier.

    ``sources_searched`` carries every document actually consulted, which is what makes the
    negative auditable rather than merely asserted.
    """
    if not class_code:
        return []

    searched = [document_id, *(entry.document_id for entry in documents_searched)]
    if documents_searched:
        reason = GapReason.NOT_PRESENT_IN_ANY_SOURCE
        action = RecommendedAction.REQUEST_FROM_SUPPLIER
        names = ", ".join(entry.document_id for entry in documents_searched)
        detail = (
            f"read the item-master row and {len(documents_searched)} retrieved document(s) "
            f"({names}); the part number was located but this attribute is not stated in any of "
            f"them. A better document will not close this one, so it is a supplier request."
        )
    else:
        reason = GapReason.NO_SOURCE_AVAILABLE
        action = RecommendedAction.RETRY_WITH_BETTER_SOURCE
        detail = NO_DOCUMENT_DETAIL

    # **Established**, not merely present. This used to read `record.current_values()`, which
    # counted a description-derived candidate as satisfying the attribute — so once those values
    # stopped
    # publishing, a required attribute the description implied was neither populated nor a gap. It
    # vanished from both sides of the ledger, and a SKU could report 0 of 3 populated with 0 gaps,
    # which is not a state that can be acted on.
    have = {
        value.attribute_code for value in record.current_values() if value.is_publishable
    }
    # What the input proposed but nothing independent confirmed. These become gaps too, with their
    # own reason: the value is known, it just is not established.
    suggested = {
        value.attribute_code: value
        for value in record.current_values()
        if not value.method.is_independent and value.attribute_code not in have
    }

    gaps = []
    for code in registry.required_codes(class_code):
        if code in have:
            continue
        if (candidate := suggested.get(code)) is not None:
            shown = candidate.value_display or str(candidate.value_canonical or "")
            quoted = candidate.evidence[0].quote if candidate.evidence else ""
            gaps.append(
                Gap(
                    attribute_code=code,
                    reason=GapReason.SELF_DECLARED_ONLY,
                    sources_searched=searched,
                    detail=(
                        f"the customer's own description implies {shown!r} (from {quoted!r}), and "
                        f"no independent source confirms it. The parse is sound; the item master "
                        f"is the file being enriched, so this is a lead rather than a fact. "
                        f"Cheapest gap in the catalogue to close: retrieval has a specific claim "
                        f"to check."
                    ),
                    # Not a supplier request. There is a named value to verify and a manufacturer
                    # page that would settle it, so sending this to a supplier would ask them to
                    # confirm what their own site already states.
                    recommended_action=RecommendedAction.RETRY_WITH_BETTER_SOURCE,
                    is_required=True,
                )
            )
            continue
        gaps.append(
            Gap(
                attribute_code=code,
                reason=reason,
                sources_searched=searched,
                detail=detail,
                recommended_action=action,
                is_required=True,
            )
        )
    return gaps


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


def _report_unmapped(unmapped: dict | None) -> None:
    """What the document reader saw and could not place, ordered by how often it saw it.

    The highest-value output of a retrieval run after the values themselves, and it used to be
    computed and discarded. An unmapped column header is the difference between "the datasheet does
    not state this" and "the datasheet states it under a name the schema has never heard of", and
    only one of those is closed by fetching more documents. Each line here is usually a one-line
    ``table_headers`` addition in schema/attributes/.
    """
    if not unmapped:
        return
    headers = unmapped.get("unmapped_headers") or Counter()
    labels = unmapped.get("unmapped_labels") or Counter()
    refused = unmapped.get("refused") or Counter()
    if not headers and not labels and not refused:
        return

    print("\nthe reader saw these and could not place them")
    print("  (each is a candidate one-line addition to schema/attributes/)")
    for title, counter in (
        ("table headers", headers),
        ("spec labels", labels),
    ):
        if not counter:
            continue
        print(f"  {title}:")
        for text, count in counter.most_common(8):
            flat = " ".join(str(text).split())[:58]
            print(f"    {count:>5}x  {flat}")
    if refused:
        print("  refused after mapping (the label matched, the value did not survive):")
        for text, count in refused.most_common(5):
            flat = " ".join(str(text).split())[:58]
            print(f"    {count:>5}x  {flat}")


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
    library=None,
    from_documents_total: int = 0,
    rows_with_documents: int = 0,
    unmapped: dict | None = None,
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
    description_total = values_total - from_documents_total
    if library is None:
        print(f"  from descriptions  {description_total}   (library ignored)")
    else:
        print(f"  from descriptions  {description_total}")
        print(
            f"  from documents     {from_documents_total}   across {rows_with_documents} row(s) "
            f"a retrieved document covered"
        )
        if rows_with_documents == 0 and len(library) == 0:
            print(
                "  The library is empty, so every value here came from a forty-character\n"
                "  description. Populate it: python scripts/retrieve_sources.py <input.csv>"
            )
    print(f"required gaps   {gaps_total}")
    _report_unmapped(unmapped)
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
