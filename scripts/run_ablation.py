"""Measure what the trust layer is worth, by turning it off.

Every claim in this project rests on one assertion: that refusing to publish unverifiable values
is worth the coverage it costs. This script is the only thing that turns that assertion into a
number.

It runs the golden set twice against the **same model, same prompt, same documents**:

*   **axiom** — the evidence contract enforced. A value whose quote cannot be located in the
    source is discarded and recorded as a gap.
*   **no-evidence-contract** — the control. The same model output, but unsupported values are
    kept, which is what a generic enrichment pipeline publishes.

The only difference between the arms is the gate. That matters: comparing against a different
model or a different prompt would confound the measurement, and the interesting question is not
"is our model better" but "does checking the work change the outcome".

Both arms are scored with the same five-outcome scorer, because binary right/wrong would hide
the whole effect — a confident wrong answer and an honest abstention both collapse to "not
correct", which is precisely the distinction being measured.

    $env:AWS_PROFILE = "axiom"
    python scripts/run_ablation.py
    python scripts/run_ablation.py --json --out evals/ablation.json

Note this costs two full passes over the golden set in Bedrock tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

from axiom.confidence import Calibrator, Priors  # noqa: E402
from axiom.evaluation import GoldenSet, run_backtest  # noqa: E402
from axiom.extract import BedrockModelClient, ModelCascade, PriceTable  # noqa: E402
from axiom.schema import load_default  # noqa: E402

DEFAULT_GOLDEN = REPO_ROOT / "data" / "golden" / "pvf_valves.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--calibration-dir", type=Path, default=REPO_ROOT / "data" / "calibration")
    parser.add_argument("--tier", default="volume")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None, help="write the comparison as JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    golden = GoldenSet.load(args.golden)
    registry = load_default()
    cascade = ModelCascade.load()
    client = BedrockModelClient(region=cascade.region, profile=args.profile)

    calibrator = _load_calibrator(args.calibration_dir)
    priors = _load_priors(args.calibration_dir)

    print(
        f"golden set {golden.name!r}: {len(golden.products)} products. "
        f"Running two arms; this makes two full passes over the set."
    )

    arms = {}
    for label, enforce in (("axiom", True), ("no-evidence-contract", False)):
        print(f"\n--- arm: {label} (enforce_evidence={enforce}) ---")
        result = run_backtest(
            golden,
            registry,
            client,
            cascade,
            calibrator=calibrator,
            priors=priors,
            start_tier=args.tier,
            limit=args.limit,
            enforce_evidence=enforce,
        )
        arms[label] = result
        print(
            f"    {result.products_run} products, {result.metrics.total} comparisons, "
            f"{result.duration_seconds:.1f}s"
        )
        for failure in result.failures:
            print(f"    FAILED {failure}", file=sys.stderr)

    comparison = _compare(arms["axiom"], arms["no-evidence-contract"], cascade)
    _report(comparison, arms, cascade)

    if args.json:
        print("\n" + json.dumps(comparison, indent=2))

    if args.out:
        target = args.out if args.out.is_absolute() else Path.cwd() / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
        print(f"\nwrote {_display_path(target)}")

    return 0


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise. Never raises on an outside path."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _load_calibrator(directory: Path) -> Calibrator:
    path = directory / "calibrator.json"
    return Calibrator.load(path) if path.exists() else Calibrator()


def _load_priors(directory: Path) -> Priors:
    path = directory / "priors.json"
    return Priors.load(path) if path.exists() else Priors()


def _compare(treatment, control, cascade) -> dict:
    """Build the side-by-side. Treatment is AXIOM; control has the contract disabled."""
    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None

    def arm(result) -> dict:
        summary = result.summary()
        return {
            "arm": result.arm,
            "enforce_evidence": result.enforce_evidence,
            "products": result.products_run,
            "comparisons": result.metrics.total,
            "correct": summary.get("correct"),
            "wrong_value": summary.get("wrong_value"),
            "missed": summary.get("missed"),
            "correctly_abstained": summary.get("correctly_abstained"),
            "hallucinated": summary.get("hallucinated"),
            "precision": summary.get("precision"),
            "recall": summary.get("recall"),
            "f1": summary.get("f1"),
            "hallucination_rate": summary.get("hallucination_rate"),
            "citation_coverage": summary.get("citation_coverage"),
            "cost_usd": result.usage.cost_usd(tier_prices),
            "duration_seconds": round(result.duration_seconds, 2),
        }

    treatment_arm, control_arm = arm(treatment), arm(control)

    # Values the control published that AXIOM withheld, and what happened to them. This is the
    # substance of the trade: coverage bought at the price of correctness, quantified.
    treatment_by_key = {
        (c.sku, c.attribute_code): c for c in treatment.metrics.comparisons
    }
    withheld_but_right = 0
    withheld_and_wrong = 0
    for comparison in control.metrics.comparisons:
        counterpart = treatment_by_key.get((comparison.sku, comparison.attribute_code))
        if counterpart is None:
            continue
        control_answered = comparison.actual is not None
        treatment_answered = counterpart.actual is not None
        if control_answered and not treatment_answered:
            if comparison.outcome.name == "CORRECT":
                withheld_but_right += 1
            else:
                withheld_and_wrong += 1

    return {
        "golden_set": treatment.golden_set,
        "treatment": treatment_arm,
        "control": control_arm,
        "delta": {
            key: _delta(treatment_arm.get(key), control_arm.get(key))
            for key in (
                "correct",
                "wrong_value",
                "missed",
                "hallucinated",
                "precision",
                "recall",
                "f1",
                "citation_coverage",
            )
        },
        "withheld_by_the_contract": {
            "would_have_been_correct": withheld_but_right,
            "would_have_been_wrong_or_fabricated": withheld_and_wrong,
        },
    }


def _delta(treatment, control):
    if treatment is None or control is None:
        return None
    return round(treatment - control, 4)


def _report(comparison: dict, arms: dict, cascade) -> None:
    treatment, control = comparison["treatment"], comparison["control"]
    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None

    print("\n" + "=" * 78)
    print(f"ABLATION — {comparison['golden_set']}")
    print("=" * 78)
    print("  Same model, same prompt, same documents. The only difference is whether a")
    print("  value without a locatable quote is discarded or published.\n")

    rows = [
        ("correct", "correct"),
        ("wrong value", "wrong_value"),
        ("missed", "missed"),
        ("hallucinated", "hallucinated"),
    ]
    print(f"  {'outcome':<24} {'AXIOM':>10} {'control':>10} {'delta':>10}")
    print("  " + "-" * 56)
    for label, key in rows:
        print(
            f"  {label:<24} {_fmt(treatment[key]):>10} {_fmt(control[key]):>10} "
            f"{_fmt(comparison['delta'][key], signed=True):>10}"
        )

    print()
    for label, key in (
        ("precision", "precision"),
        ("recall", "recall"),
        ("F1", "f1"),
        ("citation coverage", "citation_coverage"),
    ):
        print(
            f"  {label:<24} {_pct(treatment[key]):>10} {_pct(control[key]):>10} "
            f"{_fmt(comparison['delta'][key], signed=True):>10}"
        )

    withheld = comparison["withheld_by_the_contract"]
    total_withheld = (
        withheld["would_have_been_correct"] + withheld["would_have_been_wrong_or_fabricated"]
    )
    print("\n  THE TRADE")
    print(f"    values the contract withheld    {total_withheld}")
    print(f"      of those, actually correct    {withheld['would_have_been_correct']}")
    print(f"      of those, wrong or invented   {withheld['would_have_been_wrong_or_fabricated']}")

    if total_withheld == 0:
        print(
            "\n    The contract withheld nothing on this set: every value the model produced"
            "\n    could be located in its source. That is a clean result and also a weak"
            "\n    test — a harder corpus, with scanned PDFs or values stated only in prose,"
            "\n    would exercise the gate properly."
        )
    else:
        precision_gain = comparison["delta"]["precision"]
        print(
            f"\n    Enforcing evidence cost {withheld['would_have_been_correct']} correct "
            f"value(s) and prevented "
            f"{withheld['would_have_been_wrong_or_fabricated']} bad one(s)."
        )
        if precision_gain is not None:
            print(f"    Precision moved {precision_gain:+.4f}.")

    for label, result in arms.items():
        cost = result.usage.cost_usd(tier_prices)
        tokens = f"{result.usage.input_tokens}/{result.usage.output_tokens}"
        print(f"\n  {label}: {result.usage.calls} calls, tokens {tokens}", end="")
        print(f", cost ${cost:.4f}" if cost is not None else ", cost unavailable")

    print(
        "\n  Caveat: one golden set of "
        f"{treatment['products']} products, authored by the same person who wrote the"
        "\n  fixtures. It measures the gate, not the model, and it is far too small to"
        "\n  generalise from."
    )


def _fmt(value, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:+.4f}" if signed else f"{value:.4f}"
    return f"{value:+d}" if signed else str(value)


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
