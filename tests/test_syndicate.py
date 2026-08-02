"""Tests for channel pre-flight validation, exporters and certificate emission.

The property doing the most work: **a value in the review queue must never reach a feed.**
Everything else here is format detail; that one is the difference between risk control being
real and being decorative.
"""

from __future__ import annotations

import json

import pytest
from axiom.core.certificate import build_certificate
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.gaps import Gap, GapReason
from axiom.core.product import Classification, ClassificationScheme, ProductRecord
from axiom.core.validation import ValidationLayer, ValidationResult
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.schema import load_default
from axiom.syndicate import (
    Cx1PimExporter,
    SchemaOrgExporter,
    export_all,
    preflight,
    publishable_values,
    render_title,
    truncate_title,
)

CLASS_CODE = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60


@pytest.fixture(scope="module")
def registry():
    return load_default()


def span() -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp",
        document_id="ba100",
        document_sha256=SHA,
        quote="600 PSI WOG",
        page=1,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        quote_verified=True,
        match_score=1.0,
    )


def accepted(code: str, canonical, display: str | None = None) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=display if display is not None else str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.95,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[span()],
    )


def queued(code: str, canonical, display: str | None = None) -> AttributeValue:
    value = accepted(code, canonical, display)
    value.status = ValueStatus.QUEUED_FOR_REVIEW
    return value


def complete_record() -> ProductRecord:
    """A record satisfying every cx1_pim requirement."""
    record = ProductRecord(
        tenant_id="demo",
        sku="BA-100-075",
        mpn="BA-100-075",
        mpn_normalized="BA-100-075",
        brand="Milwaukee Valve",
        supplier_id="milwaukee",
        class_code=CLASS_CODE,
        schema_version=f"{CLASS_CODE}@v1",
    )
    record.classifications.append(
        Classification(
            scheme=ClassificationScheme.INTERNAL,
            code=CLASS_CODE,
            path=["Plumbing", "Valves", "Ball Valves", "Two-Piece"],
            confidence=0.92,
        )
    )
    record.classifications.append(
        Classification(scheme=ClassificationScheme.ETIM, code="EC002714", confidence=0.87)
    )
    for value in (
        accepted("nominal_size", Quantity(magnitude=19.05, unit="mm"), '3/4"'),
        accepted("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), "600 psi"),
        accepted("body_material", "Bronze C84400"),
        accepted("end_connection", "NPT Threaded"),
        accepted("selling_uom", "Each"),
        accepted("port_type", "Full Port"),
    ):
        record.add_value(value)
    return record


# ===================================================================== pre-flight


def test_complete_record_is_ready(registry):
    readiness = preflight(complete_record(), registry, "cx1_pim")
    assert readiness.ready is True
    assert readiness.missing == []
    assert readiness.not_publishable == []


def test_missing_required_attribute_blocks_publication(registry):
    record = complete_record()
    record.attribute_values = [
        v for v in record.attribute_values if v.attribute_code != "pressure_rating_wog"
    ]
    readiness = preflight(record, registry, "cx1_pim")
    assert readiness.ready is False
    assert "pressure_rating_wog" in readiness.missing


def test_queued_required_attribute_is_distinguished_from_missing(registry):
    """Different remedies: one needs a supplier, the other needs a reviewer."""
    record = complete_record()
    record.add_value(queued("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi")))
    readiness = preflight(record, registry, "cx1_pim")

    assert readiness.ready is False
    assert "pressure_rating_wog" in readiness.not_publishable
    assert "pressure_rating_wog" not in readiness.missing


def test_unknown_channel_is_reported(registry):
    readiness = preflight(complete_record(), registry, "nonexistent_channel")
    assert readiness.ready is False
    assert any("declares no" in w for w in readiness.warnings)


def test_record_without_a_class_cannot_be_published(registry):
    record = ProductRecord(tenant_id="t", sku="X")
    readiness = preflight(record, registry, "cx1_pim")
    assert readiness.ready is False
    assert any("no product class" in w for w in readiness.warnings)


def test_google_channel_requires_gtin(registry):
    """The record has no GTIN, so a channel that demands one must not pass."""
    readiness = preflight(complete_record(), registry, "google_merchant")
    assert readiness.ready is False
    assert "gtin" in readiness.missing


def test_gtin_from_the_record_satisfies_the_requirement(registry):
    record = complete_record()
    record.gtin = "012345678905"
    assert "gtin" not in preflight(record, registry, "google_merchant").missing


# ===================================================================== title rendering


def test_title_renders_from_the_template(registry):
    record = complete_record()
    profile = registry.product_class(CLASS_CODE).channel("cx1_pim")
    title = render_title(record, registry, profile)

    assert title.startswith("Milwaukee Valve BA-100-075")
    assert '3/4"' in title
    assert "Bronze" in title
    assert "Two-Piece Ball Valve" in title
    assert "600 psi" in title
    assert "Full Port" in title


def test_alloy_designation_is_shortened_for_the_title(registry):
    """'Bronze C84400' belongs in the spec table; the title says 'Bronze'."""
    record = complete_record()
    profile = registry.product_class(CLASS_CODE).channel("cx1_pim")
    title = render_title(record, registry, profile)
    assert "Bronze" in title
    assert "C84400" not in title


def test_missing_token_is_dropped_not_left_as_a_placeholder(registry):
    record = complete_record()
    record.attribute_values = [
        v for v in record.attribute_values if v.attribute_code != "port_type"
    ]
    profile = registry.product_class(CLASS_CODE).channel("cx1_pim")
    title = render_title(record, registry, profile)

    assert "{" not in title
    assert "}" not in title
    assert not title.endswith(",")
    assert ",," not in title


def test_queued_value_does_not_appear_in_the_title(registry):
    record = complete_record()
    record.add_value(queued("port_type", "Full Port"))
    profile = registry.product_class(CLASS_CODE).channel("cx1_pim")
    assert "Full Port" not in render_title(record, registry, profile)


def test_truncation_respects_word_boundaries():
    title = "Milwaukee Valve BA-100-075 Three Quarter Inch Bronze Ball Valve"
    assert truncate_title(title, 30) == "Milwaukee Valve BA-100-075"
    assert truncate_title(title, 500) == title
    assert truncate_title(title, None) == title


def test_oversize_title_warns_rather_than_blocks(registry):
    """Losing the tail of a title is far less damaging than withholding the product."""
    record = complete_record()
    record.brand = "Milwaukee Valve " * 20
    readiness = preflight(record, registry, "cx1_pim")
    assert readiness.ready is True
    assert any("truncated" in w for w in readiness.warnings)


# ===================================================================== the publication gate


def test_only_publishable_values_are_exported(registry):
    """The property that makes risk control real rather than decorative."""
    record = complete_record()
    record.add_value(queued("seat_material", "RPTFE"))

    values = publishable_values(record)
    assert "seat_material" not in {v.attribute_code for v in values}

    result = Cx1PimExporter().export(record, registry)
    assert result.published
    codes = {a["code"] for a in json.loads(result.payload)["attributes"]}
    assert "seat_material" not in codes


def test_withheld_values_are_reported_not_silently_dropped(registry):
    """A merchandiser needs to know the feed is thinner than the record, and why."""
    record = complete_record()
    record.add_value(queued("seat_material", "RPTFE"))
    result = Cx1PimExporter().export(record, registry)
    assert "seat_material" in result.withheld


def test_value_failing_validation_is_not_exported(registry):
    record = complete_record()
    poisoned = accepted("seat_material", "RPTFE")
    poisoned.validations = [
        ValidationResult.failed(ValidationLayer.L2_DOMAIN_RULE, "R_TEST", "contradiction")
    ]
    record.add_value(poisoned)

    result = Cx1PimExporter().export(record, registry)
    codes = {a["code"] for a in json.loads(result.payload)["attributes"]}
    assert "seat_material" not in codes


def test_unverified_evidence_is_not_exported(registry):
    record = complete_record()
    unverified = accepted("seat_material", "RPTFE")
    unverified.evidence = [
        EvidenceSpan(
            span_id="sp",
            document_id="d",
            document_sha256=SHA,
            quote="RPTFE",
            quote_verified=False,
        )
    ]
    record.add_value(unverified)

    result = Cx1PimExporter().export(record, registry)
    codes = {a["code"] for a in json.loads(result.payload)["attributes"]}
    assert "seat_material" not in codes


def test_export_refuses_when_not_ready(registry):
    record = complete_record()
    record.attribute_values = [
        v for v in record.attribute_values if v.attribute_code != "selling_uom"
    ]
    result = Cx1PimExporter().export(record, registry)
    assert result.published is False
    assert result.payload is None
    assert "selling_uom" in result.readiness.missing


def test_force_overrides_readiness_for_inspection(registry):
    record = complete_record()
    record.attribute_values = [
        v for v in record.attribute_values if v.attribute_code != "selling_uom"
    ]
    result = Cx1PimExporter().export(record, registry, force=True)
    assert result.published is True
    assert result.readiness.ready is False, "forcing must not rewrite the verdict"


# ===================================================================== CX1 PIM shape


def test_pim_payload_shape(registry):
    payload = json.loads(Cx1PimExporter().export(complete_record(), registry).payload)

    assert payload["sku"] == "BA-100-075"
    assert payload["brand"] == "Milwaukee Valve"
    assert payload["class_code"] == CLASS_CODE
    assert payload["category_path"] == ["Plumbing", "Valves", "Ball Valves", "Two-Piece"]
    assert payload["title"]


def test_pim_separates_magnitude_from_unit(registry):
    """A single '600 psi' string is readable and useless for faceting."""
    payload = json.loads(Cx1PimExporter().export(complete_record(), registry).payload)
    pressure = next(a for a in payload["attributes"] if a["code"] == "pressure_rating_wog")
    assert pressure["value"] == 600.0
    assert pressure["unit"] == "psi"
    assert pressure["display"] == "600 psi"


def test_pim_carries_provenance_per_value(registry):
    """A PIM receiving values without knowing which were human-approved cannot make its own
    trust decisions."""
    payload = json.loads(Cx1PimExporter().export(complete_record(), registry).payload)
    for attribute in payload["attributes"]:
        assert "source" in attribute
        assert "confidence" in attribute
        assert "reviewed" in attribute


def test_pim_includes_gaps(registry):
    record = complete_record()
    record.add_gap(
        Gap(attribute_code="cv_flow_coefficient", reason=GapReason.REFERRED_ELSEWHERE)
    )
    payload = json.loads(Cx1PimExporter().export(record, registry).payload)
    assert payload["gaps"][0]["code"] == "cv_flow_coefficient"


def test_pim_excludes_unconfirmed_hts(registry):
    record = complete_record()
    record.classifications.append(
        Classification(scheme=ClassificationScheme.HTS, code="8481.80.30", confidence=0.61)
    )
    payload = json.loads(Cx1PimExporter().export(record, registry).payload)
    schemes = {c["scheme"] for c in payload["classifications"]}
    assert "HTS" not in schemes


def test_pim_csv_is_wide_format(registry):
    csv_text = Cx1PimExporter().to_csv(complete_record(), registry)
    header, row = csv_text.strip().split("\n")
    assert header.startswith("sku,mpn,brand,class_code")
    assert "pressure_rating_wog" in header
    assert "BA-100-075" in row


def test_range_is_rendered_for_a_channel(registry):
    record = complete_record()
    record.add_value(
        accepted(
            "temperature_range",
            ValueRange(minimum=-28.9, maximum=185.6, unit="degC"),
            "-20 to 366 degF",
        )
    )
    payload = json.loads(Cx1PimExporter().export(record, registry).payload)
    temp = next(a for a in payload["attributes"] if a["code"] == "temperature_range")
    assert temp["value"] == "-28.9..185.6"
    assert temp["unit"] == "degC"


# ===================================================================== schema.org


def test_jsonld_is_valid_product_shape(registry):
    payload = json.loads(SchemaOrgExporter().export(complete_record(), registry).payload)
    assert payload["@context"] == "https://schema.org"
    assert payload["@type"] == "Product"
    assert payload["sku"] == "BA-100-075"
    assert payload["name"], "a Product without a name is invalid JSON-LD"
    assert payload["brand"] == {"@type": "Brand", "name": "Milwaukee Valve"}


def test_jsonld_specs_are_typed_properties_not_prose(registry):
    """Prose is invisible to an agent that needs to compare a pressure rating."""
    payload = json.loads(SchemaOrgExporter().export(complete_record(), registry).payload)
    properties = {p["propertyID"]: p for p in payload["additionalProperty"]}

    assert properties["pressure_rating_wog"]["@type"] == "PropertyValue"
    assert properties["pressure_rating_wog"]["value"] == 600.0
    assert properties["pressure_rating_wog"]["unitText"] == "psi"


def test_jsonld_omits_unit_for_unitless_values(registry):
    payload = json.loads(SchemaOrgExporter().export(complete_record(), registry).payload)
    material = next(
        p for p in payload["additionalProperty"] if p["propertyID"] == "body_material"
    )
    assert "unitText" not in material


def test_jsonld_falls_back_to_the_sku_rather_than_a_category_name(registry):
    """A name that reads 'Two-Piece Ball Valve' on ten thousand SKUs is worse than the SKU.

    With no brand, MPN or specs available, the title template collapses to just the class
    name — which is a duplicate-content signal and cannot disambiguate a search result. The
    ugly-but-identifying SKU is the better answer.
    """
    record = ProductRecord(tenant_id="t", sku="BARE-SKU", class_code=CLASS_CODE)
    result = SchemaOrgExporter().export(record, registry, force=True)
    assert json.loads(result.payload)["name"] == "BARE-SKU"


def test_jsonld_uses_the_title_when_it_actually_identifies_the_product(registry):
    payload = json.loads(SchemaOrgExporter().export(complete_record(), registry).payload)
    assert "Milwaukee Valve" in payload["name"]
    assert payload["name"] != "BA-100-075", "a real title should be preferred to the bare SKU"


def test_jsonld_category_uses_the_confident_path(registry):
    payload = json.loads(SchemaOrgExporter().export(complete_record(), registry).payload)
    assert payload["category"] == "Plumbing > Valves > Ball Valves > Two-Piece"


# ===================================================================== delta publishing


def test_identical_records_hash_identically(registry):
    exporter = Cx1PimExporter()
    first = exporter.export(complete_record(), registry)
    second = exporter.export(complete_record(), registry)
    assert first.content_hash == second.content_hash


def test_changed_value_changes_the_hash(registry):
    exporter = Cx1PimExporter()
    baseline = exporter.export(complete_record(), registry)

    changed = complete_record()
    changed.add_value(accepted("pressure_rating_wog", Quantity(magnitude=400.0, unit="psi")))
    assert exporter.export(changed, registry).content_hash != baseline.content_hash


def test_export_all_produces_every_channel(registry):
    results = export_all(complete_record(), registry)
    assert set(results) == {"cx1_pim", "schema_org"}
    assert all(r.published for r in results.values())


def test_export_summary_is_reportable(registry):
    summary = Cx1PimExporter().export(complete_record(), registry).summary()
    for key in ("channel", "published", "values", "content_hash", "ready"):
        assert key in summary


# ===================================================================== certificate


def test_certificate_certifies_only_publishable_values(registry):
    record = complete_record()
    record.add_value(queued("seat_material", "RPTFE"))

    certificate = build_certificate(
        record,
        required_attribute_codes=registry.required_codes(CLASS_CODE),
        pipeline_version="axiom-test",
        cost_usd=0.0187,
        wall_clock_seconds=11.4,
    )

    codes = {a["code"] for a in certificate.attributes}
    assert "seat_material" not in codes
    assert certificate.summary.queued_for_review >= 1


def test_certificate_signature_verifies_and_detects_tampering(registry):
    certificate = build_certificate(
        complete_record(),
        required_attribute_codes=registry.required_codes(CLASS_CODE),
        pipeline_version="axiom-test",
    )
    assert certificate.verify_signature() is True
    assert certificate.model_copy(update={"sku": "OTHER"}).verify_signature() is False


def test_certificate_carries_citations(registry):
    certificate = build_certificate(
        complete_record(),
        required_attribute_codes=registry.required_codes(CLASS_CODE),
        pipeline_version="axiom-test",
    )
    payload = json.loads(certificate.to_json())
    pressure = next(a for a in payload["attributes"] if a["code"] == "pressure_rating_wog")
    assert pressure["evidence"][0]["page"] == 1
    assert pressure["evidence"][0]["verified"] is True


def test_certificate_reports_completeness_against_required_only(registry):
    certificate = build_certificate(
        complete_record(),
        required_attribute_codes=registry.required_codes(CLASS_CODE),
        pipeline_version="axiom-test",
    )
    quality = certificate.summary.quality_index
    assert 0.0 < quality.completeness < 1.0, "the fixture deliberately omits some required attrs"
    assert quality.verifiability == 1.0
