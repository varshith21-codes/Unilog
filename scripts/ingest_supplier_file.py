"""Ingest a supplier flat file and propose a column mapping.

The other half of ingestion. ``run_pipeline.py`` starts from a datasheet, which is where the rich
specifications live; this starts from the spreadsheet or ERP extract that tells you *which SKUs
exist in the first place*. Both land in the same content-addressed store, so a row and a quote
are anchored to source bytes the same way.

What this actually saves is operator time. Every supplier invents their own headers — ``PN``,
``MFR PART NO``, ``Cat No`` and ``Item`` all mean manufacturer part number — and mapping them by
hand is a per-file tax rather than a per-supplier one. So the mapping is *proposed with
confidence*, confirmed once, and then remembered:

    # first file from this supplier: inspect the proposal
    python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv --supplier milwaukee

    # fix what it could not resolve, and remember the result
    python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv --supplier milwaukee \\
        --map "WT/EA (lb)=each_weight" --confirm

    # every later file from the same supplier maps itself
    python scripts/ingest_supplier_file.py data/samples/next-file.csv --supplier milwaukee

Nothing here calls a model. Header matching is a small closed problem where a synonym table plus
fuzzy matching beats an LLM on accuracy, costs nothing and runs offline — so this whole path is
deterministic and free, which is also why it is safe to run on a five-hundred-thousand-row file.

**A proposal is not a mapping.** Only ``--confirm`` writes to the per-supplier memory, and only
confidently matched columns are written. A guess persisted without review would silently corrupt
every future file from that supplier, which is worse than mapping nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.ingest import (
    FLAT_FILE_SUFFIXES,
    RECORD_FIELDS,
    ColumnMapping,
    FlatFile,
    IngestError,
    LocalArtifactStore,
    MappingMemory,
    infer_mapping,
    ingest_file,
    read_flat_file,
)
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"
DEFAULT_MEMORY = REPO_ROOT / "data" / "ingest" / "column-mappings.json"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "ingest"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="supplier file (.csv, .tsv, .xlsx, .xls)")
    parser.add_argument(
        "--supplier",
        default=None,
        help=(
            "supplier id. Required for --confirm, because a remembered mapping is remembered "
            "*per supplier* — there is nowhere to file it without one."
        ),
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="HEADER=TARGET",
        help=(
            "override one column, e.g. --map \"WT/EA (lb)=each_weight\". Repeatable. An override "
            "is treated as human-confirmed, so it maps at full confidence."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "persist the confident columns of this mapping for --supplier, so later files from "
            "them map themselves. Without this the run is read-only."
        ),
    )
    parser.add_argument(
        "--memory",
        type=Path,
        default=DEFAULT_MEMORY,
        help="where per-supplier confirmed mappings live",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "write the mapped rows here as JSON. This is the 'before' item master: what the "
            "distributor already knows, in canonical field names, before any enrichment."
        ),
    )
    parser.add_argument("--rows", type=int, default=8, help="sample rows to preview")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"not a file: {args.source}", file=sys.stderr)
        return 2

    suffix = args.source.suffix.lower()
    if suffix not in FLAT_FILE_SUFFIXES:
        print(
            f"{args.source.name} is not a flat file. This entry point reads "
            f"{', '.join(sorted(FLAT_FILE_SUFFIXES))}; for a datasheet use run_pipeline.py.",
            file=sys.stderr,
        )
        return 2

    if args.confirm and not args.supplier:
        print(
            "--confirm needs --supplier: a confirmed mapping is stored per supplier, and "
            "there is nowhere to file one without knowing whose it is.",
            file=sys.stderr,
        )
        return 2

    # --- stage 1: ingest -------------------------------------------------------
    # Identical to a datasheet's path on purpose. A row cited in a review has to be traceable to
    # immutable bytes just as much as a quote does.
    store = LocalArtifactStore(DEFAULT_STORE)
    artifact = ingest_file(args.source, store, supplier_id=args.supplier)

    # --- stage 2: read ---------------------------------------------------------
    try:
        flat = read_flat_file(store.get(artifact.storage_uri), filename=args.source.name)
    except IngestError as exc:
        print(f"could not read {args.source.name}: {exc}", file=sys.stderr)
        return 1

    # --- stage 3: infer the mapping --------------------------------------------
    registry = load_default()
    valid_targets = set(registry.attribute_codes)

    memory = MappingMemory(args.memory)
    known = memory.get(args.supplier) if args.supplier else {}

    try:
        overrides = _parse_overrides(args.map, flat, valid_targets)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Overrides go in as though already remembered. A human typing a mapping *is* the
    # confirmation step, so re-guessing a column they just named would be perverse.
    mapping = infer_mapping(
        list(flat.headers),
        supplier_id=args.supplier,
        known={**known, **overrides},
        valid_targets=valid_targets,
    )

    # A mapping is keyed target -> header, so it can hold only one column per target. When two
    # columns claim the same target the first wins and the other is dropped — including when the
    # dropped one was typed by hand. Saying so matters: the operator would otherwise re-run the
    # same --map next time and assume it took.
    dropped = {
        target: header
        for target, header in overrides.items()
        if mapping.resolved.get(target) != header
    }

    remembered = 0
    if args.confirm:
        memory.remember(args.supplier, mapping.resolved)
        remembered = len(mapping.resolved)

    rows = _apply_mapping(flat, mapping)

    out_path = None
    if args.out is not None:
        out_path = _write_rows(args.out, artifact, flat, mapping, rows)

    if args.json:
        print(
            json.dumps(
                {
                    "document": {
                        "id": artifact.document.document_id,
                        "sha256": artifact.sha256,
                        "doc_type": artifact.document.doc_type.value,
                        "size_bytes": artifact.size_bytes,
                        "cached": artifact.was_already_stored,
                    },
                    "file": {
                        "headers": list(flat.headers),
                        "rows": len(flat),
                        "sheet_name": flat.sheet_name,
                    },
                    "mapping": _mapping_payload(mapping, flat),
                    "records": rows,
                    "confirmed": remembered,
                    "out": str(out_path) if out_path else None,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    _report_ingest(artifact, flat, args.source)
    _report_mapping(mapping, flat, registry)
    _report_rows(rows, args.rows)
    _report_memory(memory, mapping, args, remembered, dropped)
    if out_path:
        print(f"\n  mapped rows: {_display(out_path)}")

    # A file whose identity column could not be resolved has not really been ingested — there is
    # no key to enrich against. Non-zero so a batch driver notices rather than logging success.
    if not _identity_resolved(mapping):
        print(
            "\n  NO IDENTITY COLUMN: neither 'sku' nor 'mpn' was confidently mapped, so these "
            "rows cannot be joined to anything.\n"
            "  Name one explicitly, e.g. --map \"" + (flat.headers[0] if flat.headers else "PN")
            + "=mpn\"",
            file=sys.stderr,
        )
        return 1

    return 0


def _display(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise. Never raises on an outside path."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _parse_overrides(
    raw: list[str], flat: FlatFile, valid_targets: set[str]
) -> dict[str, str]:
    """Parse ``--map HEADER=TARGET`` into ``{target: header}``, validating both halves.

    Both are checked against reality rather than accepted on trust. A typo in a header silently
    maps nothing, and a typo in a target silently invents an attribute the schema has never heard
    of — and both would then be *persisted* by ``--confirm``.
    """
    overrides: dict[str, str] = {}
    for entry in raw:
        header, _, target = entry.partition("=")
        header, target = header.strip(), target.strip()
        if not header or not target:
            raise ValueError(f"--map expects HEADER=TARGET, got {entry!r}")
        if header not in flat.headers:
            raise ValueError(
                f"--map header {header!r} is not in this file. Available: "
                + ", ".join(flat.headers)
            )
        if target not in valid_targets and target not in RECORD_FIELDS:
            raise ValueError(
                f"--map target {target!r} is neither a schema attribute nor a record field "
                f"({', '.join(RECORD_FIELDS)}). Add it to schema/attributes/ first."
            )
        overrides[target] = header
    return overrides


def _apply_mapping(flat: FlatFile, mapping: ColumnMapping) -> list[dict[str, object]]:
    """Project rows onto canonical field names, keeping raw strings.

    Deliberately no normalisation, no unit conversion and no validation. This is the same
    separation extraction observes: a bad unit conversion must never be able to masquerade as a
    bad mapping. Values leave here exactly as the supplier wrote them.

    Empty cells are dropped rather than carried as empty strings, so a column that mapped
    successfully but is blank for this row reads as a gap instead of a value.
    """
    resolved = mapping.resolved
    record_fields = {f: resolved[f] for f in RECORD_FIELDS if f in resolved}
    attribute_targets = {t: h for t, h in resolved.items() if t not in RECORD_FIELDS}

    records: list[dict[str, object]] = []
    for row in flat.rows:
        record: dict[str, object] = {}
        for field, header in record_fields.items():
            if value := row.get(header, "").strip():
                record[field] = value

        attributes = {
            target: value
            for target, header in attribute_targets.items()
            if (value := row.get(header, "").strip())
        }
        record["attributes"] = attributes

        # Kept so nothing is silently lost. A column nobody could map is still information, and
        # the operator who has to resolve it needs to see what was in it.
        unmapped = {
            header: value
            for header in mapping.unmapped
            if (value := row.get(header, "").strip())
        }
        if unmapped:
            record["unmapped"] = unmapped

        records.append(record)
    return records


def _identity_resolved(mapping: ColumnMapping) -> bool:
    return bool({"sku", "mpn"} & set(mapping.resolved))


def _write_rows(
    out: Path,
    artifact,
    flat: FlatFile,
    mapping: ColumnMapping,
    rows: list[dict[str, object]],
) -> Path:
    base = out if out.is_absolute() else Path.cwd() / out
    target = (
        base
        if base.suffix == ".json"
        else base / f"{artifact.document.document_id}.rows.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "source": {
                    "document_id": artifact.document.document_id,
                    "sha256": artifact.sha256,
                    "filename": artifact.original_filename,
                    "supplier_id": artifact.document.supplier_id,
                    "fetched_at": artifact.document.fetched_at.isoformat(),
                },
                # The mapping travels with the rows. Reading the output later without knowing
                # which header produced which field would make the values unauditable.
                "mapping": {t: h for t, h in mapping.resolved.items()},
                "unmapped_headers": list(mapping.unmapped),
                "row_count": len(flat),
                "records": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return target


def _mapping_payload(mapping: ColumnMapping, flat: FlatFile) -> dict[str, object]:
    return {
        "coverage": round(mapping.coverage(), 4),
        "resolved": dict(mapping.resolved),
        "unmapped": list(mapping.unmapped),
        "needs_confirmation": [
            {"header": m.header, "target": m.target, "confidence": m.confidence}
            for m in mapping.needs_confirmation
        ],
        "notes": list(mapping.notes),
        "columns": [
            {
                "header": m.header,
                "target": m.target,
                "confidence": m.confidence,
                "method": m.method,
                "unit_hint": m.unit_hint,
                "fill_rate": round(flat.non_empty_ratio(m.header), 4),
            }
            for m in mapping.matches
        ],
    }


# --------------------------------------------------------------------------- reporting


def _report_ingest(artifact, flat: FlatFile, source: Path) -> None:
    print(f"\n{'=' * 78}\nINGEST\n{'=' * 78}")
    print(f"  file       {source.name}")
    print(f"  document   {artifact.document.document_id}")
    print(f"  sha256     {artifact.sha256[:16]}...")
    print(f"  type       {artifact.document.doc_type.value}")
    print(f"  size       {artifact.size_bytes:,} bytes")
    cached = "yes (identical bytes already stored)" if artifact.was_already_stored else "no"
    print(f"  cached     {cached}")

    print(f"\n{'=' * 78}\nREAD\n{'=' * 78}")
    print(f"  rows       {len(flat):,}")
    print(f"  columns    {len(flat.headers)}")
    if flat.sheet_name:
        print(f"  sheet      {flat.sheet_name}")


def _report_mapping(mapping: ColumnMapping, flat: FlatFile, registry) -> None:
    print(f"\n{'=' * 78}\nCOLUMN MAPPING\n{'=' * 78}")
    print(f"  {'header':<20} {'target':<24} {'conf':>5}  {'method':<16} {'fill':>5}  unit")
    print(f"  {'-' * 76}")

    for match in mapping.matches:
        target = match.target or "—"
        flag = " " if match.is_confident else "?"
        fill = flat.non_empty_ratio(match.header)
        print(
            f" {flag}{match.header[:19]:<20} {target[:23]:<24} {match.confidence:>5.2f}  "
            f"{match.method:<16} {fill:>5.0%}  {match.unit_hint or ''}"
        )

    print(f"\n  coverage   {mapping.coverage():.0%} of columns mapped confidently")

    # The three things an operator has to act on, each stated separately because the actions
    # differ: confirm a guess, name an unknown, or resolve a collision.
    if mapping.needs_confirmation:
        print("\n  NEEDS CONFIRMATION (matched, but not confidently):")
        for match in mapping.needs_confirmation:
            print(
                f"    {match.header!r} -> {match.target} @ {match.confidence:.2f} "
                f"({match.method})"
            )
            print(f'      confirm with: --map "{match.header}={match.target}"')

    # `ColumnMapping.unmapped` means "not confident", which includes the low-confidence guesses
    # already listed above. Listing those twice under two headings with two different suggested
    # actions would just be confusing, so this heading covers only columns with no target at all.
    proposed = {m.header for m in mapping.needs_confirmation}
    unresolved = [h for h in mapping.unmapped if h not in proposed]
    if unresolved:
        print("\n  UNMAPPED (no target proposed — name one, or leave it out):")
        for header in unresolved:
            fill = flat.non_empty_ratio(header)
            sample = next((v for v in flat.column(header) if v.strip()), "")
            print(f"    {header!r} — {fill:.0%} populated, e.g. {sample[:40]!r}")

    for note in mapping.notes:
        print(f"\n  NOTE: {note}")

    # A column mapped to an attribute the schema declares with a canonical unit, where the
    # header carries a *different* unit, is the quiet killer in supplier feeds: the number is
    # right and the dimension is wrong.
    conflicts = []
    for match in mapping.matches:
        if not (match.is_confident and match.unit_hint and match.target):
            continue
        if match.target in RECORD_FIELDS:
            continue
        try:
            definition = registry.attribute(match.target)
        except KeyError:
            continue
        canonical = definition.canonical_unit
        if canonical and match.unit_hint != canonical:
            conflicts.append((match.header, match.unit_hint, match.target, canonical))

    if conflicts:
        print("\n  HEADER UNITS DIFFER FROM CANONICAL (values need conversion, not just mapping):")
        for header, hint, target, canonical in conflicts:
            print(f"    {header!r} is in {hint}; {target} is canonically {canonical}")


def _report_rows(rows: list[dict[str, object]], limit: int) -> None:
    print(f"\n{'=' * 78}\nMAPPED ROWS\n{'=' * 78}")
    if not rows:
        print("  no data rows")
        return

    populated = sum(len(r.get("attributes", {})) for r in rows) / len(rows)
    print(f"  {len(rows):,} rows, {populated:.1f} attributes per row on average")

    for row in rows[:limit]:
        identity = row.get("sku") or row.get("mpn") or "?"
        attributes = row.get("attributes", {})
        rendered = ", ".join(f"{k}={v}" for k, v in list(attributes.items())[:5])
        print(f"\n    {identity}")
        print(f"      {rendered[:96]}")
        if len(attributes) > 5:
            print(f"      … and {len(attributes) - 5} more")

    if len(rows) > limit:
        print(f"\n  … {len(rows) - limit:,} further rows not shown")


def _report_memory(
    memory: MappingMemory,
    mapping: ColumnMapping,
    args,
    remembered: int,
    dropped: dict[str, str],
) -> None:
    print(f"\n{'=' * 78}\nSUPPLIER MEMORY\n{'=' * 78}")
    print(f"  store      {_display(args.memory)}")

    if dropped:
        print("\n  OVERRIDE NOT APPLIED — another column already claims that target:")
        for target, header in dropped.items():
            winner = mapping.resolved.get(target)
            print(f"    {header!r} -> {target} lost to {winner!r}")
        print(
            "    A target holds one column. Map the loser somewhere else, or map this one to a\n"
            "    different target, or drop the duplicate column from the file."
        )

    if remembered:
        print(f"  SAVED      {remembered} columns for supplier {args.supplier!r}")
        print("             every later file from them maps itself, with no operator step")
    elif args.supplier and memory.get(args.supplier):
        applied = sum(1 for m in mapping.matches if m.method == "supplier_memory")
        print(f"  applied    {applied} columns from a previously confirmed mapping")
        print("  read-only  pass --confirm to update it")
    else:
        print(f"  nothing remembered for {args.supplier or '(no --supplier given)'}")
        print("  read-only  pass --supplier and --confirm to remember this mapping")

    if others := memory.suppliers():
        print(f"  suppliers  {', '.join(others)}")


if __name__ == "__main__":
    sys.exit(main())
