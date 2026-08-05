"""Tests for extraction, the evidence contract and the model cascade.

Every test here runs against a stub client. Extraction correctness — contract parsing, quote
verification, gap creation, escalation — is decided by our code, not by the model, so it must
be testable without a network call. The live-model check lives in scripts/smoke_extraction.py.
"""

from __future__ import annotations

import json

import pytest
from axiom.core.gaps import GapReason, RecommendedAction
from axiom.core.values import DerivationMethod, ValueStatus
from axiom.extract import (
    Certainty,
    ContractError,
    Extractor,
    ModelCascade,
    ModelError,
    StubModelClient,
    UsageLedger,
    classify_abstention,
    invoke_with_cascade,
    parse_contract,
)
from axiom.schema import load_default

CLASS_CODE = "PLB.VLV.BALL.2PC"


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture
def cascade():
    return ModelCascade(
        region="us-east-2",
        tiers={
            "volume": "stub-volume",
            "mid": "stub-mid",
            "frontier": "stub-frontier",
        },
    )


def item(code: str, **kwargs) -> dict:
    base = {
        "attribute_code": code,
        "found": True,
        "value_raw": None,
        "evidence_quote": None,
        "evidence_page": 1,
        "certainty": "high",
        "reason": None,
    }
    base.update(kwargs)
    return base


# --------------------------------------------------------------------- contract parsing


def test_plain_json_array_parses():
    payload = json.dumps([item("body_material", value_raw="Bronze", evidence_quote="Bronze")])
    items = parse_contract(payload)
    assert items[0].attribute_code == "body_material"
    assert items[0].found is True


def test_markdown_fence_is_tolerated():
    """A fence around valid JSON is a formatting nuisance, not a correctness problem."""
    inner = json.dumps([item("body_material", value_raw="Bronze", evidence_quote="Bronze")])
    assert len(parse_contract(f"```json\n{inner}\n```")) == 1


def test_leading_prose_is_tolerated():
    inner = json.dumps([item("seat_material", value_raw="RPTFE", evidence_quote="RPTFE")])
    assert len(parse_contract(f"Here are the results:\n{inner}")) == 1


def test_object_wrapper_is_unwrapped():
    inner = {"attributes": [item("port_type", value_raw="Full Port", evidence_quote="Full Port")]}
    assert len(parse_contract(json.dumps(inner))) == 1


def test_invalid_json_raises_so_the_cascade_escalates():
    with pytest.raises(ContractError, match="not valid JSON"):
        parse_contract("{this is not json")


def test_empty_response_raises():
    with pytest.raises(ContractError, match="empty response"):
        parse_contract("   ")


def test_array_of_nothing_useful_raises():
    with pytest.raises(ContractError, match="no usable contract items"):
        parse_contract(json.dumps(["nonsense", 42, {}]))


def test_hallucinated_attribute_codes_are_dropped():
    payload = json.dumps(
        [
            item("body_material", value_raw="Bronze", evidence_quote="Bronze"),
            item("invented_attribute", value_raw="x", evidence_quote="x"),
        ]
    )
    items = parse_contract(payload, expected_codes=("body_material",))
    assert [i.attribute_code for i in items] == ["body_material"]


def test_duplicate_codes_keep_the_first():
    payload = json.dumps(
        [
            item("body_material", value_raw="Bronze", evidence_quote="Bronze"),
            item("body_material", value_raw="Brass", evidence_quote="Brass"),
        ]
    )
    assert parse_contract(payload)[0].value_raw == "Bronze"


def test_list_values_are_joined_for_multi_enum():
    payload = json.dumps(
        [item("approvals", value_raw=["UL", "CSA"], evidence_quote="UL listed, CSA certified")]
    )
    assert parse_contract(payload)[0].value_raw == "UL, CSA"


def test_boolean_value_is_coerced_to_text():
    payload = json.dumps([item("lead_free_compliant", value_raw=True, evidence_quote="lead-free")])
    assert parse_contract(payload)[0].value_raw == "true"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", Certainty.HIGH),
        ("LOW", Certainty.LOW),
        ("nonsense", Certainty.MEDIUM),
        (None, Certainty.MEDIUM),
    ],
)
def test_certainty_coercion(raw, expected):
    payload = json.dumps([item("port_type", value_raw="x", evidence_quote="x", certainty=raw)])
    assert parse_contract(payload)[0].certainty is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(4, 4), ("page 7", 7), (0, None), (-2, None), (True, None), ("n/a", None)]
)
def test_page_coercion(raw, expected):
    payload = json.dumps([item("port_type", value_raw="x", evidence_quote="x", evidence_page=raw)])
    assert parse_contract(payload)[0].evidence_page == expected


def test_found_value_without_a_quote_is_not_well_formed():
    """No quote, no value. This is the contract's whole point."""
    payload = json.dumps([item("body_material", value_raw="Bronze", evidence_quote=None)])
    assert parse_contract(payload)[0].is_well_formed is False


def test_abstention_is_always_well_formed():
    payload = json.dumps([item("gtin", found=False, reason="not stated")])
    assert parse_contract(payload)[0].is_well_formed is True


@pytest.mark.parametrize(
    ("reason", "bucket"),
    [
        ("Consult factory", "deferred"),
        ("see derating chart, page 7", "deferred"),
        ("available upon request", "deferred"),
        ('stated only for 1/2" size', "applicability"),
        ("applies to a different size", "applicability"),
        ("not stated anywhere in the document", "absent"),
        (None, "absent"),
    ],
)
def test_abstention_classification(reason, bucket):
    """'Consult factory' means ask the supplier; silence means nobody has it. Different
    follow-up actions, so they must not collapse into one reason code."""
    assert classify_abstention(reason) == bucket


# --------------------------------------------------------------------- cascade


def test_cascade_escalation_path(cascade: ModelCascade):
    assert cascade.escalation_path("volume") == ["volume", "mid", "frontier"]
    assert cascade.escalation_path("mid") == ["mid", "frontier"]
    assert cascade.escalation_path("frontier") == ["frontier"]


def test_cascade_skips_unpinned_tiers():
    partial = ModelCascade(region="us-east-2", tiers={"volume": "v", "frontier": "f"})
    assert partial.escalation_path("volume") == ["volume", "frontier"]


def test_unpinned_tier_raises(cascade: ModelCascade):
    with pytest.raises(ModelError, match="not pinned"):
        cascade.model_for("nonexistent")


def test_cheapest_tier_is_used_when_it_succeeds(cascade: ModelCascade):
    client = StubModelClient(['[{"attribute_code":"a","found":false}]'])
    ledger = UsageLedger()
    response, _ = invoke_with_cascade(
        client, cascade, system="s", user="u", validate=lambda t: json.loads(t), ledger=ledger
    )
    assert response.tier == "volume"
    assert ledger.calls == 1
    assert ledger.escalations == 0


def test_unusable_output_escalates(cascade: ModelCascade):
    """Escalation is driven by whether the output was usable, not by a guess made upfront."""
    client = StubModelClient(["not json at all", '[{"attribute_code":"a","found":false}]'])
    ledger = UsageLedger()
    response, _ = invoke_with_cascade(
        client,
        cascade,
        system="s",
        user="u",
        validate=lambda t: parse_contract(t),
        ledger=ledger,
    )
    assert response.tier == "mid"
    assert ledger.calls == 2
    assert ledger.escalations == 1


def test_model_error_on_one_tier_moves_to_the_next(cascade: ModelCascade):
    client = StubModelClient(
        [ModelError("throttled"), '[{"attribute_code":"a","found":false}]']
    )
    response, _ = invoke_with_cascade(
        client, cascade, system="s", user="u", validate=lambda t: parse_contract(t)
    )
    assert response.tier == "mid"


def test_all_tiers_failing_raises_with_every_reason(cascade: ModelCascade):
    client = StubModelClient(["bad", "worse", "worst"])
    with pytest.raises(ModelError, match="every tier failed") as exc:
        invoke_with_cascade(
            client, cascade, system="s", user="u", validate=lambda t: parse_contract(t)
        )
    message = str(exc.value)
    assert "volume:" in message and "mid:" in message and "frontier:" in message


# --------------------------------------------------------------------- usage ledger


def test_ledger_accumulates_and_merges():
    a, b = UsageLedger(), UsageLedger()
    client = StubModelClient(["x" * 40, "y" * 80])
    a.record(client.converse(model_id="m", tier="volume", system="s", user="u" * 100))
    b.record(client.converse(model_id="m", tier="mid", system="s", user="u" * 200), escalated=True)
    a.merge(b)
    assert a.calls == 2
    assert a.escalations == 1
    assert a.by_tier == {"volume": 1, "mid": 1}


def test_cost_is_none_without_a_price_table():
    """A plausible made-up cost is worse than no cost, because it invites decisions."""
    ledger = UsageLedger()
    client = StubModelClient(["x" * 40])
    ledger.record(client.converse(model_id="m", tier="volume", system="s", user="u" * 100))
    assert ledger.cost_usd(None) is None
    assert ledger.cost_usd({}) is None
    assert ledger.cost_usd({"volume": (0.10, 0.40)}) is not None


def test_cost_is_none_when_a_used_tier_has_no_price():
    ledger = UsageLedger()
    client = StubModelClient(["x", "y"])
    ledger.record(client.converse(model_id="m", tier="volume", system="s", user="u"))
    ledger.record(client.converse(model_id="m", tier="frontier", system="s", user="u"))
    assert ledger.cost_usd({"volume": (0.1, 0.4)}) is None


# --------------------------------------------------------------------- end to end


def _extractor(registry, cascade, responses):
    return Extractor(registry, StubModelClient(responses), cascade, start_tier="volume")


def test_verified_value_becomes_an_attribute_value(registry, cascade, parsed_datasheet):
    payload = json.dumps(
        [
            item(
                "pressure_rating_wog",
                value_raw="600 PSI WOG",
                evidence_quote="600 PSI WOG @ 73 degF",
                evidence_page=1,
            )
        ]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("pressure_rating_wog",),
    )

    assert len(result.values) == 1
    value = result.values[0]
    assert value.value_raw == "600 PSI WOG"
    assert value.method is DerivationMethod.DOCUMENT_EXTRACTION
    assert value.has_verified_evidence is True
    assert value.evidence[0].match_score == 1.0
    assert value.prompt_version and value.schema_version
    assert result.citation_coverage == 1.0


def test_value_from_a_table_is_marked_as_table_extraction(registry, cascade, parsed_datasheet):
    # The quote is the carton-quantity cell itself, not the part-number cell. A citation has to
    # land on the text that states the value, or the entailment gate discards it.
    payload = json.dumps(
        [item("case_quantity", value_raw="12", evidence_quote="12", evidence_page=1)]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("case_quantity",),
    )
    value = result.values[0]
    assert value.method is DerivationMethod.TABLE_EXTRACTION
    assert value.evidence[0].table_ref == "t1:r3:c3"


def test_unlocatable_quote_is_discarded_and_becomes_a_gap(registry, cascade, parsed_datasheet):
    """The core anti-fabrication path: a plausible value with an invented quote."""
    payload = json.dumps(
        [
            item(
                "cv_flow_coefficient",
                value_raw="18.5",
                evidence_quote="Flow Coefficient (Cv) .......... 18.5",
            )
        ]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("cv_flow_coefficient",),
    )

    assert result.values == []
    assert len(result.rejected) == 1
    gap = result.gaps[0]
    assert gap.reason is GapReason.EXTRACTED_BUT_UNVERIFIABLE
    assert "could not be located" in gap.detail
    assert "18.5" in gap.detail, "the rejected claim must remain visible to a reviewer"


def test_value_claimed_without_a_quote_is_discarded(registry, cascade, parsed_datasheet):
    payload = json.dumps([item("body_material", value_raw="Bronze C84400", evidence_quote=None)])
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("body_material",),
    )
    assert result.values == []
    assert result.gaps[0].reason is GapReason.EXTRACTED_BUT_UNVERIFIABLE
    assert "no supporting quote" in result.gaps[0].detail


def test_deferred_value_becomes_a_supplier_request(registry, cascade, parsed_datasheet):
    payload = json.dumps(
        [item("cv_flow_coefficient", found=False, reason="Consult factory; no value stated")]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("cv_flow_coefficient",),
    )
    gap = result.gaps[0]
    assert gap.reason is GapReason.REFERRED_ELSEWHERE
    assert gap.recommended_action is RecommendedAction.REQUEST_FROM_SUPPLIER
    assert gap.reason.is_actionable_by_supplier is True


def test_applicability_abstention_routes_to_research(registry, cascade, parsed_datasheet):
    payload = json.dumps(
        [item("operating_torque", found=False, reason='stated only for 1/2" size')]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("operating_torque",),
    )
    assert result.gaps[0].recommended_action is RecommendedAction.HUMAN_RESEARCH


def test_omitted_attribute_becomes_an_explicit_gap(registry, cascade, parsed_datasheet):
    payload = json.dumps(
        [item("body_material", value_raw="Bronze C84400", evidence_quote="Bronze C84400")]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("body_material", "seat_material"),
    )
    gaps = {g.attribute_code: g for g in result.gaps}
    assert "seat_material" in gaps
    assert "absent from the model response" in gaps["seat_material"].detail


def test_required_flag_is_carried_onto_gaps(registry, cascade, parsed_datasheet):
    payload = json.dumps([item("pressure_rating_wog", found=False, reason="not stated")])
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("pressure_rating_wog",),
    )
    assert result.gaps[0].is_required is True


def test_total_model_failure_does_not_masquerade_as_empty_attributes(
    registry, cascade, parsed_datasheet
):
    """'We could not read the source' and 'the value is not stated' are different claims."""
    client = StubModelClient([ModelError("throttled")] * 3)
    extractor = Extractor(registry, client, cascade, start_tier="volume")
    result = extractor.extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("body_material", "seat_material"),
    )
    assert result.values == []
    assert len(result.gaps) == 2
    for gap in result.gaps:
        assert gap.reason is GapReason.NO_SOURCE_AVAILABLE
        assert gap.recommended_action is RecommendedAction.RETRY_WITH_BETTER_SOURCE


def test_extraction_does_not_normalise_values(registry, cascade, parsed_datasheet):
    """Extraction stores what the source said. Canonicalisation is a separate stage, so the
    raw text stays available for audit."""
    payload = json.dumps(
        [
            item(
                "nominal_size",
                value_raw='3/4"',
                evidence_quote='3/4"',
                evidence_page=1,
            )
        ]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("nominal_size",),
    )
    value = result.values[0]
    assert value.value_raw == '3/4"'
    assert value.value_canonical is None
    assert value.status is ValueStatus.CANDIDATE


def test_summary_reports_usable_numbers(registry, cascade, parsed_datasheet):
    payload = json.dumps(
        [
            item("body_material", value_raw="Bronze C84400", evidence_quote="Bronze C84400"),
            item("seat_material", found=False, reason="not stated"),
        ]
    )
    result = _extractor(registry, cascade, [payload]).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("body_material", "seat_material"),
    )
    summary = result.summary()
    assert summary["requested"] == 2
    assert summary["values"] == 1
    assert summary["gaps"] == 1
    assert summary["citation_coverage"] == 1.0
    assert summary["input_tokens"] > 0


def test_prompt_actually_sent_contains_the_parsed_table(registry, cascade, parsed_datasheet):
    """Guards the wiring between docintel and the prompt: the model must see table
    structure, not flattened prose."""
    client = StubModelClient([json.dumps([item("case_quantity", found=False, reason="x")])])
    Extractor(registry, client, cascade).extract(
        parsed_datasheet,
        class_code=CLASS_CODE,
        target_sku="BA-100-075",
        only_codes=("case_quantity",),
    )
    sent = client.calls[0]["user"]
    assert '<table id="t1">' in sent
    assert "BA-100-075" in sent
    assert '<page number="1">' in sent
