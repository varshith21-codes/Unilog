"""One datasheet in, a complete record per orderable part number out.

This is the scalability argument, made concrete. A datasheet describes a series: a shared
specification block plus an ordering table with a row per part number. Running the full pipeline
once per SKU re-parses the document and re-asks a model to pick the right table row every time —
N times the cost, and N chances to read a neighbouring row.

Instead: extract once, then derive each variant deterministically from its own table cells.

    $env:AWS_PROFILE = "axiom"
    python scripts/explode_variants.py data/samples/ba100.txt --sku BA-100-075
    python scripts/explode_variants.py data/samples/ba100.txt --sku BA-100-075 --save-sessions

The saving is real but it is not the main point. The main point is that the row-selection step no
longer involves a model, so the failure it used to be capable of — a value genuinely present in
the document and wrong for the part — is gone by construction rather than by measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
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
from axiom.console import build_bundle, serialise_cost  # noqa: E402
from axiom.core.certificate import build_certificate  # noqa: E402
from axiom.core.product import ProductRecord  # noqa: E402
from axiom.docintel import parse_artifact  # noqa: E402
from axiom.extract import (  # noqa: E402
    BedrockModelClient,
    Extractor,
    ModelCascade,
    PriceTable,
    StubModelClient,
    UsageLedger,
    detect_variant_table,
    explode,
    is_size_scoped,
)
from axiom.ingest import LocalArtifactStore, ingest_file  # noqa: E402
from axiom.normalize import BrandMaster, clean_mpn, normalize_all  # noqa: E402
from axiom.review import build_session  # noqa: E402
from axiom.schema import load_default  # noqa: E402
from axiom.syndicate import export_all  # noqa: E402
from axiom.validate import Validator  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sku", required=True, help="reference SKU to extract shared specs from")
    parser.add_argument("--class-code", default="PLB.VLV.BALL.2PC")
    parser.add_argument("--brand", default=None)
    parser.add_argument("--supplier", default=None)
    parser.add_argument("--tier", default="volume")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--risk-budget", type=float, default=DEFAULT_EPSILON)
    parser.add_argument(
        "--calibration-dir", type=Path, default=REPO_ROOT / "data" / "calibration"
    )
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--save-sessions",
        action="store_true",
        help="write a review session and console bundle for every variant",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.dry_run and args.save_sessions:
        print(
            "--dry-run cannot be combined with --save-sessions: a dry run extracts nothing, so "
            "it would overwrite every variant's saved session with an empty one.",
            file=sys.stderr,
        )
        return 2
    if not args.source.is_file():
        print(f"not a file: {args.source}", file=sys.stderr)
        return 2

    registry = load_default()
    store = LocalArtifactStore(DEFAULT_STORE)
    artifact = ingest_file(args.source, store, supplier_id=args.supplier)
    parsed = parse_artifact(store.get(artifact.storage_uri), artifact.document)

    table = detect_variant_table(parsed, registry, args.class_code)
    if table is None:
        print(
            f"no ordering table found in {args.source.name}. This document describes a single "
            f"part number; use scripts/run_pipeline.py instead.",
            file=sys.stderr,
        )
        return 1

    _report_table(table)

    # --- one extraction, for the reference SKU ---------------------------------
    cascade = ModelCascade.load()
    client = (
        StubModelClient([json.dumps([])])
        if args.dry_run
        else BedrockModelClient(region=cascade.region, profile=args.profile)
    )

    classifier = Classifier(registry, client=client, cascade=cascade, tier=args.tier)
    classification = classifier.classify(parsed.full_text, sku=args.sku)
    class_code = classification.class_code or args.class_code

    extractor = Extractor(registry, client, cascade, start_tier=args.tier)
    extraction = extractor.extract(
        parsed,
        class_code=class_code,
        target_sku=args.sku,
        include_optional=args.include_optional,
    )
    normalized, norm_issues = normalize_all(extraction.values, registry)

    brands = BrandMaster.load()
    brand = brands.resolve(args.brand) if args.brand else None
    reference = ProductRecord(
        tenant_id="demo",
        sku=args.sku,
        mpn=args.sku,
        mpn_normalized=clean_mpn(args.sku, brand=brand.brand if brand else None),
        brand=brand.brand.name if brand and brand.resolved else args.brand,
        brand_id=brand.brand.brand_id if brand and brand.resolved else None,
        supplier_id=args.supplier,
        class_code=class_code,
        schema_version=extraction.schema_version,
        source_document_ids=[artifact.document.document_id],
    )
    reference.classifications.extend(classification.classifications)
    for value in normalized:
        reference.add_value(value)
    for gap in extraction.gaps:
        reference.add_gap(gap)

    _report_inheritance(reference, table)

    # --- explode ---------------------------------------------------------------
    children = explode(reference, table, parsed, registry)

    usage = UsageLedger()
    usage.merge(classification.usage)
    usage.merge(extraction.usage)
    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None
    total_cost = usage.cost_usd(tier_prices)

    calibrator, priors, policy = _load_calibration(args.calibration_dir, args.risk_budget)

    results = []
    for child in children:
        # Normalise again: values taken straight from table cells are raw source text.
        normalised_child, child_issues = normalize_all(child.current_values(), registry)
        child.attribute_values = []
        for value in normalised_child:
            child.add_value(value)

        report = Validator(registry).validate(child)
        for value in child.current_values():
            findings = report.per_attribute.get(value.attribute_code, [])
            if findings:
                value.validations = [*value.validations, *findings]

        scores, features = {}, {}
        for value in child.current_values():
            feature = extract_features(value, priors=priors, supplier_id=args.supplier)
            features[value.attribute_code] = feature
            scores[value.attribute_code] = calibrator.predict(feature)

        decisions = apply_policy(child.current_values(), scores, policy)
        certificate = build_certificate(
            child,
            required_attribute_codes=registry.required_codes(child.class_code),
            pipeline_version=f"axiom-{axiom.__version__}",
            # Document cost is amortised across the variants it produced. Attributing the whole
            # extraction to one child would misreport four of the five.
            cost_usd=(total_cost / len(children)) if total_cost is not None else None,
            wall_clock_seconds=round(usage.latency_ms / 1000 / len(children), 2),
        )
        exports = export_all(child, registry)

        results.append(
            {
                "record": child,
                "report": report,
                "scores": scores,
                "features": features,
                "decisions": decisions,
                "certificate": certificate,
                "exports": exports,
                "issues": child_issues,
            }
        )

        if args.save_sessions:
            session = build_session(
                child,
                parsed,
                registry,
                decisions,
                scores,
                policy,
                quality=certificate.summary.quality_index.to_dict(),
            )
            session.save(REPO_ROOT / "data" / "sessions" / f"{child.sku}.json")

            bundle = build_bundle(
                registry=registry,
                record=child,
                artifact=artifact,
                classification=classification,
                extraction=extraction,
                normalization_issues=child_issues,
                validation=report,
                scores=scores,
                features={c: f.explain() for c, f in features.items()},
                decisions=decisions,
                certificate=certificate,
                exports=exports,
                cost=serialise_cost(
                    usage,
                    cost_usd=(total_cost / len(children)) if total_cost is not None else None,
                    cost_by_tier=None,
                    prices=prices,
                ),
            )
            payload = {
                "bundle": bundle,
                "document": _document_payload(artifact, parsed),
                "pages": _pages_payload(parsed),
                "class_definition": _class_payload(registry, child.class_code),
                "policy": policy.summary(),
                "calibrator": "trained" if calibrator.is_trained else "untrained-heuristic",
            }
            target = REPO_ROOT / "data" / "console" / f"{child.sku}.bundle.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    _report_variants(results, registry, policy)
    _report_economics(usage, total_cost, len(children), prices)

    if args.save_sessions:
        print(f"\n  wrote {len(children)} sessions to data/sessions/")
        print(f"  wrote {len(children)} bundles to data/console/")

    if args.json:
        print(
            json.dumps(
                {
                    "table": table.summary(),
                    "variants": [
                        {
                            "sku": r["record"].sku,
                            "parent_sku": r["record"].parent_sku,
                            "values": len(r["record"].current_values()),
                            "publishable": len(r["record"].publishable_values()),
                            "gaps": len(r["record"].gaps),
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    return 0


def _load_calibration(directory: Path, epsilon: float):
    calibrator_path = directory / "calibrator.json"
    priors_path = directory / "priors.json"
    calibration_path = directory / "calibration_set.json"

    calibrator = (
        Calibrator.load(calibrator_path) if calibrator_path.exists() else Calibrator()
    )
    priors = Priors.load(priors_path) if priors_path.exists() else Priors()
    if calibration_path.exists():
        payload = json.loads(calibration_path.read_text(encoding="utf-8"))
        policy = select_threshold(payload["scores"], payload["labels"], epsilon=epsilon)
    else:
        policy = select_threshold([], [], epsilon=epsilon)
    return calibrator, priors, policy


def _document_payload(artifact, parsed):
    from axiom.console import serialise_document

    return serialise_document(artifact, parsed)


def _pages_payload(parsed):
    from axiom.console import serialise_pages

    return serialise_pages(parsed)


def _class_payload(registry, class_code):
    from axiom.console import serialise_class

    return serialise_class(registry, class_code) if class_code else None


def _report_table(table) -> None:
    print("=" * 78)
    print("ORDERING TABLE")
    print("=" * 78)
    print(f"  table          {table.table_id} on page {table.page}")
    print(f"  variants       {len(table.rows)}")
    print(f"  part numbers   {', '.join(table.variant_skus)}")
    print(f"  per-variant    {', '.join(table.mapped_codes) or '(none)'}")
    if table.unmapped_headers:
        # An unmapped column is a missing `table_headers` entry in the schema, which is a
        # one-line fix. Dropping it silently would lose real data.
        print(f"  UNMAPPED       {', '.join(table.unmapped_headers)}")
        print("                 add these to `table_headers` in schema/attributes/*.yaml")


def _report_inheritance(reference, table) -> None:
    per_variant = set(table.mapped_codes)
    inherited, withheld = [], []
    for value in reference.current_values():
        if value.attribute_code in per_variant:
            continue
        note = is_size_scoped(value)
        (withheld if note else inherited).append((value.attribute_code, note))

    print("\n" + "=" * 78)
    print("INHERITANCE")
    print("=" * 78)
    print(f"  reference SKU  {reference.sku}")
    print(f"  from the table {len(per_variant)} attribute(s), per variant, by cell lookup")
    print(f"  inherited      {len(inherited)} series-level attribute(s)")
    if withheld:
        print(f"  WITHHELD       {len(withheld)} size-scoped attribute(s):")
        for code, note in withheld:
            print(f"                   {code} — stated only for {note!r}")
        print("                 inheriting these would attach a precisely-cited wrong value")


def _report_variants(results, registry, policy) -> None:
    print("\n" + "=" * 78)
    print("VARIANTS")
    print("=" * 78)

    # Without this line, a run where nothing publishes looks like a failure of extraction when
    # it is usually the risk budget doing its job.
    verdict = "validated" if policy.achievable else "NOT ACHIEVABLE"
    print(
        f"  policy         {policy.epsilon:.0%} budget, threshold "
        f"{policy.threshold:.3f} — {verdict}"
    )
    if not policy.achievable:
        print(f"                 {policy.reason}")
        print("                 nothing can auto-publish at this budget; try --risk-budget 0.05")

    print(f"\n  {'sku':<14} {'parent':<14} {'values':>6} {'pub':>5} {'gaps':>5}  channels")
    print("  " + "-" * 68)
    for result in results:
        record = result["record"]
        published = [n for n, e in result["exports"].items() if e.published]
        print(
            f"  {record.sku:<14} {(record.parent_sku or '—'):<14} "
            f"{len(record.current_values()):>6} {len(record.publishable_values()):>5} "
            f"{len(record.gaps):>5}  {', '.join(published) or 'none'}"
        )

    # The property that matters: each variant's size must come from its own row.
    print("\n  per-variant values, with the cell each came from:")
    for result in results:
        record = result["record"]
        cells = [
            f"{v.attribute_code}={v.value_display or v.value_raw} [{v.evidence[0].table_ref}]"
            for v in record.current_values()
            if v.evidence and v.evidence[0].table_ref
        ]
        print(f"    {record.sku:<14} {' | '.join(cells)}")


def _report_economics(usage, total_cost, variant_count: int, prices) -> None:
    print("\n" + "=" * 78)
    print("ECONOMICS")
    print("=" * 78)
    print(f"  model calls    {usage.calls} for {variant_count} variants")
    print(f"  tokens         {usage.input_tokens} in / {usage.output_tokens} out")

    if total_cost is None:
        print("\n  cost unavailable (no price table, or an unpriced tier was used)")
        return

    per_variant = total_cost / variant_count
    print(f"\n  total          ${total_cost:.6f}")
    print(f"  per variant    ${per_variant:.6f}")
    print(
        f"  per-SKU extraction would have cost roughly "
        f"${total_cost * variant_count:.6f} for the same {variant_count} records "
        f"({variant_count}x the document)"
    )
    for scale, label in ((100_000, "100k"), (500_000, "500k")):
        print(f"  at {label:<11} ${per_variant * scale:,.2f}")
    print(
        "\n  The saving assumes variants share a document, which is what an ordering table"
        "\n  means. It does not generalise to a catalogue of single-part datasheets."
    )


if __name__ == "__main__":
    raise SystemExit(main())
