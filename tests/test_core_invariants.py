"""Tests for the structural invariants of the core domain model.

These are the tests that matter most in the whole project. They assert that the
"evidence or null" principle is enforced by the type system rather than by developer
discipline, because discipline does not survive a hackathon.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

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
from axiom.core.certificate import (
    QualityIndex,
    RichnessComponents,
    build_certificate,
    richness_for,
)
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


# ============================================ richness, and the unmeasured/zero distinction
#
# Richness carries a tenth of the composite weight. For most of this project's life it was
# hardcoded to 0.0, so every composite the system reported was understated by up to ten
# points for a reason that had nothing to do with the data. These guard the fix, and the fix
# rests entirely on one distinction: a dimension nobody could observe is not a dimension that
# scored badly.


def channel(published: bool):
    """Minimal stand-in for a syndicate export. `richness_for` reads only `.published`."""
    return SimpleNamespace(published=published)


def test_an_unmeasured_richness_is_none_not_zero():
    components = RichnessComponents()

    assert components.score() is None
    assert components.observed() == []


def test_channel_readiness_is_the_share_that_passed_preflight():
    components = richness_for(
        ProductRecord(tenant_id="t", sku="S"),
        exports={"pim": channel(False), "schema_org": channel(True)},
    )

    assert components.channel_readiness == 0.5
    assert components.score() == 0.5
    assert components.observed() == ["channel_readiness"]


def test_copy_that_was_never_attempted_is_unobserved():
    """Not zero. A pipeline option nobody selected is not an absence of usable prose."""
    components = richness_for(ProductRecord(tenant_id="t", sku="S"), copy=None)

    assert components.copy_depth is None


def test_copy_that_was_withheld_scores_zero():
    """Attempted and blocked *is* a real absence of publishable prose, unlike never trying."""
    components = richness_for(
        ProductRecord(tenant_id="t", sku="S"),
        copy={"published": False, "headline": "Bronze ball valve"},
    )

    assert components.copy_depth == 0.0
    assert components.score() == 0.0


def test_copy_depth_is_the_share_of_fields_populated():
    components = richness_for(
        ProductRecord(tenant_id="t", sku="S"),
        copy={
            "published": True,
            "headline": "Bronze ball valve",
            "short_description": "A valve.",
            "long_description": "",
            "bullets": [],
        },
    )

    assert components.copy_depth == 0.5


def test_relationships_and_assets_stay_unobserved():
    """Both depend on modules that do not exist. Scoring them zero would report a data-quality
    deficit where the truth is a missing feature — the same reason a validation layer reports
    SKIPPED rather than FAIL when its precondition is absent."""
    components = richness_for(
        ProductRecord(tenant_id="t", sku="S"), exports={"pim": channel(True)}
    )

    assert components.relationships is None
    assert components.assets is None
    assert components.score() == 1.0, "the mean is over what was observed, not over four slots"


# ------------------------------------------------------------------ the composite


def index(**overrides) -> QualityIndex:
    values = {"completeness": 0.8, "verifiability": 1.0, "consistency": 1.0}
    values.update(overrides)
    return QualityIndex(**values)


def test_an_unmeasured_richness_does_not_drag_the_composite_down():
    """The bug this fixes. Multiplying an absent richness by 0.10 and adding zero does not leave
    the composite alone — it removes a tenth of it, which is indistinguishable from a product with
    genuinely no assets, no copy and no channel readiness."""
    unmeasured = index(richness=None)
    scored_zero = index(richness=0.0)

    assert unmeasured.composite > scored_zero.composite
    # Renormalised over the three measured dimensions rather than divided by a weight never applied.
    expected = (0.8 * 0.35 + 1.0 * 0.30 + 1.0 * 0.25) / 0.90
    assert unmeasured.composite == pytest.approx(round(expected, 4))


def test_a_measured_zero_still_counts_against_the_composite():
    """The mirror of the above. Observing that nothing is publishable to any channel is a real
    finding and must not be discarded along with the unmeasured case."""
    assert index(richness=0.0).composite < index(richness=1.0).composite


def test_the_composite_reports_which_dimensions_it_spans():
    assert index(richness=None).measured_dimensions == [
        "completeness",
        "consistency",
        "verifiability",
    ]
    assert len(index(richness=0.4).measured_dimensions) == 4


def test_the_composite_is_serialised_rather_than_left_to_the_client():
    """It used to be a bare property, so `model_dump` dropped it and the console reimplemented the
    weighting in TypeScript. The formula then existed in two languages and would have disagreed
    with itself the moment richness became optional."""
    dumped = index(richness=0.5).model_dump(mode="json")

    assert "composite" in dumped
    assert "measured_dimensions" in dumped
    assert dumped["composite"] == index(richness=0.5).composite
