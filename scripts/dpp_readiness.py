"""Digital Product Passport readiness: how far is this catalogue from the regulation?

Tier 3, item 22. Blueprint Part 3.3 makes the timing argument — the ESPR central registry was
scheduled for July 2026, batteries carry mandatory passports from February 2027, and further product
groups phase in through 2030.

What this reports is deliberately not a passport. It is the **distance to one**, with the two kinds
of shortfall kept apart:

    data gap    -> the profile maps this field to an attribute and no value was extracted.
                   Re-run the pipeline.
    schema gap  -> nothing in the attribute dictionary can express this field at all.
                   Re-running the pipeline will never help. Go and talk to the supplier.

Collapsing those into one percentage is what makes a compliance dashboard useless, so they are
reported as two numbers and the second is never presented without the first.

    # the whole catalogue
    python scripts/dpp_readiness.py

    # one SKU, field by field, with the payload it would carry
    python scripts/dpp_readiness.py --sku BA-100-075 --payload

    # persist: data/dpp/{sku}.json, plus evals/dpp.json for the sweep
    python scripts/dpp_readiness.py --write

No model calls and no network — the profile is YAML, the records are on disk, and the rest is
lookup. Free, offline, reproducible bit-for-bit.

**Which records.** By default the golden corpus, whose values are hand-authored; `measured: false`
travels in the payload so a reader is never left to assume otherwise. ``--from-bundles`` uses real
pipeline output instead.

Exit codes: 0 on success, 2 on a bad invocation, 3 when ``--require`` is not met.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom.compliance import (
    PassportProfile,
    assess_readiness,
    format_readiness,
    format_readiness_sweep,
    sweep_readiness,
)
from axiom.resolve import load_catalogue
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = REPO_ROOT / "data" / "console"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "dpp"
DEFAULT_SWEEP_OUT = REPO_ROOT / "evals" / "dpp.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sku", help="assess one record in full instead of the catalogue")
    parser.add_argument(
        "--payload",
        action="store_true",
        help="also print the passport payload as far as it can honestly be assembled",
    )
    parser.add_argument(
        "--from-bundles",
        action="store_true",
        help="use real pipeline output from data/console rather than the golden corpus",
    )
    parser.add_argument("--bundles", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument(
        "--require",
        type=float,
        default=None,
        metavar="SHARE",
        help=(
            "exit 3 unless mean readiness over the fields the schema can express reaches this "
            "share, e.g. 0.8. Scoped to addressable fields on purpose: gating on the headline "
            "figure would fail a build for a regulation nobody can satisfy yet."
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="persist the result")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args()

    registry = load_default()

    try:
        profile = PassportProfile.load_default()
    except (OSError, ValueError) as exc:
        print(f"could not load the DPP profile: {exc}", file=sys.stderr)
        return 2

    unknown = profile.check_against(registry)
    if unknown:
        print(
            f"the DPP profile references attributes the schema does not define: "
            f"{', '.join(unknown)}",
            file=sys.stderr,
        )
        return 2

    catalogue = load_catalogue(
        registry, bundles=args.bundles, prefer_bundles=args.from_bundles
    )
    if not len(catalogue):
        print("no records to assess", file=sys.stderr)
        return 2

    if args.sku:
        return _run_single(args.sku, catalogue, registry, profile, args)
    return _run_sweep(catalogue, registry, profile, args)


def _run_sweep(catalogue, registry, profile, args) -> int:
    reports = [
        assess_readiness(record, registry, profile) for record in catalogue.records
    ]
    result = sweep_readiness(
        reports,
        source_note=catalogue.source.note,
        measured=catalogue.source.is_measured,
    )
    payload = _envelope(catalogue, result.to_dict())

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_readiness_sweep(result))

    code = _persist(payload, args.out or DEFAULT_SWEEP_OUT, args, label="the sweep")
    if code:
        return code
    return _gate(result.mean_addressable_readiness, args)


def _run_single(sku: str, catalogue, registry, profile, args) -> int:
    record = catalogue.get(sku)
    if record is None:
        print(
            f"'{sku}' is not in this catalogue. Available: {', '.join(catalogue.skus())}",
            file=sys.stderr,
        )
        return 2

    report = assess_readiness(record, registry, profile)
    body = report.to_dict()
    if args.payload:
        body["payload"] = report.payload()
    envelope = _envelope(catalogue, body)

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        print(format_readiness(report))
        if args.payload:
            print("")
            print("  PASSPORT PAYLOAD")
            print(json.dumps(report.payload(), indent=2, default=str))

    code = _persist(envelope, args.out or DEFAULT_OUT_DIR / f"{sku}.json", args, label="the report")
    if code:
        return code
    return _gate(report.addressable_readiness, args)


def _gate(value: float, args) -> int:
    if args.require is None:
        return 0
    if value + 1e-9 < args.require:
        print(
            f"\nFAIL: addressable readiness {value:.1%} is below the required "
            f"{args.require:.1%}.",
            file=sys.stderr,
        )
        return 3
    return 0


def _envelope(catalogue, report: dict) -> dict:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "catalogue": catalogue.summary(),
        "report": report,
    }


def _persist(payload: dict, out: Path, args, *, label: str) -> int:
    if not args.write:
        print(f"\n(dry run — pass --write to persist {label})")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\n  written: {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
