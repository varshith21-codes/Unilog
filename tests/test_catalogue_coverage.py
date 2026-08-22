"""Whole-catalogue classification: coverage as a floor, identity grounding as an absolute.

`test_lighting_class.py` established the pattern for a class with no labelled ground truth: assert
coverage as a floor because reading more of the cohort is an improvement, and assert precision
absolutely because there is no answer sheet to catch a regression in it. This file generalises that
from one class to all of them.

WHAT CAN AND CANNOT BE ASSERTED HERE, because the distinction is the whole design of this file.

There is no labelled classification ground truth anywhere in this repository. `data/golden/` holds
attribute values for valves and dishwashers and no class labels for anything else, so nothing here
can assert "this row belongs to that class" for the catalogue at large. Two things can be asserted
without it, and together they are worth more than a coverage percentage:

* **Identity grounding.** Every classified row must contain one of its winning class's own
  `identity_terms`. The candidate index guarantees this by construction, so this checks the
  guarantee rather than the behaviour — and it is the assertion that fails the moment a class is
  loosened into guessing, which is the failure mode that coverage alone rewards.

* **Non-contamination.** A class must not win rows outside its own cohort. Expressed per class as a
  ceiling on how many rows it may take, which catches a guard that has been widened to grab
  neighbours.

Coverage is asserted as a floor and deliberately NOT as a target. A classifier that assigned every
row to something would score 100% here and could be wrong on most of them; that is why the floor
sits alongside the two precision assertions rather than on its own.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest
from axiom.classify.candidates import tokenize
from axiom.classify.classifier import Classifier
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"

pytestmark = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="client sample not in this checkout"
)

# Measured with `python scripts/score_classification.py "Unihack_ Sample Dataset - Input.csv"`.
#
# Before this taxonomy existed the schema held four classes and 785 of 1,000 rows abstained with
# `no_viable_candidate` — the correct answer to "which of ball valve, gate valve, dishwasher or lamp
# is this composite deck board", and worth nothing to the client.
TOTAL_ROWS = 1000
COVERAGE_FLOOR = 980

# What the remaining twenty rows are, so the floor is read as a measurement rather than a shortfall.
#
# ELEVEN reach no candidate. They are genuinely miscellaneous: a heater kit, two insulated drink
# bottles, an unintelligible row ("TW52 WH INSIDE CAS VIN WRAPPED"), a vacuum paper bag, a nailer
# magazine, a grip, a miter sled, a hex key set written only as "Spks", and a combo kit whose
# description names no tool. A class built to absorb them would have to be defined on generic
# vocabulary, and would then win rows belonging to real classes. One is excluded on purpose:
# "97708 Police 800L Headlight", pinned by test_lighting_class.py.
#
# NINE abstain as `ambiguous_no_model`, which is a refusal to guess rather than a gap. Every one is
# genuinely dual-natured: "Kneeling Pad& Bttl Opener" is two products, a "Tape Light" is lighting
# whose first word is another class, a "Voltage Detector w/ LED" is an instrument with an indicator,
# a "Ratchet & Socket Set 4-Drawer Tool Box" is a hand tool in a box, a "Coil Roofing - Nailer Kit"
# is a power tool for roofing. These are what the adjudication layer exists for; on the
# deterministic path no model is attached, so they abstain with the runner-up and margin recorded.
UNCLASSIFIED_CEILING = TOTAL_ROWS - COVERAGE_FLOOR

# Per-class floors and ceilings for the cohorts large enough for a bound to mean something.
#
# The ceiling is the half that catches a regression. A guard that is widened to pick up a few more
# rows of its own cohort usually picks up its neighbours' too, and coverage rises while precision
# falls — which a floor alone would report as an improvement. Ceilings are set with headroom above
# the measured figure, because reading more of a genuine cohort is an improvement and swallowing a
# neighbouring one is not, and the gap between those is what the profiler measured.
#
#   class                          measured   floor   ceiling
COHORT_BOUNDS: dict[str, tuple[int, int]] = {
    "LGT.LMP.GEN":              (205, 260),   # 220 measured; profiler counted 242 lighting rows
    "BLD.DCK.BOARD":            (150, 185),   # 166; profiler counted 167
    "TOL.PWR.GEN":              (85, 125),    # 97; profiler counted 103
    "ABR.WHL.BONDED":           (40, 55),     # 46
    "BLD.RAIL.POST":            (37, 55),     # 45
    "TOL.HND.GEN":              (30, 46),     # 38
    "TOL.ACC.DRIVERBIT":        (30, 42),     # 35
    "ELC.DEV.WIRING":           (22, 34),     # 27
    "ABR.COATED.GEN":           (20, 32),     # 24
    "TOL.LAY.GEN":              (20, 30),     # 24
    "SAF.APP.GEN":              (20, 30),     # 24
    "TOL.ACC.SAWBLADE":         (20, 30),     # 24
    "APP.KIT.COOKING":          (19, 29),     # 23
    "APP.LND.GEN":              (12, 28),     # 22
    "TOL.PWR.BATTERY":          (17, 27),     # 21
    "ELC.BOX.GEN":              (13, 23),     # 17
    "APP.KIT.REFRIGERATION":    (12, 20),     # 16
    "BLD.PNL.SHEATHING":        (10, 20),     # 14
    "BLD.WDW.GEN":              (8, 18),      # 13
    "APP.KIT.COUNTERTOP":       (10, 18),     # 13
    "BLD.ROOF.GEN":             (9, 17),      # 12
    "APP.KIT.DISHWASHER.BUILTIN": (10, 12),   # 10; the two ground-truth rows live here
    "SAF.EYE.GEN":              (8, 14),      # 10
    "ELC.WIRE.GEN":             (6, 14),      # 9
    "LGT.FAN.CEILING":          (7, 12),      # 9
}

# The demo vertical. The client's file contains no valves at all, so these must win NOTHING — and
# that is a real assertion rather than a formality: the valve classes are the ones whose attribute
# vocabulary (`Port Type`, `Ball`, size fractions) historically attracted decor plates, bit
# assortments and grinding wheels.
CLASSES_WITH_NO_ROWS_IN_THIS_FILE = ("PLB.VLV.BALL.2PC", "PLB.VLV.GATE.BRZ")


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def outcome(registry):
    """Classify every row once and share it. No model client, on purpose.

    The deterministic batch path in `axiom.delivery.batch` builds `Classifier(registry)` with no
    client either, so an ambiguous case abstains rather than being resolved by coin flip. Measuring
    with a model attached would overstate what a real run of this pipeline delivers.
    """
    classifier = Classifier(registry)
    with open(SAMPLE, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    winners: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    ungrounded: list[tuple[str, str]] = []

    for row in rows:
        text = (row.get("Part_Desc") or "").strip()
        result = classifier.classify(text, sku=(row.get("Mfg_Part_Num") or "").strip() or None)
        methods[result.method] += 1
        code = result.class_code
        if code is None:
            continue
        winners[code] += 1
        terms = frozenset(
            token
            for term in registry.product_class(code).identity_terms
            for token in tokenize(term)
        )
        if terms and not (terms & set(tokenize(text))):
            ungrounded.append((code, text))

    return {
        "rows": len(rows),
        "winners": winners,
        "methods": methods,
        "ungrounded": ungrounded,
        "classified": sum(winners.values()),
    }


# --------------------------------------------------------------------- the sample itself


def test_the_sample_is_the_file_these_numbers_were_measured_against(outcome):
    assert outcome["rows"] == TOTAL_ROWS


# --------------------------------------------------------------------- precision


def test_every_classified_row_carries_an_identity_term_of_its_winner(outcome):
    """The one precision statement available with no labelled data, asserted absolutely.

    A row that classified without containing any of its winning class's identity terms means the
    guard was bypassed — either a class was added with no `identity_terms`, or the admission step
    stopped being consulted before scoring. Both are silent failures that cost precision everywhere
    and show up in no individual row.
    """
    ungrounded = outcome["ungrounded"]
    assert ungrounded == [], (
        f"{len(ungrounded)} row(s) classified with no identity term of their winning class:\n"
        + "\n".join(f"  {code}  {text}" for code, text in ungrounded[:20])
    )


def test_no_class_is_unguarded(registry):
    """A class with no identity_terms is a candidate for every query in the catalogue.

    Kept separate from the grounding test because it is the CAUSE rather than the symptom, and
    because it is checkable without the sample file: the grounding test skips a class it has no rows
    for, and an unguarded class added tomorrow might have none today.
    """
    unguarded = [
        code for code in registry.class_codes
        if not registry.product_class(code).identity_terms
    ]
    assert unguarded == [], (
        f"these classes are admitted for every query and will contaminate their neighbours: "
        f"{unguarded}"
    )


def test_the_demo_vertical_wins_nothing_in_a_file_that_contains_none_of_it(outcome):
    """The valve classes must take no rows. This file has no valves.

    Not a formality: these are the classes whose attribute vocabulary historically attracted a decor
    plate, a torsion-bit assortment and a grinding wheel, and the decor plate outscored every real
    dishwasher while doing it.
    """
    for code in CLASSES_WITH_NO_ROWS_IN_THIS_FILE:
        assert outcome["winners"][code] == 0, (
            f"{code} won {outcome['winners'][code]} row(s) in a file containing no valves"
        )


@pytest.mark.parametrize("code", sorted(COHORT_BOUNDS))
def test_each_cohort_stays_inside_its_measured_bounds(code, outcome):
    """Floor and ceiling together. The ceiling is the half that catches a widened guard.

    Loosening a guard to gain a few rows of its own cohort usually gains its neighbours' too, and
    coverage rises while precision falls — which a floor on its own reports as an improvement.
    """
    floor, ceiling = COHORT_BOUNDS[code]
    won = outcome["winners"][code]
    assert won >= floor, f"{code} fell to {won} rows, below its measured floor of {floor}"
    assert won <= ceiling, (
        f"{code} took {won} rows, above its ceiling of {ceiling} — check whether its "
        f"identity_terms have started matching a neighbouring cohort"
    )


# --------------------------------------------------------------------- coverage


def test_coverage_does_not_regress(outcome):
    """A floor, not a target. See this module's docstring for why it is not asserted alone."""
    classified = outcome["classified"]
    assert classified >= COVERAGE_FLOOR, (
        f"coverage regressed to {classified}/{TOTAL_ROWS} rows, below the measured floor of "
        f"{COVERAGE_FLOOR}"
    )


def test_abstentions_are_honest_rather_than_silent(outcome):
    """Every unclassified row must carry a REASON, not just a missing class.

    The two abstention methods mean different things and both are correct outcomes:
    `no_viable_candidate` says nothing in the schema matched, which is a coverage gap;
    `ambiguous_no_model` says two classes were too close to separate without adjudication, which
    is a refusal to guess. A row that abstained under any other method would be a bug rather than
    a judgement.
    """
    methods = outcome["methods"]
    abstained = outcome["rows"] - outcome["classified"]
    accounted = methods["no_viable_candidate"] + methods["ambiguous_no_model"]
    assert accounted == abstained, (
        f"{abstained} rows abstained but only {accounted} did so for a stated reason; "
        f"methods were {dict(methods)}"
    )


def test_most_rows_need_no_model_call(outcome):
    """Retrieval settles the great majority, which is what keeps this affordable.

    Cost is proportional to the rows retrieval CANNOT settle, so this is a budget assertion as much
    as an accuracy one. If it fails, classification has started paying for adjudication it used to
    get for free.
    """
    settled = outcome["methods"]["retrieval_only"] + outcome["methods"]["retrieval_decisive"]
    assert settled >= 0.97 * outcome["rows"], (
        f"only {settled} of {outcome['rows']} rows were settled by retrieval alone"
    )


def test_the_unclassified_tail_stays_small(outcome):
    """The complement of the coverage floor, asserted separately because it reads differently.

    Coverage as a percentage invites rounding; a row count does not. See the note beside
    UNCLASSIFIED_CEILING for what the remaining rows actually are — eleven genuinely miscellaneous
    products and nine honest refusals to guess between two plausible classes.
    """
    unclassified = outcome["rows"] - outcome["classified"]
    assert unclassified <= UNCLASSIFIED_CEILING, (
        f"{unclassified} rows reached no class, above the measured ceiling of "
        f"{UNCLASSIFIED_CEILING}"
    )
