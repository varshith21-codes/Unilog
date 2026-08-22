"""Measure the retrieval dominance band that DECISIVE_DOMINANCE has to sit inside.

`DECISIVE_DOMINANCE` is the ratio at or below which the runner-up is considered beaten and no
model is asked to adjudicate. It has to sit in the gap between two measured populations:

* **correct** — cases where retrieval already picked the right class, so paying for a model call
  would be waste. Their dominance must be at or *below* the threshold.
* **ambiguous** — cases where the text genuinely does not separate two classes, so deciding
  without adjudication would be a coin flip. Their dominance must be *above* it.

The gap between ``max(correct)`` and ``min(ambiguous)`` is the whole safety margin, and a
threshold outside it is a silent accuracy regression rather than a test failure. This script
prints both populations, the gap, and where the current constant falls, so re-measuring after a
schema change is mechanical instead of archaeological.

    python scripts/measure_dominance.py
    python scripts/measure_dominance.py --sweep-corpus 4 8 32 128 512 4096

``--sweep-corpus`` rebuilds the index with different values of
:data:`axiom.classify.candidates.IDF_REFERENCE_CORPUS` and reports the band for each, which is
how that constant was chosen. It also demonstrates the property the constant exists for: run
`--sweep-classes` and the band should barely move as classes are added.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Cases where retrieval is expected to have already decided. Every one of these must land at or
# below the threshold, or it starts costing a model call it does not need — and on the deterministic
# batch path, where no model is attached, it abstains and delivers nothing instead.
#
# Only strings that produce a RUNNER-UP are measurements. A probe with one candidate never consults
# the threshold, so it is reported separately rather than counted; the harness prints which ones
# fell into that bucket so a probe that quietly stops measuring anything is visible.
CORRECT: dict[str, str] = {
    "ball valve": (
        "Two-Piece Full Port Bronze Ball Valve, Bronze C84400 body, RPTFE seat, "
        "600 PSI WOG, NPT threaded, lever handle"
    ),
    "gate valve": (
        "Bronze Gate Valve, rising stem, Bronze C84400 body, 150 PSI WSP steam rating, "
        "NPT threaded, handwheel"
    ),
    "dishwasher": "PDSH4816AF Dishwasher SS - Display Only",
    "lamp": "565374 75W Led A19 Med 27k 4pk",
    "decking": "1nx6-12' Tide Pool Grooved - Trex Enhance Basics Decking",
    "cut-off disc": '49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc',
    "driver bit": 'DPH22B 2" #2 Phillips Drive - Bit',
    "range": 'PB900YVFS Elect 30" Range SS',
    # --- multi-candidate cases from the full catalogue ---------------------------------------
    #
    # These are the ones that actually exercise the threshold, and each is a real row that
    # abstained at some point while this taxonomy was being built. An impact wrench is a power
    # tool, not a hand tool; a hammer drill is a power tool, not a hammer; an angle grinder is a
    # power tool, not a coffee grinder.
    "impact wrench": '3048-20 Milw M12 1/4" Impact Wrench w/Friction Ring',
    "hammer drill": "D25333K Dewalt Hammer Drill",
    "angle grinder": 'DCG410B Dewalt 20V 4-1/2"-5" - Angle Grinder (Bare)',
    "orbit sander": '2535-20 Milw 3" Orbit Sander - M12',
    "washing machine": "TR7006WN Speed Queen Washer Wh",
    "azek decking": "1x6-20' Castle Gate Grooved - Landmark Azek PVC Decking",
    "heated glove": "M701B-21L Milw L Black Heated Work Glove Liners",
    "rafter square": 'MLSQ0120 Milw 12" RafterSquare',
}

# Cases where the text genuinely does not separate the candidates. Every one must land strictly
# above the threshold, or it gets resolved by coin flip.
AMBIGUOUS: dict[str, str] = {
    "shared valve vocabulary": "bronze valve NPT threaded",
    "bronze NPT 150 PSI": "Bronze C84400 body NPT threaded valve 150 PSI",
    # A nailer for roofing: the tool class and the roofing class each hold half the evidence, and
    # nothing in the string settles which noun is the product. Abstaining is the right answer.
    "roofing nailer": "2909-21 Milw M18 Coil Roofing - Nailer Kit",
    # A wrench set sold in a Packout box. Genuinely both a hand tool and tool storage.
    "wrench set in a storage box": "48-22-9484 Milw Packout 15pc - SAE Wrench Set",
    # "Tape Light" is a lighting product whose first word is the whole of another class.
    "tape light": "64-110 Satco Tape Light 16'",
}


def _band(index) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Dominance for every probe, plus the probes that produced no runner-up."""
    correct: dict[str, float] = {}
    ambiguous: dict[str, float] = {}
    singletons: list[str] = []

    for population, out in ((CORRECT, correct), (AMBIGUOUS, ambiguous)):
        for label, text in population.items():
            ranked = index.search(text, limit=5)
            if len(ranked) < 2:
                # A single candidate is not a dominance measurement at all — the classifier takes
                # the `retrieval_only` path and never consults the threshold. Reported rather
                # than silently skipped, because a probe that stops having a runner-up is how
                # this harness quietly becomes vacuous.
                singletons.append(f"{label} ({len(ranked)} candidate(s))")
                continue
            out[label] = ranked[1].score / ranked[0].score
    return correct, ambiguous, singletons


def _report(correct, ambiguous, singletons, threshold: float) -> bool:
    print(f"{'population':<12} {'case':<26} {'dominance':>10}")
    print("-" * 52)
    for label, value in sorted(correct.items(), key=lambda kv: kv[1]):
        flag = "" if value <= threshold else "  <-- ABOVE THRESHOLD"
        print(f"{'correct':<12} {label:<26} {value:>10.4f}{flag}")
    print(f"{'':<12} {'--- threshold ---':<26} {threshold:>10.4f}")
    for label, value in sorted(ambiguous.items(), key=lambda kv: kv[1]):
        flag = "" if value > threshold else "  <-- AT OR BELOW THRESHOLD"
        print(f"{'ambiguous':<12} {label:<26} {value:>10.4f}{flag}")

    if singletons:
        print(f"\nno runner-up (threshold never consulted): {', '.join(singletons)}")

    if not correct or not ambiguous:
        print("\nboth populations must be non-empty to report a gap")
        return False

    ceiling, floor = max(correct.values()), min(ambiguous.values())
    print(f"\nhardest correct   {ceiling:.4f}")
    print(f"easiest ambiguous {floor:.4f}")
    if floor <= ceiling:
        print(f"GAP CLOSED by {ceiling - floor:.4f} — no threshold separates these populations")
        return False
    print(f"gap               {floor - ceiling:.4f}  ({ceiling:.4f} .. {floor:.4f})")
    print(f"midpoint          {(ceiling + floor) / 2:.4f}   <- the value to pin")
    ok = ceiling <= threshold < floor
    print(f"threshold {threshold} is {'INSIDE' if ok else 'OUTSIDE'} the gap")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sweep-corpus",
        type=float,
        nargs="+",
        metavar="K",
        help="rebuild the index at these IDF_SATURATION values and report each band",
    )
    parser.add_argument(
        "--sweep-classes",
        action="store_true",
        help="report the band at 4, then all, classes — the stability the constant exists for",
    )
    parser.add_argument(
        "--sweep-identity",
        type=int,
        nargs="+",
        metavar="W",
        help="rebuild the index at these IDENTITY_TERM_WEIGHT values and report each band",
    )
    args = parser.parse_args(argv)

    from axiom.classify import candidates as candidates_module
    from axiom.classify.candidates import CandidateIndex
    from axiom.classify.classifier import DECISIVE_DOMINANCE
    from axiom.schema import load_default

    registry = load_default()

    if args.sweep_corpus:
        print("IDF_SATURATION sweep\n")
        rows = []
        for value in args.sweep_corpus:
            original = candidates_module.IDF_SATURATION
            candidates_module.IDF_SATURATION = value
            try:
                correct, ambiguous, _ = _band(CandidateIndex.build(registry))
            finally:
                candidates_module.IDF_SATURATION = original
            if not correct or not ambiguous:
                rows.append((value, None, None))
                continue
            rows.append((value, max(correct.values()), min(ambiguous.values())))
        print(f"{'K':>8} {'hardest correct':>17} {'easiest ambiguous':>19} {'gap':>9}")
        print("-" * 57)
        for value, ceiling, floor in rows:
            if ceiling is None:
                print(f"{value:>8} {'(incomplete)':>17}")
                continue
            print(f"{value:>8g} {ceiling:>17.4f} {floor:>19.4f} {floor - ceiling:>9.4f}")
        return 0

    if args.sweep_identity:
        print("IDENTITY_TERM_WEIGHT sweep\n")
        print(f"{'weight':>8} {'hardest correct':>17} {'easiest ambiguous':>19} {'gap':>9}")
        print("-" * 57)
        original = candidates_module.IDENTITY_TERM_WEIGHT
        try:
            for value in args.sweep_identity:
                candidates_module.IDENTITY_TERM_WEIGHT = value
                correct, ambiguous, _ = _band(CandidateIndex.build(registry))
                if not correct or not ambiguous:
                    print(f"{value:>8} {'(incomplete)':>17}")
                    continue
                ceiling, floor = max(correct.values()), min(ambiguous.values())
                print(f"{value:>8} {ceiling:>17.4f} {floor:>19.4f} {floor - ceiling:>9.4f}")
        finally:
            candidates_module.IDENTITY_TERM_WEIGHT = original
        return 0

    if args.sweep_classes:
        every = registry.class_codes
        subset = ["APP.KIT.DISHWASHER.BUILTIN", "LGT.LMP.GEN", "PLB.VLV.BALL.2PC",
                  "PLB.VLV.GATE.BRZ"]
        subset = [c for c in subset if c in every]
        print("band stability as the taxonomy grows\n")
        print(f"{'classes':>8} {'hardest correct':>17} {'easiest ambiguous':>19} {'gap':>9}")
        print("-" * 57)
        for label, codes in (("original", subset), ("all", every)):
            index = CandidateIndex(
                [
                    candidates_module._profile_for(registry, registry.product_class(code))
                    for code in codes
                ]
            )
            correct, ambiguous, _ = _band(index)
            if not correct or not ambiguous:
                print(f"{len(codes):>8} {'(incomplete)':>17}   {label}")
                continue
            ceiling, floor = max(correct.values()), min(ambiguous.values())
            print(
                f"{len(codes):>8} {ceiling:>17.4f} {floor:>19.4f} {floor - ceiling:>9.4f}   {label}"
            )
        return 0

    print(f"{len(registry.class_codes)} classes, "
          f"IDF_SATURATION = {candidates_module.IDF_SATURATION}\n")
    correct, ambiguous, singletons = _band(CandidateIndex.build(registry))
    return 0 if _report(correct, ambiguous, singletons, DECISIVE_DOMINANCE) else 1


if __name__ == "__main__":
    sys.exit(main())
