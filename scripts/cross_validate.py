"""Validation layer L4: extract the same SKU from several sources and compare them.

The layer nothing else in the stack can substitute for. L0 through L3 all reason about a single
observation — whether it parses, whether its unit is coherent, whether it contradicts a sibling
field, whether it is plausible for the class. All four pass happily on a value that is simply
wrong, because a wrong figure printed in a datasheet is a well-formed figure. Two independent
sources are the cheapest way to catch that.

    python scripts/cross_validate.py \\
        data/samples/ba100.txt data/samples/ba100-catalog.txt --sku BA-100-075

    # offline, with scripted responses, to see the mechanism without spending tokens
    python scripts/cross_validate.py \\
        data/samples/ba100.txt data/samples/ba100-catalog.txt --sku BA-100-075 --dry-run

The two sample sources are chosen to disagree in a realistic way rather than a contrived one. The
manufacturer datasheet is ``Rev C 2024-08``; the distributor catalogue is ``Rev A 2022-03`` and
still prints the older 400 PSI rating and a stale carton quantity. That is not a data-quality
failure on the catalogue's part — it is a currency problem, and L4's job is to tell the two apart.

Values from every source accumulate on one record through
:meth:`ProductRecord.add_candidate`, deliberately *without* superseding each other. Superseding on
arrival would resolve every disagreement by read order, silently, and there would be nothing left
for this layer to adjudicate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom.core.product import ProductRecord
from axiom.docintel import find_revision, parse_artifact
from axiom.extract import (
    BedrockModelClient,
    Extractor,
    ModelCascade,
    StubModelClient,
    UsageLedger,
)
from axiom.ingest import IngestError, LocalArtifactStore, ingest_file, ingest_url, is_url
from axiom.normalize import normalize_all
from axiom.schema import load_default
from axiom.validate import CrossSourceValidator, Validator, promote_resolved

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"

# Scripted responses for --dry-run, one per source, in the order the sources are given.
#
# Hand-written rather than recorded, and labelled as such wherever the output is shown. Their only
# purpose is to make the *comparison* mechanism demonstrable without a model call: two sources that
# agree on four attributes and disagree on two, one of them carrying a newer revision. Every quote
# below is verbatim from the corresponding sample file, so the evidence contract still applies and
# a typo here shows up as a rejected claim rather than a passing test.
_DRY_RUN_SCRIPTS: tuple[str, ...] = (
    json.dumps(
        [
            {
                "found": True,
                "attribute_code": "body_material",
                "value_raw": "Bronze C84400",
                "evidence_quote": "Body Material .................. Bronze C84400",
            },
            {
                "found": True,
                "attribute_code": "seat_material",
                "value_raw": "RPTFE",
                "evidence_quote": "Seat Material .................. RPTFE",
            },
            {
                "found": True,
                "attribute_code": "pressure_rating_wog",
                "value_raw": "600 PSI",
                "evidence_quote": "Pressure Rating ................ 600 PSI WOG @ 73 degF",
            },
            {
                "found": True,
                "attribute_code": "case_quantity",
                "value_raw": "12",
                "evidence_quote": "BA-100-075       3/4\"        Lever        12",
            },
            {
                "found": True,
                "attribute_code": "end_connection",
                "value_raw": "NPT threaded",
                "evidence_quote": "End Connection ................. NPT threaded, female both ends",
            },
        ]
    ),
    json.dumps(
        [
            {
                "found": True,
                "attribute_code": "body_material",
                "value_raw": "Bronze C84400",
                "evidence_quote": "Body Material .................. Bronze C84400",
            },
            {
                "found": True,
                "attribute_code": "seat_material",
                "value_raw": "RPTFE",
                "evidence_quote": "Seat Material .................. RPTFE",
            },
            # The two genuine conflicts, both stale rather than wrong.
            {
                "found": True,
                "attribute_code": "pressure_rating_wog",
                "value_raw": "400 PSI",
                "evidence_quote": "Pressure Rating ................ 400 PSI WOG",
            },
            {
                "found": True,
                "attribute_code": "case_quantity",
                "value_raw": "10",
                "evidence_quote": "BA-100-075       3/4\"        Lever        10",
            },
            {
                "found": True,
                "attribute_code": "end_connection",
                "value_raw": "NPT threaded",
                "evidence_quote": "End Connection ................. NPT threaded",
            },
            # Only this source mentions it, so L4 reports SKIPPED rather than corroborated.
            {
                "found": True,
                "attribute_code": "country_of_origin",
                "value_raw": "Taiwan",
                "evidence_quote": "Country of Origin .............. Taiwan",
            },
        ]
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources",
        nargs="+",
        help="two or more documents describing the same SKU. Paths or https:// URLs.",
    )
    parser.add_argument("--sku", required=True)
    parser.add_argument("--class-code", default="PLB.VLV.BALL.2PC")
    parser.add_argument(
        "--supplier",
        action="append",
        default=[],
        metavar="ID",
        help=(
            "supplier id per source, in order. Used with --trust to break a tie when revisions "
            "cannot be ordered."
        ),
    )
    parser.add_argument(
        "--trust",
        action="append",
        default=[],
        metavar="ID=WEIGHT",
        help=(
            "per-supplier reliability in [0,1], e.g. --trust milwaukee=0.9. Only ever used to "
            "*order* two sources, never to overrule a revision marker: a reliable supplier is a "
            "reason to prefer their figure, not evidence the other figure is wrong."
        ),
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help=(
            "supersede the losing side of each resolved conflict. Unresolved conflicts keep both "
            "candidates current so they stay visible in the review queue."
        ),
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help=(
            "write the findings to data/cross-source/<SKU>.json, which the API joins onto the "
            "console bundle at read time so a reviewer sees the disagreement in the workspace"
        ),
    )
    parser.add_argument("--tier", default="volume")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no model call; replay scripted responses for the two sample sources",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if len(args.sources) < 2:
        print(
            "L4 compares sources against each other, so it needs at least two. With one document "
            "every verdict would be SKIPPED, which is worse than not running the layer.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run and len(args.sources) > len(_DRY_RUN_SCRIPTS):
        print(
            f"--dry-run has scripted responses for {len(_DRY_RUN_SCRIPTS)} sources, "
            f"{len(args.sources)} were given. Drop --dry-run to call a real model.",
            file=sys.stderr,
        )
        return 2

    try:
        trust = _parse_trust(args.trust)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    registry = load_default()
    store = LocalArtifactStore(DEFAULT_STORE)
    cascade = ModelCascade.load()

    client = (
        StubModelClient(list(_DRY_RUN_SCRIPTS[: len(args.sources)]))
        if args.dry_run
        else BedrockModelClient(region=cascade.region, profile=args.profile)
    )
    extractor = Extractor(registry, client, cascade, start_tier=args.tier)

    record = ProductRecord(
        tenant_id="demo",
        sku=args.sku,
        mpn=args.sku,
        class_code=args.class_code,
        schema_version=registry.product_class(args.class_code).schema_version,
    )
    documents: dict[str, object] = {}
    usage = UsageLedger()
    per_source: list[dict] = []

    for index, source in enumerate(args.sources):
        supplier = args.supplier[index] if index < len(args.supplier) else None
        try:
            artifact = _ingest(source, store, supplier)
        except IngestError as exc:
            print(f"ingest failed for {source}: {exc}", file=sys.stderr)
            return 1

        raw = store.get(artifact.storage_uri)
        parsed = parse_artifact(raw, artifact.document)

        # Revision awareness. Read from the parsed document rather than the filesystem: when a copy
        # was downloaded says nothing about when the specification was written, and ordering two
        # documents by retrieval time would let collection order decide which one wins.
        marker = find_revision(parsed.full_text)
        document = artifact.document
        if marker is not None and not document.revision_label:
            document = document.model_copy(update={"revision_label": marker.label})
        documents[document.document_id] = document

        result = extractor.extract(
            parsed,
            class_code=args.class_code,
            target_sku=args.sku,
            include_optional=args.include_optional,
        )
        usage.merge(result.usage)

        normalised, _issues = normalize_all(result.values, registry)
        for value in normalised:
            # add_candidate, not add_value: competing observations must coexist for L4 to have
            # anything to compare.
            record.add_candidate(value)

        per_source.append(
            {
                "source": str(source),
                "document_id": document.document_id,
                "revision_label": document.revision_label,
                "revision_method": marker.method if marker else None,
                "supplier_id": supplier,
                "values": len(normalised),
                "parser": parsed.parser,
            }
        )

    report = CrossSourceValidator(registry, trust=trust).validate(record, documents)

    promoted = 0
    if args.promote:
        promoted = promote_resolved(record, report)

    saved_path = None
    if args.save:
        saved_path = _save(args.sku, per_source, report, dry_run=args.dry_run)

    # L0-L3 still run. L4 answers a different question and replaces none of them.
    single = Validator(registry).validate(record)

    if args.json:
        print(
            json.dumps(
                {
                    "sku": args.sku,
                    "dry_run": args.dry_run,
                    "sources": per_source,
                    "cross_source": report.to_dict(),
                    "single_source_validation": single.summary(),
                    "promoted": promoted,
                    "saved": str(saved_path) if saved_path else None,
                },
                indent=2,
                default=str,
            )
        )
    else:
        _report(args, per_source, report, single, promoted, usage)
        if saved_path:
            print(f"\n  written: {saved_path.relative_to(REPO_ROOT)}")
            print("  the API joins this onto the console bundle for this SKU")

    # An unresolved conflict is a blocking failure: the system must not choose between two
    # contradicting sources silently, and a non-zero exit is how a batch driver learns that.
    return 1 if report.unresolved else 0


def _save(sku: str, sources: list[dict], report, *, dry_run: bool) -> Path:
    """Write the L4 findings for one SKU, for the API to join onto its bundle.

    A separate artifact rather than a rewrite of the console bundle, following the same pattern as
    review sessions: the bundle is the immutable record of what a *single-source* pipeline run
    produced, and folding a later multi-source analysis into it would destroy the ability to ask
    what that run actually said. The join happens at read time.

    ``dry_run`` is recorded in the payload. Findings derived from scripted responses must not be
    presentable as a real measurement, and the console reads this flag to say so.
    """
    target = REPO_ROOT / "data" / "cross-source" / f"{sku}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "sku": sku,
                "generated_at": datetime.now(UTC).isoformat(),
                "dry_run": dry_run,
                "sources": sources,
                "report": report.to_dict(),
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _ingest(source: str, store, supplier: str | None):
    if is_url(source):
        return ingest_url(source, store, supplier_id=supplier)
    path = Path(source)
    if not path.is_file():
        raise IngestError(f"not a file and not a fetchable URL: {source}")
    return ingest_file(path, store, supplier_id=supplier)


def _parse_trust(entries: list[str]) -> dict[str, float]:
    trust: dict[str, float] = {}
    for entry in entries:
        supplier, _, raw = entry.partition("=")
        if not supplier or not raw:
            raise ValueError(f"--trust expects ID=WEIGHT, got {entry!r}")
        try:
            weight = float(raw)
        except ValueError as exc:
            raise ValueError(f"--trust weight {raw!r} is not a number") from exc
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"--trust weight {weight} is outside [0, 1]")
        trust[supplier] = weight
    return trust


def _report(args, per_source, report, single, promoted: int, usage) -> None:
    print(f"\n{'=' * 78}\nSOURCES\n{'=' * 78}")
    if args.dry_run:
        print("  DRY RUN: responses are scripted, not produced by a model\n")

    for entry in per_source:
        revision = entry["revision_label"] or "no revision marker"
        print(f"  {entry['document_id']}")
        print(f"    file      {entry['source']}")
        print(f"    revision  {revision}", end="")
        print(f"  (parsed via {entry['revision_method']})" if entry["revision_method"] else "")
        if entry["supplier_id"]:
            print(f"    supplier  {entry['supplier_id']}")
        print(f"    values    {entry['values']} ({entry['parser']} parser)")

    print(f"\n{'=' * 78}\nVALIDATE (L4 — CROSS-SOURCE AGREEMENT)\n{'=' * 78}")
    summary = report.summary()

    if not report.applicable:
        print(
            "  NOT APPLICABLE: fewer than two documents contributed values, so agreement could\n"
            "  not be checked. This is not a pass."
        )
        return

    print(
        f"  {summary['documents']} sources | corroborated {summary['corroborated']} | "
        f"disagreements {summary['disagreements']} "
        f"({summary['unresolved']} unresolved) | single-source {summary['single_source']}"
    )

    if report.corroborated:
        print("\n  CORROBORATED — two independent sources state the same value:")
        for code in report.corroborated:
            print(f"    {code}")
        print(
            "    This is the strongest evidence the system can produce. A single citation proves\n"
            "    a value was printed; two prove it was not a typo."
        )

    resolved = [d for d in report.disagreements if d.resolved]
    if resolved:
        print("\n  DISAGREED, RESOLVED BY REVISION — the older value is stale, not wrong:")
        for disagreement in resolved:
            print(f"    {disagreement.attribute_code}")
            for observation in disagreement.observations:
                mark = "->" if observation is disagreement.winner else "  "
                print(f"      {mark} {observation.describe()}")
            print(f"         {disagreement.reason}")

    if report.unresolved:
        print("\n  DISAGREED, UNRESOLVED — the system will not choose between these:")
        for disagreement in report.unresolved:
            print(f"    {disagreement.attribute_code}")
            for observation in disagreement.observations:
                print(f"         {observation.describe()}")
            print(f"         {disagreement.reason}")
        print(
            "\n    Left for a human deliberately. Picking a side on no evidence is the one thing\n"
            "    this layer exists to prevent."
        )

    if report.single_source:
        print(
            f"\n  SINGLE SOURCE (skipped, not corroborated): "
            f"{', '.join(report.single_source)}"
        )

    if promoted:
        print(
            f"\n  promoted: superseded {promoted} value(s) on the losing side of a "
            f"resolved conflict"
        )

    print(f"\n{'=' * 78}\nVALIDATE (L0-L3)\n{'=' * 78}")
    s = single.summary()
    print(
        f"  checks {s['checks']} | failures {s['failures']} | warnings {s['warnings']} | "
        f"consistency {s['consistency']:.1%}"
    )
    print(
        "\n  L4 replaces none of these. L0-L3 judge one observation at a time and all four pass\n"
        "  on a value that is well-formed and wrong; only a second source catches that."
    )

    if usage.calls:
        print(f"\n  {usage.calls} model call(s), {usage.input_tokens}/{usage.output_tokens} tokens")


if __name__ == "__main__":
    sys.exit(main())
