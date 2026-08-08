"""Cross-reference and equivalence: what else will do when the part they wanted is out of stock?

Tier 3, item 20. Compatibility judged on **normalised specification values under declared
interchange semantics**, not on text similarity — "bronze ball valve 3/4 NPT 600WOG" and "bronze
ball valve 3/4 NPT 400WOG" are nearly identical strings and one of them fails at 500 psi.

    # rank every substitute for one part
    python scripts/cross_reference.py --sku BA-100-100

    # one directional pair, in full
    python scripts/cross_reference.py --sku 77C-105R --against 77C-105

    # every ordered pair in the catalogue, with the asymmetry evidence
    python scripts/cross_reference.py --sweep

    # persist: data/equivalence/{sku}.json for the console, evals/equivalence.json for a sweep
    python scripts/cross_reference.py --sku BA-100-100 --write
    python scripts/cross_reference.py --sweep --write

No model calls and no documents. Every verdict is arithmetic and lookup over values already on
disk, so this is free, offline and reproducible bit-for-bit.

**Which catalogue.** By default the golden corpus, because fifteen SKUs across three manufacturers
is the only set here wide enough for a ranked substitute list to mean anything — but its values are
hand-authored, so those verdicts exercise the comparison logic rather than the extraction that
feeds it in production. ``--from-bundles`` uses real pipeline output instead, and falls back with
the reason recorded when too few SKUs have been through the pipeline. The distinction travels in
the payload as ``source`` and ``measured``, so a reader is never left to assume.

**Direction matters.** ``--sku A --against B`` asks whether B can replace A, which is a different
question from whether A can replace B, and the two routinely have different answers.

Exit codes: 0 on success, 2 on a bad invocation or an unknown SKU.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom.resolve import (
    Catalogue,
    cross_reference,
    equivalence,
    format_cross_reference,
    format_equivalence,
    format_sweep,
    load_catalogue,
    sweep,
)
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = REPO_ROOT / "data" / "console"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "equivalence"
DEFAULT_SWEEP_OUT = REPO_ROOT / "evals" / "equivalence.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sku", help="the reference part: what the customer asked for")
    parser.add_argument(
        "--against",
        help="a single candidate. Asks whether it can replace --sku, not the reverse.",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="compare every ordered pair in the catalogue instead of one reference",
    )
    parser.add_argument(
        "--from-bundles",
        action="store_true",
        help=(
            "use real pipeline output from data/console rather than the golden corpus. Falls "
            "back to the corpus, with the reason recorded, when too few bundles exist"
        ),
    )
    parser.add_argument("--bundles", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument(
        "--limit", type=int, default=None, help="cap the number of candidates reported"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="persist the result")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args()

    if not args.sweep and not args.sku:
        print(
            "pass --sku to rank substitutes for a part, or --sweep for every pair",
            file=sys.stderr,
        )
        return 2
    if args.against and not args.sku:
        print("--against needs --sku to compare against", file=sys.stderr)
        return 2

    registry = load_default()
    catalogue = load_catalogue(
        registry, bundles=args.bundles, prefer_bundles=args.from_bundles
    )
    if len(catalogue) < 2:
        print(
            f"a cross-reference needs at least two records; the catalogue holds "
            f"{len(catalogue)}. Produce pipeline output first:\n"
            f"  python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075 "
            f"--include-optional --save-session",
            file=sys.stderr,
        )
        return 2

    if args.sweep:
        return _run_sweep(catalogue, registry, args)
    return _run_reference(catalogue, registry, args)


def _run_sweep(catalogue: Catalogue, registry, args) -> int:
    result = sweep(catalogue, registry)
    payload = _envelope(catalogue, result.to_dict())

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_sweep(result))

    out = args.out or DEFAULT_SWEEP_OUT
    return _persist(payload, out, args, label="the sweep")


def _run_reference(catalogue: Catalogue, registry, args) -> int:
    if catalogue.get(args.sku) is None:
        print(
            f"'{args.sku}' is not in this catalogue. Available: "
            f"{', '.join(catalogue.skus()) or 'none'}",
            file=sys.stderr,
        )
        return 2

    if args.against:
        if catalogue.get(args.against) is None:
            print(
                f"'{args.against}' is not in this catalogue. Available: "
                f"{', '.join(catalogue.skus())}",
                file=sys.stderr,
            )
            return 2
        report = equivalence(
            catalogue.get(args.sku), catalogue.get(args.against), registry
        )
        payload = _envelope(catalogue, report.to_dict())
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(format_equivalence(report))
        out = args.out or DEFAULT_OUT_DIR / f"{args.sku}.json"
        return _persist(payload, out, args, label="the verdict")

    result = cross_reference(args.sku, catalogue, registry, limit=args.limit)
    payload = _envelope(catalogue, result.to_dict())

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_cross_reference(result))

    out = args.out or DEFAULT_OUT_DIR / f"{args.sku}.json"
    return _persist(payload, out, args, label="the cross-reference")


def _envelope(catalogue: Catalogue, report: dict) -> dict:
    """Wrap a report with the provenance of the catalogue it was computed over.

    Same envelope shape ``cross_validate.py --save`` writes, and for the same reason: the console
    joins this onto a bundle at read time and has to be able to say in prose whether the values
    behind a verdict were measured or hand-authored.
    """
    # The catalogue's own summary already travels inside `report` (both CrossReference.summary
    # and Sweep.summary spread it), so source, measured, records and skus are not repeated here.
    # One number, one name.
    return {
        "generated_at": datetime.now(UTC).isoformat(),
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
