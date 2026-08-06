"""The CI regression gate: fail the build if the measured numbers got worse.

Module M15, and the blueprint rates it above shipping another feature. The reason is narrow and
correct: every accuracy figure in the README is one careless prompt edit away from being false,
and no unit test in this repository would notice. The test suite proves the code does what it
says. This proves the *pipeline* still performs as well as it did.

    # measure, then judge
    $env:AWS_PROFILE = "axiom"
    python scripts/run_backtest.py --json > evals/candidate.json
    python scripts/check_regression.py evals/candidate.json

    # after a deliberate improvement, hold the gain
    python scripts/check_regression.py evals/candidate.json --update

Exit codes are what CI reads: ``0`` clean, ``1`` a metric regressed or could not be compared,
``2`` the invocation itself was wrong. A metric missing from the candidate report counts as a
failure rather than a skip — a gate that can be defeated by deleting a number from a JSON file
is not a gate.

**On rebaselining.** ``--update`` is a decision, not a formality, so it refuses to write a
*lower* number without ``--accept-regression`` on top. That combination is the audit trail: a
diff that lowers a metric is visible in review, whereas quietly editing evals/baseline.json to
match a worse run is the one change that silently voids every claim the README makes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom.evaluation import (
    TRACKED,
    Direction,
    check_regression,
    format_regression_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "evals" / "baseline.json"

# Keys copied into a refreshed baseline. Everything else in a backtest summary is run-specific
# noise — wall-clock duration and token counts vary with network conditions and would make every
# rebaseline a large, unreadable diff.
PROVENANCE_KEYS = (
    "golden_set",
    "arm",
    "enforce_evidence",
    "prompt_version",
    "model_ids",
    "products",
    "comparisons",
    "calibration_samples",
    "model_calls",
)

COUNT_KEYS = ("correct", "wrong_value", "missed", "correctly_abstained", "hallucinated")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candidate",
        type=Path,
        help="backtest summary to judge, from `run_backtest.py --json`",
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update",
        action="store_true",
        help="write the candidate's metrics into the baseline, holding an improvement",
    )
    parser.add_argument(
        "--accept-regression",
        action="store_true",
        help=(
            "required alongside --update to record a metric that got *worse*. Separate flag on "
            "purpose: lowering a baseline should be a visible decision, not a side effect."
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    candidate = _load(args.candidate, "candidate")
    if candidate is None:
        return 2

    # A missing baseline is a hard failure, not an implicit pass. On a fresh checkout the gate
    # must complain loudly rather than wave through the first run as "nothing to compare".
    if not args.baseline.is_file():
        print(
            f"no baseline at {args.baseline}. The gate cannot pass without one — record the "
            f"current measurement first:\n"
            f"  python scripts/check_regression.py {args.candidate} --baseline "
            f"{args.baseline} --update",
            file=sys.stderr,
        )
        return 1

    baseline = _load(args.baseline, "baseline")
    if baseline is None:
        return 2

    report = check_regression(baseline, candidate)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_regression_report(report))

    if args.update:
        code = _update(args.baseline, baseline, candidate, report, args.accept_regression)
        if code:
            return code

    if report.passed:
        return 0

    if not args.json:
        print(
            "  The gate blocks this change. Either fix the regression, or — if the loss is\n"
            "  intended and understood — record it deliberately with:\n"
            f"    python scripts/check_regression.py {args.candidate} --update "
            f"--accept-regression\n",
            file=sys.stderr,
        )
    return 1


def _load(path: Path, label: str) -> dict | None:
    if not path.is_file():
        print(f"no {label} file at {path}", file=sys.stderr)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"{label} at {path} is not valid JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict):
        print(f"{label} at {path} is not a JSON object", file=sys.stderr)
        return None
    return payload


def _update(
    path: Path,
    baseline: dict,
    candidate: dict,
    report,
    accept_regression: bool,
) -> int:
    """Rewrite the baseline from the candidate, refusing to silently lower a metric."""
    if report.fatal and not accept_regression:
        for reason in report.fatal:
            print(f"refusing to update: {reason}", file=sys.stderr)
        print(
            "\nIf the corpus genuinely changed, that is a new baseline rather than an updated "
            "one — pass --accept-regression to confirm you intend to replace it.",
            file=sys.stderr,
        )
        return 2

    lowered = [c for c in report.comparisons if c.verdict.blocks]
    if lowered and not accept_regression:
        print(
            "refusing to update: that would write a worse number into the baseline.",
            file=sys.stderr,
        )
        for comparison in lowered:
            print(f"  {comparison.guard.metric}: {comparison.reason}", file=sys.stderr)
        print(
            "\nPass --accept-regression as well if the loss is understood and intended. The "
            "two-flag requirement exists so the diff is a decision somebody reviewed.",
            file=sys.stderr,
        )
        return 2

    tracked = {guard.metric for guard in TRACKED}
    payload: dict[str, object] = {}

    # The comment block is the file's own documentation and survives a rebaseline. Losing it
    # would leave a future reader with no idea these are measurements rather than targets.
    if "_comment" in baseline:
        payload["_comment"] = baseline["_comment"]

    payload["measured_at"] = datetime.now(UTC).date().isoformat()
    if sha := _git_sha():
        payload["git_sha"] = sha

    for key in PROVENANCE_KEYS:
        if key in candidate:
            payload[key] = candidate[key]
    for key in COUNT_KEYS:
        if key in candidate:
            payload[key] = candidate[key]
    for metric in sorted(tracked):
        if metric in candidate and metric not in payload:
            payload[metric] = candidate[metric]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\n  baseline updated: {path.relative_to(REPO_ROOT)}")
    moved = [c for c in report.comparisons if c.delta not in (None, 0.0)]
    for comparison in moved:
        arrow = "improved" if comparison.verdict.name == "IMPROVED" else "changed"
        print(
            f"    {comparison.guard.metric}: {comparison.baseline:g} -> "
            f"{comparison.candidate:g} ({arrow})"
        )
    print("  Commit it in the same change as the code that moved the numbers.")
    return 0


def _git_sha() -> str | None:
    """The commit these numbers were measured at, when it can be determined.

    Best-effort: the gate has to work in a tarball with no git history, so failure is silent.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def describe_guards() -> str:
    """The tracked set, for documentation and for `--help` readers."""
    lines = ["Tracked metrics:", ""]
    for guard in TRACKED:
        arrow = "higher is better" if guard.direction is Direction.HIGHER_IS_BETTER else (
            "lower is better"
        )
        bound = (
            f", absolute bound {guard.absolute_bound:g}"
            if guard.absolute_bound is not None
            else ""
        )
        lines.append(
            f"  {guard.metric:<24} {arrow}, tolerance {guard.tolerance:g}{bound}\n"
            f"      {guard.rationale}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
