"""Before/after cohort study: what enrichment did to the catalogue's quality index.

Tier 2, item 17. Everything else measured here answers "is the extraction correct?". This answers
the question a distributor actually asks — "what is my catalogue worth now?" — against the state
it was in beforehand, scored by the same index through the same code.

    # 1. the "before" state: the ERP item master, mapped to canonical fields
    python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv \\
        --supplier milwaukee --map "WT/EA (lb)=each_weight" --confirm --out data/ingest

    # 2. the "after" state already exists as console bundles from pipeline runs
    # 3. compare them
    python scripts/run_cohort.py --write

No model calls. Both arms are scored from artifacts already on disk, so this is free, offline and
deterministic — which also means it can run in CI, unlike the backtest.

**On the control arm.** ``--control`` holds SKUs out of the treatment and scores them from their
before-state twice. Since nothing happened to them their delta must be exactly zero, and any
movement means the *scorer* changed between the two measurements rather than the data. That is a
narrower claim than "control group" usually implies, and it is the one this design supports; the
module docstring in ``axiom.evaluation.cohort`` says so at greater length rather than letting the
report imply a randomised trial.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.evaluation import build_study, format_study, load_records
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROWS = REPO_ROOT / "data" / "ingest"
DEFAULT_BUNDLES = REPO_ROOT / "data" / "console"
DEFAULT_OUT = REPO_ROOT / "evals" / "cohort.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        type=Path,
        default=DEFAULT_ROWS,
        help=(
            "mapped item-master rows from ingest_supplier_file.py --out (a .rows.json file, or a "
            "directory of them). This is the before state."
        ),
    )
    parser.add_argument(
        "--bundles",
        type=Path,
        default=DEFAULT_BUNDLES,
        help="console bundles written by run_pipeline.py --save-session. The after state.",
    )
    parser.add_argument(
        "--class-code",
        default="PLB.VLV.BALL.2PC",
        help=(
            "class for before-state rows. The item master has no reliable category — "
            "mis-assigned categories are part of what enrichment fixes — so the required "
            "attribute set has to be named rather than read from the row."
        ),
    )
    parser.add_argument(
        "--control",
        action="append",
        default=[],
        metavar="SKU",
        help=(
            "hold a SKU out of the treatment arm. Repeatable. A control SKU is scored from its "
            "before-state twice, so any movement exposes a change in the scorer itself."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="where to write the study"
    )
    parser.add_argument(
        "--write", action="store_true", help="persist the study to --out"
    )
    parser.add_argument("--json", action="store_true", help="emit the study as JSON")
    args = parser.parse_args()

    registry = load_default()

    rows = _load_rows(args.rows)
    if rows is None:
        return 2
    if not rows:
        print(
            f"no item-master rows found under {args.rows}. Produce them first:\n"
            "  python scripts/ingest_supplier_file.py data/samples/supplier-feed.csv "
            "--supplier milwaukee --out data/ingest",
            file=sys.stderr,
        )
        return 2

    before = load_records(rows, registry, class_code=args.class_code)

    after = _load_bundles(args.bundles, registry)
    if not after:
        print(
            f"no console bundles found under {args.bundles}. Produce at least one first:\n"
            "  python scripts/run_pipeline.py data/samples/ba100.txt --sku BA-100-075 "
            "--include-optional --save-session",
            file=sys.stderr,
        )
        return 2

    # A SKU can only be a control if it has a before-state to be scored from.
    control = [sku for sku in args.control if sku in before]
    if missing := sorted(set(args.control) - set(before)):
        print(
            f"ignoring --control for SKUs with no item-master row: {', '.join(missing)}",
            file=sys.stderr,
        )

    study = build_study(
        before=before,
        after=after,
        registry=registry,
        control_skus=control,
        schema_version=registry.product_class(args.class_code).schema_version,
    )

    if not study.treatment:
        print(
            "no SKU appears in both the item master and the enriched output, so there is "
            "nothing to compare. The before and after states must overlap on SKU.",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(study.to_dict(), indent=2, default=str))
    else:
        print(format_study(study))

    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(study.to_dict(), indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"  written: {args.out.relative_to(REPO_ROOT)}")
        print("  the console reads this for its Quality Index page")

    # A drifted control means the study is invalid, and a script whose output is a claim must
    # exit non-zero when the claim cannot be supported.
    if study.control and study.drifted:
        return 1
    return 0


def _load_rows(path: Path) -> list[dict] | None:
    """Read one .rows.json file, or every one in a directory."""
    if path.is_file():
        paths = [path]
    elif path.is_dir():
        paths = sorted(path.glob("*.rows.json"))
    else:
        print(f"no such path: {path}", file=sys.stderr)
        return None

    rows: list[dict] = []
    for candidate in paths:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"{candidate.name} is not valid JSON: {exc}", file=sys.stderr)
            return None
        records = payload.get("records") if isinstance(payload, dict) else payload
        if isinstance(records, list):
            rows.extend(r for r in records if isinstance(r, dict))
    return rows


def _load_bundles(directory: Path, registry) -> dict[str, ProductRecord]:
    """Rebuild after-state records from persisted console bundles.

    The bundle is read rather than the pipeline re-run, for the same reason the API reads it: a
    fresh run would cost money and return slightly different numbers, which would make the study
    non-reproducible.
    """
    if not directory.is_dir():
        return {}

    records: dict[str, ProductRecord] = {}
    for path in sorted(directory.glob("*.bundle.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            bundle = payload["bundle"]
        except (OSError, ValueError, KeyError):
            continue

        record = _record_from_bundle(bundle, registry)
        if record is not None:
            records[record.sku] = record
    return records


def _record_from_bundle(bundle: dict, registry) -> ProductRecord | None:
    """Reconstruct enough of a ProductRecord for the quality index to be recomputed.

    Recomputed rather than read from the bundle's stored certificate on purpose. The whole design
    of this study rests on both arms passing through one scorer, so lifting the after-score out of
    a file written by an earlier version of that scorer would reintroduce exactly the drift the
    control arm exists to detect.
    """
    meta = bundle.get("record") or {}
    sku = bundle.get("sku") or meta.get("sku")
    if not sku:
        return None

    record = ProductRecord(
        tenant_id=meta.get("tenant_id", "demo"),
        sku=sku,
        mpn=meta.get("mpn") or sku,
        mpn_normalized=meta.get("mpn_normalized"),
        brand=meta.get("brand"),
        supplier_id=meta.get("supplier_id"),
        class_code=bundle.get("class_code") or meta.get("class_code"),
        schema_version=meta.get("schema_version"),
    )

    known = set(registry.attribute_codes)
    for entry in bundle.get("values", []):
        code = entry.get("attribute_code")
        if code not in known:
            continue

        method = _method(entry.get("method"))
        evidence = _evidence(entry, method)
        if method.requires_evidence and not evidence:
            # The bundle says this was extracted but carries no span. Rather than silently
            # downgrading the method — which would quietly award it verifiability it did not
            # earn — record it as the legacy state it now resembles.
            method = DerivationMethod.LEGACY_RECORD

        try:
            record.add_value(
                AttributeValue(
                    attribute_code=code,
                    value_raw=entry.get("value_raw"),
                    value_canonical=entry.get("value_canonical"),
                    value_display=entry.get("value_display"),
                    method=method,
                    confidence=entry.get("confidence"),
                    status=_status(entry.get("status")),
                    evidence=evidence,
                )
            )
        except ValueError:
            # A bundle from an older schema can carry a value this build cannot construct.
            # Skipping it understates the after-state, which is the safe direction to be wrong in.
            continue

    return record


def _method(raw: object) -> DerivationMethod:
    try:
        return DerivationMethod(str(raw))
    except ValueError:
        return DerivationMethod.LEGACY_RECORD


def _status(raw: object) -> ValueStatus:
    try:
        return ValueStatus(str(raw))
    except ValueError:
        return ValueStatus.CANDIDATE


def _evidence(entry: dict, method: DerivationMethod) -> list[EvidenceSpan]:
    """Rebuild evidence spans, preserving whether the quote was actually verified.

    Only ``quote_verified`` matters to the index, but the geometry is carried anyway so a
    reconstructed record behaves like a real one if it is ever handed to something else.
    """
    spans: list[EvidenceSpan] = []
    for index, raw in enumerate(entry.get("evidence") or []):
        if not isinstance(raw, dict):
            continue
        box = raw.get("bbox") or {}
        try:
            spans.append(
                EvidenceSpan(
                    span_id=raw.get("span_id") or f"{entry.get('attribute_code')}-{index}",
                    document_id=raw.get("document_id") or "unknown",
                    document_sha256=raw.get("document_sha256") or "0" * 64,
                    quote=raw.get("quote") or "",
                    page=int(raw.get("page") or 1),
                    bbox=BoundingBox(
                        x0=float(box.get("x0", 0)),
                        y0=float(box.get("y0", 0)),
                        x1=float(box.get("x1", 1)),
                        y1=float(box.get("y1", 1)),
                    ),
                    quote_verified=bool(raw.get("quote_verified")),
                    match_score=float(raw.get("match_score") or 0.0),
                    table_ref=raw.get("table_ref"),
                )
            )
        except (TypeError, ValueError):
            continue
    del method
    return spans


if __name__ == "__main__":
    sys.exit(main())
