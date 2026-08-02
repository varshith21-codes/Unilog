"""Tests for the structural invariants of the core domain model.

These are the tests that matter most in the whole project. They assert that the
"evidence or null" principle is enforced by the type system rather than by developer
discipline, because discipline does not survive a hackathon.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core import (
    AttributeValue,
    Classification,
    DerivationMethod,
    EvidenceSpan,
    Gap,
    GapReason,
    ProductRecord,
    Quantity,
    SourceDocument,
    ValidationLayer,
    ValidationResult,
    ValueRange,
    ValueStatus,
)
from axiom.core.certificate import build_certificate
from axiom.core.evidence import BoundingBox, DocumentType
from axiom.core.product import ClassificationScheme
from axiom.core.validation import Severity
from pydantic import ValidationError

SHA = "9f2c" + "0" * 60


def make_span(*, verified: bool = True, quote: str = "600 PSI WOG @ 73°F") -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp_1",
        document_id="milwaukee-bv-series.pdf",
        document_sha256=SHA,
        quote=quote,
        page=4,
        bbox=BoundingBox(x0=312, y0=508, x1=486, y1=528),
        table_ref="t1:r14:c3",
        quote_verified=verified,
        match_score=1.0,
    )


# --------------------------------------------------------------------- evidence invariant


def test_extraction_without_evidence_is_rejected():
    """The central invariant. An extracted value must say where it came from."""
    with pytest.raises(ValidationError) as exc:
        AttributeValue(
            attribute_code="pressure_rating_wog",
            value_raw="600 PSI",
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.99,
            evidence=[],
        )
    assert "requires at least one evidence span" in str(exc.value)
    assert "record a Gap instead" in str(exc.value)


@pytest.mark.parametrize(
    "method",
    [
        DerivationMethod.DOCUMENT_EXTRACTION,
        DerivationMethod.TABLE_EXTRACTION,
        DerivationMethod.IMAGE_EXTRACTION,
        DerivationMethod.WEB_EXTRACTION,
        DerivationMethod.SUPPLIER_FEED,
    ],
)
def test_every_extraction_family_method_requires_evidence(method: DerivationMethod):
    assert method.requires_evidence
    with pytest.raises(ValidationError):
        AttributeValue(
            attribute_code="body_material", method=method, confidence=0.9, evidence=[]
        )


@pytest.mark.parametrize(
    "method",
    [
        DerivationMethod.UNIT_CONVERSION,
        DerivationMethod.COMPUTED,
        DerivationMethod.HUMAN_ENTRY,
        DerivationMethod.PART_NUMBER_GRAMMAR,
    ],
)
def test_non_extraction_methods_may_omit_evidence(method: DerivationMethod):
    value = AttributeValue(attribute_code="nominal_size", method=method, confidence=0.8)
    assert value.evidence == []


def test_inferred_value_cannot_be_auto_accepted():
    """Inference is allowed, but it must never be published without a human looking."""
    with pytest.raises(ValidationError) as exc:
        AttributeValue(
            attribute_code="seat_material",
            method=DerivationMethod.PART_NUMBER_GRAMMAR,
            confidence=0.97,
            status=ValueStatus.AUTO_ACCEPTED,
        )
    assert "cannot be AUTO_ACCEPTED" in str(exc.value)


def test_inferred_value_may_be_human_approved():
    value = AttributeValue(
        attribute_code="seat_material",
        method=DerivationMethod.PART_NUMBER_GRAMMAR,
        confidence=0.97,
        status=ValueStatus.HUMAN_APPROVED,
        reviewed_by="analyst@example.com",
    )
    assert value.is_publishable


# --------------------------------------------------------------------- publishability


def test_unverified_quote_blocks_publication():
    """A value whose quote could not be matched back to the source is not publishable."""
    value = AttributeValue(
        attribute_code="pressure_rating_wog",
        value_canonical=Quantity(magnitude=600, unit="psi"),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.99,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[make_span(verified=False)],
    )
    assert value.has_verified_evidence is False
    assert value.is_publishable is False


def test_blocking_validation_failure_prevents_publication():
    value = AttributeValue(
        attribute_code="lead_free_compliant",
        value_canonical=True,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.95,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[make_span()],
        validations=[
            ValidationResult.failed(
                ValidationLayer.L6_FORMAL,
                "AR_POLICY_lead_free_material",
                "Lead-free claim is inconsistent with a leaded alloy body.",
                counterexample="body_material=Brass C36000 is a leaded alloy",
                suggested_fix="Set lead_free_compliant=false or cite an NSF/ANSI 372 certificate",
            )
        ],
    )
    assert value.is_publishable is False
    assert len(value.failed_validations()) == 1


def test_warning_does_not_block_publication():
    value = AttributeValue(
        attribute_code="cv_flow_coefficient",
        value_canonical=18.5,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.88,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[make_span(quote="Cv 18.5")],
        validations=[
            ValidationResult.warned(
                ValidationLayer.L3_STATISTICAL, "class_distribution_zscore", "Mild outlier",
                detail="z=2.1",
            )
        ],
    )
    assert value.is_publishable is True


def test_error_verdict_is_blocking_but_warning_severity_is_not():
    blocking = ValidationResult.failed(
        ValidationLayer.L2_DOMAIN_RULE, "R_PACK_WEIGHT", "inconsistent"
    )
    soft = ValidationResult.failed(
        ValidationLayer.L2_DOMAIN_RULE,
        "R_FULLPORT_CV",
        "below expected floor",
        severity=Severity.WARNING,
    )
    assert blocking.is_blocking is True
    assert soft.is_blocking is False
    assert soft.needs_review is True


# --------------------------------------------------------------------- value types


def test_range_must_be_ordered():
    with pytest.raises(ValidationError):
        ValueRange(minimum=60, maximum=-20, unit="celsius")
    ok = ValueRange(minimum=-20, maximum=60, unit="celsius")
    assert str(ok) == "-20 to 60 celsius"


def test_sha256_validation_rejects_garbage():
    with pytest.raises(ValidationError):
        SourceDocument(
            document_id="d1",
            uri="s3://bucket/d1.pdf",
            sha256="not-a-hash",
            fetched_at=datetime.now(UTC),
        )


def test_sha256_accepts_prefixed_form_and_normalizes():
    doc = SourceDocument(
        document_id="d1",
        uri="s3://bucket/d1.pdf",
        sha256="sha256:" + SHA.upper(),
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime.now(UTC),
    )
    assert doc.sha256 == SHA


def test_bounding_box_rejects_inverted_coordinates():
    with pytest.raises(ValidationError):
        BoundingBox(x0=500, y0=10, x1=100, y1=20)


def test_evidence_locator_is_human_readable():
    assert make_span().locator() == "milwaukee-bv-series.pdf p.4 t1:r14:c3"


# --------------------------------------------------------------------- record behaviour


def test_add_value_supersedes_previous_and_keeps_history():
    record = ProductRecord(tenant_id="t1", sku="MIL-BV075-LF", class_code="PLB.VLV.BALL.2PC")
    first = AttributeValue(
        attribute_code="pressure_rating_wog",
        value_canonical=Quantity(magnitude=400, unit="psi"),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.7,
        evidence=[make_span(quote="400 PSI")],
    )
    record.add_value(first)

    second = AttributeValue(
        attribute_code="pressure_rating_wog",
        value_canonical=Quantity(magnitude=600, unit="psi"),
        method=DerivationMethod.HUMAN_CORRECTION,
        confidence=1.0,
        status=ValueStatus.HUMAN_APPROVED,
        reviewed_by="analyst@example.com",
    )
    record.add_value(second)

    assert len(record.attribute_values) == 2, "history must be retained, not overwritten"
    assert len(record.current_values()) == 1
    current = record.get("pressure_rating_wog")
    assert current is not None
    assert current.version == 2
    assert current.value_canonical == Quantity(magnitude=600, unit="psi")
    assert first.status == ValueStatus.SUPERSEDED
    assert first.superseded_by == 2


def test_conflicts_surface_multiple_candidates():
    record = ProductRecord(tenant_id="t1", sku="X1")
    for i, (mag, doc) in enumerate([(24.0, "datasheet"), (120.0, "erp_export")]):
        record.attribute_values.append(
            AttributeValue(
                attribute_code="voltage",
                value_canonical=Quantity(magnitude=mag, unit="V"),
                method=DerivationMethod.DOCUMENT_EXTRACTION,
                confidence=0.8,
                evidence=[
                    EvidenceSpan(
                        span_id=f"sp_{i}",
                        document_id=doc,
                        document_sha256=SHA,
                        quote=f"{mag:g}V",
                        quote_verified=True,
                    )
                ],
            )
        )
    conflicts = record.conflicts()
    assert "voltage" in conflicts
    assert len(conflicts["voltage"]) == 2


def test_hts_classification_requires_human_confirmation():
    unconfirmed = Classification(
        scheme=ClassificationScheme.HTS, code="8481.80.30", confidence=0.61
    )
    assert unconfirmed.is_publishable is False

    confirmed = Classification(
        scheme=ClassificationScheme.HTS,
        code="8481.80.30",
        confidence=0.61,
        reviewed_by="trade@example.com",
    )
    assert confirmed.is_publishable is True

    etim = Classification(scheme=ClassificationScheme.ETIM, code="EC002714", confidence=0.93)
    assert etim.is_publishable is True


def test_fill_rate_and_verifiability():
    record = ProductRecord(tenant_id="t1", sku="X1")
    record.add_value(
        AttributeValue(
            attribute_code="a",
            value_canonical="x",
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.99,
            status=ValueStatus.AUTO_ACCEPTED,
            evidence=[make_span()],
        )
    )
    record.add_value(
        AttributeValue(
            attribute_code="b",
            value_canonical="y",
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.99,
            status=ValueStatus.AUTO_ACCEPTED,
            evidence=[make_span(verified=False)],
        )
    )
    # 'a' is publishable; 'b' has an unverified quote so it is not; 'c' is absent.
    assert record.fill_rate(["a", "b", "c"]) == pytest.approx(1 / 3)
    assert record.verifiability() == 1.0  # of the publishable set, all are verified


# --------------------------------------------------------------------- certificate


def test_certificate_only_certifies_publishable_values_and_signature_verifies():
    record = ProductRecord(
        tenant_id="t1",
        sku="MIL-BV075-LF",
        mpn="BV075-LF",
        brand="Milwaukee Valve",
        class_code="PLB.VLV.BALL.2PC",
        schema_version="ballvalve.v3",
    )
    record.classifications.append(
        Classification(
            scheme=ClassificationScheme.ETIM,
            code="EC002714",
            confidence=0.93,
            method="retrieval+llm",
        )
    )
    record.add_value(
        AttributeValue(
            attribute_code="pressure_rating_wog",
            value_raw="600 PSI WOG @ 73°F",
            value_canonical=Quantity(magnitude=600, unit="psi"),
            value_display="600 PSI WOG",
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.98,
            status=ValueStatus.AUTO_ACCEPTED,
            model_tier="mid",
            evidence=[make_span()],
            validations=[
                ValidationResult.passed(ValidationLayer.L1_DIMENSION, "unit_quantity_kind_match")
            ],
        )
    )
    # queued value must NOT appear in the certified attribute list
    record.add_value(
        AttributeValue(
            attribute_code="cv_flow_coefficient",
            value_canonical=18.5,
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.42,
            status=ValueStatus.QUEUED_FOR_REVIEW,
            evidence=[make_span(quote="Cv 18.5")],
        )
    )
    record.add_gap(
        Gap(
            attribute_code="seat_material",
            reason=GapReason.NOT_PRESENT_IN_ANY_SOURCE,
            sources_searched=["milwaukee-bv-series.pdf", "manufacturer_site"],
            revenue_exposure_usd=4120.0,
        )
    )

    cert = build_certificate(
        record,
        required_attribute_codes=["pressure_rating_wog", "cv_flow_coefficient", "seat_material"],
        pipeline_version="axiom-0.1.0",
        cost_usd=0.0187,
        wall_clock_seconds=11.4,
    )

    certified_codes = [a["code"] for a in cert.attributes]
    assert certified_codes == ["pressure_rating_wog"]
    assert cert.summary.attributes_populated == 1
    assert cert.summary.queued_for_review == 1
    assert cert.summary.gaps_required == 1
    assert cert.summary.quality_index.completeness == pytest.approx(1 / 3)
    assert cert.verify_signature() is True

    # the JSON must be renderable and contain a clickable-style citation
    payload = cert.to_json()
    assert "milwaukee-bv-series.pdf" in payload
    assert '"page": 4' in payload


def test_certificate_signature_detects_tampering():
    record = ProductRecord(tenant_id="t1", sku="X1")
    cert = build_certificate(
        record, required_attribute_codes=[], pipeline_version="axiom-0.1.0"
    )
    tampered = cert.model_copy(update={"sku": "X2"})
    assert tampered.verify_signature() is False
