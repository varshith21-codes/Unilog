"""Score classification over a whole item master: coverage, and the precision guard behind it.

Coverage on its own is a vanity number. A classifier that assigns every row to something scores
100% and may be wrong on most of them, and on an unlabelled file there is no answer sheet to
catch it. So this reports two things that together mean something:

* **Coverage** — the share of rows that reached a class. A floor to be raised.
* **Identity-grounding** — for every classified row, whether the source text actually contains one
  of the winning class's own `identity_terms`. The candidate index guarantees this by construction;
  checking it here tests the guarantee rather than trusting it, and it is the one precision
  statement that can be made with no labelled data at all.

A row that classified without carrying an identity term of its winner is reported as FABRICATED
and makes the run fail. That is the signal that a class has been loosened into guessing.

    python scripts/score_classification.py "Unihack_ Sample Dataset - Input.csv"
    python scripts/score_classification.py "Unihack_ Sample Dataset - Input.csv" --unclassified 40
    python scripts/score_classification.py SAMPLE.csv --class-code BLD.DCK.BOARD
    python scripts/score_classification.py SAMPLE.csv --json evals/classification_score.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def identity_tokens(registry, code: str) -> frozenset[str]:
    """The winning class's identity terms, tokenised the way the index tokenises them."""
    from axiom.classify.candidates import tokenize

    return frozenset(
        token
        for term in registry.product_class(code).identity_terms
        for token in tokenize(term)
    )


def _explain(text: str) -> int:
    """Why one string classified the way it did.

    Admission and scoring are separate steps and they fail differently: a class that is not
    admitted is invisible no matter how well its vocabulary matches, and a class that is admitted
    on a stray token still has to be out-scored by something. Reporting both together is the only
    way to tell "the identity guard excluded it" from "it lost the ranking", which are opposite
    fixes.
    """
    from axiom.classify.candidates import CandidateIndex, tokenize
    from axiom.classify.classifier import Classifier
    from axiom.schema import load_default

    registry = load_default()
    index = CandidateIndex.build(registry)
    query = set(tokenize(text))

    print(f"text    {text!r}")
    print(f"tokens  {sorted(query)}\n")

    ranked = index.search(text, limit=10)
    by_code = {c.code: c for c in ranked}

    print(f"{'class':<34} {'admitted':>9} {'score':>9}  matched identity terms")
    print("-" * 92)
    for code in registry.class_codes:
        terms = frozenset(
            token
            for term in registry.product_class(code).identity_terms
            for token in tokenize(term)
        )
        hit = sorted(terms & query)
        admitted = (not terms) or bool(hit)
        candidate = by_code.get(code)
        score = f"{candidate.score:.4f}" if candidate else "-"
        note = ", ".join(hit) if hit else ("(unguarded)" if not terms else "")
        print(f"{code:<34} {'yes' if admitted else 'no':>9} {score:>9}  {note}")

    result = Classifier(registry).classify(text)
    print(f"\nranking   {[(c.code, c.score) for c in ranked]}")
    if len(ranked) >= 2:
        print(f"dominance {ranked[1].score / ranked[0].score:.4f}  "
              f"(runner-up as a share of the leader)")
    print(f"decision  {result.method}  ->  {result.class_code}")
    if result.abstain_reason:
        print(f"reason    {result.abstain_reason}")
    return 0


def _sweep_floor(args, rows, registry, identity) -> int:
    """Coverage and identity grounding at several viability floors.

    The floor exists to reject a candidate that matched an identity term and shares no other
    vocabulary. Where to put it is an empirical question, and it is worth measuring rather than
    reasoning about because the score is a COSINE and cosine is length-asymmetric: a five-token
    item-master string cannot score against a class's thousand-token vocabulary the way a
    twenty-token datasheet paragraph does, whatever the quality of the match.

    Fabrication is the column that matters. If it stays at zero as the floor drops, the floor was
    not the thing providing precision — the identity guard was.
    """
    from axiom.classify import classifier as classifier_module
    from axiom.classify.candidates import tokenize
    from axiom.classify.classifier import Classifier

    print(f"{'floor':>8} {'classified':>11} {'coverage':>9} {'fabricated':>11} {'ambiguous':>10}")
    print("-" * 54)
    original = classifier_module.MIN_VIABLE_SCORE
    try:
        for floor in args.sweep_floor:
            classifier_module.MIN_VIABLE_SCORE = floor
            classifier = Classifier(registry)
            classified = fabricated = ambiguous = 0
            for row in rows:
                text = (row.get(args.column) or "").strip()
                result = classifier.classify(text)
                if result.method == "ambiguous_no_model":
                    ambiguous += 1
                code = result.class_code
                if code is None:
                    continue
                classified += 1
                terms = identity[code]
                if terms and not (terms & set(tokenize(text))):
                    fabricated += 1
            print(f"{floor:>8g} {classified:>11} {classified / len(rows):>8.1%} "
                  f"{fabricated:>11} {ambiguous:>10}")
    finally:
        classifier_module.MIN_VIABLE_SCORE = original
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", nargs="?", default="", type=Path)
    parser.add_argument("--column", default="Part_Desc")
    parser.add_argument(
        "--class-code", help="report only rows whose winning class is this code"
    )
    parser.add_argument(
        "--unclassified", type=int, default=0, help="print this many unclassified rows"
    )
    parser.add_argument(
        "--ambiguous", type=int, default=0, help="print this many rows that abstained on ambiguity"
    )
    parser.add_argument("--json", type=Path, help="write the summary to this path")
    parser.add_argument(
        "--explain",
        metavar="TEXT",
        help="explain one string instead of scoring the file: which classes were admitted, "
        "what they scored, and which identity term let each one in",
    )
    parser.add_argument(
        "--sweep-floor",
        type=float,
        nargs="+",
        metavar="S",
        help="report coverage and fabrication at these MIN_VIABLE_SCORE values",
    )
    args = parser.parse_args(argv)

    if args.explain is not None:
        return _explain(args.explain)

    if not args.csv_path.is_file():
        print(f"not found: {args.csv_path}", file=sys.stderr)
        return 2

    from axiom.classify.classifier import Classifier
    from axiom.schema import load_default

    registry = load_default()
    # No model client: the batch path has none either, so an ambiguous case must abstain rather
    # than be resolved by coin flip. Measuring with a model attached would overstate what a
    # deterministic run actually delivers.
    classifier = Classifier(registry)
    identity = {code: identity_tokens(registry, code) for code in registry.class_codes}
    unguarded = sorted(code for code, terms in identity.items() if not terms)

    with open(args.csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if args.sweep_floor:
        return _sweep_floor(args, rows, registry, identity)

    winners: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    fabricated: list[tuple[str, str]] = []
    unclassified: list[str] = []
    ambiguous: list[tuple[str, str]] = []

    from axiom.classify.candidates import tokenize

    for row in rows:
        text = (row.get(args.column) or "").strip()
        result = classifier.classify(text, sku=(row.get("Mfg_Part_Num") or "").strip() or None)
        methods[result.method] += 1
        code = result.class_code

        if code is None:
            if result.method == "ambiguous_no_model":
                ambiguous.append((text, result.abstain_reason or ""))
            else:
                unclassified.append(text)
            continue

        winners[code] += 1
        terms = identity[code]
        if terms and not (terms & set(tokenize(text))):
            fabricated.append((code, text))

    total = len(rows)
    classified = sum(winners.values())

    print(f"{total} rows from {args.csv_path.name}")
    print(f"{len(registry.class_codes)} classes in the schema\n")

    print(f"{'class':<34} {'rows':>5} {'share':>7}")
    print("-" * 49)
    for code, count in winners.most_common():
        print(f"{code:<34} {count:>5} {count / total * 100:>6.1f}%")
    print("-" * 49)
    print(f"{'CLASSIFIED':<34} {classified:>5} {classified / total * 100:>6.1f}%")
    print(f"{'unclassified':<34} {total - classified:>5} "
          f"{(total - classified) / total * 100:>6.1f}%")

    print("\ndecision path")
    print("-" * 49)
    for method, count in methods.most_common():
        print(f"  {method:<32} {count:>5}")

    if unguarded:
        print(f"\nWARNING: {len(unguarded)} class(es) declare no identity_terms and are therefore "
              f"admitted for every query: {', '.join(unguarded)}")

    print("\nidentity grounding")
    print("-" * 49)
    if fabricated:
        print(f"  FABRICATED: {len(fabricated)} classified row(s) carry no identity term of "
              f"their winning class")
        for code, text in fabricated[:20]:
            print(f"    {code:<32} {text}")
    else:
        print(f"  clean: all {classified} classified rows carry an identity term of their winner")

    if args.class_code:
        print(f"\nrows won by {args.class_code}")
        print("-" * 49)
        shown = 0
        for row in rows:
            text = (row.get(args.column) or "").strip()
            if classifier.classify(text).class_code == args.class_code:
                print(f"  {text}")
                shown += 1
        print(f"  ({shown} rows)")

    if args.unclassified and unclassified:
        print(f"\nunclassified sample ({min(args.unclassified, len(unclassified))} of "
              f"{len(unclassified)})")
        print("-" * 49)
        for text in unclassified[: args.unclassified]:
            print(f"  {text}")

    if args.ambiguous and ambiguous:
        print(f"\nabstained on ambiguity ({min(args.ambiguous, len(ambiguous))} of "
              f"{len(ambiguous)})")
        print("-" * 49)
        for text, reason in ambiguous[: args.ambiguous]:
            print(f"  {text}\n      {reason}")

    summary = {
        "rows": total,
        "classes": len(registry.class_codes),
        "classified": classified,
        "coverage": round(classified / total, 4) if total else 0.0,
        "fabricated": len(fabricated),
        "unguarded_classes": unguarded,
        "by_class": dict(winners.most_common()),
        "by_method": dict(methods.most_common()),
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")

    # Fabrication is a hard failure; low coverage is not. Reading more of the catalogue is an
    # improvement to be measured, but a row classified on no identity evidence is a defect.
    return 1 if fabricated or unguarded else 0


if __name__ == "__main__":
    sys.exit(main())
