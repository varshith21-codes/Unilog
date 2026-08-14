"""Score a delivery file against the client's ground truth.

    python scripts/score_delivery.py `
      data/delivery/Unihack__Sample_Dataset_-_Input.delivery.csv `
      "Unihack_ Expected Output - Delivery Format.csv"

The guide says judges will look for field-level accuracy, character-limit compliance and LOV
conformance, so this is a deliverable rather than a convenience.

It is built to *not flatter*. Three things it does that a naive scorer would not:

* Counts over-filling as a failure. 173 of the client's 252 columns are blank in ground truth, and
  a scorer that only penalised missing cells would reward inventing data.
* Excludes both-empty cells from the accuracy denominator. Including them would report ~90%
  accuracy on this format before a single value had been enriched.
* Prints fractions, not percentages, below 20 observations. Two ground-truth rows cannot support
  "93.3%".

Exit code is 1 when a hard failure is present — an over-filled column or a breached character
limit — so this can gate CI. Missing values do not fail the build: on this dataset they are
expected until the retrieval stage runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

from axiom.delivery import load_default  # noqa: E402
from axiom.delivery.scoring import render_report, score_rows  # noqa: E402

DEFAULT_GROUND_TRUTH = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("actual", type=Path, help="the delivery CSV we produced")
    parser.add_argument(
        "expected",
        type=Path,
        nargs="?",
        default=DEFAULT_GROUND_TRUTH,
        help="ground-truth delivery CSV (default: the client's example in the repo root)",
    )
    parser.add_argument("--json", type=Path, help="also write the machine-readable summary here")
    parser.add_argument(
        "--group", action="append", default=[], help="score only these column groups; repeatable"
    )
    parser.add_argument(
        "--max-failures", type=int, default=25, help="how many failing cells to print"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail on missed values, not just on over-fill and limit breaches",
    )
    args = parser.parse_args()

    for path in (args.actual, args.expected):
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 1

    fmt = load_default()
    expected = _read(args.expected)
    actual = _read(args.actual)

    _check_header(fmt, args.expected, expected)
    _check_header(fmt, args.actual, actual)

    columns = None
    if args.group:
        columns = [c.name for c in fmt.columns if c.group in set(args.group)]
        if not columns:
            print(
                f"no columns in groups {args.group}; known groups: {', '.join(fmt.groups)}",
                file=sys.stderr,
            )
            return 1

    report = score_rows(fmt, expected, actual, columns=columns)
    if not report.rows:
        print(
            "no rows could be joined on Mfg_Part_Num. Ground truth covers "
            f"{len(expected)} part numbers and the output covers {len(actual)}; they do not "
            "intersect.",
            file=sys.stderr,
        )
        return 1

    print(render_report(report, max_failures=args.max_failures))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.summary(), indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return _verdict(report, strict=args.strict)


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _check_header(fmt, path: Path, rows: list[dict[str, str]]) -> None:
    """Warn when a file's columns differ from the contract.

    Not fatal: a caller may legitimately want to score a partial or older file. But a silent
    column mismatch would show up as wholesale inaccuracy with no explanation, so it is said out
    loud before the numbers appear.
    """
    if not rows:
        return
    present = set(rows[0])
    declared = set(fmt.header)
    if missing := declared - present:
        print(
            f"WARNING: {path.name} is missing {len(missing)} contract columns "
            f"(e.g. {', '.join(sorted(missing)[:5])}); they will score as empty",
            file=sys.stderr,
        )
    if extra := present - declared:
        print(
            f"WARNING: {path.name} has {len(extra)} columns the contract does not declare "
            f"(e.g. {', '.join(sorted(extra)[:5])}); they are ignored",
            file=sys.stderr,
        )


def _verdict(report, *, strict: bool) -> int:
    """Hard failures gate the build; soft ones are reported and tolerated."""
    problems: list[str] = []
    if report.overfilled:
        problems.append(
            f"{report.overfilled} cell(s) populated where the client's format is empty"
        )
    if not report.compliant:
        problems.append(f"{report.constraint_violations} character-limit breach(es)")
    if report.unmatched_expected:
        problems.append(
            f"{len(report.unmatched_expected)} ground-truth row(s) produced no output"
        )
    if strict:
        missed = report.counts()
        from axiom.delivery.scoring import Verdict

        if missed[Verdict.MISSED]:
            problems.append(f"{missed[Verdict.MISSED]} missing value(s) (--strict)")

    print()
    if problems:
        print("FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS - no over-fill, no limit breaches, every ground-truth row produced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
