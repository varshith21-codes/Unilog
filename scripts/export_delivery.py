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
from axiom.classify import Classifier  # noqa: E402
from axiom.core.evidence import EvidenceSpan  # noqa: E402
from axiom.core.product import ProductRecord  # noqa: E402
from axiom.core.values import (  # noqa: E402
    AttributeValue,
    DerivationMethod,
    ValueStatus,
)
from axiom.delivery import (  # noqa: E402
    DeliveryFormatExporter,
    DeliveryRow,
    DeliveryRowBuilder,
    SupplierRow,
    load_default,
)
from axiom.delivery.source import INPUT_COLUMNS  # noqa: E402
from axiom.extract.description import (  # noqa: E402
    AbbreviationTable,
    extract_from_description,
    to_attribute_values,
)
from axiom.ingest import profile_rows, sha256_bytes  # noqa: E402
from axiom.normalize import normalize_all  # noqa: E402
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
        "--golden",
        type=Path,
        help="seed attribute values from a golden set, e.g. data/golden/unilog_dishwashers.yaml. "
        "THIS IS AN ARM, NOT THE RESULT: the golden values are transcribed from the client's own "
        "answer sheet, so a run using them measures the projection and the description recipes "
        "given correct extraction, and says nothing about extraction itself. Always report it "
        "alongside the unseeded run, never instead of it.",
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

    missing = [c for c in INPUT_COLUMNS if c not in rows[0]]
    if missing:
        print(
            f"input is missing expected columns: {', '.join(missing)}\n"
            f"found: {', '.join(rows[0])}",
            file=sys.stderr,
        )
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

    selected = _select(rows, args)
    classifier = Classifier(registry)
    builder = DeliveryRowBuilder(fmt, registry)
    abbreviations = None if args.no_description_extraction else AbbreviationTable.load()

    # The item master is itself the source document these values are cited against, so it is
    # content-hashed the same way any other arrival is. A citation that named the file without
    # pinning its content would not survive the supplier sending a corrected version.
    document_id = args.source.stem
    document_sha256 = sha256_bytes(args.source.read_bytes())

    golden = _load_golden(args.golden) if args.golden else {}
    if golden and not args.quiet:
        print(
            f"ARM: attributes supplied for {len(golden)} SKU(s) from "
            f"{args.golden.name}. This measures the projection and the description recipes, not\n"
            f"     extraction — the values come from the client's own answer sheet.\n"
        )

    built: list[DeliveryRow] = []
    methods: Counter[str] = Counter()
    extracted_total = 0
    refused_total = 0
    seeded_total = 0
    skipped = 0

    for raw in selected:
        source = SupplierRow.parse(raw)
        if not source.identified:
            skipped += 1
            if not args.quiet:
                print("  SKIP  (no part number)")
            continue

        class_code, method = _classify(classifier, source, args.class_code)
        methods[method] += 1

        if class_code is None and args.classified_only:
            skipped += 1
            if not args.quiet:
                print(f"  SKIP  {source.mpn:24s} unclassified ({method})")
            continue

        record = ProductRecord(
            tenant_id="unilog",
            sku=source.mpn or "",
            mpn=source.mpn,
            class_code=class_code,
            source_document_ids=[document_id],
        )
        if class_code:
            record.classifications.extend(_classifications(classifier, source))

        # Read what the description itself evidences. Deterministic, class-scoped, and every value
        # cites the substring it came from — which is why these are allowed through the publish
        # gate while a legacy item-master value is not.
        extracted = 0
        if abbreviations is not None and class_code and source.description:
            result = extract_from_description(
                source.description,
                registry=registry,
                class_code=class_code,
                abbreviations=abbreviations,
            )
            values = to_attribute_values(
                result,
                document_id=document_id,
                document_sha256=document_sha256,
                schema_version=registry.product_class(class_code).schema_version,
            )
            for value in values:
                record.add_value(value)
            extracted = len(values)
            extracted_total += extracted
            refused_total += len(result.refused)

        # The supplied arm. Seeded after description extraction so a golden value supersedes a
        # weaker reading of the same attribute rather than colliding with it.
        seeded = 0
        entry = golden.get(source.mpn or "")
        if entry:
            if entry.get("class_code") and not record.class_code:
                record.class_code = entry["class_code"]
            seeded = _seed_from_golden(record, entry, document_sha256, registry)
            seeded_total += seeded

        row = builder.build(
            record,
            source=source,
            reference_urls=list(entry.get("reference_urls", [])) if entry else None,
            # Brand and manufacturer cannot be resolved from a six-column input, so the arm
            # supplies them. Retrieval will supply them the same way.
            brand=entry.get("brand") if entry else None,
            manufacturer=entry.get("manufacturer") if entry else None,
        )
        built.append(row)
        if not args.quiet:
            notes = []
            if extracted:
                notes.append(f"+{extracted} desc")
            if seeded:
                notes.append(f"+{seeded} golden")
            suffix = f"  ({', '.join(notes)})" if notes else ""
            print(
                f"  {source.mpn:24s} {class_code or '-':28s} "
                f"{row.populated_count:3d} cells  {method}{suffix}"
            )

    if not built:
        print("nothing to write: every row was skipped", file=sys.stderr)
        return 1

    exporter = DeliveryFormatExporter(fmt)
    export = exporter.to_csv(built)
    sidecar = exporter.sidecar(built)

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.source.stem.replace(" ", "_")
    csv_path = args.out / f"{stem}.delivery.csv"
    sidecar_path = args.out / f"{stem}.provenance.json"
    csv_path.write_text(export.csv_text, encoding="utf-8", newline="")
    sidecar_path.write_text(sidecar, encoding="utf-8")

    _report(
        export,
        methods,
        skipped,
        csv_path,
        sidecar_path,
        extracted_total=extracted_total,
        refused_total=refused_total,
        seeded_total=seeded_total,
        golden_path=args.golden,
    )
    return 0


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _load_golden(path: Path) -> dict[str, dict]:
    """Golden products keyed by SKU."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {p["sku"]: p for p in payload.get("products", []) if p.get("sku")}


def _seed_from_golden(
    record: ProductRecord, entry: dict, document_sha256: str, registry
) -> int:
    """Attach golden attribute values to a record, citing the golden set as their source.

    The citation names the golden file rather than pretending to a datasheet page. A span that
    claimed a manufacturer document we never opened would be the exact dishonesty this system is
    built to prevent, and it would also make the arm impossible to tell apart from a real run.

    Values are written in SOURCE FORM ("120 V", "50-1/4 in") and pushed through the same
    `normalize_all` the extractor's output goes through — the convention
    `data/golden/pvf_valves.yaml` already establishes. That matters for a concrete reason: the grid
    keeps magnitude
    and unit in separate columns, and only normalisation turns "120 V" into a Quantity the exporter
    can split. Seeding pre-normalised values would leave the unit welded to the magnitude and score
    every quantity cell wrong.
    """
    attributes = entry.get("attributes") or {}
    if not attributes:
        return 0

    values = [
        AttributeValue(
            attribute_code=code,
            value_raw=str(value),
            method=DerivationMethod.SUPPLIER_FEED,
            confidence=1.0,
            status=ValueStatus.AUTO_ACCEPTED,
            evidence=[
                EvidenceSpan(
                    span_id=f"golden-{entry['sku']}-{code}",
                    document_id="golden:unilog_dishwashers_v1",
                    document_sha256=document_sha256,
                    quote=str(value),
                    quote_verified=True,
                    match_score=1.0,
                )
            ],
            prompt_version="golden@v1",
        )
        for code, value in attributes.items()
    ]

    normalized, _ = normalize_all(values, registry, class_code=entry.get("class_code"))
    for value in normalized:
        record.add_value(value)
    return len(normalized)


def _select(rows: list[dict[str, str]], args) -> list[dict[str, str]]:
    if args.mpn:
        wanted = {m.strip() for m in args.mpn}
        rows = [r for r in rows if (r.get("Mfg_Part_Num") or "").strip() in wanted]
    if args.limit:
        rows = rows[: args.limit]
    return rows


def _classify(
    classifier: Classifier, source: SupplierRow, forced: str | None
) -> tuple[str | None, str]:
    if forced:
        return forced, "forced"
    if not source.description:
        return None, "no_description"
    result = classifier.classify(source.description, sku=source.mpn)
    return result.class_code, result.method


def _classifications(classifier: Classifier, source: SupplierRow):
    """Re-run classification to capture the Classification objects, not just the code.

    Called only for rows that classified, so the cost is one extra deterministic retrieval pass
    over a 35-character string. Restructuring `_classify` to return both would be tidier and is
    worth doing if this ever runs over a million rows; at a thousand it is not measurable.
    """
    if not source.description:
        return []
    return classifier.classify(source.description, sku=source.mpn).classifications


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
    extracted_total: int,
    refused_total: int,
    seeded_total: int = 0,
    golden_path: Path | None = None,
) -> None:
    print()
    print("=" * 78)
    print(f"wrote {_display(csv_path)}")
    print(f"      {_display(sidecar_path)}")
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
    else:
        print(
            f"NOTE: the {extracted} extracted cells come from the DESCRIPTION STRING only, each\n"
            "      citing the substring it was read from. No manufacturer documents were\n"
            "      attached, so anything a supplier did not abbreviate into the description is\n"
            "      still absent - series names, cycle counts, dimensions and approvals among\n"
            "      them. Attach source documents to close that gap."
        )


if __name__ == "__main__":
    raise SystemExit(main())
