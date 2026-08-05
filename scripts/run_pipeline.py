"""End-to-end pipeline runner: artifact in, cited values out.

Wires the stages built so far into the shape the finished product will have, and prints what
each stage produced. Stages arrive here as they are built, so this script doubles as a live
progress report on the spine.

    ingest -> parse -> extract -> (normalize) -> (validate) -> (certificate)

Run against a real Bedrock model:

    $env:AWS_PROFILE = "axiom"
    python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075

Or offline, replaying a saved model response:

    python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.classify import Classifier
from axiom.confidence import (
    DEFAULT_EPSILON,
    Calibrator,
    Priors,
    apply_policy,
    extract_features,
    select_threshold,
)
from axiom.console import (
    build_bundle,
    jsonable,
    serialise_class,
    serialise_copy,
    serialise_cost,
    serialise_document,
    serialise_pages,
)
from axiom.core.certificate import build_certificate
from axiom.core.product import ProductRecord
from axiom.docintel import parse_artifact
from axiom.extract import (
    BedrockModelClient,
    Extractor,
    ModelCascade,
    PriceTable,
    StubModelClient,
    UsageLedger,
)
from axiom.generate import ClaimVerdict, CopyGenerator, build_fact_sheet, load_policy
from axiom.ingest import LocalArtifactStore, ingest_file
from axiom.normalize import BrandMaster, clean_mpn, normalize_all
from axiom.review import build_session
from axiom.schema import load_default
from axiom.syndicate import export_all
from axiom.validate import Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="datasheet to process (.txt or .pdf)")
    parser.add_argument("--sku", required=True, help="target SKU within the document")
    parser.add_argument("--class-code", default="PLB.VLV.BALL.2PC")
    parser.add_argument("--supplier", default=None)
    parser.add_argument("--brand", default=None, help="raw brand string, to test unification")
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        default=REPO_ROOT / "data" / "calibration",
        help="directory holding calibrator.json, priors.json and calibration_set.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the certificate and channel exports to this directory",
    )
    parser.add_argument(
        "--save-session",
        action="store_true",
        help=(
            "write a review session to data/sessions/ and a console bundle to data/console/, "
            "which is what the API serves to the review workspace and the dashboards"
        ),
    )
    parser.add_argument(
        "--risk-budget",
        type=float,
        default=DEFAULT_EPSILON,
        help=(
            "maximum acceptable error rate on auto-published values, e.g. 0.05 for 5%%. "
            "This is the risk dial: a tighter budget publishes less."
        ),
    )
    parser.add_argument(
        "--generate-copy",
        action="store_true",
        help=(
            "also generate marketing copy from the publishable values and claim-check it. "
            "Costs one extra model call; copy that fails the check is reported, not published."
        ),
    )
    parser.add_argument("--tier", default="volume", help="cheapest tier to start the cascade")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--dry-run", action="store_true", help="no model call; stub response")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"not a file: {args.source}", file=sys.stderr)
        return 2

    # A dry run stubs an empty model response, so it produces a record with no values at all.
    # Persisting that would overwrite a real session and its bundle with an empty one, silently
    # destroying whatever the last real run established — the review queue and every dashboard
    # would go blank with no indication why. Refuse rather than warn: the combination has no
    # legitimate use, and by the time a warning is read the data is already gone.
    if args.dry_run and args.save_session:
        print(
            "--dry-run cannot be combined with --save-session: a dry run extracts nothing, "
            "so it would replace the saved session and console bundle for "
            f"{args.sku} with empty ones.\n"
            "Drop --save-session to smoke-test the wiring, or drop --dry-run to record a "
            "real run.",
            file=sys.stderr,
        )
        return 2

    registry = load_default()

    # --- stage 1: ingest -------------------------------------------------------
    store = LocalArtifactStore(DEFAULT_STORE)
    artifact = ingest_file(args.source, store, supplier_id=args.supplier)

    # --- stage 2: parse --------------------------------------------------------
    raw = store.get(artifact.storage_uri)
    parsed = parse_artifact(raw, artifact.document)

    # --- model client ----------------------------------------------------------
    cascade = ModelCascade.load()
    if args.dry_run:
        client = StubModelClient([json.dumps([])])
    else:
        client = BedrockModelClient(region=cascade.region, profile=args.profile)

    # --- stage 3: classify -----------------------------------------------------
    # Runs before extraction because the class decides which attributes to ask for. When
    # classification abstains, --class-code is the explicit fallback rather than a guess.
    classifier = Classifier(registry, client=client, cascade=cascade, tier=args.tier)
    classification = classifier.classify(parsed.full_text, sku=args.sku)
    class_code = classification.class_code or args.class_code

    # --- stage 4: extract ------------------------------------------------------
    # Extraction never normalises and never validates. It reports `value_raw` plus a quote,
    # and nothing else, so a unit bug can never be mistaken for an extraction bug.
    extractor = Extractor(registry, client, cascade, start_tier=args.tier)
    result = extractor.extract(
        parsed,
        class_code=class_code,
        target_sku=args.sku,
        include_optional=args.include_optional,
    )

    # --- stage 5: normalize ----------------------------------------------------
    normalized, norm_issues = normalize_all(result.values, registry)

    # --- stage 6: validate -----------------------------------------------------
    brands = BrandMaster.load()
    brand = brands.resolve(args.brand) if args.brand else None
    record = ProductRecord(
        tenant_id="demo",
        sku=args.sku,
        mpn=args.sku,
        mpn_normalized=clean_mpn(args.sku, brand=brand.brand if brand else None),
        brand=brand.brand.name if brand and brand.resolved else args.brand,
        brand_id=brand.brand.brand_id if brand and brand.resolved else None,
        supplier_id=args.supplier,
        class_code=class_code,
        schema_version=result.schema_version,
        source_document_ids=[artifact.document.document_id],
    )
    record.classifications.extend(classification.classifications)
    for value in normalized:
        record.add_value(value)
    for gap in result.gaps:
        record.add_gap(gap)

    report = Validator(registry).validate(record)

    # --- stage 7: score and decide ---------------------------------------------
    # Validation results are attached to each value first, so the confidence features can see
    # them. Scoring before validating would ignore the strongest independent signal available.
    for value in record.current_values():
        findings = report.per_attribute.get(value.attribute_code, [])
        if findings:
            value.validations = [*value.validations, *findings]

    calibrator, priors, policy = _load_calibration(args.calibration_dir, args.risk_budget)
    scores: dict[str, float] = {}
    feature_map = {}
    for value in record.current_values():
        features = extract_features(value, priors=priors, supplier_id=args.supplier)
        feature_map[value.attribute_code] = features
        scores[value.attribute_code] = calibrator.predict(features)

    decisions = apply_policy(record.current_values(), scores, policy)

    # --- stage 8: certificate and channel exports ------------------------------
    # Cost covers classification *and* extraction. Classification is a real model call against
    # a real prompt, and reporting only extraction would understate the true cost per SKU by
    # whatever the cheapest stage happens to cost — flattering, and wrong.
    usage = UsageLedger()
    usage.merge(classification.usage)
    usage.merge(result.usage)

    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None
    cost_usd = usage.cost_usd(tier_prices)

    certificate = build_certificate(
        record,
        required_attribute_codes=registry.required_codes(class_code),
        pipeline_version=f"axiom-{__import__('axiom').__version__}",
        cost_usd=cost_usd,
        wall_clock_seconds=round(usage.latency_ms / 1000, 2),
    )
    exports = export_all(record, registry)

    # --- stage 9: constrained copy generation ----------------------------------
    # Runs last, and only from values that already survived every earlier gate. Generating
    # before the acceptance decision would let a queued value into a product description.
    generated = None
    if args.generate_copy:
        sheet = build_fact_sheet(record, registry)
        generator = CopyGenerator(client, ModelCascade.load(), load_policy(), tier="mid")
        generated = generator.generate(sheet)
        usage.merge(generated.usage)
        cost_usd = usage.cost_usd(tier_prices)

    session_path = None
    bundle_path = None
    if args.save_session:
        session = build_session(
            record,
            parsed,
            registry,
            decisions,
            scores,
            policy,
            quality=certificate.summary.quality_index.to_dict(),
        )
        session_path = session.save(
            REPO_ROOT / "data" / "sessions" / f"{args.sku}.json"
        )

        # The console bundle is the wider projection: certificate, channel readiness,
        # classification candidates and the validation report, none of which a review session
        # carries. Persisting it here rather than recomputing it in the API is what keeps
        # model calls off the request path — a dashboard that re-ran extraction on every page
        # load would be both slow and non-deterministic.
        bundle_path = _save_bundle(
            registry=registry,
            record=record,
            parsed=parsed,
            artifact=artifact,
            classification=classification,
            extraction=result,
            normalization_issues=norm_issues,
            validation=report,
            scores=scores,
            features={code: f.explain() for code, f in feature_map.items()},
            decisions=decisions,
            certificate=certificate,
            exports=exports,
            policy=policy,
            calibrator=calibrator,
            cost=serialise_cost(
                usage,
                cost_usd=cost_usd,
                cost_by_tier=usage.cost_by_tier(tier_prices),
                prices=prices,
            ),
            copy=serialise_copy(generated),
        )

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.sku}.certificate.json").write_text(
            certificate.to_json(), encoding="utf-8"
        )
        for name, export in exports.items():
            if export.payload:
                (args.out / f"{args.sku}.{name}.json").write_text(
                    export.payload, encoding="utf-8"
                )

    if args.json:
        print(
            json.dumps(
                _as_dict(artifact, parsed, result, record, report), indent=2, default=str
            )
        )
        return 0

    _report_classify(classification, class_code, args.class_code)
    _report(artifact, parsed, result, registry, class_code)
    _report_normalize(normalized, norm_issues)
    _report_validate(report, record, brand)
    _report_decide(decisions, scores, feature_map, policy, calibrator)
    _report_cost(usage, prices, tier_prices, cost_usd, len(record.current_values()))
    if generated is not None:
        _report_copy(generated)
    _report_publish(certificate, exports, args.out)
    if session_path:
        print(f"\n  review session: {session_path.relative_to(REPO_ROOT)}")
    if bundle_path:
        print(f"  console bundle: {bundle_path.relative_to(REPO_ROOT)}")
    if session_path or bundle_path:
        print("  serve them:     python -m uvicorn apps.api.main:app --port 8000")
        print("  console:        cd apps/console; npm run dev")
    return 0


def _report_cost(usage, prices, tier_prices, cost_usd, value_count: int) -> None:
    """The cost-per-SKU meter.

    Extrapolating to a catalogue is the whole point of measuring this: nobody cares about a
    tenth of a cent, they care whether 500,000 SKUs costs $200 or $200,000.
    """
    print("\n" + "=" * 78)
    print("COST")
    print("=" * 78)

    print(f"  calls          {usage.calls} ({usage.escalations} escalations)")
    print(f"  tokens         {usage.input_tokens} in / {usage.output_tokens} out")
    print(f"  latency        {usage.latency_ms / 1000:.1f}s")

    if prices is None:
        print("\n  no price table. Fetch one:")
        print("    python scripts/fetch_bedrock_prices.py --write")
        return

    if cost_usd is None:
        used = sorted(usage.by_tier)
        missing = [tier for tier in used if tier not in (tier_prices or {})]
        print(
            f"\n  cost unavailable: no published price for tier(s) {', '.join(missing)}. "
            "Reporting a partial total would understate it, so none is reported."
        )
        return

    breakdown = usage.cost_by_tier(tier_prices) or {}
    print(f"\n  price list     {prices.region}, effective {_effective_date(prices)}")
    if prices.is_stale:
        age = prices.age_days()
        print(f"  WARNING        price table is {age:.0f} days old; re-run the fetch script")

    print(f"\n  {'tier':<12} {'calls':>6} {'tokens in':>11} {'tokens out':>11} {'USD':>12}")
    print("  " + "-" * 56)
    for tier in sorted(usage.by_tier, key=lambda t: -breakdown.get(t, 0)):
        print(
            f"  {tier:<12} {usage.by_tier[tier]:>6} "
            f"{usage.input_by_tier.get(tier, 0):>11} "
            f"{usage.output_by_tier.get(tier, 0):>11} "
            f"{breakdown.get(tier, 0):>12.6f}"
        )

    print(f"\n  cost this SKU  ${cost_usd:.6f}")
    if value_count:
        print(f"  per value      ${cost_usd / value_count:.6f} across {value_count} values")
    for scale, label in ((10_000, "10k"), (100_000, "100k"), (500_000, "500k")):
        print(f"  at {label:<11} ${cost_usd * scale:,.2f}")
    print(
        "\n  Extrapolation assumes this document's size and cascade path are typical. A"
        "\n  catalogue of larger datasheets, or one that escalates more often, costs more."
    )


def _report_copy(generated) -> None:
    print("\n" + "=" * 78)
    print("GENERATED COPY")
    print("=" * 78)

    if generated.error:
        print(f"  not generated: {generated.error}")
        return

    summary = generated.report.summary()
    print(f"  model      {generated.model_id} ({generated.model_tier})")
    print(f"  attempts   {generated.attempts}")
    print(
        f"  claims     {summary['claims']} checked — {summary['supported']} supported, "
        f"{summary['unsupported']} unsupported, {summary['banned']} banned"
    )
    print(f"\n  {generated.headline}")
    if generated.short_description:
        print(f"  {generated.short_description}")

    for claim in generated.report.claims:
        if claim.verdict is not ClaimVerdict.SUPPORTED:
            mark = "BANNED" if claim.verdict is ClaimVerdict.BANNED else "UNSUPPORTED"
            print(f"    {mark:<12}[{claim.kind.value}] {claim.text!r} — {claim.reason}")

    print()
    if generated.published:
        print("  publishable: every checkable assertion traces to a verified attribute")
    else:
        print("  BLOCKED: copy is withheld while any claim is unsupported")


def _effective_date(prices) -> str:
    dates = {
        price.effective_date for price in prices.models.values() if price.effective_date
    }
    return ", ".join(sorted(d[:10] for d in dates)) if dates else "unknown"


def _save_bundle(
    *,
    registry,
    record,
    parsed,
    artifact,
    classification,
    extraction,
    normalization_issues,
    validation,
    scores,
    features,
    decisions,
    certificate,
    exports,
    policy,
    calibrator,
    cost=None,
    copy=None,
) -> Path:
    """Write one SKU's console bundle to ``data/console/``.

    The document and class definition travel with the bundle rather than being looked up by
    the API. A bundle has to stay readable against the schema version it was produced under —
    if the API resolved the class at read time, editing a YAML file would silently rewrite the
    history of every run that came before it.
    """
    bundle = build_bundle(
        registry=registry,
        record=record,
        artifact=artifact,
        classification=classification,
        extraction=extraction,
        normalization_issues=normalization_issues,
        validation=validation,
        cost=cost,
        copy=copy,
        scores=scores,
        features=features,
        decisions=decisions,
        certificate=certificate,
        exports=exports,
    )
    payload = {
        "bundle": bundle,
        "document": serialise_document(artifact, parsed),
        "pages": serialise_pages(parsed),
        "class_definition": (
            serialise_class(registry, record.class_code) if record.class_code else None
        ),
        "policy": jsonable(policy.summary()),
        "calibrator": "trained" if calibrator.is_trained else "untrained-heuristic",
    }

    target = REPO_ROOT / "data" / "console" / f"{record.sku}.bundle.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return target


def _report_publish(certificate, exports, out_dir) -> None:
    print(f"\n{'=' * 78}\nCERTIFICATE & EXPORTS\n{'=' * 78}")
    summary = certificate.summary
    print(f"  certificate {certificate.certificate_id}")
    print(f"  signature   {certificate.signature[:26]}…  verified={certificate.verify_signature()}")
    print(
        f"  certified   {summary.attributes_populated} values "
        f"({summary.attributes_with_evidence} with verified evidence, "
        f"{summary.attributes_inferred} inferred)"
    )
    quality = summary.quality_index
    print(
        f"  quality     completeness {quality.completeness:.1%} | "
        f"verifiability {quality.verifiability:.1%} | "
        f"consistency {quality.consistency:.1%} | composite {quality.composite:.1%}"
    )

    print(f"\n  {'channel':<14} {'published':<10} {'values':>6}  detail")
    print(f"  {'-' * 74}")
    for name, export in exports.items():
        state = "yes" if export.published else "BLOCKED"
        detail = ""
        if not export.published:
            blockers = []
            if export.readiness.missing:
                blockers.append(f"missing {', '.join(export.readiness.missing)}")
            if export.readiness.not_publishable:
                blockers.append(f"queued {', '.join(export.readiness.not_publishable)}")
            detail = "; ".join(blockers) or "; ".join(export.readiness.warnings)
        elif export.withheld:
            detail = f"withheld {', '.join(export.withheld)}"
        print(f"  {name:<14} {state:<10} {export.value_count:>6}  {detail[:36]}")

    if out_dir:
        print(f"\n  written to {out_dir}")


def _load_calibration(directory: Path, epsilon: float = DEFAULT_EPSILON):
    """Load a trained calibrator, learned priors and a validated risk policy if they exist.

    All three are optional. With none of them present the system cold-starts: scores come from
    a capped heuristic and the policy is unachievable, so every value queues for review. That
    is the correct opening state — automation coverage should be earned from review outcomes,
    not granted before any exist.
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


def _report_decide(decisions, scores, feature_map, policy, calibrator) -> None:
    print(f"\n{'=' * 78}\nSCORE & DECIDE\n{'=' * 78}")
    state = "trained" if calibrator.is_trained else "UNTRAINED (heuristic, capped at 0.85)"
    print(f"  calibrator {state}")
    summary = policy.summary()
    print(
        f"  budget     {summary['epsilon']:.0%} max error at "
        f"{summary['confidence_level']:.0%} confidence"
    )
    if policy.achievable:
        print(f"  threshold  {summary['threshold']:.3f}")
        print(
            f"  guarantee  {summary['coverage']:.1%} coverage, error at most "
            f"{summary['error_upper_bound']:.1%} (from {summary['calibration_size']} samples)"
        )
    else:
        print(f"  NO VALIDATED POLICY: {summary['reason']}")

    accepted = [d for d in decisions if d.accepted]
    print(f"\n  auto-accepted {len(accepted)}/{len(decisions)}")
    print(f"\n  {'attribute':<24} {'score':>6}  {'decision':<22} why")
    print(f"  {'-' * 76}")
    for decision in sorted(decisions, key=lambda d: (-d.score, d.attribute_code)):
        verdict = "AUTO-ACCEPT" if decision.accepted else "review"
        print(
            f"  {decision.attribute_code:<24} {decision.score:>6.3f}  "
            f"{verdict + ' / ' + decision.reason_code:<22} {decision.detail[:28]}"
        )

    # Show which signals drove the top score, so a reviewer can see the reasoning.
    if decisions:
        top = max(decisions, key=lambda d: d.score)
        features = feature_map.get(top.attribute_code)
        if features:
            driving = sorted(features.explain().items(), key=lambda kv: -kv[1])[:5]
            rendered = ", ".join(f"{name}={val:.2f}" for name, val in driving)
            print(f"\n  strongest signals for {top.attribute_code}: {rendered}")

    del scores


def _report_classify(classification, resolved: str, fallback: str) -> None:
    print(f"\n{'=' * 78}\nCLASSIFY\n{'=' * 78}")
    summary = classification.summary()
    print(f"  method     {summary['method']}")
    print(f"  candidates {summary['candidates_considered']}")
    for candidate in classification.candidates:
        print(f"    {candidate.score:.4f}  {candidate.code}  ({candidate.path_text})")

    if classification.abstained:
        print(f"  ABSTAINED  {classification.abstain_reason}")
        print(f"  falling back to --class-code {fallback}")
        return

    internal = classification.internal
    print(f"  class      {internal.code} @ {internal.confidence:.2f}")
    print(f"  path       {' > '.join(internal.path)}  (confident to level {len(internal.path)})")
    levels = ", ".join(f"L{i + 1}={c:.2f}" for i, c in enumerate(internal.level_confidences))
    print(f"  per-level  {levels}")
    if internal.rationale:
        print(f"  rationale  {internal.rationale[:88]}")
    for other in classification.classifications:
        if other.scheme.value != "internal":
            print(f"  {other.scheme.value:<10} {other.code} @ {other.confidence:.2f} "
                  f"({other.method})")
    del resolved


def _report_normalize(normalized, issues) -> None:
    print(f"\n{'=' * 78}\nNORMALIZE\n{'=' * 78}")
    canonical = sum(1 for v in normalized if v.value_canonical is not None)
    print(f"  canonicalised {canonical}/{len(normalized)} values")
    blocking = [i for i in issues if i.is_blocking]
    if blocking:
        print(f"  {len(blocking)} blocking issue(s):")
        for issue in blocking:
            print(f"    [{issue.layer.value}] {issue.rule_id}: {issue.reason}")
    warnings = [i for i in issues if not i.is_blocking and i.verdict.value == "warn"]
    for issue in warnings:
        print(f"    warn [{issue.layer.value}] {issue.rule_id}: {issue.reason}")

    print(f"\n  {'attribute':<24} {'canonical':<28} display")
    print(f"  {'-' * 74}")
    for value in sorted(normalized, key=lambda v: v.attribute_code):
        canonical_text = "-" if value.value_canonical is None else str(value.value_canonical)
        print(
            f"  {value.attribute_code:<24} {canonical_text[:27]:<28} "
            f"{value.value_display or '-'}"
        )


def _report_validate(report, record, brand) -> None:
    print(f"\n{'=' * 78}\nVALIDATE (L0-L3)\n{'=' * 78}")
    if brand is not None:
        status = f"{brand.brand.name} ({brand.method})" if brand.resolved else "UNRESOLVED"
        print(f"  brand      {status}")
        print(f"  mpn        {record.mpn} -> {record.mpn_normalized}")

    summary = report.summary()
    print(
        f"  checks {summary['checks']} | failures {summary['failures']} | "
        f"warnings {summary['warnings']} | skipped rules {summary['skipped_rules']}"
    )
    print(f"  consistency {summary['consistency']:.1%}")

    if report.failures:
        print("\n  FAILURES:")
        for result in report.failures:
            print(f"    [{result.layer.value}] {result.rule_id}")
            print(f"      {result.reason}")
            if result.counterexample:
                print(f"      counterexample: {result.counterexample}")
            if result.suggested_fix:
                print(f"      fix: {result.suggested_fix}")

    if report.warnings:
        print("\n  WARNINGS:")
        for result in report.warnings:
            print(f"    [{result.layer.value}] {result.rule_id}: {result.reason[:88]}")

    if report.skipped_rules:
        print("\n  RULES NOT EVALUATED (missing dependencies, not passes):")
        for rule_id, why in sorted(report.skipped_rules.items()):
            print(f"    {rule_id}: {why}")


def _report(artifact, parsed, result, registry, class_code) -> None:
    print(f"\n{'=' * 78}\nINGEST\n{'=' * 78}")
    print(f"  document   {artifact.document.document_id}")
    print(f"  sha256     {artifact.sha256[:16]}...")
    print(f"  type       {artifact.document.doc_type.value}")
    print(f"  size       {artifact.size_bytes:,} bytes")
    cached = "yes (identical bytes already stored)" if artifact.was_already_stored else "no"
    print(f"  cached     {cached}")

    print(f"\n{'=' * 78}\nPARSE\n{'=' * 78}")
    print(f"  parser     {parsed.parser}")
    print(f"  pages      {parsed.page_count}")
    print(f"  lines      {len(parsed.all_lines())}")
    tables = parsed.all_tables()
    print(f"  tables     {len(tables)}")
    for table in tables:
        print(f"    {table.table_id}: {table.row_count} rows x {table.col_count} cols "
              f"| header: {' | '.join(table.header[:4])}")
    for warning in parsed.warnings:
        print(f"  WARNING    {warning}")

    print(f"\n{'=' * 78}\nEXTRACT\n{'=' * 78}")
    summary = result.summary()
    print(
        f"  requested {summary['requested']} | values {summary['values']} | "
        f"gaps {summary['gaps']} | rejected {summary['rejected_unverifiable']}"
    )
    print(
        f"  tokens {summary['input_tokens']}/{summary['output_tokens']} | "
        f"escalations {summary['escalations']} | {summary['latency_ms']} ms"
    )
    if result.response:
        print(f"  model      {result.response.model_id} ({result.response.tier})")
    print(f"  prompt     {result.prompt_version} | schema {result.schema_version}")
    print(f"  citation coverage {summary['citation_coverage']:.1%}")

    print(f"\n  {'attribute':<24} {'value':<30} citation")
    print(f"  {'-' * 76}")
    for value in sorted(result.values, key=lambda v: v.attribute_code):
        span = value.evidence[0]
        citation = f"p.{span.page}"
        if span.table_ref:
            citation += f" {span.table_ref}"
        citation += f" ({span.match_score:.2f})"
        print(f"  {value.attribute_code:<24} {str(value.value_raw)[:29]:<30} {citation}")

    required = set(registry.required_codes(class_code))
    if result.gaps:
        print(f"\n  gaps ({sum(1 for g in result.gaps if g.is_required)} required):")
        for gap in sorted(result.gaps, key=lambda g: (not g.is_required, g.attribute_code)):
            flag = "REQ" if gap.attribute_code in required else "   "
            action = gap.recommended_action.value if gap.recommended_action else "-"
            print(f"    {flag} {gap.attribute_code:<24} {gap.reason.value:<28} -> {action}")
            if gap.detail:
                print(f"        {gap.detail[:100]}")

    if result.rejected:
        print("\n  REJECTED CLAIMS (quote not locatable in source):")
        for claim in result.rejected:
            print(f"    {claim.attribute_code}: {claim.value_raw!r}")
            print(f"      claimed quote: {claim.evidence_quote!r}")

    populated = [v.attribute_code for v in result.values]
    completeness = registry.completeness(class_code, populated)
    print(f"\n  weighted completeness (required only): {completeness:.1%}")


def _as_dict(artifact, parsed, result, record=None, report=None) -> dict:
    payload = {
        "document": {
            "id": artifact.document.document_id,
            "sha256": artifact.sha256,
            "pages": parsed.page_count,
            "tables": len(parsed.all_tables()),
            "parser": parsed.parser,
        },
        "extraction": result.summary(),
        "values": [
            {
                "code": v.attribute_code,
                "value_raw": v.value_raw,
                "value_canonical": (
                    v.value_canonical.model_dump()
                    if hasattr(v.value_canonical, "model_dump")
                    else v.value_canonical
                ),
                "value_display": v.value_display,
                "method": v.method.value,
                "confidence": v.confidence,
                "evidence": [
                    {
                        "page": s.page,
                        "table_ref": s.table_ref,
                        "quote": s.quote,
                        "verified": s.quote_verified,
                        "match_score": s.match_score,
                    }
                    for s in v.evidence
                ],
            }
            for v in (record.current_values() if record else result.values)
        ],
        "gaps": [g.to_certificate_entry() for g in result.gaps],
    }
    if report is not None:
        payload["validation"] = {
            **report.summary(),
            "failures": [
                {
                    "layer": r.layer.value,
                    "rule": r.rule_id,
                    "reason": r.reason,
                    "counterexample": r.counterexample,
                }
                for r in report.failures
            ],
            "skipped_rules": report.skipped_rules,
        }
    return payload


if __name__ == "__main__":
    sys.exit(main())
