"""Supplier CSV in, Unilog delivery CSV out.

The batch driver the delivery format needs and the rest of the repo did not have: every other
entry point processes one document plus one SKU, and this one walks a row file.

    python scripts/export_delivery.py "Unihack_ Sample Dataset - Input.csv" `
      --out data/delivery --limit 50

It runs **entirely offline and makes no model calls.** Classification is the deterministic
retrieval step (`CandidateIndex` scoring plus the dominance test); when retrieval is not decisive
the row abstains rather than guessing, exactly as it would with a model absent anywhere else in
the pipeline. That is the honest behaviour and it is also what makes this runnable in CI.

What that means for the output, stated plainly so the numbers are not a surprise: with no source
document attached to a row, there is nothing to extract from, so the attribute grid emits its
labels and almost no values. The row is structurally complete and specification-poor. Filling it
requires the retrieval stage — fetch the manufacturer URL, parse it, extract with evidence — which
is `run_pipeline.py`'s job. This script is the projection, not the enrichment.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

import yaml  # noqa: E402
from axiom.delivery import (  # noqa: E402
    DeliveryFormatExporter,
    DeliveryWorkbookExporter,
    load_default,
)
from axiom.delivery.batch import (  # noqa: E402
    NO_PART_NUMBER,
    BatchOptions,
    DocumentSource,
    InputColumnsError,
    RowOutcome,
    parse_documents,
    run_batch,
    select_rows,
    validate_input_columns,
)
from axiom.delivery.source import INPUT_COLUMNS  # noqa: E402
from axiom.docintel import ParsedDocument  # noqa: E402
from axiom.ingest import profile_rows, sha256_bytes  # noqa: E402
from axiom.schema import load_default as load_schema  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "delivery"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="supplier CSV (the Unilog item master)")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output directory (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--limit", type=int, help="process only the first N rows")
    parser.add_argument(
        "--mpn",
        action="append",
        default=[],
        help="process only these part numbers; repeatable. Useful for scoring against the "
        "ground-truth rows.",
    )
    parser.add_argument(
        "--class-code",
        help="force this class on every row, bypassing classification. For evaluating the "
        "projection in isolation from retrieval accuracy.",
    )
    parser.add_argument(
        "--classified-only",
        action="store_true",
        help="skip rows that could not be classified, rather than emitting an identity-only row",
    )
    parser.add_argument(
        "--no-description-extraction",
        action="store_true",
        help="do not read attributes out of Part_Desc. Useful for measuring what the description "
        "contributes: run with and without, and diff the score.",
    )
    parser.add_argument(
        "--document",
        type=Path,
        action="append",
        default=[],
        help="a manufacturer document to extract from, repeatable. Read deterministically: "
        "specification lines and the ordering row for each part number, cited to a line or a "
        "table cell. No model call and no credentials. This is real extraction, unlike --golden.",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        help="seed attribute values from a golden set, e.g. data/golden/unilog_dishwashers.yaml. "
        "THIS IS AN ARM, NOT THE RESULT: the golden values are transcribed from the client's own "
        "answer sheet, so a run using them measures the projection and the description recipes "
        "given correct extraction, and says nothing about extraction itself. Always report it "
        "alongside the unseeded run, never instead of it.",
    )
    parser.add_argument(
        "--xlsx",
        action="store_true",
        help="also write the delivery file as an XLSX workbook, with the provenance sidecar "
        "rendered onto its own sheets. What to hand a human: Excel reinterprets a CSV on import, "
        "turning 50-1/4 into a date and 0123 into 123.",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the per-row log")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 1

    fmt = load_default()
    registry = load_schema()
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

    # Profile before processing. A column that is 100% sentinel is a column the supplier believes
    # they are sending and are not, which is worth saying out loud once per file.
    profiles = profile_rows(list(INPUT_COLUMNS), rows)
    if not args.quiet:
        print(f"read {len(rows)} rows from {args.source.name}")
        for header in INPUT_COLUMNS:
            profile = profiles[header]
            flag = "  <- carries no data" if not profile.carries_data else ""
            print(
                f"  {header:14s} real {profile.populated:5d}"
                f"  sentinel {profile.placeholders:5d}"
                f"  distinct {profile.distinct:5d}{flag}"
            )
        print()

    selected = select_rows(rows, mpns=args.mpn, limit=args.limit)

    # The item master is itself the source document these values are cited against, so it is
    # content-hashed the same way any other arrival is. A citation that named the file without
    # pinning its content would not survive the supplier sending a corrected version.
    document_id = args.source.stem
    document_sha256 = sha256_bytes(args.source.read_bytes())

    documents = _load_documents(args.document)
    if documents and not args.quiet:
        for parsed in documents:
            tables = len(parsed.all_tables())
            print(
                f"document {parsed.document.document_id}: {parsed.page_count} page(s), "
                f"{tables} table(s), parser={parsed.parser}"
            )
        print()

    golden = _load_golden(args.golden) if args.golden else {}
    if golden and not args.quiet:
        print(
            f"ARM: attributes supplied for {len(golden)} SKU(s) from "
            f"{args.golden.name}. This measures the projection and the description recipes, not\n"
            f"     extraction — the values come from the client's own answer sheet.\n"
        )

    result = run_batch(
        selected,
        fmt=fmt,
        registry=registry,
        document_id=document_id,
        document_sha256=document_sha256,
        options=BatchOptions(
            class_code=args.class_code,
            classified_only=args.classified_only,
            read_descriptions=not args.no_description_extraction,
        ),
        documents=documents,
        golden=golden,
        on_row=None if args.quiet else _log,
    )

    if not result.rows:
        print("nothing to write: every row was skipped", file=sys.stderr)
        return 1

    exporter = DeliveryFormatExporter(fmt)
    export = exporter.to_csv(result.rows)
    sidecar = exporter.sidecar(result.rows)

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.source.stem.replace(" ", "_")
    csv_path = args.out / f"{stem}.delivery.csv"
    sidecar_path = args.out / f"{stem}.provenance.json"
    csv_path.write_text(export.csv_text, encoding="utf-8", newline="")
    sidecar_path.write_text(sidecar, encoding="utf-8")

    xlsx_path = None
    if args.xlsx:
        workbook = DeliveryWorkbookExporter(fmt).to_workbook(
            result.rows, source_name=args.source.name
        )
        xlsx_path = args.out / f"{stem}.delivery.xlsx"
        xlsx_path.write_bytes(workbook.data)

    _report(
        export,
        result.methods,
        result.skipped,
        csv_path,
        sidecar_path,
        xlsx_path=xlsx_path,
        extracted_total=result.extracted_total,
        refused_total=result.refused_total,
        seeded_total=result.seeded_total,
        from_documents_total=result.from_documents_total,
        document_refused_total=result.document_refused_total,
        golden_path=args.golden,
    )
    return 0


def _log(outcome: RowOutcome) -> None:
    """The per-row line. Formatting stays here because it is a console concern."""
    if outcome.skipped == NO_PART_NUMBER:
        print("  SKIP  (no part number)")
        return
    if outcome.skipped:
        print(f"  SKIP  {outcome.mpn:24s} {outcome.skipped} ({outcome.method})")
        return

    notes = []
    if outcome.from_description:
        notes.append(f"+{outcome.from_description} desc")
    if outcome.from_documents:
        notes.append(f"+{outcome.from_documents} doc")
    if outcome.from_golden:
        notes.append(f"+{outcome.from_golden} golden")
    suffix = f"  ({', '.join(notes)})" if notes else ""
    print(
        f"  {outcome.mpn:24s} {outcome.class_code or '-':28s} "
        f"{outcome.populated:3d} cells  {outcome.method}{suffix}"
    )


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _load_documents(paths: list[Path]) -> list[ParsedDocument]:
    """Read each ``--document`` off disk and hand it to the shared parser.

    A missing path is reported and skipped rather than fatal: the interesting failure is a batch
    that produced no citations, and that shows up in the report either way.
    """
    sources = []
    for path in paths:
        if not path.is_file():
            print(f"no such document: {path}", file=sys.stderr)
            continue
        sources.append(
            DocumentSource(
                data=path.read_bytes(),
                name=path.name,
                # Resolved first: `as_uri` refuses a relative path, and a caller naturally types
                # `--document data/samples/ba100.txt`.
                uri=path.resolve().as_uri(),
                stem=path.stem,
            )
        )
    return parse_documents(sources)


def _load_golden(path: Path) -> dict[str, dict]:
    """Golden products keyed by SKU."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {p["sku"]: p for p in payload.get("products", []) if p.get("sku")}


def _display(path: Path) -> str:
    """Repo-relative where possible, absolute otherwise.

    `relative_to` raises rather than returning the input when the path is outside the root, and a
    traceback from the summary printer after the file has already been written successfully is a
    silly way to fail.
    """
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _report(
    export,
    methods: Counter,
    skipped: int,
    csv_path,
    sidecar_path,
    *,
    xlsx_path: Path | None = None,
    extracted_total: int,
    refused_total: int,
    seeded_total: int = 0,
    from_documents_total: int = 0,
    document_refused_total: int = 0,
    golden_path: Path | None = None,
) -> None:
    print()
    print("=" * 78)
    print(f"wrote {_display(csv_path)}")
    print(f"      {_display(sidecar_path)}")
    if xlsx_path is not None:
        print(f"      {_display(xlsx_path)}")
    print()
    print(f"rows            {export.row_count}   (skipped {skipped})")
    print(f"columns         {len(export.populated_by_column)} of 252 populated at least once")
    print(f"content hash    {export.content_hash[:16]}")
    print()
    print("classification")
    for method, count in methods.most_common():
        print(f"  {method:22s} {count}")
    print()
    print("cells by provenance")
    for name, count in sorted(export.provenance_totals.items(), key=lambda kv: -kv[1]):
        print(f"  {name:22s} {count}")
    print()
    print(f"from Part_Desc  {extracted_total} values extracted, {refused_total} refused")
    if from_documents_total or document_refused_total:
        print(
            f"from documents  {from_documents_total} values extracted, "
            f"{document_refused_total} refused   <- real extraction, cited to a line or a cell"
        )
    if seeded_total:
        print(
            f"from golden     {seeded_total} values seeded from "
            f"{golden_path.name if golden_path else 'a golden set'}   <- ARM, not extraction"
        )
    print(f"withheld cells  {export.withheld_count}   (had a value, not allowed to publish)")
    compliance = "PASS" if export.compliant else f"FAIL ({len(export.violations)})"
    print(f"char limits     {compliance}")
    for violation in export.violations[:10]:
        print(f"  row {violation['row']} {violation['column']}: {violation['problems']}")

    # The honest headline. Say what the specifications came from and, more importantly, what they
    # could not come from, rather than letting a reader infer that 252 columns were filled.
    print()
    extracted = export.provenance_totals.get("extracted", 0)
    if extracted == 0:
        print(
            "NOTE: zero extracted cells. Nothing in these descriptions evidenced an attribute\n"
            "      the class declares, and no manufacturer documents were attached. The grid\n"
            "      therefore carries its labels and no values."
        )
    elif from_documents_total:
        print(
            f"NOTE: {from_documents_total} value(s) were read from attached manufacturer\n"
            "      documents, each cited to a specification line or an ordering-table cell that\n"
            "      can be highlighted. This is real extraction: deterministic, offline, and with\n"
            "      no model in the loop. Anything the layout does not state plainly - prose,\n"
            "      footnotes, qualified claims - still needs the model path."
        )
    else:
        print(
            f"NOTE: the {extracted} extracted cells come from the DESCRIPTION STRING only, each\n"
            "      citing the substring it was read from. No manufacturer documents were\n"
            "      attached, so anything a supplier did not abbreviate into the description is\n"
            "      still absent - series names, cycle counts, dimensions and approvals among\n"
            "      them. Attach source documents with --document to close that gap."
        )


if __name__ == "__main__":
    raise SystemExit(main())
