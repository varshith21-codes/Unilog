"""Spec drift: the supplier reissued the datasheet — which records are now wrong?

Tier 3, item 24. Compares one source document against its own earlier revision and classifies
every attribute change by direction, because the direction decides the urgency:

    a rating that FELL   -> every page built on the old revision now overclaims
    a rating that ROSE   -> the published value is merely conservative

Same attribute, same magnitude, opposite consequence. A differ that only reports "this changed"
hands back a list to check, which is what a distributor already has.

    # the whole family: how far one reissued datasheet reaches
    python scripts/detect_drift.py

    # one SKU, in full, including the unchanged attributes
    python scripts/detect_drift.py --sku BA-100-050 --show-unchanged

    # persist for the console: data/drift/{sku}.json, plus evals/drift.json for the sweep
    python scripts/detect_drift.py --write

No model calls and no network. The revision markers are read off the pages, the hashes are real,
and every verdict is arithmetic and lookup over values already on disk — so this is free, offline
and reproducible bit-for-bit.

**Which records.** By default the two golden sets, which hold ground truth for each revision. Those
values are hand-authored, so the report exercises the *drift logic* rather than the extraction that
would supply it in production; `measured: false` travels in the payload so the console says so in
prose instead of leaving a reader to assume.

**Direction needs an order.** Which document is newer is established from the revision marker
printed on the page, never from a filename or a file timestamp. If the two cannot be ordered, every
difference is reported as an unordered revision and no direction is claimed — deliberately
unhelpful, because a confident wrong direction sends exactly the wrong records to the wrong queue.

Exit codes: 0 on success, 2 on a bad invocation, 3 when a compliance claim lost its basis
(``--fail-on-compliance``, for a scheduled run that should page someone).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom.drift import (
    RevisionPairError,
    detect_drift_across,
    format_drift,
    format_drift_sweep,
    golden_revision_pair,
    sweep_drift,
)
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "drift"
DEFAULT_SWEEP_OUT = REPO_ROOT / "evals" / "drift.json"

BEFORE_DEFAULT = "pvf_valves.yaml"
AFTER_DEFAULT = "pvf_valves_revd.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        default=BEFORE_DEFAULT,
        help=f"golden set naming the older revision (default: {BEFORE_DEFAULT})",
    )
    parser.add_argument(
        "--after",
        default=AFTER_DEFAULT,
        help=f"golden set naming the newer revision (default: {AFTER_DEFAULT})",
    )
    parser.add_argument("--sku", help="report one SKU in full instead of the family sweep")
    parser.add_argument(
        "--show-unchanged",
        action="store_true",
        help="include attributes both revisions agree on",
    )
    parser.add_argument(
        "--fail-on-compliance",
        action="store_true",
        help="exit 3 if any compliance claim lost its basis. For a scheduled run.",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="persist the result")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args()

    registry = load_default()

    try:
        pair = golden_revision_pair(args.before, args.after, registry)
    except (RevisionPairError, FileNotFoundError, ValueError) as exc:
        print(f"could not read a revision pair: {exc}", file=sys.stderr)
        return 2

    if pair.failures:
        for failure in pair.failures:
            print(f"  warning: {failure}", file=sys.stderr)

    if not pair.common_skus():
        print(
            f"the two revisions share no SKUs, so there is nothing to compare. "
            f"'{args.before}' holds {len(pair.before)} and '{args.after}' holds "
            f"{len(pair.after)}.",
            file=sys.stderr,
        )
        return 2

    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )

    if args.sku:
        return _run_single(args.sku, reports, pair, args)
    return _run_sweep(reports, pair, args)


def _run_sweep(reports, pair, args) -> int:
    result = sweep_drift(
        reports, source_note=pair.source_note, measured=pair.measured
    )
    payload = _envelope(pair, result.to_dict())

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_drift_sweep(result))
        _print_lifecycle(pair)

    out = args.out or DEFAULT_SWEEP_OUT
    code = _persist(payload, out, args, label="the sweep")
    if code:
        return code
    if args.fail_on_compliance and result.compliance_impacts():
        print(
            f"\nFAIL: {len(result.compliance_impacts())} compliance claim(s) no longer have a "
            f"basis in the current revision.",
            file=sys.stderr,
        )
        return 3
    return 0


def _run_single(sku: str, reports, pair, args) -> int:
    report = next((r for r in reports if r.sku == sku), None)
    if report is None:
        print(
            f"'{sku}' is not in both revisions. Comparable SKUs: "
            f"{', '.join(pair.common_skus())}",
            file=sys.stderr,
        )
        return 2

    payload = _envelope(pair, report.to_dict())

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_drift(report, show_unchanged=args.show_unchanged))

    out = args.out or DEFAULT_OUT_DIR / f"{sku}.json"
    code = _persist(payload, out, args, label="the report")
    if code:
        return code
    if args.fail_on_compliance and report.compliance_impacts():
        print(
            f"\nFAIL: {len(report.compliance_impacts())} compliance claim(s) affected on {sku}.",
            file=sys.stderr,
        )
        return 3
    return 0


def _print_lifecycle(pair) -> None:
    """Report appeared/disappeared SKUs separately from drift.

    A part that vanished between revisions is a lifecycle event, not an attribute change, and
    filing "this product no longer exists" under the same heading as "its seat material changed"
    would bury the more consequential of the two.
    """
    if pair.only_before():
        print("")
        print(
            f"  {len(pair.only_before())} SKU(s) present in the old revision only "
            f"(discontinued, not drifted): {', '.join(pair.only_before())}"
        )
    if pair.only_after():
        print("")
        print(
            f"  {len(pair.only_after())} SKU(s) introduced by the new revision: "
            f"{', '.join(pair.only_after())}"
        )


def _envelope(pair, report: dict) -> dict:
    """Wrap a report with the provenance of the revision pair it was computed over.

    Same envelope shape the cross-reference and L4 artifacts use, and for the same reason: the
    console joins this onto a bundle at read time and has to be able to say in prose whether the
    values behind a verdict were measured or hand-authored.
    """
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "revision_pair": pair.summary(),
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
