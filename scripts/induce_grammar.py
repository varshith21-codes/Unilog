"""Induce a part-number grammar from the golden set, and validate it on held-out parts.

Tier 3, item 19. `BA-100-075` is a 3/4" valve from the BA-100 series, and a merchandiser reads
that off the string without opening a datasheet. This learns to do the same from examples instead
of from a hand-written regex per supplier.

    python scripts/induce_grammar.py                       # report only
    python scripts/induce_grammar.py --ablation            # also show what the guard buys
    python scripts/induce_grammar.py --write               # persist evals/grammar.json

No model calls, no documents, no network — the corpus supplies part numbers and ground truth and
everything in between is arithmetic. So unlike the backtest this is free, offline, and
reproducible bit-for-bit, which is why it can run in CI.

**The headline is not the rule count.** Induction always succeeds, and every rule it returns fits
the data it was induced from perfectly — that is what induction is. So the number that means
something is held-out accuracy: hide a part number, induce without it, and see whether the grammar
can reconstruct that part. Leave-one-out, so every SKU takes a turn.

Exit codes: 0 on success, 1 if the grammar fabricated a value on held-out data (a grammar that
invents is worse than no grammar, since it costs nothing and therefore scales), 2 on a bad
invocation or an unreadable corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.evaluation import GoldenSet
from axiom.evaluation.grammar import (
    DEFAULT_MIN_SUPPORT,
    format_comparison,
    format_grammar_report,
    run_held_out,
)
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "evals" / "grammar.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="golden set YAML (defaults to data/golden/pvf_valves.yaml)",
    )
    parser.add_argument(
        "--min-support",
        type=int,
        default=DEFAULT_MIN_SUPPORT,
        help=(
            "how many independent observations must agree on a single token before a memorised "
            "rule is believed. Two is the smallest number that means anything: with one, every "
            "attribute is trivially a function of every varying segment"
        ),
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="also run with the corroboration guard off, and contrast the two",
    )
    parser.add_argument(
        "--no-rules",
        action="store_true",
        help="omit the per-rule inventory from the report",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write", action="store_true", help="persist the result to --out")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args()

    if args.min_support < 1:
        print("--min-support must be at least 1", file=sys.stderr)
        return 2

    try:
        golden = GoldenSet.load(args.golden) if args.golden else GoldenSet.load_default()
    except (OSError, ValueError) as exc:
        print(f"could not load the golden set: {exc}", file=sys.stderr)
        return 2

    registry = load_default()
    result = run_held_out(golden, registry, min_support=args.min_support)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(format_grammar_report(result, show_rules=not args.no_rules))

    if args.ablation:
        unguarded = run_held_out(golden, registry, min_support=1)
        if not args.json:
            print()
            print(format_comparison(result, unguarded))

    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result.to_dict(), indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"\n  written: {args.out.relative_to(REPO_ROOT)}")
    else:
        print("\n(dry run — pass --write to persist the result to evals/)")

    # A fabricated value is the one outcome that must fail the build. Everything a grammar
    # produces is free and unattended, so a fabrication rate above zero would scale faster than
    # any review capacity could absorb it.
    if result.metrics.hallucinated:
        print(
            f"\nFAIL: the grammar produced {result.metrics.hallucinated} value(s) for attributes "
            f"the golden set records as absent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
