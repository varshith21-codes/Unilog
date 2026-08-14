"""DPP readiness (Tier 3, item 22).

The tests that matter here are about *what a status means*, because the whole value of the module is
that it refuses to average two different kinds of failure. A field the schema cannot express and a
field nobody extracted are both "not ready" and they want completely different responses, so the
tests assert the distinction directly rather than checking a percentage.

The other cluster guards the evidence bar. A DPP field is a regulatory assertion, and the attributes
behind these fields all declare `evidence_requirement: strict`. A test suite that let an unverified
or inferred value count as ready would quietly undo that declaration one layer later.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.compliance import (
    FieldStatus,
    PassportProfile,
    Requirement,
    assess_readiness,
    format_readiness,
    format_readiness_sweep,
    sweep_readiness,
)
from axiom.core.evidence import DocumentType, EvidenceSpan, SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.resolve import records_from_golden
from axiom.schema import load_default

SHA = "b1c2" + "0" * 60


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def profile():
    return PassportProfile.load_default()


@pytest.fixture
def document() -> SourceDocument:
    return SourceDocument(
        document_id="doc@b1c20000",
        uri="local://doc",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        supplier_id="milwaukee",
    )


def _span(document: SourceDocument, *, verified: bool = True) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="s1",
        document_id=document.document_id,
        document_sha256=document.sha256,
        quote="as stated on the datasheet",
        page=1,
        quote_verified=verified,
    )


def _record(sku: str = "X") -> ProductRecord:
    return ProductRecord(tenant_id="t", sku=sku, class_code="PLB.VLV.BALL.2PC")


def _extracted(
    code: str,
    canonical: object,
    document: SourceDocument,
    *,
    verified: bool = True,
    status: ValueStatus = ValueStatus.AUTO_ACCEPTED,
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_canonical=canonical,
        value_display=str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.95,
        status=status,
        evidence=[_span(document, verified=verified)],
    )


def _assessment(report, field_id: str):
    return next(a for a in report.assessments if a.field.id == field_id)


# --------------------------------------------------------------------- the profile itself


def test_the_profile_only_maps_attributes_the_schema_defines(registry, profile):
    """A misspelled mapping would otherwise be scored as an unfixable schema gap."""
    assert profile.check_against(registry) == []


def test_the_profile_declares_both_mapped_and_unmapped_fields(profile):
    """The module's whole point is the contrast; a profile with only one kind is misconfigured."""
    mapped = [f for f in profile.fields if f.is_mapped]
    unmapped = [f for f in profile.fields if not f.is_mapped]

    assert mapped and unmapped
    assert all(f.note for f in unmapped), "an unmapped field must explain why it cannot be mapped"


def test_a_profile_with_duplicate_field_ids_is_refused(tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text(
        "name: dup\nversion: '1'\nfields:\n"
        "  - {id: a, name: A, group: g}\n"
        "  - {id: a, name: A2, group: g}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate field ids"):
        PassportProfile.load(path)


def test_only_mandatory_fields_are_scored(profile):
    """A score that moves because of a requirement not yet in force is one nobody will trust."""
    assert Requirement.MANDATORY.is_scored
    assert not Requirement.CONDITIONAL.is_scored
    assert not Requirement.RECOMMENDED.is_scored


# --------------------------------------------------------------------- status semantics


def test_an_unmapped_field_is_a_schema_gap_not_a_zero(registry, profile):
    record = _record()
    report = assess_readiness(record, registry, profile)
    assessment = _assessment(report, "carbon_footprint")

    assert assessment.status is FieldStatus.UNMAPPED
    assert assessment.status.is_schema_gap
    assert not assessment.status.is_data_gap
    assert "re-extraction" in assessment.reason


def test_a_mapped_field_with_no_value_is_a_data_gap(registry, profile):
    report = assess_readiness(_record(), registry, profile)
    assessment = _assessment(report, "product_identifier")

    assert assessment.status is FieldStatus.ABSENT
    assert assessment.status.is_data_gap
    assert not assessment.status.is_schema_gap
    assert assessment.missing == ("gtin",)


def test_the_two_gap_kinds_are_never_merged(registry, profile):
    """The distinction that decides whether a team calls the pipeline or the supplier."""
    report = assess_readiness(_record(), registry, profile)

    assert report.data_gaps()
    assert report.schema_gaps()
    assert not {a.field.id for a in report.data_gaps()} & {
        a.field.id for a in report.schema_gaps()
    }


def test_a_verified_value_makes_its_field_ready(registry, profile, document):
    record = _record()
    record.add_value(_extracted("gtin", "00812345678901", document))

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.VERIFIED
    assert assessment.is_ready
    assert assessment.contributing == ("gtin",)


# --------------------------------------------------------------------- the evidence bar


def test_an_unverified_quote_does_not_satisfy_a_passport_field(registry, profile, document):
    """A regulatory assertion resting on an unverified citation is the case this system refuses."""
    record = _record()
    record.add_value(_extracted("gtin", "00812345678901", document, verified=False))

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.UNVERIFIED
    assert not assessment.is_ready


def test_a_value_awaiting_review_does_not_satisfy_a_passport_field(
    registry, profile, document
):
    record = _record()
    record.add_value(
        _extracted(
            "gtin", "00812345678901", document, status=ValueStatus.QUEUED_FOR_REVIEW
        )
    )

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.UNVERIFIED


def test_an_inferred_value_is_reported_as_inferred_however_confident(registry, profile):
    """The attributes behind these fields forbid inference by declaration; so does this layer."""
    record = _record()
    record.add_value(
        AttributeValue(
            attribute_code="gtin",
            value_canonical="00812345678901",
            method=DerivationMethod.PART_NUMBER_GRAMMAR,
            confidence=0.99,
            status=ValueStatus.HUMAN_APPROVED,
        )
    )

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.INFERRED
    assert not assessment.is_ready
    assert "inference" in assessment.reason


def test_a_human_entered_value_counts_as_sourced(registry, profile):
    """A named person is an accountable source — the same rule the rest of the system applies."""
    record = _record()
    record.add_value(
        AttributeValue(
            attribute_code="gtin",
            value_canonical="00812345678901",
            method=DerivationMethod.HUMAN_ENTRY,
            confidence=1.0,
            status=ValueStatus.HUMAN_APPROVED,
        )
    )

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.VERIFIED


def test_a_legacy_value_never_satisfies_a_passport_field(registry, profile):
    """An item-master row of unknown origin is a gap wearing a value's clothing."""
    record = _record()
    record.add_value(
        AttributeValue(
            attribute_code="gtin",
            value_canonical="00812345678901",
            method=DerivationMethod.LEGACY_RECORD,
            confidence=0.5,
            status=ValueStatus.HUMAN_APPROVED,
        )
    )

    assessment = _assessment(
        assess_readiness(record, registry, profile), "product_identifier"
    )
    assert assessment.status is FieldStatus.UNVERIFIED


# --------------------------------------------------------------------- multi-attribute fields


def test_a_field_needing_several_attributes_is_partial_until_all_are_verified(
    registry, profile, document
):
    """substances_of_concern_present needs all three flags: one certification is not the others."""
    record = _record()
    record.add_value(_extracted("lead_free_compliant", True, document))

    assessment = _assessment(
        assess_readiness(record, registry, profile), "substances_of_concern_present"
    )
    assert assessment.status is FieldStatus.PARTIAL
    assert assessment.contributing == ("lead_free_compliant",)
    assert set(assessment.missing) == {"prop65_warning_required", "rohs_compliant"}


def test_all_three_substance_flags_together_satisfy_the_field(registry, profile, document):
    record = _record()
    for code in ("lead_free_compliant", "prop65_warning_required", "rohs_compliant"):
        record.add_value(_extracted(code, True, document))

    assessment = _assessment(
        assess_readiness(record, registry, profile), "substances_of_concern_present"
    )
    assert assessment.status is FieldStatus.VERIFIED
    assert len(assessment.contributing) == 3


# --------------------------------------------------------------------- scoring


def test_readiness_does_not_renormalise_away_the_unmapped_fields(registry, profile, document):
    """Dropping them from the denominator would report a catalogue as nearly ready when most of
    the work has not been started."""
    record = _record()
    record.add_value(_extracted("gtin", "00812345678901", document))
    report = assess_readiness(record, registry, profile)

    mandatory = len(report.scored())
    assert report.readiness == pytest.approx(1 / mandatory)
    assert report.addressable_readiness > report.readiness


def test_addressable_readiness_covers_only_what_the_schema_can_express(
    registry, profile, document
):
    record = _record()
    report = assess_readiness(record, registry, profile)

    addressable = [a for a in report.scored() if not a.status.is_schema_gap]
    assert 0 < len(addressable) < len(report.scored())
    assert report.addressable_readiness == 0.0


def test_an_empty_record_scores_zero_rather_than_dividing_by_zero(registry, profile):
    report = assess_readiness(_record(), registry, profile)
    assert report.readiness == 0.0
    assert report.addressable_readiness == 0.0


# --------------------------------------------------------------------- payload


def test_the_payload_lists_every_field_including_the_empty_ones(registry, profile, document):
    """Omitting a gap would let a consumer mistake an absent field for an asserted one."""
    record = _record()
    record.add_value(_extracted("gtin", "00812345678901", document))
    payload = assess_readiness(record, registry, profile).payload()

    assert set(payload["fields"]) == {f.id for f in profile.fields}
    assert payload["fields"]["carbon_footprint"]["status"] == "unmapped"
    assert payload["fields"]["product_identifier"]["status"] == "verified"
    assert payload["registrable"] is False


def test_a_payload_is_only_registrable_when_every_mandatory_field_is_ready(
    registry, profile, document
):
    record = _record()
    for declared in profile.mandatory:
        for code in declared.satisfied_by:
            record.add_value(_extracted(code, "value", document))

    report = assess_readiness(record, registry, profile)
    # The unmapped mandatory fields can never be satisfied, so this must still refuse.
    assert not report.payload()["registrable"]
    assert report.addressable_readiness == 1.0


# --------------------------------------------------------------------- the real corpus


def test_the_golden_corpus_reports_more_schema_gaps_than_data_gaps(registry, profile):
    """The honest headline: most of the distance to a passport is not extraction work."""
    catalogue = records_from_golden(registry)
    reports = [assess_readiness(r, registry, profile) for r in catalogue.records]
    sweep = sweep_readiness(reports, source_note=catalogue.source.note, measured=False)
    summary = sweep.summary()

    assert summary["records"] == 15
    assert summary["registrable"] == 0
    assert len(summary["schema_gap_fields"]) == 9
    assert summary["status_counts"]["unmapped"] > summary["status_counts"]["absent"]


def test_the_schema_gaps_are_identical_on_every_record(registry, profile):
    """They are a property of the schema, not of any record, so they must not vary."""
    catalogue = records_from_golden(registry)
    gap_sets = {
        tuple(a.field.id for a in assess_readiness(r, registry, profile).schema_gaps())
        for r in catalogue.records
    }
    assert len(gap_sets) == 1


def test_gtin_blocks_every_record_in_the_corpus(registry, profile):
    """The corpus records gtin as absent throughout, so the passport's primary key is missing."""
    catalogue = records_from_golden(registry)
    reports = [assess_readiness(r, registry, profile) for r in catalogue.records]
    sweep = sweep_readiness(reports)

    assert sweep.blocking_fields()["product_identifier"] == 15


def test_addressable_readiness_is_higher_than_headline_readiness_on_the_corpus(
    registry, profile
):
    catalogue = records_from_golden(registry)
    reports = [assess_readiness(r, registry, profile) for r in catalogue.records]
    sweep = sweep_readiness(reports)

    assert 0 < sweep.mean_readiness < sweep.mean_addressable_readiness < 1


# --------------------------------------------------------------------- rendering


def test_the_report_separates_the_two_gap_kinds_in_prose(registry, profile, document):
    record = _record()
    record.add_value(_extracted("gtin", "00812345678901", document))
    rendered = format_readiness(assess_readiness(record, registry, profile))

    assert "DPP READINESS" in rendered
    assert "WHY THE UNMAPPED FIELDS CANNOT BE FIXED BY RE-EXTRACTION" in rendered
    assert "not a current breach" in rendered


def test_the_sweep_states_that_schema_gaps_are_not_data_gaps(registry, profile):
    catalogue = records_from_golden(registry)
    reports = [assess_readiness(r, registry, profile) for r in catalogue.records]
    rendered = format_readiness_sweep(
        sweep_readiness(reports, source_note=catalogue.source.note, measured=False)
    )

    assert "schema gaps, not data gaps" in rendered
    assert "Re-running extraction will not move them" in rendered
    assert "hand-authored" in rendered


def test_the_sweep_payload_round_trips_as_json(registry, profile):
    import json

    catalogue = records_from_golden(registry)
    reports = [assess_readiness(r, registry, profile) for r in catalogue.records]
    payload = json.loads(
        json.dumps(sweep_readiness(reports).to_dict(), default=str)
    )

    assert payload["records"] == 15
    assert payload["schema_gap_fields"]
    assert payload["reports"][0]["assessments"]
