"""The lighting class: the first one added to close a coverage gap rather than to measure.

Lighting is the largest cohort in the client's 1,000-row sample at 208 rows. Before this class,
990 of 1,000 rows abstained with `no_viable_candidate` — the correct answer to "which of ball
valve, gate valve or dishwasher is this LED tube", and worth nothing to the client.

There is no labelled ground truth for lighting anywhere in this repository, so these tests cannot
assert accuracy the way `test_dishwasher_class.py` does against the client's own rows. What they
can assert, and what actually protects the category, is that every value produced traces to a
substring the supplier wrote, that the identity guard has not been loosened, and that the two
domain traps in this vocabulary stay shut.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.classify.candidates import CandidateIndex
from axiom.extract.description import (
    AbbreviationTable,
    extract_from_description,
    to_attribute_values,
)
from axiom.normalize import normalize_all
from axiom.schema import Requirement, load_default

LIGHTING = "LGT.LMP.GEN"
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"

# Real strings from the client's sample. Every expectation below is readable off the string.
REAL_ROWS = [
    (
        '141465 15W Flor 18" T12 27k',
        {"wattage": "15W", "lamp_technology": "Flor", "lamp_shape": "T12"},
    ),
    (
        "467316 150W Sodium Med 21k",
        {"wattage": "150W", "lamp_technology": "Sodium", "lamp_base": "Med"},
    ),
    (
        "565374 75W Led A19 Med 27k 4pk",
        {
            "wattage": "75W",
            "lamp_technology": "Led",
            "lamp_shape": "A19",
            "lamp_base": "Med",
        },
    ),
    ("45573BK Kichler Wall Light", {"luminaire_form": "Wall"}),
    ("65-1224 4' Led Strip Light", {"lamp_technology": "Led", "luminaire_form": "Strip"}),
]


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def abbreviations():
    return AbbreviationTable.load()


@pytest.fixture(scope="module")
def index(registry):
    return CandidateIndex.build(registry)


def read(text, registry, abbreviations):
    result = extract_from_description(
        text, registry=registry, class_code=LIGHTING, abbreviations=abbreviations
    )
    return {m.attribute_code: m for m in result.matches}


# --------------------------------------------------------------------- the published layout


def test_the_grid_bindings_fit_the_format(registry):
    """A binding past the last slot is dropped by the builder with a note on every row.

    The format carries fifty attribute slots. The dishwasher class binds fifteen because that is
    what the client's ground truth showed for dishwashers, which reads like a ceiling and is not
    one — worth a test, because believing it would cost a class real specifications for nothing.
    """
    from axiom.delivery import load_default as load_format

    named = {
        "warranty_terms",
        "included_technology",
        "approvals",
        "country_of_origin",
        "prop65_warning_required",
        "gtin",
    }
    definition = registry.product_class(LIGHTING)
    grid = [b for b in definition.slot_bindings() if b.code not in named]
    capacity = load_format().slots("attribute_grid", "label")

    assert capacity == 50, "the shipped delivery format changed shape"
    assert len(grid) <= capacity, (
        f"{len(grid)} grid bindings against {capacity} slots — the surplus is dropped with a note "
        f"on every row: {[b.code for b in grid[capacity:]]}"
    )


def test_the_grid_order_is_pinned(registry):
    """Reordering shifts every subsequent column in a file the client ingests.

    Unlike the dishwasher grid this order is ours rather than copied from ground truth, which makes
    it no less binding once shipped — it just means this test is the only record of it.
    """
    definition = registry.product_class(LIGHTING)
    assert [b.code for b in definition.slot_bindings()][:17] == [
        "product_series",
        "luminaire_form",
        "lamp_technology",
        "wattage",
        "color_temperature",
        "lumen_output",
        "voltage_rating",
        "voltage_range_supported",
        "lamp_shape",
        "lamp_base",
        "beam_angle",
        "color_rendering_index",
        "rated_life",
        "dimmable",
        "nominal_length",
        "pack_quantity",
        "finish_color",
    ]


def test_only_what_a_bulb_and_a_fixture_share_is_required(registry):
    """A hardwired wall fixture has no cap and no envelope designation.

    The class deliberately covers lamps and luminaires together, so requiring a lamp-only
    attribute would score every fixture in the cohort as incomplete for lacking something it
    cannot have.
    """
    definition = registry.product_class(LIGHTING)
    assert set(definition.required_codes) == {"lamp_technology", "wattage"}
    for code in ("lamp_shape", "lamp_base", "beam_angle"):
        assert definition.binding(code).requirement is Requirement.OPTIONAL


def test_the_class_publishes_no_generated_descriptions(registry):
    """No labelled lighting row exists, so a description recipe would be an invented template.

    Eleven blank cells score as missed. Eleven plausible sentences score as wrong, and a client
    cannot tell the second kind from a correct one without checking every row by hand.
    """
    recipes = REPO_ROOT / "schema" / "descriptions"
    books = {p.stem for p in recipes.glob("*.yaml")}
    assert "lighting_general" not in books
    assert "lamp_general" not in books


# --------------------------------------------------------------------- identity


def test_the_identity_guard_admits_the_cohort(index):
    """Every shape the three lighting suppliers actually write."""
    for text in (
        '141465 15W Flor 18" T12 27k',
        "801274 10w LED 6\" Retro 50k",
        "45297BK Kichler Wall Lt",
        "62-1852 14\" Satco Led Ceiling Lt",
        "65-771R3 Nuvo Highbay Light",
        "467316 150W Sodium Med 21k",
    ):
        ranked = index.search(text)
        assert ranked, f"{text!r} produced no candidate at all"
        assert ranked[0].code == LIGHTING, f"{text!r} ranked {ranked[0].code} first"


def test_the_identity_guard_is_no_looser_than_before(index):
    """The six strings pinned by test_classify, re-checked against the new class.

    Adding a class adds vocabulary, and vocabulary is how false positives happen. This class binds
    `wattage`, `voltage_rating` and `finish_color`, which are exactly as promiscuous as the
    `Port Type` and `Plug Type` that made a decor plate look like a ball valve.
    """
    for text in (
        "5522-5EV 2 Port Decor Plate",
        "1x6-20' Castle Gate Grooved - Landmark Azek PVC Decking",
        "R5GSRA1THD 15A GFCI Plug",
        "JWBS-14SFX 14in Bandsaw JTP-714400K",
        "IBMG90K003 Vessel Impact Ball Torsion Bit Assort 5pc",
        '49-94-0533 Milw 7"x1/4"x7/8" Metal Grinding Wheel',
        # Not in the original six: a headlight, which contains "light" as a substring but
        # tokenises whole. Included because a substring guard would admit it and be wrong.
        "97708 Police 800L Headlight",
    ):
        assert index.search(text) == [], f"{text!r} should not be a candidate for any class"


# --------------------------------------------------------------------- extraction


@pytest.mark.parametrize(("text", "expected"), REAL_ROWS)
def test_real_supplier_strings_yield_their_stated_values(text, expected, registry, abbreviations):
    matches = read(text, registry, abbreviations)
    for code in expected:
        assert code in matches, f"{code} not read from {text!r}; got {sorted(matches)}"


@pytest.mark.parametrize(("text", "expected"), REAL_ROWS)
def test_every_value_quotes_the_substring_it_came_from(text, expected, registry, abbreviations):
    """The property that makes this category auditable without ground truth.

    Accuracy cannot be asserted against a labelled row that does not exist. What can be asserted
    is that the value was read from a span of the supplier's own string and that the span really
    says what the value claims — which is checkable by slicing the original text.
    """
    matches = read(text, registry, abbreviations)
    for code in expected:
        match = matches[code]
        span = text[match.start : match.end]
        assert span, f"{code} produced an empty span"
        assert span.lower() in text.lower()
        assert span.lower() == expected[code].lower(), (
            f"{code} cites {span!r} but the expectation was {expected[code]!r}"
        )


def test_wattage_normalises_to_watts_with_a_unit(registry, abbreviations):
    result = extract_from_description(
        '141465 15W Flor 18" T12 27k',
        registry=registry,
        class_code=LIGHTING,
        abbreviations=abbreviations,
    )
    values, _ = normalize_all(
        to_attribute_values(result, document_id="item-master", document_sha256="0" * 64),
        registry,
        class_code=LIGHTING,
    )
    wattage = next(v for v in values if v.attribute_code == "wattage")
    assert wattage.value_canonical.magnitude == pytest.approx(15.0)
    assert wattage.value_canonical.unit == "W"


# --------------------------------------------------------------------- the two traps


def test_the_colour_temperature_shorthand_is_never_read_literally(registry, abbreviations):
    """`27k` means 2700 K in the lighting trade. It must not become 27 K.

    27 kelvin is colder than liquid nitrogen. The expansion is real and standard, but there is no
    labelled lighting row here to verify it against, and a systematic 100x error across 208 rows
    is worse than 208 blank cells. So the attribute exists, its plausible_range starts at 1500 to
    refuse a literal reading, and nothing in this release performs the expansion.

    This test fails the day someone adds the expansion without ground truth behind it.
    """
    for text in (
        '141465 15W Flor 18" T12 27k',
        "467316 150W Sodium Med 21k",
        "573989 40W Led Med 27K 4pk",
    ):
        matches = read(text, registry, abbreviations)
        assert "color_temperature" not in matches, (
            f"colour temperature was read from {text!r} as "
            f"{matches['color_temperature'].value_raw!r}; the shorthand needs ground truth first"
        )

    # And if it ever were read literally, L3 must refuse it.
    definition = registry.attribute("color_temperature")
    low, _high = definition.plausible_range
    assert low > 27.0, "a literal reading of '27k' would pass the plausibility floor"


def test_retro_does_not_become_a_downlight(registry, abbreviations):
    """"Retro" says retrofit, and a retrofit is as often a lamp as a fitting.

    `801274 10w LED 6" Retro 50k` genuinely is a recessed downlight retrofit, so the tempting
    entry would be right on this row. One token cannot decide between a fitting and a bulb, and
    being right by luck on the row you looked at is how a whole category gets mislabelled.
    """
    matches = read('801274 10w LED 6" Retro 50k', registry, abbreviations)
    assert "luminaire_form" not in matches
    # The wattage and the technology are still read; only the guess is declined.
    assert matches["wattage"].value_raw.lower() == "10w"
    assert matches["lamp_technology"].value_raw.upper() == "LED"


def test_the_efficacy_rule_catches_a_decimal_error(registry):
    """Lumens over watts above 250 lm/W is above the laboratory ceiling for white LED.

    Both figures are individually plausible, so nothing else in the stack catches it — which is
    why this is an error rather than a warning.
    """
    definition = registry.product_class(LIGHTING)
    rule = next(
        r
        for r in definition.cross_field_rules
        if r.id == "R_EFFICACY_IS_PHYSICALLY_POSSIBLE"
    )
    assert rule.severity.value == "error"
    assert set(rule.references) == {"lumen_output", "wattage"}


# --------------------------------------------------------------------- coverage, measured


@pytest.mark.skipif(not SAMPLE.is_file(), reason="client sample not in this checkout")
def test_coverage_on_the_client_sample_does_not_regress(registry, index, abbreviations):
    """The number this class was added for, asserted as a floor.

    Measured at 204 lighting rows of 1,000 with zero rows classified that carry no lighting term.
    Precision is asserted absolutely; coverage as a floor, because reading more of the cohort is
    an improvement and reading less is a regression.
    """
    with open(SAMPLE, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1000

    identity = frozenset(
        token
        for term in registry.product_class(LIGHTING).identity_terms
        for token in term.lower().split()
    )

    classified, values, fabricated = 0, 0, []
    for row in rows:
        text = row["Part_Desc"]
        ranked = index.search(text)
        if not ranked or ranked[0].code != LIGHTING:
            continue
        classified += 1
        # Every classified row must carry an identity token. The guard guarantees it; this checks
        # the guarantee rather than trusting it.
        if not (identity & set(text.lower().replace('"', " ").split())):
            fabricated.append(text)
        result = extract_from_description(
            text, registry=registry, class_code=LIGHTING, abbreviations=abbreviations
        )
        for match in result.matches:
            span = text[match.start : match.end]
            assert span, f"empty span for {match.attribute_code} in {text!r}"
            values += 1

    assert not fabricated, (
        "rows classified as lighting with no lighting term in the source:\n"
        + "\n".join(fabricated[:10])
    )
    assert classified >= 204, f"lighting coverage regressed to {classified} rows (was 204)"
    assert values >= 380, f"extracted values regressed to {values} (was 393)"
