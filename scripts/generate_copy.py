"""Generate product copy from verified facts only, then prove it stayed inside them.

Reads a saved console bundle (written by ``run_pipeline.py --save-session``), builds a fact sheet
from its **publishable** values, generates copy, and runs the deterministic claim check. Copy is
reported as publishable only if every checkable assertion in it traces back to a verified
attribute.

    $env:AWS_PROFILE = "axiom"
    python scripts/generate_copy.py --sku BA-100-075
    python scripts/generate_copy.py --sku BA-100-075 --audit
    python scripts/generate_copy.py --sku BA-100-075 --out data/out

``--audit`` additionally runs a set of deliberately fabricated descriptions through the checker.
That matters more than the generated copy passing: a checker that never rejects anything is
indistinguishable from no checker, and the only way to show it works is to feed it something it
must refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

from axiom.core.product import ProductRecord  # noqa: E402
from axiom.extract import (  # noqa: E402
    BedrockModelClient,
    ModelCascade,
    PriceTable,
    StubModelClient,
)
from axiom.generate import (  # noqa: E402
    ClaimVerdict,
    CopyGenerator,
    build_fact_sheet,
    check_copy,
    load_policy,
)
from axiom.schema import load_default  # noqa: E402

CONSOLE_DIR = REPO_ROOT / "data" / "console"

# Deliberately wrong copy for --audit. Each entry names the failure it should provoke; if the
# checker ever passes one of these, it has stopped working.
FABRICATIONS = [
    (
        "inflated pressure rating",
        "Rated to 1200 psi WOG for the most demanding industrial service.",
    ),
    ("plausible nearby figure", "Rated to 650 psi WOG."),
    ("invented standard", "Conforms to MSS SP-110 and ASME B16.34."),
    ("wrong number on a real standard", "Certified to NSF/ANSI 372 for lead content."),
    ("invented alloy", "Precision-machined Bronze C89833 body."),
    ("unsupported compliance claim", "Lead-free bronze, safe for drinking water systems."),
    ("comparative claim", "The best bronze ball valve in its class."),
    ("promissory claim", "Guaranteed leak-free for the lifetime of the installation."),
    ("extended temperature range", "Operates from -40 degF to 500 degF."),
    ("invented stem grade", "316 stainless steel stem and ball."),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--bundle-dir", type=Path, default=CONSOLE_DIR)
    parser.add_argument("--tier", default="mid")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--audit", action="store_true", help="also try to break the checker")
    parser.add_argument("--no-generate", action="store_true", help="audit only, no model call")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = load_default()
    policy = load_policy()

    record = _load_record(args.bundle_dir, args.sku, registry)
    if record is None:
        return 2

    sheet = build_fact_sheet(record, registry)
    _report_sheet(sheet)

    if not sheet.facts:
        print(
            "\nno publishable attributes: there is nothing verified to write from, so no copy "
            "is generated. This is the correct outcome, not a failure.",
            file=sys.stderr,
        )
        return 1

    result = None
    if not args.no_generate:
        cascade = ModelCascade.load()
        client = (
            StubModelClient(['{"headline":"stub"}'])
            if args.profile == "stub"
            else BedrockModelClient(region=cascade.region, profile=args.profile)
        )
        generator = CopyGenerator(client, cascade, policy, tier=args.tier)
        result = generator.generate(sheet)
        _report_copy(result, cascade)

    if args.audit:
        _report_audit(sheet, policy)

    if args.json and result is not None:
        print("\n" + json.dumps(result.to_dict(), indent=2))

    if args.out and result is not None:
        out_dir = args.out if args.out.is_absolute() else Path.cwd() / args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{args.sku}.copy.json"
        target.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        print(f"\n  wrote {_display_path(target)}")

    if result is not None and not result.published:
        return 1
    return 0


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise. Never raises on an outside path."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _load_record(bundle_dir: Path, sku: str, registry) -> ProductRecord | None:
    """Rebuild a record from a persisted bundle.

    Copy generation reads the same artifact the console does, so it can never be shown facts the
    dashboards do not have.
    """
    path = bundle_dir / f"{sku}.bundle.json"
    if not path.is_file():
        print(
            f"no bundle for {sku!r} at {path}. Generate one with:\n"
            f"  python scripts/run_pipeline.py <source> --sku {sku} --save-session",
            file=sys.stderr,
        )
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    bundle = payload["bundle"]

    record = ProductRecord.model_validate(
        {
            **bundle["record"],
            "attribute_values": bundle["values"],
            "gaps": bundle["gaps"],
            "classifications": bundle["classifications"],
        }
    )
    return record


def _report_sheet(sheet) -> None:
    print("=" * 78)
    print(f"FACT SHEET — {sheet.sku}")
    print("=" * 78)
    print(f"  brand          {sheet.brand or '—'}")
    print(f"  class          {sheet.class_name}")
    print(f"  verified facts {len(sheet.facts)}")
    for fact in sheet.facts:
        flag = "  [compliance]" if fact.is_compliance_claim else ""
        print(f"    {fact.attribute_code:<24} {fact.display}{flag}")

    if sheet.withheld_codes:
        # These exist on the record but are not publishable. The generator never sees them.
        print(f"\n  withheld from the generator ({len(sheet.withheld_codes)}):")
        print(f"    {', '.join(sheet.withheld_codes)}")
        print("    a value awaiting review must not appear in copy that publishes before it")


def _report_copy(result, cascade) -> None:
    print("\n" + "=" * 78)
    print("GENERATED COPY")
    print("=" * 78)

    if result.error:
        print(f"  FAILED: {result.error}")
        return

    print(f"  model          {result.model_id} ({result.model_tier})")
    print(f"  attempts       {result.attempts}")

    prices = PriceTable.load()
    cost = result.usage.cost_usd(prices.tier_prices(cascade) if prices else None)
    if cost is not None:
        print(f"  cost           ${cost:.6f}")

    print(f"\n  {result.headline}\n")
    print(f"  {result.short_description}\n")
    for paragraph in result.long_description.split("\n"):
        if paragraph.strip():
            print(f"  {paragraph.strip()}")
    print()
    for bullet in result.bullets:
        print(f"    - {bullet}")

    _report_claims(result.report)

    print()
    if result.published:
        print("  PUBLISHABLE — every checkable assertion traces to a verified attribute")
    else:
        print("  BLOCKED — copy is not publishable while any claim is unsupported")


def _report_claims(report) -> None:
    summary = report.summary()
    print("\n" + "-" * 78)
    print("CLAIM CHECK")
    print("-" * 78)
    print(
        f"  {summary['claims']} claims — {summary['supported']} supported, "
        f"{summary['unsupported']} unsupported, {summary['banned']} banned"
    )

    for claim in report.claims:
        if claim.verdict is ClaimVerdict.SUPPORTED:
            print(f"    ok    [{claim.kind.value:<11}] {claim.text!r} <- {claim.supported_by}")
    for claim in report.claims:
        if claim.verdict is not ClaimVerdict.SUPPORTED:
            mark = "BANNED" if claim.verdict is ClaimVerdict.BANNED else "FAIL  "
            print(f"    {mark}[{claim.kind.value:<11}] {claim.text!r} — {claim.reason}")


def _report_audit(sheet, policy) -> None:
    """Feed the checker copy it must refuse.

    A checker that never rejects anything is indistinguishable from no checker at all, so its
    value is only demonstrable by trying to get something past it.
    """
    print("\n" + "=" * 78)
    print("CLAIM-CHECK AUDIT — fabricated copy that must be refused")
    print("=" * 78)

    caught = 0
    for label, text in FABRICATIONS:
        report = check_copy({"audit": text}, sheet, policy)
        blocked = [c for c in report.claims if c.verdict.blocks_publication]
        if blocked:
            caught += 1
            reasons = ", ".join(f"{c.text!r}" for c in blocked[:3])
            print(f"  caught  {label:<34} {reasons}")
        else:
            print(f"  MISSED  {label:<34} {text!r}")

    print(f"\n  {caught}/{len(FABRICATIONS)} fabrications refused")
    if caught < len(FABRICATIONS):
        print("  A miss here is a hole in the checker, not a curiosity.")

    honest = (
        f'{sheet.brand or ""} {sheet.sku} bronze ball valve with a full port and NPT '
        f"threaded ends."
    )
    control = check_copy({"audit": honest}, sheet, policy)
    verdict = "passes" if control.passed else "WRONGLY BLOCKED"
    print(f"  control: honest copy {verdict}")


if __name__ == "__main__":
    raise SystemExit(main())
