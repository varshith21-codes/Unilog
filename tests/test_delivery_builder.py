"""Projecting a record onto a delivery row.

The tests that matter most here are the refusals. It is easy to write a builder that fills 252
columns; the whole value of this one is that it declines to fill a cell it cannot account for, and
records why. So there are as many assertions about what does *not* appear as about what does.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import Classification, ClassificationScheme, LifecycleStatus, ProductRecord
from axiom.core.specifications import ManufacturerSpecification, specification_id
from axiom.core.validation import ValidationLayer, ValidationResult
from axiom.core.values import AttributeValue, DerivationMethod, Quantity, ValueStatus
from axiom.delivery import (
    DeliveryFormatExporter,
    DeliveryRowBuilder,
    Provenance,
    SupplierRow,
    load_default,
)
from axiom.schema import load_default as load_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DELIVERY_CSV = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
DISHWASHER = "APP.KIT.DISHWASHER.BUILTIN"
SHA = "b" * 64

PDSH = {
    "Mfg_Part_Num": "PDSH4816AF",
    "Part_Desc": "PDSH4816AF Dishwasher SS - Display Only",
    "E1_Brand": "-- Unbranded --",
    "Unilog_Brand": "-- No Unilog Brand --",
    "DIB_Brand": "-- No DIB Brand --",
    "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
}

BRANDED = {
    "Mfg_Part_Num": "DCB1104",
    "Part_Desc": "DCB1104 Dewalt 12V/20V Charger - 4 Amp",
    "E1_Brand": "-- Unbranded --",
    "Unilog_Brand": "-- No Unilog Brand --",
    "DIB_Brand": "DEWALT",
    "Part_Manuf": "Black & Decker/dewlt (2585)",
}


@pytest.fixture(scope="module")
def fmt():
    return load_default()


@pytest.fixture(scope="module")
def registry():
    return load_schema()


@pytest.fixture
def builder(fmt, registry):
    return DeliveryRowBuilder(fmt, registry)


def span(quote: str, *, verified: bool = True, document_sha256: str = SHA) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"sp-{abs(hash((quote, document_sha256))) % 9999}",
        document_id="frigidaire-pdsh4816af",
        document_sha256=document_sha256,
        quote=quote,
        page=2,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        quote_verified=verified,
        match_score=1.0 if verified else 0.4,
    )


def manufacturer_specification(
    label: str,
    value: str,
    *,
    mapped_attribute_code: str | None = None,
    citable_as_manufacturer: bool = True,
    document_sha256: str = SHA,
) -> ManufacturerSpecification:
    quote = f"{label} .................... {value}"
    return ManufacturerSpecification(
        specification_id=specification_id(document_sha256, label, value),
        label_raw=label,
        value_raw=value,
        evidence=[span(quote, document_sha256=document_sha256)],
        confidence=0.94,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        mapped_attribute_code=mapped_attribute_code,
        citable_as_manufacturer=citable_as_manufacturer,
        model_id="test-model",
        prompt_version="extract.v3",
        schema_version=f"{DISHWASHER}@v1",
    )


def extracted(code, canonical, display=None, *, verified=True, status=None) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=display or str(canonical),
        value_canonical=canonical,
        value_display=display or str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.93,
        status=status or (ValueStatus.AUTO_ACCEPTED if verified else ValueStatus.QUEUED_FOR_REVIEW),
        evidence=[span(display or str(canonical), verified=verified)],
    )


def record_for(row: dict[str, str], *, class_code: str | None = DISHWASHER) -> ProductRecord:
    source = SupplierRow.parse(row)
    record = ProductRecord(
        tenant_id="unilog",
        sku=source.mpn or "unknown",
        mpn=source.mpn,
        class_code=class_code,
    )
    if class_code:
        record.classifications.append(
            Classification(
                scheme=ClassificationScheme.INTERNAL,
                code=class_code,
                path=["Appliances & Consumer Electronics"],
                confidence=0.88,
            )
        )
    return record


# --------------------------------------------------------------------------- structure


def test_row_is_always_the_full_width(builder, fmt):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert len(row.as_list()) == 252
    assert set(row.as_dict()) == set(fmt.header)


def test_unset_cells_are_empty_not_missing(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["UPC"] == ""
    assert built["LENGTH"] == ""


def test_values_serialise_in_column_order(builder, fmt):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert row.as_list() == [built[name] for name in fmt.header]


# --------------------------------------------------------------------------- passthrough


def test_input_columns_are_echoed_verbatim_including_sentinels(builder):
    """The echo exists so the client can join our file to theirs; 'improving' it breaks that."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    for column, value in PDSH.items():
        assert built[column] == value
    assert built["E1_Brand"] == "-- Unbranded --"
    assert row.cells["E1_Brand"].provenance is Provenance.PASSTHROUGH


# --------------------------------------------------------------------------- refusals


def test_client_key_columns_are_never_written(builder):
    """PART_NUMBER and SKU - MY_PART_NUMBER belong to the client's key space."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["PART_NUMBER"] == ""
    assert built["SKU - MY_PART_NUMBER"] == ""


def test_writing_an_unavailable_column_is_refused_and_noted(builder, fmt):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    builder._set(row, "PART_NUMBER", "20887830", Provenance.DERIVED)
    assert row.as_dict()["PART_NUMBER"] == ""
    assert any("refused to write 'PART_NUMBER'" in note for note in row.notes)


def test_unverified_extraction_is_withheld_not_published(builder):
    """The one rule that must never bend."""
    record = record_for(PDSH)
    record.add_value(
        extracted("voltage_rating", Quantity(magnitude=120.0, unit="V"), "120 V", verified=False)
    )
    row = builder.build(record, source=SupplierRow.parse(PDSH))

    assert row.as_dict()["ATTRIBUTE_VALUE 4"] == ""
    withheld = {w.column: w for w in row.withheld}
    assert "ATTRIBUTE_VALUE 4" in withheld
    assert withheld["ATTRIBUTE_VALUE 4"].attribute_code == "voltage_rating"
    assert "verified evidence span" in withheld["ATTRIBUTE_VALUE 4"].reason


def test_legacy_item_master_value_is_withheld_with_a_specific_reason(builder):
    """A value nobody can source is a gap wearing a value's clothing."""
    record = record_for(PDSH)
    record.add_value(
        AttributeValue(
            attribute_code="primary_material",
            value_raw="Stainless Steel",
            value_canonical="Stainless Steel",
            value_display="Stainless Steel",
            method=DerivationMethod.LEGACY_RECORD,
            confidence=0.0,
            status=ValueStatus.CANDIDATE,
        )
    )
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert row.as_dict()["ATTRIBUTE_VALUE 13"] == ""
    reason = next(w.reason for w in row.withheld if w.attribute_code == "primary_material")
    assert "legacy item-master value" in reason


def test_failed_validation_is_withheld_naming_the_rule(builder):
    record = record_for(PDSH)
    value = extracted("sound_level", Quantity(magnitude=47.0, unit="dBA"), "47 dBA")
    value.validations = [
        ValidationResult.failed(
            ValidationLayer.L3_STATISTICAL, "R_SOUND_PLAUSIBLE", "outside the class distribution"
        )
    ]
    record.add_value(value)
    row = builder.build(record, source=SupplierRow.parse(PDSH))

    assert row.as_dict()["ATTRIBUTE_VALUE 12"] == ""
    reason = next(w.reason for w in row.withheld if w.attribute_code == "sound_level")
    assert "R_SOUND_PLAUSIBLE" in reason


def test_unspsc_is_left_blank_even_though_the_class_declares_one(builder):
    """Filling a column the client deliberately left empty is a defect, not extra credit."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert row.as_dict()["UNSPSC"] == ""
    assert any("UNSPSC 52141505 is available" in note for note in row.notes)


def test_generated_cells_are_refused_when_nothing_is_established(builder, fmt, registry):
    """Copy assembled from no established facts would be pure invention."""
    empty = ProductRecord(tenant_id="unilog", sku="X", mpn=None, class_code=None)
    row = builder.build(empty, descriptions={"MOBILE_DESC": "A" * 65})
    assert row.as_dict()["MOBILE_DESC"] == ""
    assert any("no established cell supports it" in note for note in row.notes)


def test_generated_cells_are_allowed_once_facts_exist(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    text = "Rheem Manufacturing FRIGIDAIRE, Dishwasher, Professional Series, PDSH4816AF"
    row2 = builder.build(
        record_for(PDSH), source=SupplierRow.parse(PDSH), descriptions={"MOBILE_DESC": text}
    )
    assert row.as_dict()["MOBILE_DESC"] == ""
    assert row2.as_dict()["MOBILE_DESC"] == text
    assert row2.cells["MOBILE_DESC"].provenance is Provenance.GENERATED


def test_unknown_description_column_raises(builder):
    with pytest.raises(KeyError, match="not a delivery-format column"):
        builder.build(
            record_for(PDSH), source=SupplierRow.parse(PDSH), descriptions={"NOPE": "x"}
        )


# --------------------------------------------------------------------------- taxonomy


def test_both_hierarchies_are_emitted_from_their_own_declarations(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert (built["Dept"], built["Class"], built["Fine"]) == (
        "Appliances",
        "Large Appliances",
        "Dishwashers",
    )
    assert built["Classpath"] == (
        "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers"
    )


def test_classpath_uses_a_bare_separator(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert " > " not in row.as_dict()["Classpath"]


def test_product_name_is_the_bare_noun(builder):
    """Ground truth says 'Dishwasher'; the class is called 'Built-In Dishwasher'."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert row.as_dict()["Product Name"] == "Dishwasher"


def test_unclassified_record_notes_the_missing_taxonomy(builder):
    row = builder.build(record_for(PDSH, class_code=None), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["Classpath"] == ""
    assert built["ATTRIBUTE_LABEL 1"] == ""
    assert any("unclassified" in note for note in row.notes)


# --------------------------------------------------------------------------- attribute grid


def test_every_bound_attribute_gets_a_label_even_with_no_value(builder):
    """Ground truth row 1 labels slots 2, 7 and 14 with nothing beside them."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_LABEL 1"] == "Series"
    assert built["ATTRIBUTE_LABEL 15"] == "Additional Information"
    assert built["ATTRIBUTE_VALUE 1"] == ""


def test_grid_labels_stop_at_the_classes_binding_count(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_LABEL 15"] != ""
    assert built["ATTRIBUTE_LABEL 16"] == ""
    assert built["ATTRIBUTE_LABEL 50"] == ""


def test_unknown_manufacturer_specification_uses_a_residual_slot_with_provenance(builder):
    label = "Ball / Stem"
    displayed = "Chrome-plated brass / Brass"
    quote = "Ball / Stem .................... Chrome-plated brass / Brass"
    record = record_for(PDSH)
    record.add_manufacturer_specification(
        ManufacturerSpecification(
            specification_id=specification_id(SHA, label, displayed),
            label_raw=label,
            value_raw=displayed,
            evidence=[span(quote)],
            confidence=0.94,
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            mapped_attribute_code=None,
            citable_as_manufacturer=True,
            model_id="test-model",
            prompt_version="extract.v3",
            schema_version=f"{DISHWASHER}@v1",
        )
    )

    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_LABEL 15"] == "Additional Information"
    assert built["ATTRIBUTE_LABEL 16"] == label
    assert built["ATTRIBUTE_VALUE 16"] == displayed
    assert row.cells["ATTRIBUTE_LABEL 16"].provenance is Provenance.EXTRACTED
    assert row.cells["ATTRIBUTE_VALUE 16"].provenance is Provenance.EXTRACTED
    assert row.cells["ATTRIBUTE_VALUE 16"].evidence == (
        "frigidaire-pdsh4816af p.2",
    )
    assert row.sidecar()["manufacturer_specifications"] == [
        {
            "specification_id": specification_id(SHA, label, displayed),
            "label": label,
            "value": displayed,
            "mapped_attribute_code": None,
            "citable_as_manufacturer": True,
            "confidence": 0.94,
            "evidence": ["frigidaire-pdsh4816af p.2"],
            "delivery_status": "emitted",
        }
    ]


def test_untrusted_specification_stays_sidecar_only(builder):
    record = record_for(PDSH)
    record.add_manufacturer_specification(
        manufacturer_specification(
            "Finish", "Matte Black", citable_as_manufacturer=False
        )
    )

    row = builder.build(record, source=SupplierRow.parse(PDSH))

    assert row.as_dict()["ATTRIBUTE_LABEL 16"] == ""
    assert row.sidecar()["manufacturer_specifications"][0]["delivery_status"] == (
        "untrusted_source"
    )
    assert any("not verified as manufacturer-owned" in note for note in row.notes)


def test_same_label_different_value_conflict_emits_neither(builder):
    record = record_for(PDSH)
    record.add_manufacturer_specification(
        manufacturer_specification("Finish", "Matte Black")
    )
    record.add_manufacturer_specification(
        manufacturer_specification("Finish", "Gloss Black")
    )

    row = builder.build(record, source=SupplierRow.parse(PDSH))
    statuses = {
        item["value"]: item["delivery_status"]
        for item in row.sidecar()["manufacturer_specifications"]
    }

    assert row.as_dict()["ATTRIBUTE_LABEL 16"] == ""
    assert statuses == {"Gloss Black": "conflict", "Matte Black": "conflict"}
    assert sum("label conflict" in note for note in row.notes) == 1


def test_duplicate_statement_emits_once_and_keeps_both_provenance_rows(builder):
    record = record_for(PDSH)
    record.add_manufacturer_specification(
        manufacturer_specification(
            "Finish", "Matte Black", document_sha256="a" * 64
        )
    )
    record.add_manufacturer_specification(
        manufacturer_specification(
            "FINISH", "matte   black", document_sha256="c" * 64
        )
    )

    row = builder.build(record, source=SupplierRow.parse(PDSH))
    statuses = [
        item["delivery_status"]
        for item in row.sidecar()["manufacturer_specifications"]
    ]

    assert row.as_dict()["ATTRIBUTE_LABEL 16"] == "Finish"
    assert statuses == ["emitted", "duplicate_statement"]


def test_blank_mapping_metadata_normalizes_to_source_only_and_can_emit(builder):
    specification = manufacturer_specification(
        "Finish", "Matte Black", mapped_attribute_code="   "
    )
    record = record_for(PDSH)
    record.add_manufacturer_specification(specification)

    row = builder.build(record, source=SupplierRow.parse(PDSH))

    assert specification.mapped_attribute_code is None
    assert row.as_dict()["ATTRIBUTE_LABEL 16"] == "Finish"
    assert row.sidecar()["manufacturer_specifications"][0]["delivery_status"] == "emitted"


def test_mapped_raw_specification_cannot_bypass_typed_publication(builder):
    record = record_for(PDSH)
    record.add_manufacturer_specification(
        manufacturer_specification(
            "Material", "Stainless Steel", mapped_attribute_code="primary_material"
        )
    )

    row = builder.build(record, source=SupplierRow.parse(PDSH))

    assert row.as_dict()["ATTRIBUTE_LABEL 16"] == ""
    assert row.sidecar()["manufacturer_specifications"][0]["delivery_status"] == (
        "typed_mapped"
    )


def test_reversing_specification_insertion_keeps_residual_output_identical(builder):
    specifications = [
        manufacturer_specification("Zeta Field", "Second"),
        manufacturer_specification("Alpha Field", "First"),
    ]

    records = [record_for(PDSH), record_for(PDSH)]
    for specification in specifications:
        records[0].add_manufacturer_specification(specification)
    for specification in reversed(specifications):
        records[1].add_manufacturer_specification(specification)

    rows = [builder.build(record, source=SupplierRow.parse(PDSH)) for record in records]

    assert rows[0].as_dict() == rows[1].as_dict()
    assert (
        rows[0].sidecar()["manufacturer_specifications"]
        == rows[1].sidecar()["manufacturer_specifications"]
    )
    assert rows[0].as_dict()["ATTRIBUTE_LABEL 16"] == "Alpha Field"


def test_residual_overflow_selection_is_deterministic_and_preserved(builder, fmt):
    specifications = [
        manufacturer_specification(f"Spec {index:02d}", f"Value {index:02d}")
        for index in range(fmt.slots("attribute_grid", "label") + 2)
    ]
    records = [
        record_for(PDSH, class_code=None),
        record_for(PDSH, class_code=None),
    ]
    for specification in specifications:
        records[0].add_manufacturer_specification(specification)
    for specification in reversed(specifications):
        records[1].add_manufacturer_specification(specification)

    rows = [builder.build(record, source=SupplierRow.parse(PDSH)) for record in records]
    sidecars = [row.sidecar()["manufacturer_specifications"] for row in rows]
    capacity = fmt.slots("attribute_grid", "label")

    assert rows[0].as_dict() == rows[1].as_dict()
    assert sidecars[0] == sidecars[1]
    assert len(sidecars[0]) == capacity + 2
    assert [item["delivery_status"] for item in sidecars[0][:capacity]] == [
        "emitted"
    ] * capacity
    assert [item["delivery_status"] for item in sidecars[0][capacity:]] == [
        "capacity_overflow",
        "capacity_overflow",
    ]
    assert rows[0].as_dict()["ATTRIBUTE_LABEL 1"] == "Spec 00"
    assert rows[0].as_dict()[f"ATTRIBUTE_LABEL {capacity}"] == f"Spec {capacity - 1:02d}"


def test_quantity_splits_into_value_and_uom(builder):
    """The unit belongs in its own column so the client can facet on it."""
    record = record_for(PDSH)
    record.add_value(extracted("sound_level", Quantity(magnitude=47.0, unit="dBA"), "47 dBA"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_VALUE 12"] == "47"
    assert built["ATTRIBUTE_UOM 12"] == "dBA"


def test_string_attributes_leave_the_uom_empty(builder):
    record = record_for(PDSH)
    record.add_value(extracted("primary_material", "Stainless Steel"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_VALUE 13"] == "Stainless Steel"
    assert built["ATTRIBUTE_UOM 13"] == ""


def test_integer_attribute_renders_without_a_unit(builder):
    record = record_for(PDSH)
    record.add_value(extracted("wash_cycle_count", 5, "5"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["ATTRIBUTE_VALUE 3"] == "5"
    assert built["ATTRIBUTE_UOM 3"] == ""


def test_named_column_attributes_bypass_the_grid(builder):
    """Warranty has its own column and must not consume a numbered slot."""
    record = record_for(PDSH)
    record.add_value(
        extracted("warranty_terms", "1 Year Manufacturer, 1 Year Labor and Parts")
    )
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["Warranty"] == "1 Year Manufacturer, 1 Year Labor and Parts"
    assert built["ATTRIBUTE_LABEL 16"] == ""


def test_multi_enum_joins_with_the_clients_pipe(builder):
    record = record_for(PDSH)
    record.add_value(extracted("approvals", ["ENERGY STAR", "NSF-61", "UL"], "UL, NSF-61"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert row.as_dict()["Standard/Approvals"] == "ENERGY STAR|NSF-61|UL"


def test_boolean_renders_as_yes_or_no(builder):
    record = record_for(PDSH)
    record.add_value(extracted("prop65_warning_required", True, None))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert row.as_dict()["Prop 65"] == "Yes"


# --------------------------------------------------------------------------- identity


def test_brand_is_taken_from_a_column_that_held_one(builder):
    row = builder.build(record_for(BRANDED), source=SupplierRow.parse(BRANDED))
    assert row.as_dict()["BRAND_NAME"] == "DEWALT"
    assert row.cells["BRAND_NAME"].source == "DIB_Brand"


def test_brand_is_left_blank_when_the_input_has_none(builder):
    """Ground truth expects FRIGIDAIRE(R), which appears nowhere in the input row.

    So the honest output is blank-pending-retrieval rather than a guess assembled from
    Part_Manuf, which here names a buying co-op.
    """
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert row.as_dict()["BRAND_NAME"] == ""
    assert any("requires retrieval" in note for note in row.notes)


def test_distributor_is_not_published_as_the_manufacturer(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert row.as_dict()["MANUFACTURER_NAME"] == ""
    withheld = next(w for w in row.withheld if w.column == "MANUFACTURER_NAME")
    assert "buying co-op" in withheld.reason


def test_a_real_manufacturer_is_proposed_at_low_confidence(builder):
    """Proposed, not verified: exact casing and legal suffixes need the approved master."""
    row = builder.build(record_for(BRANDED), source=SupplierRow.parse(BRANDED))
    assert row.as_dict()["MANUFACTURER_NAME"] == "Black & Decker/dewlt"
    assert row.cells["MANUFACTURER_NAME"].confidence == 0.50


def test_mpn_is_echoed_into_the_manufacturer_part_number(builder):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    assert row.as_dict()["MANUFACTURER_PART_NUMBER"] == "PDSH4816AF"


# --------------------------------------------------------------------------- identifiers


def test_twelve_digit_gtin_populates_upc(builder):
    record = record_for(PDSH)
    record.gtin = "012345678905"
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["GTIN"] == "012345678905"
    assert built["UPC"] == "012345678905"
    assert built["EAN"] == ""


def test_thirteen_digit_gtin_populates_ean(builder):
    record = record_for(PDSH)
    record.gtin = "0123456789012"
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    built = row.as_dict()
    assert built["EAN"] == "0123456789012"
    assert built["UPC"] == ""


def test_discontinued_lifecycle_is_flagged(builder):
    record = record_for(PDSH)
    record.lifecycle_status = LifecycleStatus.DISCONTINUED
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert row.as_dict()["Discontinued"] == "Yes"


# --------------------------------------------------------------------------- evidence + assets


def test_first_url_is_the_manufacturer_url(builder):
    url = "https://www.frigidaire.com/en/p/owner-center/product-support/PDSH4816AF"
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH), reference_urls=[url])
    built = row.as_dict()
    assert built["MFR URL"] == url
    assert row.cells["MFR URL"].provenance is Provenance.EVIDENCE


def test_extra_urls_fill_the_reference_slots_in_order(builder):
    urls = [f"https://example.com/{n}" for n in range(4)]
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH), reference_urls=urls)
    built = row.as_dict()
    assert built["MFR URL"] == urls[0]
    assert built["Ref URL 1"] == urls[1]
    assert built["Ref URL 3"] == urls[3]
    assert built["Ref URL 4"] == ""


def test_url_overflow_is_reported(builder):
    urls = [f"https://example.com/{n}" for n in range(9)]
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH), reference_urls=urls)
    assert any("not emitted" in note for note in row.notes)


def test_features_fill_their_slots_and_overflow_is_reported(builder):
    features = [f"Feature {n}" for n in range(25)]
    row = builder.build(
        record_for(PDSH), source=SupplierRow.parse(PDSH), features=features
    )
    built = row.as_dict()
    assert built["ITEM_FEATURES_1"] == "Feature 0"
    assert built["ITEM_FEATURES_20"] == "Feature 19"
    assert any("20 slots" in note for note in row.notes)


def test_assets_are_written_where_supplied(builder):
    row = builder.build(
        record_for(PDSH),
        source=SupplierRow.parse(PDSH),
        assets={
            "Product Image": "FRIGIDAIRE_PDSH4816AF.jpg",
            "Specification Sheet": "FRIGIDAIRE_PDSH4816AF_Specification_Sheet.pdf",
        },
    )
    built = row.as_dict()
    assert built["Product Image"] == "FRIGIDAIRE_PDSH4816AF.jpg"
    assert built["Alternate Image 1"] == ""


# --------------------------------------------------------------------------- constraints


def test_constraint_violations_are_detected_on_the_built_row(builder):
    """A limit enforced only at generation time is not enforced once anything else can write."""
    row = builder.build(
        record_for(PDSH),
        source=SupplierRow.parse(PDSH),
        descriptions={"INVOICE_DESC": "this is far too long to be an invoice line and lowercase"},
    )
    problems = row.violations()
    assert "INVOICE_DESC" in problems
    assert len(problems["INVOICE_DESC"]) == 2


def test_compliant_copy_reports_no_violations(builder):
    row = builder.build(
        record_for(PDSH),
        source=SupplierRow.parse(PDSH),
        descriptions={"INVOICE_DESC": "DISHWASHER LEG 5 SST 120V 15A 50-1/4IN"},
    )
    assert row.violations() == {}


# --------------------------------------------------------------------------- sidecar


def test_sidecar_accounts_for_every_populated_cell(builder):
    record = record_for(PDSH)
    record.add_value(extracted("sound_level", Quantity(magnitude=47.0, unit="dBA"), "47 dBA"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))

    sidecar = row.sidecar()
    assert sidecar["populated"] == row.populated_count
    assert sidecar["of_columns"] == 252
    assert len(sidecar["cells"]) == row.populated_count
    assert sidecar["provenance"]["passthrough"] == 6
    assert sidecar["provenance"]["extracted"] == 1


def test_cited_cells_carry_their_locators(builder):
    record = record_for(PDSH)
    record.add_value(extracted("sound_level", Quantity(magnitude=47.0, unit="dBA"), "47 dBA"))
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert row.cited_columns() == ("ATTRIBUTE_VALUE 12",)
    assert row.cells["ATTRIBUTE_VALUE 12"].evidence


def test_withheld_cells_appear_in_the_sidecar(builder):
    record = record_for(PDSH)
    record.add_value(
        extracted("voltage_rating", Quantity(magnitude=120.0, unit="V"), "120 V", verified=False)
    )
    row = builder.build(record, source=SupplierRow.parse(PDSH))
    assert any(w["attribute"] == "voltage_rating" for w in row.sidecar()["withheld"])


# --------------------------------------------------------------------------- exporter


def test_export_header_matches_the_contract(builder, fmt):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    export = DeliveryFormatExporter(fmt).to_csv([row])
    header = next(csv.reader(export.csv_text.splitlines()[:1]))
    assert header == list(fmt.header)


@pytest.mark.skipif(not CLIENT_DELIVERY_CSV.exists(), reason="client CSV not present")
def test_export_header_matches_the_clients_file(builder, fmt):
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    export = DeliveryFormatExporter(fmt).to_csv([row])
    ours = next(csv.reader(export.csv_text.splitlines()[:1]))
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        theirs = next(csv.reader(handle))
    assert ours == theirs


def test_every_exported_row_is_the_full_width(builder, fmt):
    rows = [
        builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH)),
        builder.build(record_for(BRANDED), source=SupplierRow.parse(BRANDED)),
    ]
    export = DeliveryFormatExporter(fmt).to_csv(rows)
    parsed = list(csv.reader(export.csv_text.splitlines()))
    assert len(parsed) == 3
    assert all(len(line) == 252 for line in parsed)


def test_export_uses_lf_not_crlf(builder, fmt):
    """The csv default emits CRLF on every platform, which becomes CRCRLF on Windows."""
    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    export = DeliveryFormatExporter(fmt).to_csv([row])
    assert "\r\n" not in export.csv_text


def test_export_reports_violations_with_the_offending_row(builder, fmt):
    row = builder.build(
        record_for(PDSH),
        source=SupplierRow.parse(PDSH),
        descriptions={"INVOICE_DESC": "far too long to fit in a forty character invoice line"},
    )
    export = DeliveryFormatExporter(fmt).to_csv([row])
    assert not export.compliant
    assert export.violations[0]["column"] == "INVOICE_DESC"
    assert export.violations[0]["mpn"] == "PDSH4816AF"


def test_export_is_content_hashed_for_delta_publishing(builder, fmt):
    exporter = DeliveryFormatExporter(fmt)
    first = exporter.to_csv([builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))])
    second = exporter.to_csv([builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))])
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64


def test_exporter_refuses_a_row_built_against_another_contract(builder, fmt, registry, tmp_path):
    from axiom.delivery.format import DeliveryFormat

    other = DeliveryFormat.load(
        _write_minimal(tmp_path)
    )
    stray = DeliveryRowBuilder(other, registry).build(record_for(PDSH, class_code=None))
    with pytest.raises(ValueError, match="different delivery format"):
        DeliveryFormatExporter(fmt).to_csv([stray])


def test_sidecar_json_documents_the_provenance_rules(builder, fmt):
    import json

    row = builder.build(record_for(PDSH), source=SupplierRow.parse(PDSH))
    payload = json.loads(DeliveryFormatExporter(fmt).sidecar([row]))
    legend = payload["provenance_legend"]
    assert legend["extracted"]["requires_evidence_span"] is True
    assert legend["passthrough"]["requires_evidence_span"] is False
    assert legend["unavailable"]["may_be_populated"] is False
    assert legend["generated"]["may_support_generation"] is False
    assert payload["records"][0]["mpn"] == "PDSH4816AF"


def _write_minimal(tmp_path: Path) -> Path:
    path = tmp_path / "other.yaml"
    path.write_text(
        "format:\n  name: other\n  sections:\n    - group: g\n      provenance: derived\n"
        "      columns: [OnlyColumn]\n",
        encoding="utf-8",
    )
    return path
