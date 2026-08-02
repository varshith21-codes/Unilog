"""Run the masked-attribute backtest and write the calibration artifacts.

This is the script that turns claims into numbers. It hides known-correct values, runs the
pipeline against the source documents alone, scores what comes back, and writes the calibration
set that lets risk-controlled auto-accept operate at all.

    $env:AWS_PROFILE = "axiom"
    python scripts/run_backtest.py --write

Without ``--write`` it reports and changes nothing, which is the right default for a script
whose output governs what gets published automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.confidence import Calibrator, Priors
from axiom.evaluation import (
    GoldenSet,
    format_report,
    run_backtest,
    write_calibration_artifacts,
)
from axiom.extract import BedrockModelClient, ModelCascade
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--golden",
        type=Path,
        default=REPO_ROOT / "data" / "golden" / "pvf_valves.yaml",
    )
    parser.add_argument(
        "--calibration-dir", type=Path, default=REPO_ROOT / "data" / "calibration"
    )
    parser.add_argument("--tier", default="volume")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--write",
        action="store_true",
        help="persist calibration_set.json and priors.json (governs auto-accept)",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--detail", action="store_true", help="list every non-correct comparison"
    )
    args = parser.parse_args()

    registry = load_default()
    golden = GoldenSet.load(args.golden)
    cascade = ModelCascade.load()
    client = BedrockModelClient(region=cascade.region, profile=args.profile)

    calibrator = Calibrator()
    priors = Priors()
    if (path := args.calibration_dir / "calibrator.json").exists():
        calibrator = Calibrator.load(path)
    if (path := args.calibration_dir / "priors.json").exists():
        priors = Priors.load(path)

    print(
        f"golden set '{golden.name}': {len(golden)} products, "
        f"{golden.comparison_count} comparisons "
        f"({golden.absent_count} of them known-absent)\n"
    )

    result = run_backtest(
        golden,
        registry,
        client,
        cascade,
        calibrator=calibrator,
        priors=priors,
        start_tier=args.tier,
        store_root=REPO_ROOT / "data" / "cache" / "artifacts",
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(result.summary(), indent=2))
    else:
        print(format_report(result))

    if args.detail:
        _print_detail(result)

    if args.write:
        written = write_calibration_artifacts(result, args.calibration_dir)
        print("\nwrote:")
        for name, path in written.items():
            print(f"  {name}: {path.relative_to(REPO_ROOT)}")
        print(
            "\nRe-run scripts/run_pipeline.py to see auto-accept operate against the "
            "measured error budget."
        )
    else:
        print("\n(dry run — pass --write to persist calibration artifacts)")

    # A hallucination is a hard failure. Everything else is a number to improve.
    return 1 if result.metrics.hallucinated else 0


def _print_detail(result) -> None:
    from axiom.evaluation import Outcome

    problems = [c for c in result.metrics.comparisons if c.outcome is not Outcome.CORRECT]
    problems = [c for c in problems if c.outcome is not Outcome.CORRECTLY_ABSTAINED]
    if not problems:
        print("\n  no incorrect comparisons")
        return

    print(f"\n  {len(problems)} comparison(s) needing attention:")
    for comparison in sorted(problems, key=lambda c: (c.outcome.value, c.sku)):
        print(f"    [{comparison.outcome.value}] {comparison.sku} / {comparison.attribute_code}")
        print(f"        expected: {comparison.expected!r}")
        print(f"        actual:   {comparison.actual!r}")
        if comparison.detail:
            print(f"        {comparison.detail}")


if __name__ == "__main__":
    sys.exit(main())
