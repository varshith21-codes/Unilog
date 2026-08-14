"""The agent surface (Tier 3, item 21).

The tests here are almost entirely about one invariant: **no value crosses this boundary without its
provenance**, and **no compliance claim is matched on unverified data**.

That emphasis is not arbitrary. An agent is a relay — it restates whatever it receives as confident
prose, and it cannot recover a distinction the tool layer dropped. So a test asserting that
`get_product` returns the right pressure rating is nearly worthless next to one asserting that the
rating arrived with `verified` attached, because only the second failure mode ends with a contractor
being told an unverified figure as fact.

The MCP adapter is tested for wiring only. It is a six-line shell over these functions by design,
and the guarantees are asserted where they are implemented.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.agents import (
    Filter,
    ToolError,
    check_substitution,
    explain_value,
    find_substitutes,
    get_product,
    list_classes,
    search_products,
    value_view,
)
from axiom.core.evidence import DocumentType, EvidenceSpan, SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.resolve import Catalogue, CatalogueSource, records_from_golden
from axiom.schema import load_default

SHA = "c3d4" + "0" * 60


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def catalogue(registry):
    return records_from_golden(registry)


@pytest.fixture
def document() -> SourceDocument:
    return SourceDocument(
        document_id="doc@c3d40000",
        uri="local://doc",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        supplier_id="milwaukee",
    )


def _span(document: SourceDocument, *, verified: bool) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="s1",
        document_id=document.document_id,
        document_sha256=document.sha256,
        quote="Pressure Rating ... 600 PSI WOG",
        page=1,
        quote_verified=verified,
    )


def _extracted(
    code: str,
    canonical: object,
    document: SourceDocument,
    *,
    verified: bool = True,
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_canonical=canonical,
        value_display=str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.93,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[_span(document, verified=verified)],
    )


def _single(record: ProductRecord) -> Catalogue:
    return Catalogue(source=CatalogueSource.PIPELINE, records=[record])


def _record(sku: str = "TEST-1") -> ProductRecord:
    return ProductRecord(
        tenant_id="t", sku=sku, class_code="PLB.VLV.BALL.2PC", brand="Milwaukee Valve"
    )


# --------------------------------------------------------------------- the provenance contract


def test_every_attribute_arrives_with_provenance(catalogue, registry):
    """The invariant. A value an agent cannot qualify is a value it will overstate."""
    result = get_product("BA-100-075", catalogue, registry)

    assert result["attributes"]
    for attribute in result["attributes"]:
        assert "provenance" in attribute
        assert set(attribute["provenance"]) >= {"method", "verified", "confidence", "citations"}
        assert "usable_for_compliance" in attribute


def test_an_unverified_compliance_claim_is_marked_unusable(registry, document):
    record = _record()
    record.add_value(_extracted("lead_free_compliant", True, document, verified=False))

    view = value_view(
        record.get("lead_free_compliant"), registry.attribute("lead_free_compliant")
    )
    assert view.compliance_claim
    assert not view.verified
    assert not view.usable_for_compliance


def test_a_verified_compliance_claim_is_usable(registry, document):
    record = _record()
    record.add_value(_extracted("lead_free_compliant", True, document))

    view = value_view(
        record.get("lead_free_compliant"), registry.attribute("lead_free_compliant")
    )
    assert view.verified and view.usable_for_compliance


def test_an_inferred_compliance_claim_is_never_usable(registry):
    """Inference may not satisfy a legal claim, whatever confidence it carries."""
    value = AttributeValue(
        attribute_code="lead_free_compliant",
        value_canonical=True,
        method=DerivationMethod.PART_NUMBER_GRAMMAR,
        confidence=0.99,
        status=ValueStatus.HUMAN_APPROVED,
    )
    view = value_view(value, registry.attribute("lead_free_compliant"))

    assert not view.usable_for_compliance
    assert view.method == "part_number_grammar"


def _inferred(code: str, canonical: object) -> AttributeValue:
    """A publishable value with no document behind it.

    This is the case the tool layer actually has to catch. An *unverified extraction* never gets
    this far — `AttributeValue.is_publishable` already refuses it, because the extraction family
    requires verified evidence. What can still slip through is an inference a reviewer approved:
    publishable by status, sourced by nothing.
    """
    return AttributeValue(
        attribute_code=code,
        value_canonical=canonical,
        value_display=str(canonical),
        method=DerivationMethod.PART_NUMBER_GRAMMAR,
        confidence=0.97,
        status=ValueStatus.HUMAN_APPROVED,
    )


def test_an_unverified_extraction_never_reaches_the_agent_at_all(registry, document):
    """The stronger, earlier guarantee: the core model will not publish it, so no tool sees it."""
    record = _record()
    record.add_value(_extracted("potable_water_approved", True, document, verified=False))

    assert record.get("potable_water_approved") is not None
    assert not record.get("potable_water_approved").is_publishable

    result = get_product("TEST-1", _single(record), registry)
    assert result["attributes"] == []


def test_the_product_response_names_the_claims_it_cannot_stand_behind(registry, document):
    """An approved inference is publishable and still may not satisfy a legal claim."""
    record = _record()
    record.add_value(_inferred("potable_water_approved", True))
    record.add_value(
        _extracted("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), document)
    )

    result = get_product("TEST-1", _single(record), registry)
    advisory = result["advisory"]

    assert advisory["compliance_claims_not_usable"] == ["potable_water_approved"]
    assert "usable_for_compliance is false" in advisory["note"]
    # The verified specification value is unaffected.
    codes = {a["attribute_code"]: a for a in result["attributes"]}
    assert codes["pressure_rating_wog"]["usable_for_compliance"] is True


# --------------------------------------------------------------------- typed values


def test_a_quantity_arrives_as_a_number_with_its_unit_alongside(registry, document):
    """An agent comparing pressures needs arithmetic, not the string "600 psi"."""
    record = _record()
    record.add_value(
        _extracted("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), document)
    )

    view = value_view(
        record.get("pressure_rating_wog"), registry.attribute("pressure_rating_wog")
    )
    assert view.value == 600.0
    assert isinstance(view.value, float)
    assert view.unit == "psi"


def test_a_range_arrives_with_both_bounds(registry, document):
    record = _record()
    record.add_value(
        _extracted(
            "temperature_range",
            ValueRange(minimum=-29.0, maximum=185.0, unit="degC"),
            document,
        )
    )

    view = value_view(
        record.get("temperature_range"), registry.attribute("temperature_range")
    )
    assert view.value == {"minimum": -29.0, "maximum": 185.0}
    assert view.unit == "degC"


def test_a_multi_enum_arrives_as_a_list(registry, document):
    record = _record()
    record.add_value(_extracted("approvals", ["UL", "CSA"], document))

    view = value_view(record.get("approvals"), registry.attribute("approvals"))
    assert view.value == ["UL", "CSA"]


# --------------------------------------------------------------------- the schema tool


def test_list_classes_gives_an_agent_what_it_needs_to_form_a_query(registry):
    result = list_classes(registry)

    assert result["classes"]
    assert set(result["filter_operators"]) == {"eq", "gte", "lte", "contains"}
    first = result["classes"][0]
    assert {"class_code", "name", "attributes", "external_codes"} <= set(first)

    attribute = first["attributes"][0]
    assert {"attribute_code", "datatype", "canonical_unit", "compliance_claim"} <= set(attribute)


def test_list_classes_reports_the_canonical_unit_so_a_filter_can_be_written(registry):
    result = list_classes(registry)
    attributes = {
        a["attribute_code"]: a
        for c in result["classes"]
        for a in c["attributes"]
    }
    assert attributes["pressure_rating_wog"]["canonical_unit"] == "psi"


# --------------------------------------------------------------------- search


def test_a_filter_is_written_the_way_a_datasheet_states_it(catalogue, registry):
    """"600 PSI" is normalised by the same pipeline extracted values go through."""
    result = search_products(
        catalogue,
        registry,
        filters=(Filter("pressure_rating_wog", "eq", "600 PSI"),),
    )
    assert result["count"] > 0


def test_gte_finds_the_safe_upgrades(catalogue, registry):
    at_least = search_products(
        catalogue,
        registry,
        filters=(Filter("pressure_rating_wog", "gte", "500 PSI"),),
    )
    at_most = search_products(
        catalogue,
        registry,
        filters=(Filter("pressure_rating_wog", "lte", "500 PSI"),),
    )
    assert at_least["count"] and at_most["count"]
    assert not (
        {p["sku"] for p in at_least["products"]} & {p["sku"] for p in at_most["products"]}
    )


def test_contains_matches_a_listing_inside_a_multi_enum(catalogue, registry):
    result = search_products(
        catalogue, registry, filters=(Filter("approvals", "contains", "NSF/ANSI 61"),)
    )
    assert result["count"] > 0


def test_search_refuses_to_match_a_compliance_claim_satisfied_by_inference(registry):
    """An agent asking for lead-free parts is asking a legal question, and a guess is the wrong
    kind of answer to it — even a guess a reviewer signed off."""
    record = _record()
    record.add_value(_inferred("lead_free_compliant", True))

    assert record.get("lead_free_compliant").is_publishable

    result = search_products(
        _single(record),
        registry,
        filters=(Filter("lead_free_compliant", "eq", "true"),),
    )

    assert result["count"] == 0
    assert result["excluded_for_unverified_compliance"] == ["TEST-1"]


def test_the_same_query_succeeds_once_the_claim_is_verified(registry, document):
    record = _record()
    record.add_value(_extracted("lead_free_compliant", True, document, verified=True))

    result = search_products(
        _single(record),
        registry,
        filters=(Filter("lead_free_compliant", "eq", "true"),),
    )

    assert result["count"] == 1
    assert result["excluded_for_unverified_compliance"] == []


def test_a_specification_attribute_is_not_held_to_the_compliance_bar(registry):
    """A wrong rating disappoints; a wrong compliance flag is a liability. Hence the asymmetry.

    The same inferred provenance that excludes a lead-free claim still returns a pressure rating —
    with `verified: false` attached so the agent can qualify it.
    """
    record = _record()
    record.add_value(_inferred("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi")))

    result = search_products(
        _single(record),
        registry,
        filters=(Filter("pressure_rating_wog", "eq", "600 PSI"),),
    )
    assert result["count"] == 1
    assert result["products"][0]["matched_on"][0]["provenance"]["verified"] is False
    assert result["excluded_for_unverified_compliance"] == []


def test_search_is_truncated_rather_than_silently_capped(catalogue, registry):
    result = search_products(catalogue, registry, limit=2)
    assert len(result["products"]) == 2
    assert result["truncated"] is True
    assert result["count"] > 2


# --------------------------------------------------------------------- errors


def test_an_unknown_sku_is_an_error_not_an_empty_result(catalogue, registry):
    """An agent handed [] concludes the catalogue is empty; an error is recoverable."""
    with pytest.raises(ToolError, match="not in this catalogue"):
        get_product("NOPE-1", catalogue, registry)


def test_an_unknown_attribute_filter_names_the_remedy(catalogue, registry):
    with pytest.raises(ToolError, match="list_classes"):
        search_products(
            catalogue, registry, filters=(Filter("not_an_attribute", "eq", "x"),)
        )


def test_an_unknown_class_is_refused(catalogue, registry):
    with pytest.raises(ToolError, match="unknown class"):
        search_products(catalogue, registry, class_code="NOPE")


def test_an_unparseable_filter_value_explains_the_expected_shape(catalogue, registry):
    with pytest.raises(ToolError, match="could not interpret"):
        search_products(
            catalogue,
            registry,
            filters=(Filter("pressure_rating_wog", "eq", "not a pressure"),),
        )


def test_an_unknown_operator_is_refused_at_construction():
    with pytest.raises(ToolError, match="unknown operator"):
        Filter("pressure_rating_wog", "approximately", "600 PSI")


def test_an_ordering_operator_on_a_range_is_refused(catalogue, registry):
    """`gte` needs one magnitude and a range has two. Silently comparing a bound would mislead."""
    with pytest.raises(ToolError, match="single magnitude"):
        search_products(
            catalogue,
            registry,
            filters=(Filter("temperature_range", "gte", "-20 degF to 366 degF"),),
        )


# --------------------------------------------------------------------- explain


def test_explain_value_returns_the_citation_trail(registry, document):
    record = _record()
    record.add_value(
        _extracted("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), document)
    )

    result = explain_value("TEST-1", "pressure_rating_wog", _single(record), registry)

    assert result["known"] is True
    assert result["evidence"][0]["quote"].startswith("Pressure Rating")
    assert result["evidence"][0]["verified"] is True
    assert result["evidence"][0]["page"] == 1


def test_an_unknown_value_is_reported_as_unknown_not_as_false(catalogue, registry):
    """The distinction an agent must never collapse: absent is not zero, false or empty."""
    result = explain_value("BA-100-025", "cv_flow_coefficient", catalogue, registry)

    assert result["known"] is False
    assert "not zero, false or empty" in result["advisory"]


def test_explain_value_refuses_an_unknown_attribute(catalogue, registry):
    with pytest.raises(ToolError, match="unknown attribute"):
        explain_value("BA-100-025", "not_an_attribute", catalogue, registry)


# --------------------------------------------------------------------- substitution


def test_find_substitutes_delegates_to_the_equivalence_engine(catalogue, registry):
    """An agent must not be able to get a different answer than the console shows."""
    from axiom.resolve import cross_reference

    via_tool = find_substitutes("BA-100-100", catalogue, registry, limit=5)
    direct = cross_reference("BA-100-100", catalogue, registry, limit=5)

    assert via_tool["reference_sku"] == "BA-100-100"
    assert [c["sku"] for c in via_tool["candidates"]] == [
        r.candidate_sku for r in direct.reports[:5]
    ]


def test_check_substitution_is_directional(catalogue, registry):
    forward = check_substitution("77C-105", "77C-105R", catalogue, registry)
    reverse = check_substitution("77C-105R", "77C-105", catalogue, registry)

    assert forward["question"] != reverse["question"]
    assert "directional" in forward["direction_note"]
    # The reduced-port twin is the asymmetric pair the corpus exists to exercise.
    assert forward["verdict"] != reverse["verdict"]


def test_substitution_reports_which_attribute_blocks_it(catalogue, registry):
    result = find_substitutes("BA-100-025", catalogue, registry, limit=10)
    blocked = [c for c in result["candidates"] if not c["substitutable"]]

    assert blocked, "the corpus contains parts that cannot substitute for a 1/4in valve"
    assert any(c["blocking_differences"] for c in blocked)


def test_a_single_record_catalogue_cannot_cross_reference(registry, document):
    record = _record()
    record.add_value(
        _extracted("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), document)
    )
    with pytest.raises(ToolError, match="at least two records"):
        find_substitutes("TEST-1", _single(record), registry)


# --------------------------------------------------------------------- the catalogue caveat


def test_the_response_carries_the_provenance_of_the_record_set(catalogue, registry):
    """`measured: false` must reach the agent, not stop at the CLI."""
    result = get_product("BA-100-075", catalogue, registry)
    assert result["catalogue"]["measured"] is False
    assert "hand-authored" in result["catalogue"]["source_note"]


# --------------------------------------------------------------------- the MCP adapter

def _has_mcp() -> bool:
    try:
        import mcp.server  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


needs_mcp = pytest.mark.skipif(
    not _has_mcp(), reason="the optional 'mcp' extra is not installed"
)


def test_the_tool_layer_imports_without_the_mcp_extra():
    """axiom.agents must be usable with no protocol dependency at all."""
    import axiom.agents as agents

    assert not hasattr(agents, "build_server")
    assert callable(agents.get_product)


@needs_mcp
def test_the_server_registers_every_tool(catalogue, registry):
    import asyncio

    from axiom.agents.server import build_server

    server = build_server(catalogue, registry)
    tools = asyncio.run(server.list_tools())

    assert {t.name for t in tools} == {
        "axiom_list_classes",
        "axiom_get_product",
        "axiom_search_products",
        "axiom_find_substitutes",
        "axiom_check_substitution",
        "axiom_explain_value",
    }


@needs_mcp
def test_the_tool_schemas_are_generated_from_the_type_hints(catalogue, registry):
    import asyncio

    from axiom.agents.server import build_server

    tools = asyncio.run(build_server(catalogue, registry).list_tools())
    by_name = {t.name: t for t in tools}

    assert list(by_name["axiom_explain_value"].input_schema["properties"]) == [
        "sku",
        "attribute_code",
    ]
    assert "sku" in by_name["axiom_get_product"].input_schema["properties"]


@needs_mcp
def test_the_server_instructions_state_the_provenance_contract(catalogue, registry):
    from axiom.agents.server import INSTRUCTIONS

    assert "usable_for_compliance" in INSTRUCTIONS
    assert "directional" in INSTRUCTIONS


@needs_mcp
def test_a_bad_argument_becomes_a_structured_refusal_rather_than_a_crash(
    catalogue, registry
):
    """An agent can recover from `{"error": ...}`; it cannot recover from a stack trace."""
    import asyncio

    from axiom.agents.server import build_server

    server = build_server(catalogue, registry)
    result = asyncio.run(server.call_tool("axiom_get_product", {"sku": "NOPE-1"}))
    payload = getattr(result, "structured_content", result)

    assert payload["recoverable"] is True
    assert "not in this catalogue" in payload["error"]


@needs_mcp
def test_a_real_tool_call_returns_a_typed_value_with_its_unit(catalogue, registry):
    import asyncio

    from axiom.agents.server import build_server

    server = build_server(catalogue, registry)
    result = asyncio.run(
        server.call_tool(
            "axiom_explain_value",
            {"sku": "BA-100-075", "attribute_code": "pressure_rating_wog"},
        )
    )
    payload = getattr(result, "structured_content", result)

    assert payload["value"] == 600.0
    assert payload["unit"] == "psi"
    assert payload["provenance"]["verified"] is True
