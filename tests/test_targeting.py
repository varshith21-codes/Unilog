"""Tests for the targeting gate: does this document describe the product we asked about?

This exists because of a measured failure, not a hypothetical one. Handed a gate valve datasheet
and asked for a ball valve part number, extraction returned twelve confident values — and every
single one carried a quote that verified, because the words genuinely were in the document. They
described a different product.

That bounds the system's central claim in a way worth stating precisely. The evidence contract
guarantees that a published value can be traced to text in its source. It does **not** guarantee
that the text is about the requested product, and no amount of stricter quote checking would
help: the citation is real. Wrong-product attribution is a targeting failure.

``scripts/run_adversarial.py`` measured 39 fabrications out of 64 requested attributes across
three mismatched pairings before the gate existed, and 0 after. These tests keep it that way
without needing a model call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core.evidence import DocumentType, SourceDocument
from axiom.core.gaps import GapReason, RecommendedAction
from axiom.docintel import MIN_SKU_CHARS, find_sku, parse_text
from axiom.extract import Extractor, ModelCascade, StubModelClient
from axiom.normalize import mpn_variants
from axiom.schema import load_default

BALL_CLASS = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60

GATE_DATASHEET = """\
NIBCO T-113 SERIES
Bronze Gate Valve, Solid Wedge, Rising Stem            Rev B  2025-03

SPECIFICATIONS
  Body Material .................. Bronze C83600
  Wedge .......................... Solid Bronze
  Pressure Rating ................ 200 PSI WOG
  End Connection ................. NPT threaded

ORDERING INFORMATION
  Part Number      Size        Handwheel    Carton Qty
  T-113-050        1/2"        Malleable    24
  T-113-100        1"          Malleable    12
"""


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def cascade():
    return ModelCascade(region="us-east-2", tiers={"volume": "stub", "mid": "stub2"})


def document(name: str) -> SourceDocument:
    return SourceDocument(
        document_id=name,
        uri=f"local://{name}",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


@pytest.fixture
def gate_document():
    return parse_text(GATE_DATASHEET, document("gv200"))


# ===================================================================== finding a part number


def test_a_part_number_in_a_table_is_found(gate_document):
    presence = find_sku(gate_document, "T-113-100")
    assert presence.found is True
    assert presence.method == "exact"
    assert presence.location, "a found SKU must report where, so a reviewer can check"


def test_a_part_number_in_body_text_is_found(gate_document):
    presence = find_sku(gate_document, "T-113 SERIES")
    assert presence.found is True


def test_a_part_number_absent_from_the_document_is_reported_absent(gate_document):
    """The case that produced twelve fabricated values before this existed."""
    presence = find_sku(gate_document, "BA-100-075")
    assert presence.found is False
    assert presence.is_absent is True


def test_a_neighbouring_part_number_does_not_count_as_a_match(gate_document):
    """T-113-050 and T-113-100 are both present; T-113-999 is not, and nearly-matching
    the series prefix must not be enough."""
    assert find_sku(gate_document, "T-113-999").is_absent is True


def test_punctuation_differences_do_not_hide_a_real_match(gate_document):
    """Catalogues and ERPs disagree about hyphens constantly; that must not fail closed."""
    for spelling in ("T113100", "t-113-100", "T 113 100"):
        presence = find_sku(gate_document, spelling)
        assert presence.found is True, f"{spelling!r} should match T-113-100"


def test_separator_insensitive_matches_are_labelled_as_such(gate_document):
    """A looser match is still a match, but a reviewer should be able to see it was loose."""
    presence = find_sku(gate_document, "T113100")
    assert presence.found is True
    assert presence.method == "separator_insensitive"


def test_brand_aware_variants_are_accepted(gate_document):
    """`mpn_variants` knows about leading zeros and prefixes that this module does not."""
    presence = find_sku(gate_document, "T-113-100", variants=mpn_variants("T-113-100"))
    assert presence.found is True


def test_a_too_short_sku_is_not_searched_at_all(gate_document):
    """A two-character part number would match inside half the words on the page.

    ``checked=False`` is not the same as absent, and callers must not read it as a licence to
    abstain — a guard that fires at random is worse than no guard.
    """
    presence = find_sku(gate_document, "T1")
    assert presence.checked is False
    assert presence.is_absent is False, "not checked must never mean not present"
    assert len("T1") < MIN_SKU_CHARS


def test_an_empty_sku_is_handled(gate_document):
    assert find_sku(gate_document, "").checked is False


def test_presence_is_reportable(gate_document):
    summary = find_sku(gate_document, "T-113-100").summary()
    for key in ("sku", "found", "checked", "location", "method"):
        assert key in summary


# ===================================================================== the gate in extraction


def _extractor(registry, cascade, *, require: bool = True) -> Extractor:
    # The stub would happily return values for any attribute; the point is that with the gate
    # on it is never asked.
    client = StubModelClient(
        [
            StubModelClient.json_payload(
                [
                    {
                        "attribute_code": "body_material",
                        "found": True,
                        "value_raw": "Bronze C83600",
                        "evidence_quote": "Body Material .................. Bronze C83600",
                        "evidence_page": 1,
                        "certainty": "high",
                    }
                ]
            )
        ]
        * 4
    )
    return Extractor(
        registry, client, cascade, start_tier="volume", require_sku_in_document=require
    )


def test_extraction_refuses_a_document_that_does_not_mention_the_sku(
    registry, cascade, gate_document
):
    """The regression. Every attribute becomes a gap and no value is produced."""
    result = _extractor(registry, cascade).extract(
        gate_document, class_code=BALL_CLASS, target_sku="BA-100-075"
    )

    assert result.values == [], "a document about another product must yield no values"
    assert result.gaps, "the refusal has to be recorded, not silent"
    assert len(result.gaps) == len(result.requested_codes)
    assert result.targeting_failed is True


def test_refusal_costs_no_model_call(registry, cascade, gate_document):
    """The gate is deterministic, so the cheapest possible outcome is also the correct one."""
    result = _extractor(registry, cascade).extract(
        gate_document, class_code=BALL_CLASS, target_sku="BA-100-075"
    )
    assert result.usage.calls == 0
    assert result.response is None


def test_the_gap_reason_distinguishes_wrong_file_from_missing_value(
    registry, cascade, gate_document
):
    """Two different remedies.

    'This datasheet does not state the pressure rating' is a question for the supplier. 'This
    datasheet is about a different product' means someone attached the wrong file. Collapsing
    them would send a buyer chasing a supplier for data that was never missing.
    """
    result = _extractor(registry, cascade).extract(
        gate_document, class_code=BALL_CLASS, target_sku="BA-100-075"
    )
    gap = result.gaps[0]

    assert gap.reason is GapReason.NO_SOURCE_AVAILABLE
    assert gap.reason is not GapReason.NOT_PRESENT_IN_ANY_SOURCE
    assert gap.recommended_action is RecommendedAction.RETRY_WITH_BETTER_SOURCE
    assert "does not appear" in gap.detail
    assert "BA-100-075" in gap.detail


def test_required_attributes_stay_flagged_required_in_the_refusal(
    registry, cascade, gate_document
):
    """A refusal must not quietly downgrade a required attribute to optional."""
    result = _extractor(registry, cascade).extract(
        gate_document, class_code=BALL_CLASS, target_sku="BA-100-075"
    )
    required = registry.required_codes(BALL_CLASS)
    for gap in result.gaps:
        if gap.attribute_code in required:
            assert gap.is_required is True


def test_a_matching_sku_proceeds_normally(registry, cascade, gate_document):
    """The gate must not block the case it exists to protect."""
    result = _extractor(registry, cascade).extract(
        gate_document, class_code="PLB.VLV.GATE.BRZ", target_sku="T-113-100"
    )
    assert result.targeting_failed is False
    assert result.usage.calls >= 1, "a legitimate document should still be read"
    assert result.sku_presence is not None
    assert result.sku_presence.found is True


def test_the_gate_can_be_disabled_for_series_level_documents(
    registry, cascade, gate_document
):
    """A real case: some datasheets describe a series and never print orderable part numbers.

    It is opt-in because it is also the excuse a wrong-document bug would hide behind.
    """
    result = _extractor(registry, cascade, require=False).extract(
        gate_document, class_code=BALL_CLASS, target_sku="BA-100-075"
    )
    assert result.usage.calls >= 1, "with the gate off the model is consulted anyway"


def test_summary_exposes_the_targeting_outcome(registry, cascade, gate_document):
    """Visible in the pipeline report, so a silent all-gaps run is explicable."""
    summary = (
        _extractor(registry, cascade)
        .extract(gate_document, class_code=BALL_CLASS, target_sku="BA-100-075")
        .summary()
    )
    assert summary["targeting_failed"] is True
    assert summary["sku_found_in_document"] is False
    assert summary["values"] == 0


# ================================================================= withdrawn part numbers
#
# The hardest targeting case, and the only one the presence check above cannot answer. The part
# number *is* in the document — it appears once, in a note retiring it — so the gate opens and a
# model call is made. Every shared specification in the surrounding prose reads as though it
# applies to it.
#
# Measured, not hypothetical: scripts/run_adversarial.py returned 12 values for a superseded
# valve, all 12 carrying verifiable quotes, before this was handled.

WITHDRAWAL_DATASHEET = """\
APOLLO 77C SERIES
Bronze Ball Valve                                       Rev D  2025-11

DESCRIPTION
  The 77C is a two-piece bronze ball valve. The body is cast from lead-free
  bronze alloy C89833 and carries 400 PSI WOG. Seats are reinforced PTFE.

ORDERING INFORMATION
  Catalog No    DN      Size      Port      Cv      Ctn Qty
  77C-103       DN15    1/2"      Full      15.0    24
  77C-104       DN20    3/4"      Full      28.0    20

  NOTE 2: Catalog No 77C-102 (DN10) is discontinued and superseded by
  77C-103. Do not order.
"""


@pytest.fixture
def withdrawal_document():
    return parse_text(WITHDRAWAL_DATASHEET, document("ap77c"))


def test_a_withdrawn_part_is_found_but_not_offered(withdrawal_document):
    presence = find_sku(withdrawal_document, "77C-102")
    assert presence.found is True, "the part number really is in the document"
    assert presence.is_absent is False, "so the absence gate cannot catch it"
    assert presence.is_not_offered is True
    assert presence.is_extractable is False
    assert "discontinued" in (presence.withdrawal_quote or "")


def test_absent_and_withdrawn_are_not_the_same_state(withdrawal_document):
    """The remedies differ and are not interchangeable.

    An absent part number means someone attached the wrong file: go and find the right one. A
    withdrawn one means the file is correct and the part is dead: delist it. Collapsing the two
    would send a merchandiser hunting for a datasheet that does not exist.
    """
    withdrawn = find_sku(withdrawal_document, "77C-102")
    absent = find_sku(withdrawal_document, "77C-999")

    assert withdrawn.is_not_offered and not withdrawn.is_absent
    assert absent.is_absent and not absent.is_not_offered
    assert not withdrawn.is_extractable and not absent.is_extractable


def test_an_ordering_row_outranks_a_withdrawal_note(withdrawal_document):
    """77C-103 is named *inside* the note, as the replacement.

    A substring check on the note would retire the very part being introduced. A row in the
    ordering table is the strongest evidence a document offers something, so it wins.
    """
    presence = find_sku(withdrawal_document, "77C-103")
    assert presence.listed_in_table is True
    assert presence.withdrawn is False
    assert presence.is_extractable is True


def test_an_ordinary_part_is_unaffected(withdrawal_document):
    presence = find_sku(withdrawal_document, "77C-104")
    assert presence.is_extractable is True
    assert presence.withdrawn is False


def test_extraction_is_refused_for_a_withdrawn_part(registry, cascade, withdrawal_document):
    """No model call, every attribute a gap, and the remedy says delist rather than research."""
    client = StubModelClient(["[]"])
    extractor = Extractor(registry, client, cascade, start_tier="volume")

    result = extractor.extract(
        withdrawal_document, class_code=BALL_CLASS, target_sku="77C-102"
    )

    assert result.values == []
    assert result.targeting_failed is True
    assert result.gaps, "every requested attribute must be accounted for"
    assert len(client.calls) == 0, "a withdrawn part must not cost a model call"

    for gap in result.gaps:
        assert gap.reason is GapReason.NO_SOURCE_AVAILABLE
        assert gap.recommended_action is RecommendedAction.DELIST_PRODUCT
        assert "withdraw" in gap.detail
    assert any("discontinued" in gap.detail for gap in result.gaps)


def test_extraction_proceeds_for_the_replacement_part(registry, cascade, withdrawal_document):
    """The gate must not take the whole ordering table down with the retired row."""
    client = StubModelClient(["[]"])
    extractor = Extractor(registry, client, cascade, start_tier="volume")

    result = extractor.extract(
        withdrawal_document, class_code=BALL_CLASS, target_sku="77C-103"
    )

    assert result.targeting_failed is False
    # At least one — the stub returns nothing, which makes the cascade escalate a tier. The
    # point is only that the gate let extraction happen at all.
    assert len(client.calls) >= 1


def test_withdrawal_markers_are_declarative():
    """Withdrawal is a wording question, so the phrasings live where a merchandiser can add one."""
    from axiom.validate.constants import RuleConstants

    markers = RuleConstants.load().sets["WITHDRAWAL_MARKERS"]
    assert "discontinued" in markers
    assert "do not order" in markers
