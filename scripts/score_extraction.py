"""Score deterministic document extraction against hand-read ground truth.

    python scripts/score_extraction.py

Reads the three committed sample datasheets — one of them a real PDF — using nothing but document
layout, and compares every value against `data/golden/pvf_valves.yaml`. No model, no credentials,
no network. The whole run is reproducible bit-for-bit.

The measurement is split three ways on purpose, because collapsing them hides the failure that
matters:

* **agree / disagree** is precision, over the values the extractor actually produced. Asserted
  absolutely: one disagreement fails the run.
* **not extracted** is the coverage gap. Not a failure. These are the prose attributes the model
  path exists to read, and reporting them as failures would confuse "did not read it" with "read
  it wrong" — two problems with different fixes.
* **absent respected / absent violated** is abstention, scored from the golden set's `absent`
  list. This is the only number that catches fabrication, and it is the reason the golden set
  records absences at all: a system that invents freely scores identically to one that abstains
  honestly if you only ever measure the values it emitted.

Two arms are run against the same ground truth:

* **own-document** gives each part only the datasheet that describes it. This is the number to
  quote for extraction accuracy.
* **all-documents** offers every part all three datasheets, so two of the three are wrong for it.
  This measures the relevance guard rather than the extractor, and it is the arm that would catch
  the wrong-document failure — values whose quotes verify perfectly against a sheet for a
  different product. Nothing downstream can catch that, because every integrity check passes.

The arms should report identical numbers. If all-documents produces *more* values than
own-document, the guard has stopped working, and the extra values are cited fabrications.

Exit code is 1 on a disagreement, a fabrication, an unverifiable quote, or a coverage regression
below the pinned floor.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

import yaml  # noqa: E402
from axiom.core.compare import agree  # noqa: E402
from axiom.core.evidence import SourceDocument  # noqa: E402
from axiom.core.values import (  # noqa: E402
    AttributeValue,
    DerivationMethod,
    ValueStatus,
)
from axiom.docintel import ParsedDocument, parse_artifact  # noqa: E402
from axiom.extract.structured import (  # noqa: E402
    extract_structured,
    reject_unresolved,
    to_attribute_values,
    verify_quotes,
)
from axiom.ingest import detect_document_type, sha256_bytes  # noqa: E402
from axiom.normalize import normalize_all  # noqa: E402
from axiom.schema import SchemaRegistry, load_default  # noqa: E402

DEFAULT_GOLDEN = REPO_ROOT / "data" / "golden" / "pvf_valves.yaml"

# Measured, not aspirational. A drop means the extractor stopped reading something it used to read;
# a rise means the floor should be raised in the same commit that earned it.
COVERAGE_FLOOR = 115


@dataclass
class Score:
    """One arm's result."""

    label: str
    agreed: int = 0
    disagreed: int = 0
    not_extracted: int = 0
    absent_respected: int = 0
    absent_violated: int = 0
    mismatches: list[str] = field(default_factory=list)
    fabrications: list[str] = field(default_factory=list)
    quote_failures: list[str] = field(default_factory=list)

    @property
    def produced(self) -> int:
        """Values the extractor committed to. The precision denominator."""
        return self.agreed + self.disagreed

    @property
    def scored(self) -> int:
        """Every golden value with a stated source form. The coverage denominator."""
        return self.produced + self.not_extracted

    def as_dict(self) -> dict[str, object]:
        return {
            "arm": self.label,
            "agreed": self.agreed,
            "disagreed": self.disagreed,
            "not_extracted": self.not_extracted,
            "absent_respected": self.absent_respected,
            "absent_violated": self.absent_violated,
            "mismatches": self.mismatches,
            "fabrications": self.fabrications,
            "quote_failures": self.quote_failures,
        }


def _load(path: Path) -> tuple[ParsedDocument, str]:
    """Parse one document, paired with the hash that pins the content its citations point into."""
    raw = path.read_bytes()
    sha = sha256_bytes(raw)
    document = SourceDocument(
        document_id=f"{path.stem}@{sha[:8]}",
        # Resolved first: `as_uri` refuses a relative path.
        uri=path.resolve().as_uri(),
        sha256=sha,
        doc_type=detect_document_type(raw, path.name),
        fetched_at=datetime.now(UTC),
    )
    return parse_artifact(raw, document), sha


def _read(
    registry: SchemaRegistry,
    documents: dict[str, tuple[ParsedDocument, str]],
    sku: str,
    class_code: str,
    names: list[str],
    quote_failures: list[str],
) -> dict[str, AttributeValue]:
    """Everything the named documents yield for one part, keyed by attribute.

    ``setdefault`` rather than assignment: the first document to state an attribute wins, matching
    the delivery driver. Later documents do not silently overwrite an earlier citation.
    """
    produced: dict[str, AttributeValue] = {}
    for name in names:
        parsed, sha = documents[name]
        extraction = extract_structured(
            parsed, registry, class_code=class_code, target_sku=sku
        )
        for failure in verify_quotes(extraction, parsed):
            quote_failures.append(f"{sku} in {name}: {failure}")
        # reject_unresolved is a POST-normalize guard: it reads value_canonical, which normalize
        # fills. Running it first would drop everything, since canonical is None until then.
        normalized, _issues = normalize_all(
            to_attribute_values(extraction, sha), registry, class_code=class_code
        )
        kept, _dropped = reject_unresolved(normalized, registry)
        for value in kept:
            produced.setdefault(value.attribute_code, value)
    return produced


def _canonical(
    registry: SchemaRegistry, code: str, source_form: object, class_code: str
) -> object:
    """The golden value's canonical form.

    Ground truth is written in SOURCE FORM and pushed through the same normalisation the
    extractor's output goes through, so a wrong conversion factor corrupts both sides identically
    and cancels out. This file measures extraction; normalisation has its own unit tests.
    """
    reference, _issues = normalize_all(
        [
            AttributeValue(
                attribute_code=code,
                value_raw=str(source_form),
                method=DerivationMethod.HUMAN_ENTRY,
                confidence=1.0,
                status=ValueStatus.HUMAN_APPROVED,
            )
        ],
        registry,
        class_code=class_code,
    )
    return reference[0].value_canonical


def measure(
    label: str,
    registry: SchemaRegistry,
    golden: dict,
    documents: dict[str, tuple[ParsedDocument, str]],
    *,
    all_documents: bool,
) -> Score:
    score = Score(label)

    for product in golden.get("products") or []:
        source = product.get("source") or ""
        if source not in documents:
            continue
        sku = product["sku"]
        class_code = product["class_code"]
        names = list(documents) if all_documents else [source]
        produced = _read(
            registry, documents, sku, class_code, names, score.quote_failures
        )

        for code, source_form in (product.get("attributes") or {}).items():
            got = produced.get(code)
            if got is None:
                score.not_extracted += 1
                continue
            attribute = registry.attribute(code)
            expected = _canonical(registry, code, source_form, class_code)
            if agree(expected, got.value_canonical, tolerance=attribute.tolerance or 0.0):
                score.agreed += 1
            else:
                score.disagreed += 1
                score.mismatches.append(
                    f"{sku} {code}: golden {expected!r} != extracted {got.value_canonical!r}"
                )

        for code in product.get("absent") or []:
            if code in produced:
                score.absent_violated += 1
                score.fabrications.append(
                    f"{sku} {code} = {produced[code].value_canonical!r}, but the golden set "
                    f"records it as absent"
                )
            else:
                score.absent_respected += 1

    return score


def _ratio(numerator: int, denominator: int) -> str:
    """Fractions below 20 observations. Two rows cannot support "93.3%"."""
    if denominator == 0:
        return f"{numerator}/0"
    if denominator < 20:
        return f"{numerator}/{denominator}"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def render(score: Score) -> str:
    lines = [
        f"--- {score.label}",
        f"  agree              {score.agreed}",
        f"  disagree           {score.disagreed}",
        f"  not extracted      {score.not_extracted}   <- coverage gap, not an error",
        f"  absent respected   {score.absent_respected}",
        f"  absent VIOLATED    {score.absent_violated}"
        f"{'   <- fabrication' if score.absent_violated else ''}",
        f"  precision          {_ratio(score.agreed, score.produced)}",
        f"  coverage           {_ratio(score.agreed, score.scored)}",
    ]
    for failure in score.quote_failures:
        lines.append(f"  QUOTE UNVERIFIABLE {failure}")
    for mismatch in score.mismatches:
        lines.append(f"  DISAGREE {mismatch}")
    for fabrication in score.fabrications:
        lines.append(f"  FABRICATED {fabrication}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "golden",
        type=Path,
        nargs="?",
        default=DEFAULT_GOLDEN,
        help=f"golden set (default: {DEFAULT_GOLDEN.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--json", type=Path, help="also write the result as JSON")
    parser.add_argument(
        "--floor",
        type=int,
        default=COVERAGE_FLOOR,
        help=f"fail if agreements drop below this (default: {COVERAGE_FLOOR})",
    )
    args = parser.parse_args()

    if not args.golden.is_file():
        print(f"no such golden set: {args.golden}", file=sys.stderr)
        return 1

    registry = load_default()
    golden = yaml.safe_load(args.golden.read_text(encoding="utf-8")) or {}

    # The golden set names its own documents and where they live, relative to itself. Reading the
    # paths from the file rather than hardcoding them means adding a datasheet to the golden set is
    # a data change, not a code change.
    documents: dict[str, tuple[ParsedDocument, str]] = {}
    for entry in golden.get("source_documents") or []:
        path = (args.golden.parent / entry["path"]).resolve()
        if not path.is_file():
            print(f"no such document: {path}", file=sys.stderr)
            return 1
        documents[entry["id"]] = _load(path)

    print(f"Extraction score - {golden.get('name', args.golden.stem)}")
    print("=" * 78)
    for name, (parsed, _sha) in documents.items():
        tables = len(parsed.all_tables())
        print(
            f"  {name:<8} {len(parsed.pages)} page(s), {tables} table(s), "
            f"parser={parsed.parser}"
        )
    print()

    own = measure(
        "own-document (extraction accuracy)",
        registry,
        golden,
        documents,
        all_documents=False,
    )
    every = measure(
        "all-documents (relevance guard)",
        registry,
        golden,
        documents,
        all_documents=True,
    )

    print(render(own))
    print()
    print(render(every))
    print()

    problems: list[str] = []
    for score in (own, every):
        if score.disagreed:
            problems.append(f"{score.label}: {score.disagreed} value(s) disagree with ground truth")
        if score.absent_violated:
            problems.append(
                f"{score.label}: {score.absent_violated} value(s) produced for attributes the "
                f"document does not state"
            )
        if score.quote_failures:
            problems.append(
                f"{score.label}: {len(score.quote_failures)} quote(s) are not present in the "
                f"document they cite"
            )
    if own.agreed < args.floor:
        problems.append(
            f"coverage regressed: {own.agreed} agreements, expected at least {args.floor}"
        )
    # The guard's own assertion. More values with the wrong documents attached means the guard
    # stopped rejecting them, and every extra value is cited to a sheet for another product.
    if every.produced > own.produced:
        problems.append(
            f"relevance guard leaked: {every.produced} values when offered every document versus "
            f"{own.produced} from the right one"
        )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "golden_set": golden.get("name"),
                    "documents": sorted(documents),
                    "coverage_floor": args.floor,
                    "arms": [own.as_dict(), every.as_dict()],
                    "problems": problems,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.json}")

    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"PASS - {own.agreed} values agree with a human reading, none disagree, "
        f"none fabricated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
