"""A verified quote is not the same as a supporting quote.

These tests pin the distinction that the rest of the trust layer depends on. Quote verification
answers "is this text really in the document"; entailment answers "does this text say this
value". The gap between the two is where a fabrication can arrive wearing the uniform of a
checked fact, which is worse than an obvious one.

The cases here are not invented. Every ``UNSUPPORTED`` example below was produced by a real
model against a real datasheet in the golden set, passed quote verification at
``match_score 1.0``, and was published as a citable value.
"""

from __future__ import annotations

import pytest
from axiom.extract.entailment import Support, check_entailment
from axiom.schema import load_default


@pytest.fixture(scope="module")
def registry():
    return load_default()


def verdict(registry, code, value_raw, quote, table_ref=None):
    return check_entailment(
        value_raw, quote, registry.attribute(code), table_ref=table_ref
    )


# ------------------------------------------------------------------ header cells


def test_a_table_header_cannot_state_a_value(registry):
    """The failure this module was written for.

    A model returned ``selling_uom = "Each"`` citing ``"Ctn Qty"`` — the header of the
    carton-quantity column. The quote verified perfectly, because those words are on the page.
    A header names what a column means; it never states a value for a part.
    """
    v = verdict(registry, "selling_uom", "Each", "Ctn Qty", table_ref="t1:r0:c5")
    assert v.support is Support.UNSUPPORTED
    assert not v.publishable
    assert "header" in (v.detail or "")


def test_a_data_row_is_not_treated_as_a_header(registry):
    v = verdict(registry, "port_type", "Full Port", 'DN15 1/2" Full 15.0', table_ref="t1:r1:c3")
    assert v.support is Support.SUPPORTED


# ------------------------------------------------------------------------- enums


def test_an_enum_the_quote_never_names_is_dropped(registry):
    """The datasheet states no end connection anywhere, so every answer is invented."""
    v = verdict(
        registry,
        "end_connection",
        "NPT Threaded",
        "The 77C is a two-piece bronze ball valve intended for general service.",
    )
    assert v.support is Support.UNSUPPORTED


def test_an_under_read_enum_is_corrected_to_the_more_specific_value(registry):
    """"Seats are reinforced PTFE" contains both ``ptfe`` and ``reinforced ptfe``.

    The two matches cover the same text, so the longer one is simply a better reading of it.
    The evidence outranks the model's paraphrase of the evidence.
    """
    v = verdict(registry, "seat_material", "PTFE", "Seats are reinforced PTFE.")
    assert v.support is Support.CORRECTED
    assert registry.attribute("seat_material").resolve_allowed(v.value_raw) == "RPTFE"


def test_an_already_specific_enum_is_left_alone(registry):
    v = verdict(registry, "seat_material", "RPTFE", "Seats are reinforced PTFE.")
    assert v.support is Support.SUPPORTED
    assert v.value_raw == "RPTFE"


def test_two_conflicting_values_in_one_quote_are_not_resolved_here(registry):
    """``FNPT`` and ``solder ends`` sit in different parts of the quote.

    That is a contradiction in the document, not a more specific reading of one phrase.
    Picking the longer surface form would settle a real engineering conflict by string length,
    so entailment reports the claim as stated and leaves adjudication to the rules layer.
    """
    v = verdict(registry, "end_connection", "NPT Threaded", '1/2" FNPT x FNPT solder ends')
    assert v.support is Support.SUPPORTED


def test_an_alias_inside_a_longer_word_does_not_count(registry):
    """Without a boundary check the alias ``fp`` matches ``fpm`` and nearly everything passes."""
    v = verdict(registry, "port_type", "Full Port", "the fpm rating is high")
    assert v.support is Support.UNSUPPORTED


def test_a_bare_column_value_resolves_through_its_alias(registry):
    """Ordering tables print ``Full``, not ``Full Port``."""
    v = verdict(registry, "port_type", "Full Port", '77C-103 DN15 1/2" Full 15.0 24')
    assert v.support is Support.SUPPORTED


# ------------------------------------------------------------------ multi-valued


def test_an_unmentioned_certification_is_pruned_from_the_list(registry):
    """Fabricated approvals are usually additive, and they are the costliest claim here.

    Dropping the member the quote does not mention keeps the two that are real, rather than
    discarding a mostly-correct list.
    """
    v = verdict(
        registry,
        "approvals",
        "UL, NSF/ANSI 61",
        "certified to NSF/ANSI 61 and NSF/ANSI 372 for potable water",
    )
    assert v.support is Support.CORRECTED
    assert "UL" not in v.value_raw
    assert "61" in v.value_raw


def test_a_fully_invented_list_is_dropped(registry):
    v = verdict(registry, "approvals", "UL, CSA", "certified to NSF/ANSI 61 for potable water")
    assert v.support is Support.UNSUPPORTED


def test_a_fully_supported_list_passes_unchanged(registry):
    v = verdict(
        registry,
        "approvals",
        "NSF/ANSI 61, NSF/ANSI 372",
        "certified to NSF/ANSI 61 and NSF/ANSI 372 for potable water",
    )
    assert v.support is Support.SUPPORTED
    assert v.value_raw == "NSF/ANSI 61, NSF/ANSI 372"


# ---------------------------------------------------------------------- numerics


def test_a_magnitude_absent_from_its_own_citation_is_dropped(registry):
    """Reading the wrong row of an ordering table is undetectable by quote verification.

    Both rows are real text on the page. Only the value's own numbers can tell them apart.
    """
    v = verdict(
        registry,
        "cv_flow_coefficient",
        "49.0",
        '77C-105R DN25 1" Reduced 21.0 12',
        table_ref="t1:r4:c4",
    )
    assert v.support is Support.UNSUPPORTED


def test_the_right_row_is_supported(registry):
    v = verdict(
        registry,
        "cv_flow_coefficient",
        "49.0",
        '77C-105 DN25 1" Full 49.0 12',
        table_ref="t1:r3:c4",
    )
    assert v.support is Support.SUPPORTED


def test_magnitudes_compare_numerically_not_textually(registry):
    """15 and 15.0 are the same number, and a string compare would reject the citation."""
    v = verdict(registry, "cv_flow_coefficient", "15", "Cv 15.0")
    assert v.support is Support.SUPPORTED


def test_every_bound_of_a_range_must_be_present(registry):
    v = verdict(registry, "temperature_range", "-20 degF to 365 degF", "rated from -20 degF")
    assert v.support is Support.UNSUPPORTED


def test_a_range_with_both_bounds_passes(registry):
    v = verdict(
        registry,
        "temperature_range",
        "-20 degF to 365 degF",
        "Operating temperature is rated from -20 degF to 365 degF continuous.",
    )
    assert v.support is Support.SUPPORTED


def test_a_deferred_numeric_value_is_left_to_normalisation(registry):
    """"Consult factory" carries no digits, so entailment has nothing to check."""
    v = verdict(registry, "cv_flow_coefficient", "Consult factory", "Flow Coefficient (Cv)")
    assert v.support is Support.NOT_APPLICABLE


# ----------------------------------------------------------------------- strings


def test_a_string_must_appear_in_its_quote(registry):
    v = verdict(registry, "country_of_origin", "Taiwan", "Made in the United States.")
    assert v.support is Support.UNSUPPORTED


def test_a_string_present_in_the_quote_passes(registry):
    v = verdict(registry, "country_of_origin", "Taiwan", "Country of origin: Taiwan.")
    assert v.support is Support.SUPPORTED


# ---------------------------------------------------------------------- booleans


def test_booleans_are_exempt(registry):
    """``lead_free_compliant: true`` is a conclusion drawn from an alloy and a standard.

    The literal token "true" will never appear in the quote, so demanding it would reject every
    correct answer. These attributes carry a strict evidence requirement and are governed by the
    cross-field rules instead.
    """
    v = verdict(registry, "lead_free_compliant", "true", "lead-free bronze alloy C89833")
    assert v.support is Support.NOT_APPLICABLE
    assert v.publishable


# ------------------------------------------------------------------------ shared


def test_an_empty_value_is_never_supported(registry):
    assert verdict(registry, "port_type", "  ", "Full").support is Support.UNSUPPORTED


def test_an_empty_quote_is_never_support(registry):
    assert verdict(registry, "port_type", "Full Port", "").support is Support.UNSUPPORTED


# ------------------------------------------- values in the source's own phrasing
#
# The first version of this gate resolved the whole value_raw as an exact alias, which rejected
# nine correct values across two datasheets because the model had answered in the document's
# wording rather than the schema's. A trust layer that discards correct values to look strict
# is not safer, it is just less useful — and the abstentions it produces are indistinguishable
# from genuine gaps, so the damage is invisible in a hallucination count.


@pytest.mark.parametrize(
    ("code", "value_raw", "quote"),
    [
        (
            "end_connection",
            "NPT threaded, female both ends",
            "End Connection ................. NPT threaded, female both ends",
        ),
        (
            "end_connection",
            "Solder ends, C x C",
            "End Connection ................. Solder ends, C x C",
        ),
        (
            "approvals",
            "UL listed, MSS SP-80",
            "Approvals ...................... UL listed, MSS SP-80",
        ),
        (
            "approvals",
            "UL listed, CSA certified, NSF/ANSI 61",
            "Approvals ...................... UL listed, CSA certified, NSF/ANSI 61",
        ),
        (
            "handle_type",
            "Handwheel",
            "Handwheel ...................... Malleable Iron",
        ),
    ],
)
def test_a_value_phrased_the_way_the_source_phrases_it_is_supported(
    registry, code, value_raw, quote
):
    """Every one of these was a real model answer against a real datasheet in the golden set."""
    assert check_entailment(value_raw, quote, registry.attribute(code)).support is (
        Support.SUPPORTED
    )


def test_verbosity_does_not_defeat_the_fabrication_check(registry):
    """The permissive reading of the value must not become a permissive reading of the quote."""
    v = verdict(
        registry,
        "end_connection",
        "NPT threaded, female both ends",
        "The 77C is a two-piece bronze ball valve intended for general service.",
    )
    assert v.support is Support.UNSUPPORTED
