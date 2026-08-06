"""Tests for constrained copy generation and the claim-check pass.

The claim check is the only thing standing between a fluent model and a catalogue full of
authoritative-sounding fiction. Its job is narrow and absolute: every number, standards
reference, material designation and regulated claim in generated prose must trace to an attribute
that is already publishable.

Two design choices are asserted here because both are easy to erode:

**The check is deterministic, not a second model.** An LLM judge shares the writer's failure mode
— both fluent, neither checkable — so quantities are verified arithmetically after unit conversion
and tokens by lookup.

**One bad claim fails the whole piece.** Not a score, not a threshold. One invented pressure
rating makes a description wrong, and averaging it against nine correct sentences would hide
exactly the thing worth finding.
"""

from __future__ import annotations

import pytest
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.generate import (
    Claim,
    ClaimKind,
    ClaimReport,
    ClaimVerdict,
    GeneratedCopy,
    build_fact_sheet,
    check_text,
    load_policy,
)
from axiom.schema import load_default
from axiom.validate import ReasoningFinding, ReasoningReport

CLASS_CODE = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def policy():
    return load_policy()


def span() -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp",
        document_id="ba100",
        document_sha256=SHA,
        quote="q",
        page=1,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        quote_verified=True,
        match_score=1.0,
    )


def value(
    code: str,
    canonical,
    display: str | None = None,
    *,
    status: ValueStatus = ValueStatus.AUTO_ACCEPTED,
    method: DerivationMethod = DerivationMethod.DOCUMENT_EXTRACTION,
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=display if display is not None else str(canonical),
        method=method,
        confidence=0.95,
        status=status,
        evidence=[span()],
    )


def record() -> ProductRecord:
    product = ProductRecord(
        tenant_id="demo",
        sku="BA-100-075",
        mpn="BA-100-075",
        brand="Milwaukee Valve",
        class_code=CLASS_CODE,
        schema_version=f"{CLASS_CODE}@v1",
    )
    for attribute_value in (
        value("nominal_size", Quantity(magnitude=19.05, unit="mm"), '3/4"'),
        value("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), "600 psi"),
        value("steam_pressure_rating", Quantity(magnitude=150.0, unit="psi"), "150 psi"),
        value("body_material", "Bronze C84400"),
        value("seat_material", "RPTFE"),
        value("port_type", "Full Port"),
        value("end_connection", "NPT Threaded"),
        value(
            "temperature_range",
            ValueRange(minimum=-28.9, maximum=185.6, unit="degC"),
            "-20 to 366 degF",
        ),
        value("approvals", ["UL listed", "CSA certified", "NSF/ANSI 61"], "UL, CSA, NSF/ANSI 61"),
    ):
        product.add_value(attribute_value)
    return product


@pytest.fixture
def sheet(registry):
    return build_fact_sheet(record(), registry)


# ===================================================================== the fact sheet


def test_only_publishable_values_reach_the_generator(registry):
    """A value awaiting review must not appear in a description that goes live before it does."""
    product = record()
    product.add_value(
        value("cv_flow_coefficient", 22.0, status=ValueStatus.QUEUED_FOR_REVIEW)
    )
    built = build_fact_sheet(product, registry)

    codes = {fact.attribute_code for fact in built.facts}
    assert "cv_flow_coefficient" not in codes
    assert "cv_flow_coefficient" in built.withheld_codes


def test_inferred_values_are_excluded(registry):
    """A guess restated as prose reads exactly like a measured fact."""
    product = record()
    product.add_value(
        value(
            "handle_type",
            "Lever",
            status=ValueStatus.QUEUED_FOR_REVIEW,
            method=DerivationMethod.STATISTICAL_DEFAULT,
        )
    )
    built = build_fact_sheet(product, registry)
    assert "handle_type" not in {f.attribute_code for f in built.facts}


def test_the_prompt_names_the_attributes_and_nothing_else(sheet):
    prompt = sheet.to_prompt()
    assert "BA-100-075" in prompt
    assert "600 psi" in prompt
    assert "VERIFIED ATTRIBUTES" in prompt
    # What is unknown is not mentioned; listing it invites the model to fill it in.
    assert "cv_flow_coefficient" not in prompt
    assert "unknown" not in prompt.casefold()


def test_quantities_are_indexed_in_both_unit_systems(sheet):
    """Copy written from a display value says 600 psi; the canonical range is in degC."""
    units = {(q.magnitude, q.unit) for q in sheet.quantities}
    assert any(unit == "psi" and abs(mag - 600.0) < 0.01 for mag, unit in units)
    assert any(unit == "degF" and abs(mag - 366.08) < 0.1 for mag, unit in units)
    assert any(unit == "in" and abs(mag - 0.75) < 0.01 for mag, unit in units)
    assert any(unit == "degF" and abs(mag - -20.02) < 0.1 for mag, unit in units)


def test_an_empty_fact_sheet_is_reported_not_silently_empty(registry):
    bare = ProductRecord(tenant_id="t", sku="EMPTY", class_code=CLASS_CODE)
    assert len(build_fact_sheet(bare, registry)) == 0


# ===================================================================== quantities


def test_a_correctly_restated_quantity_is_supported(sheet, policy):
    claims = check_text("Rated to 600 psi WOG.", sheet, policy)
    pressure = next(c for c in claims if c.kind is ClaimKind.QUANTITY)
    assert pressure.verdict is ClaimVerdict.SUPPORTED
    assert pressure.supported_by == "pressure_rating_wog"


def test_an_invented_quantity_is_caught(sheet, policy):
    """The single most damaging thing copy can do: a plausible, wrong specification."""
    claims = check_text("Rated to 1200 psi for demanding service.", sheet, policy)
    pressure = next(c for c in claims if c.kind is ClaimKind.QUANTITY)
    assert pressure.verdict is ClaimVerdict.UNSUPPORTED


def test_a_nearby_wrong_figure_is_still_wrong(sheet, policy):
    """650 is not 600. The tolerance is tight because there is no editorial reason to round."""
    claims = check_text("Rated to 650 psi.", sheet, policy)
    assert claims[0].verdict is ClaimVerdict.UNSUPPORTED


def test_a_quantity_is_matched_across_unit_systems(sheet, policy):
    """41.4 bar is 600 psi. Copy for a metric market must not be flagged for being metric."""
    claims = check_text("Rated to 41.4 bar.", sheet, policy)
    pressure = next(c for c in claims if c.kind is ClaimKind.QUANTITY)
    assert pressure.verdict is ClaimVerdict.SUPPORTED


def test_the_same_number_in_an_incompatible_unit_is_not_support(sheet, policy):
    """600 psi does not license '600 mm'. Matching on magnitude alone would allow it."""
    claims = check_text("Overall length 600 mm.", sheet, policy)
    length = next(c for c in claims if c.kind is ClaimKind.QUANTITY)
    assert length.verdict is ClaimVerdict.UNSUPPORTED


def test_both_bounds_of_a_range_are_checked(sheet, policy):
    claims = [
        c for c in check_text("Service from -20degF to 366degF.", sheet, policy)
        if c.kind is ClaimKind.QUANTITY
    ]
    assert len(claims) == 2
    assert all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)


def test_extending_a_range_beyond_its_bound_is_caught(sheet, policy):
    claims = [
        c for c in check_text("Service up to 500degF.", sheet, policy)
        if c.kind is ClaimKind.QUANTITY
    ]
    assert any(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_an_imperial_fraction_size_is_supported(sheet, policy):
    claims = [c for c in check_text('3/4" NPT connection.', sheet, policy)
              if c.kind is ClaimKind.QUANTITY]
    assert claims and all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)


def test_the_wrong_size_is_caught(sheet, policy):
    claims = [c for c in check_text('1-1/4" NPT connection.', sheet, policy)
              if c.kind is ClaimKind.QUANTITY]
    assert any(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


# ===================================================================== standards


def test_a_verified_standard_is_supported(sheet, policy):
    claims = [c for c in check_text("UL listed and CSA certified.", sheet, policy)
              if c.kind is ClaimKind.STANDARD]
    assert claims
    assert all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)


def test_an_invented_standard_is_caught(sheet, policy):
    """Reads as authoritative, disprovable by a buyer in one search."""
    claims = [c for c in check_text("Conforms to MSS SP-110.", sheet, policy)
              if c.kind is ClaimKind.STANDARD]
    assert claims
    assert all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_the_wrong_number_on_a_real_standard_is_caught(sheet, policy):
    """The record has NSF/ANSI 61. NSF/ANSI 372 is a different standard about lead content."""
    claims = [c for c in check_text("Certified to NSF/ANSI 372.", sheet, policy)
              if c.kind is ClaimKind.STANDARD]
    assert claims
    assert all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_sentence_punctuation_does_not_reject_a_real_standard(sheet, policy):
    """`ASME B16.34` contains a dot, so the pattern must allow one — which means a sentence
    ending in a standard swallows the full stop. Comparing `NSF/ANSI 61.` against the record
    would then fail to match the very standard the product holds."""
    claims = [c for c in check_text("This valve is certified to NSF/ANSI 61.", sheet, policy)
              if c.kind is ClaimKind.STANDARD]
    assert claims
    assert all(c.verdict is ClaimVerdict.SUPPORTED for c in claims), (
        "honest copy was rejected for its punctuation"
    )


@pytest.mark.parametrize("closer", [".", ",", ";", ")", "!"])
def test_standards_are_trimmed_of_trailing_punctuation(sheet, policy, closer):
    tokens = policy.find_standards(f"Certified to NSF/ANSI 61{closer}")
    assert tokens == ["NSF/ANSI 61"]


# ===================================================================== designations


def test_a_verified_alloy_is_supported(sheet, policy):
    claims = [c for c in check_text("Bronze C84400 body.", sheet, policy)
              if c.kind is ClaimKind.DESIGNATION]
    assert claims and all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)


def test_an_invented_alloy_is_caught(sheet, policy):
    """A specific alloy is a specification, so an invented one is a specification error."""
    claims = [c for c in check_text("Bronze C89833 body.", sheet, policy)
              if c.kind is ClaimKind.DESIGNATION]
    assert claims and all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_an_invented_stem_grade_is_caught(sheet, policy):
    claims = [c for c in check_text("316 stainless stem.", sheet, policy)
              if c.kind is ClaimKind.DESIGNATION]
    assert claims and all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


# ===================================================================== regulated claims


def test_a_lead_free_claim_without_the_attribute_is_caught(sheet, policy):
    """The record says the body is C84400, which is a leaded alloy. This claim is not merely
    unsupported, it is contradicted."""
    claims = [c for c in check_text("Lead-free bronze construction.", sheet, policy)
              if c.kind is ClaimKind.REGULATED]
    assert claims
    assert all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_a_lead_free_claim_needs_the_flag_to_be_true(registry, policy):
    """Presence is not enough. A False value must not licence the claim."""
    product = record()
    product.add_value(value("lead_free_compliant", False, "No"))
    built = build_fact_sheet(product, registry)

    claims = [c for c in check_text("Lead-free construction.", built, policy)
              if c.kind is ClaimKind.REGULATED]
    assert all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_a_lead_free_claim_is_allowed_when_substantiated(registry, policy):
    product = record()
    product.add_value(value("lead_free_compliant", True, "Yes"))
    built = build_fact_sheet(product, registry)

    claims = [c for c in check_text("Lead-free construction.", built, policy)
              if c.kind is ClaimKind.REGULATED]
    assert claims
    assert all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)
    assert claims[0].supported_by == "lead_free_compliant"


def test_a_potable_water_claim_needs_its_own_attribute(sheet, policy):
    """NSF-61 in the approvals list does not substantiate it; the attribute does."""
    claims = [c for c in check_text("Safe for drinking water systems.", sheet, policy)
              if c.kind is ClaimKind.REGULATED]
    assert claims
    assert all(c.verdict is ClaimVerdict.UNSUPPORTED for c in claims)


def test_a_ul_claim_is_substantiated_by_approvals(sheet, policy):
    claims = [c for c in check_text("This valve is UL listed.", sheet, policy)
              if c.kind is ClaimKind.REGULATED]
    assert claims
    assert all(c.verdict is ClaimVerdict.SUPPORTED for c in claims)


# ===================================================================== banned language


@pytest.mark.parametrize(
    "phrase",
    [
        "the best ball valve for the job",
        "industry-leading performance",
        "guaranteed for life",
        "maintenance-free operation",
        "superior sealing",
        "fully compliant with all codes",
        "virtually leak-free",
    ],
)
def test_unsubstantiable_language_fails_on_sight(sheet, policy, phrase):
    """No fact sheet can support a comparative or a promise, so these are not checked, they
    are rejected."""
    claims = check_text(phrase, sheet, policy)
    assert any(c.verdict is ClaimVerdict.BANNED for c in claims)


def test_plain_specification_prose_passes_cleanly(sheet, policy):
    """The control. If honest copy failed, the checker would be unusable."""
    text = (
        'Milwaukee Valve BA-100-075 is a 3/4" two-piece bronze ball valve with a full port '
        "and NPT threaded ends. The bronze C84400 body is rated to 600 psi WOG, with a "
        "150 psi steam rating and RPTFE seats. UL listed and CSA certified."
    )
    claims = check_text(text, sheet, policy)
    offending = [c for c in claims if c.verdict.blocks_publication]
    assert offending == [], f"honest copy was flagged: {[c.to_dict() for c in offending]}"
    assert len(claims) > 5, "the checker should be finding real claims here, not nothing"


# ===================================================================== the report


def test_one_bad_claim_fails_the_whole_piece(sheet, policy):
    """Not a score. One invented rating makes a description wrong."""
    from axiom.generate import check_copy

    report = check_copy(
        {
            "headline": '3/4" Bronze Ball Valve',
            "short_description": "Rated to 600 psi WOG with RPTFE seats.",
            "long_description": "Also conforms to MSS SP-110.",
        },
        sheet,
        policy,
    )
    assert report.passed is False
    assert len(report.unsupported) == 1
    assert report.unsupported[0].field_name == "long_description"


def test_a_clean_piece_passes(sheet, policy):
    from axiom.generate import check_copy

    report = check_copy(
        {
            "headline": '3/4" Bronze Ball Valve',
            "short_description": "Full port, NPT threaded, rated to 600 psi WOG.",
        },
        sheet,
        policy,
    )
    assert report.passed is True
    assert report.summary()["unsupported"] == 0


def test_claims_report_which_field_they_came_from(sheet, policy):
    from axiom.generate import check_copy

    report = check_copy({"bullets": "Rated to 9000 psi"}, sheet, policy)
    assert report.unsupported[0].field_name == "bullets"


def test_the_report_is_serialisable(sheet, policy):
    import json

    from axiom.generate import check_copy

    report = check_copy({"headline": "Rated to 600 psi"}, sheet, policy)
    json.dumps([claim.to_dict() for claim in report.claims])
    json.dumps(report.summary())


# ===================================================== the publication gate, including L6
#
# `published` is the single property the console and the channel exports both read. Every gate
# that ran has to be visible in it, or the UI ends up showing "published" beside a finding that
# says otherwise.


def clean_copy(**overrides) -> GeneratedCopy:
    """Copy that passes the claim check, so each test varies exactly one thing."""
    fields = {
        "sku": "BA-100-075",
        "headline": "Bronze ball valve, 3/4 in NPT",
        "report": ClaimReport(
            claims=[
                Claim(
                    kind=ClaimKind.DESIGNATION,
                    text="Bronze C84400",
                    verdict=ClaimVerdict.SUPPORTED,
                    reason="appears in verified attribute 'body_material'",
                )
            ]
        ),
    }
    fields.update(overrides)
    return GeneratedCopy(**fields)


def formal(kind: str, rules: tuple[str, ...] = ()) -> ReasoningReport:
    return ReasoningReport(findings=[("claim", ReasoningFinding(kind=kind, rules=rules))])


def test_copy_publishes_when_the_claim_check_passes_and_l6_was_not_run():
    """L6 is optional. Not deploying a policy must not stop copy publishing."""
    copy = clean_copy()

    assert copy.formal is None
    assert copy.published is True


def test_a_proven_contradiction_withholds_copy_that_passed_the_claim_check():
    """The case that justifies the layer existing.

    Every claim traces to a verified attribute, so the claim checker is satisfied — and the
    sentence is still impossible. Only L6 catches this, and it has to be able to block.
    """
    copy = clean_copy(formal=formal("invalid", ("RLEADEDALLOY",)))

    assert copy.report.passed is True, "the claim check is satisfied"
    assert copy.published is False, "and the copy is still withheld"


def test_an_unreachable_policy_withholds_copy():
    """"We could not check" must not resolve to the same outcome as "we checked"."""
    report = ReasoningReport(error="the reasoning policy could not be reached: ExpiredToken")
    copy = clean_copy(formal=report)

    assert copy.published is False


def test_an_indeterminate_verdict_does_not_withhold_copy():
    """A solver that formed no opinion has produced nothing to act on. Blocking on it would
    make the layer a liability rather than a gate."""
    copy = clean_copy(formal=formal("tooComplex"))

    assert copy.formal.conclusive is False
    assert copy.published is True


def test_an_unsupported_claim_still_blocks_regardless_of_l6():
    """The two gates are independent; passing one does not excuse the other."""
    copy = clean_copy(
        report=ClaimReport(
            claims=[
                Claim(
                    kind=ClaimKind.QUANTITY,
                    text="800 psi",
                    verdict=ClaimVerdict.UNSUPPORTED,
                    reason="no verified attribute states this figure",
                )
            ]
        ),
        formal=formal("satisfiable"),
    )

    assert copy.published is False


def test_the_serialised_copy_distinguishes_unchecked_from_clean():
    """A console rendering `formal_check` needs to tell "not checked" from "checked, clean".
    Serialising an absent report as an empty one would read as a clean bill of health."""
    assert clean_copy().to_dict()["formal_check"] is None

    checked = clean_copy(formal=formal("satisfiable")).to_dict()["formal_check"]
    assert checked is not None
    assert checked["passed"] is True
    assert checked["conclusive"] is True
