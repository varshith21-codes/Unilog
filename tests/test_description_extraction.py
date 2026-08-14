"""Reading attributes out of a cryptic supplier description.

The description is treated as a source document, so a substring of it is a citable evidence span.
That is what lets these values through the publish gate while a legacy item-master value stays out:
the citation is provable by substring comparison rather than by fuzzy match.

The tests that carry the most weight are the refusals. A rule that reads "24 in W" as 24 watts, or
"BRS" as a specific bronze alloy, would be worse than no rule at all — it converts an unknown into
a confidently wrong value, and downstream nobody can tell the difference.
"""

from __future__ import annotations

import pytest
from axiom.core.values import DerivationMethod, Quantity, ValueStatus
from axiom.extract.description import (
    AbbreviationTable,
    default_abbreviation_path,
    extract_from_description,
    to_attribute_values,
)
from axiom.schema import load_default

DISHWASHER = "APP.KIT.DISHWASHER.BUILTIN"
BALL_VALVE = "PLB.VLV.BALL.2PC"
SHA = "d" * 64


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def table():
    return AbbreviationTable.load()


def run(description, registry, table, class_code=DISHWASHER):
    return extract_from_description(
        description, registry=registry, class_code=class_code, abbreviations=table
    )


def found(result) -> dict[str, str]:
    return {m.attribute_code: str(m.display) for m in result.matches}


# --------------------------------------------------------------------------- the real rows


def test_ground_truth_rows_yield_their_material(registry, table):
    """Both client rows are '...Dishwasher SS - Display Only'. SS is the one recoverable fact."""
    for description in (
        "PDSH4816AF Dishwasher SS - Display Only",
        "WDTS7024RZ Dishwasher SS - Display Only",
    ):
        assert found(run(description, registry, table)) == {
            "primary_material": "Stainless Steel"
        }


def test_colour_abbreviation_is_read(registry, table):
    assert found(run("KDTS424SBE Kitchen Aid Dishwasher Bk", registry, table)) == {
        "finish_color": "Black"
    }


def test_longest_abbreviation_wins(registry, table):
    """BSS is black stainless steel, not a stray SS inside a longer token."""
    result = run("Dishwasher BSS - Display Only", registry, table)
    assert found(result)["primary_material"] == "Black Stainless Steel"


def test_quantities_are_read_with_their_units(registry, table):
    result = run("PDSH4816AF Dishwasher SS 120V 15A 47dBA", registry, table)
    assert found(result) == {
        "primary_material": "Stainless Steel",
        "voltage_rating": "120 V",
        "amperage_rating": "15 A",
        "sound_level": "47 dBA",
    }


def test_quantity_canonicalises_to_the_declared_unit(registry, table):
    result = run("Dishwasher 120V", registry, table)
    match = next(m for m in result.matches if m.attribute_code == "voltage_rating")
    assert match.canonical == Quantity(magnitude=120.0, unit="V")


def test_a_space_between_number_and_unit_is_allowed(registry, table):
    assert "voltage_rating" in found(run("Dishwasher 120 V", registry, table))


# --------------------------------------------------------------------------- the refusals


def test_an_axis_label_is_not_read_as_a_unit(registry, table):
    """The client's own ground truth contains '24 in W x 24-1/4 in D', where W is Width.

    Allowing arbitrary whitespace between number and unit would read that as 24 watts. This is the
    exact string that motivated the single-optional-space rule.
    """
    result = run("Dishwasher SS 24 in W x 24-1/4 in D", registry, table)
    assert set(found(result)) == {"primary_material"}


def test_a_unit_the_class_does_not_bind_is_not_looked_for(registry, table):
    """Class scoping is what keeps unit patterns from being promiscuous.

    A dishwasher class binds no wattage attribute, so a wattage in the string is simply not
    extracted rather than being attached to whatever attribute happens to want a number.
    """
    result = run("Dishwasher SS 60W bulb included", registry, table)
    assert "wattage" not in found(result)
    assert set(found(result)) == {"primary_material"}


def test_implausible_magnitude_is_refused_with_a_reason(registry, table):
    """9000 V is not a domestic appliance supply; the plausible_range says so."""
    result = run("Dishwasher 9000V", registry, table)
    assert "voltage_rating" not in found(result)
    reasons = dict(result.refused)
    assert "voltage_rating" in reasons
    assert "plausible range" in reasons["voltage_rating"]


def test_a_compliance_code_never_publishes(registry, table):
    """'LF' in a description is not a lead-free certification.

    Reported for review rather than dropped, because a compliance token appearing in supplier text
    is exactly what a reviewer should see.
    """
    result = run("VLV BALL 3/4 600WOG LF FP THRD", registry, table, class_code=BALL_VALVE)
    assert "lead_free_compliant" not in found(result)
    reasons = dict(result.refused)
    assert "not a certification" in reasons["lead_free_compliant"]


def test_an_expansion_outside_the_enum_is_refused(registry, table, monkeypatch):
    """A declared expansion that does not snap onto a permitted value must not be written."""
    doctored = AbbreviationTable(terms={"port_type": {"zz": "Sideways Port"}})
    result = extract_from_description(
        "VLV BALL ZZ", registry=registry, class_code=BALL_VALVE, abbreviations=doctored
    )
    assert not result.matches
    assert "not a permitted value" in dict(result.refused)["port_type"]


def test_body_material_is_deliberately_not_in_the_table(table):
    """BRS cannot pick between three brasses and two bronzes, and the alloys differ in lead.

    The guide's own cryptic example is '3/8 CPLG BRS 150#'. Yielding no material from it is the
    correct answer, not a gap — `lead_free_compliant` has a cross-field rule that reads this
    attribute, so a guessed alloy would propagate into a compliance claim.
    """
    assert "body_material" not in table.attributes()


def test_the_guides_cryptic_example_yields_nothing_unsafe(registry, table):
    result = run("3/8 CPLG BRS 150#", registry, table, class_code=BALL_VALVE)
    assert "body_material" not in found(result)


def test_abbreviations_do_not_match_inside_words(registry, table):
    """'LF' must not fire inside 'SHELF'."""
    result = run("VLV BALL SHELF MOUNT", registry, table, class_code=BALL_VALVE)
    assert "lead_free_compliant" not in found(result)
    assert not result.refused


def test_no_class_means_no_extraction(registry, table):
    result = run("Dishwasher SS 120V", registry, table, class_code=None)
    assert result.matches == []


def test_unknown_class_is_handled(registry, table):
    result = run("Dishwasher SS", registry, table, class_code="NO.SUCH.CLASS")
    assert result.matches == []


def test_empty_description_yields_nothing(registry, table):
    assert run("", registry, table).matches == []


# --------------------------------------------------------------------------- evidence


def test_every_value_cites_a_verified_substring(registry, table):
    result = run("PDSH4816AF Dishwasher SS 120V", registry, table)
    values = to_attribute_values(result, document_id="item-master", document_sha256=SHA)
    assert values
    for value in values:
        assert len(value.evidence) == 1
        span = value.evidence[0]
        assert span.quote_verified
        # The claim that makes this stronger than a model extraction: the quote is provably a
        # substring of the document, checked rather than scored.
        assert span.quote in result.description
        assert span.document_sha256 == SHA


def test_spans_point_at_the_right_characters(registry, table):
    description = "PDSH4816AF Dishwasher SS 120V"
    result = run(description, registry, table)
    for match in result.matches:
        assert description[match.start : match.end] == match.value_raw


def test_values_use_the_supplier_feed_method(registry, table):
    """An extraction-family method, so the type system *requires* the evidence span."""
    values = to_attribute_values(
        run("Dishwasher SS", registry, table), document_id="d", document_sha256=SHA
    )
    assert all(v.method is DerivationMethod.SUPPLIER_FEED for v in values)
    assert all(v.method.requires_evidence for v in values)


def test_values_are_publishable_by_default(registry, table):
    values = to_attribute_values(
        run("Dishwasher SS 120V", registry, table), document_id="d", document_sha256=SHA
    )
    assert values and all(v.is_publishable for v in values)


def test_accept_false_leaves_them_for_the_policy(registry, table):
    values = to_attribute_values(
        run("Dishwasher SS", registry, table),
        document_id="d",
        document_sha256=SHA,
        accept=False,
    )
    assert all(v.status is ValueStatus.CANDIDATE for v in values)
    assert not any(v.is_publishable for v in values)


def test_prompt_version_travels_for_reproducibility(registry, table):
    values = to_attribute_values(
        run("Dishwasher SS", registry, table), document_id="d", document_sha256=SHA
    )
    assert values[0].prompt_version == "description@v1"


# --------------------------------------------------------------------------- the table itself


def test_table_loads_from_the_repository(table):
    assert default_abbreviation_path().is_file()
    assert "primary_material" in table.attributes()


def test_lookup_is_case_insensitive(registry):
    table = AbbreviationTable.load()
    for spelling in ("SS", "ss", "Ss"):
        result = extract_from_description(
            f"Dishwasher {spelling}",
            registry=registry,
            class_code=DISHWASHER,
            abbreviations=table,
        )
        assert found(result) == {"primary_material": "Stainless Steel"}, spelling


def test_ss_is_not_mapped_to_colour(table):
    """One token cannot evidence two independent attributes.

    Ground truth row 2 has Stainless Steel in both Material and Color; row 1 has it only in
    Material. Mapping SS to both would over-fill row 1, and over-fill is scored as a defect.
    """
    assert "ss" not in table.for_attribute("finish_color")
    assert "ss" in table.for_attribute("primary_material")


def test_a_missing_table_file_degrades_quietly(tmp_path):
    empty = AbbreviationTable.load(tmp_path / "nope.yaml")
    assert empty.attributes() == ()
    assert empty.for_attribute("primary_material") == {}
