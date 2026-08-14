"""Spec drift (Tier 3, item 24).

The tests worth having here are the ones about *direction*, because direction is the only thing
drift adds over a diff. A test asserting "the value changed" would pass on an implementation that
got tightened and relaxed backwards, which is the single most consequential mistake this module
can make: it would route the overclaiming records to a refresh queue and the harmless ones to an
incident.

The corpus test at the bottom checks the module against the two committed golden sets, so the
seven authored changes in `pvf_valves_revd.yaml` are asserted against an expectation written down
before the code ran rather than against the code's own output.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core.evidence import DocumentType, SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.drift import (
    Consequence,
    DriftKind,
    RevisionPairError,
    detect_drift,
    detect_drift_across,
    format_drift,
    format_drift_sweep,
    golden_revision_pair,
    sweep_drift,
)
from axiom.schema import load_default

REV_C = "Rev C 2024-08"
REV_D = "Rev D 2026-02"


@pytest.fixture(scope="module")
def registry():
    return load_default()


def _document(document_id: str, *, revision: str | None, sha: str = "a") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        uri=f"local://{document_id}",
        sha256=(sha * 64)[:64],
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        revision_label=revision,
        supplier_id="milwaukee",
    )


def _record(sku: str, values: dict[str, object]) -> ProductRecord:
    """A record whose values are publishable without needing evidence spans.

    HUMAN_APPROVED is the honest marking: a test author really is the source. Marking them as
    extractions would require fabricating citations, and the model would refuse anyway.
    """
    record = ProductRecord(tenant_id="t", sku=sku, class_code="PLB.VLV.BALL.2PC")
    for code, canonical in values.items():
        record.add_value(
            AttributeValue(
                attribute_code=code,
                value_canonical=canonical,
                value_display=str(canonical),
                method=DerivationMethod.HUMAN_ENTRY,
                confidence=1.0,
                status=ValueStatus.HUMAN_APPROVED,
            )
        )
    return record


def _drift(registry, before: dict, after: dict, *, before_rev=REV_C, after_rev=REV_D):
    return detect_drift(
        "BA-100-075",
        _record("BA-100-075", before),
        _document("old", revision=before_rev, sha="a"),
        _record("BA-100-075", after),
        _document("new", revision=after_rev, sha="b"),
        registry,
    )


def _find(report, code: str):
    return next(d for d in report.drifts if d.attribute_code == code)


# --------------------------------------------------------------------- direction: at_least


def test_a_rating_that_fell_is_tightened_not_merely_changed(registry):
    """The case with liability attached: 600 psi published, 400 psi now supported."""
    report = _drift(
        registry,
        {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")},
        {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")},
    )
    drift = _find(report, "pressure_rating_wog")

    assert drift.kind is DriftKind.TIGHTENED
    assert drift.consequence is Consequence.WITHDRAW
    assert drift.consequence.is_urgent
    assert drift.was_published


def test_a_rating_that_rose_is_relaxed_and_never_urgent(registry):
    """Same attribute, same magnitude of change, opposite direction and opposite urgency."""
    report = _drift(
        registry,
        {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")},
        {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")},
    )
    drift = _find(report, "pressure_rating_wog")

    assert drift.kind is DriftKind.RELAXED
    assert drift.consequence is Consequence.REFRESH
    assert not drift.consequence.is_urgent


def test_tightened_and_relaxed_are_not_symmetric_in_consequence(registry):
    """Guards the inversion that would matter most: the two directions must not collapse."""
    down = _find(
        _drift(
            registry,
            {"cv_flow_coefficient": 20.0},
            {"cv_flow_coefficient": 16.5},
        ),
        "cv_flow_coefficient",
    )
    up = _find(
        _drift(
            registry,
            {"cv_flow_coefficient": 16.5},
            {"cv_flow_coefficient": 20.0},
        ),
        "cv_flow_coefficient",
    )

    assert (down.kind, up.kind) == (DriftKind.TIGHTENED, DriftKind.RELAXED)
    assert down.consequence.is_urgent and not up.consequence.is_urgent


# --------------------------------------------------------------------- direction: at_most


def test_at_most_inverts_the_direction(registry):
    """prop65_warning_required is the one attribute where more is worse."""
    report = _drift(
        registry,
        {"prop65_warning_required": False},
        {"prop65_warning_required": True},
    )
    drift = _find(report, "prop65_warning_required")

    assert drift.kind is DriftKind.TIGHTENED
    assert drift.compliance_claim
    assert drift.consequence is Consequence.WITHDRAW


# --------------------------------------------------------------------- direction: encloses


def test_a_narrower_service_window_is_tightened(registry):
    """Giving up 20 degrees at the bottom is a tightening even though the top is unchanged."""
    report = _drift(
        registry,
        {"temperature_range": ValueRange(minimum=-29.0, maximum=185.0, unit="degC")},
        {"temperature_range": ValueRange(minimum=-18.0, maximum=185.0, unit="degC")},
    )
    assert _find(report, "temperature_range").kind is DriftKind.TIGHTENED


def test_a_wider_service_window_is_relaxed(registry):
    report = _drift(
        registry,
        {"temperature_range": ValueRange(minimum=-18.0, maximum=185.0, unit="degC")},
        {"temperature_range": ValueRange(minimum=-29.0, maximum=185.0, unit="degC")},
    )
    assert _find(report, "temperature_range").kind is DriftKind.RELAXED


def test_a_window_that_moved_is_revised_rather_than_ranked(registry):
    """Gained at one end, gave up at the other. Calling it either name would hide half of it."""
    report = _drift(
        registry,
        {"temperature_range": ValueRange(minimum=-29.0, maximum=185.0, unit="degC")},
        {"temperature_range": ValueRange(minimum=-18.0, maximum=200.0, unit="degC")},
    )
    drift = _find(report, "temperature_range")

    assert drift.kind is DriftKind.REVISED
    assert drift.consequence is Consequence.REVIEW


# --------------------------------------------------------------------- direction: superset


def test_losing_a_listing_is_tightened(registry):
    report = _drift(
        registry,
        {"approvals": ["UL", "CSA", "NSF-61"]},
        {"approvals": ["UL", "CSA"]},
    )
    drift = _find(report, "approvals")

    assert drift.kind is DriftKind.TIGHTENED
    assert drift.consequence is Consequence.WITHDRAW


def test_gaining_a_listing_is_relaxed(registry):
    report = _drift(
        registry,
        {"approvals": ["UL", "CSA"]},
        {"approvals": ["UL", "CSA", "NSF-372"]},
    )
    assert _find(report, "approvals").kind is DriftKind.RELAXED


def test_a_swapped_listing_is_revised_because_neither_set_contains_the_other(registry):
    report = _drift(
        registry,
        {"approvals": ["UL", "NSF-61"]},
        {"approvals": ["UL", "FM"]},
    )
    assert _find(report, "approvals").kind is DriftKind.REVISED


# --------------------------------------------------------------------- no declared direction


def test_an_unordered_enum_change_is_revised_not_ranked(registry):
    """seat_material declares no direction; ranking two polymers is a materials judgement."""
    report = _drift(
        registry,
        {"seat_material": "RPTFE"},
        {"seat_material": "PTFE"},
    )
    drift = _find(report, "seat_material")

    assert drift.kind is DriftKind.REVISED
    assert drift.consequence is Consequence.REVIEW


def test_a_cosmetic_change_does_not_demand_a_human(registry):
    """handle_type is interchange: cosmetic — a real change, but not one that blocks anything."""
    report = _drift(
        registry,
        {"handle_type": "Lever"},
        {"handle_type": "Tee"},
    )
    drift = _find(report, "handle_type")

    assert drift.kind is DriftKind.REVISED
    assert drift.consequence is Consequence.REFRESH
    assert not drift.consequence.is_urgent


# --------------------------------------------------------------------- appearance/disappearance


def test_a_value_the_new_revision_stops_stating_is_withdrawn_not_unchanged(registry):
    """The case a value-comparing differ misses entirely: it lost its source, not its truth."""
    report = _drift(
        registry,
        {"cv_flow_coefficient": 16.5},
        {},
    )
    drift = _find(report, "cv_flow_coefficient")

    assert drift.kind is DriftKind.WITHDRAWN
    assert drift.consequence is Consequence.RE_VERIFY
    assert drift.consequence.is_urgent
    assert "lost its source" in drift.reason or "no longer states" in drift.reason


def test_a_withdrawn_compliance_claim_comes_down_immediately(registry):
    """A specification that lost its citation can wait for a re-extraction. A legal claim cannot."""
    report = _drift(
        registry,
        {"potable_water_approved": True},
        {},
    )
    drift = _find(report, "potable_water_approved")

    assert drift.kind is DriftKind.WITHDRAWN
    assert drift.consequence is Consequence.WITHDRAW
    assert drift.compliance_claim


def test_a_newly_stated_value_is_added_and_not_urgent(registry):
    report = _drift(
        registry,
        {},
        {"cv_flow_coefficient": 16.5},
    )
    drift = _find(report, "cv_flow_coefficient")

    assert drift.kind is DriftKind.ADDED
    assert drift.consequence is Consequence.REFRESH
    assert not drift.was_published


def test_an_unchanged_value_costs_nothing(registry):
    report = _drift(
        registry,
        {"body_material": "Bronze C84400"},
        {"body_material": "Bronze C84400"},
    )
    drift = _find(report, "body_material")

    assert drift.kind is DriftKind.UNCHANGED
    assert drift.consequence is Consequence.NONE
    assert not report.has_drift


# --------------------------------------------------------------------- ordering


def test_an_unorderable_pair_claims_no_direction_at_all(registry):
    """The honest refusal. A confident wrong direction is worse than no direction."""
    report = _drift(
        registry,
        {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")},
        {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")},
        after_rev=None,
    )

    assert not report.ordered
    assert "cannot be placed in order" in report.order_note
    drift = _find(report, "pressure_rating_wog")
    assert drift.kind is DriftKind.REVISED
    assert drift.consequence is Consequence.REVIEW


def test_identical_revision_markers_cannot_establish_precedence(registry):
    report = _drift(
        registry,
        {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")},
        {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")},
        after_rev=REV_C,
    )

    assert not report.ordered
    assert _find(report, "pressure_rating_wog").kind is DriftKind.REVISED


def test_arguments_passed_in_the_wrong_order_are_corrected_not_inverted(registry):
    """Handing the newer document in as `before` must not report every direction backwards."""
    report = detect_drift(
        "BA-100-075",
        _record("BA-100-075", {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")}),
        _document("new", revision=REV_D, sha="b"),
        _record("BA-100-075", {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")}),
        _document("old", revision=REV_C, sha="a"),
        registry,
    )

    assert report.ordered
    assert report.before_revision == REV_C
    assert report.after_revision == REV_D
    # 600 -> 400 after correction, so still a tightening.
    assert _find(report, "pressure_rating_wog").kind is DriftKind.TIGHTENED


def test_the_same_bytes_produce_no_drift(registry):
    report = detect_drift(
        "BA-100-075",
        _record("BA-100-075", {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")}),
        _document("doc", revision=REV_C, sha="c"),
        _record("BA-100-075", {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")}),
        _document("doc", revision=REV_C, sha="c"),
        registry,
    )

    assert report.identical_source
    assert report.drifts == []
    assert not report.has_drift


# --------------------------------------------------------------------- unpublished values


def test_a_value_that_never_published_is_not_urgent_however_far_it_moved(registry):
    """Nobody saw it, so nothing has to come down. Still reported, never escalated."""
    record = ProductRecord(tenant_id="t", sku="X", class_code="PLB.VLV.BALL.2PC")
    record.add_value(
        AttributeValue(
            attribute_code="pressure_rating_wog",
            value_canonical=Quantity(magnitude=600.0, unit="psi"),
            method=DerivationMethod.HUMAN_ENTRY,
            confidence=0.4,
            status=ValueStatus.QUEUED_FOR_REVIEW,
        )
    )

    report = detect_drift(
        "X",
        record,
        _document("old", revision=REV_C, sha="a"),
        _record("X", {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")}),
        _document("new", revision=REV_D, sha="b"),
        registry,
    )
    drift = _find(report, "pressure_rating_wog")

    assert drift.kind is DriftKind.TIGHTENED
    assert not drift.was_published
    assert report.urgent() == []


# --------------------------------------------------------------------- unknown attributes


def test_an_attribute_the_schema_does_not_define_is_reported_not_ignored(registry):
    """Same contract as an unclassified interchange level: surfaced rather than silently dropped."""
    report = _drift(
        registry,
        {"body_material": "Bronze C84400"},
        {"body_material": "Bronze C84400"},
    )
    assert report.unclassified == []

    record_before = _record("Y", {})
    record_before.attribute_values.append(
        AttributeValue(
            attribute_code="not_a_real_attribute",
            value_canonical="one",
            method=DerivationMethod.HUMAN_ENTRY,
            confidence=1.0,
            status=ValueStatus.HUMAN_APPROVED,
        )
    )
    report = detect_drift(
        "Y",
        record_before,
        _document("old", revision=REV_C, sha="a"),
        _record("Y", {"not_a_real_attribute": "two"}),
        _document("new", revision=REV_D, sha="b"),
        registry,
    )

    assert "not_a_real_attribute" in report.unclassified
    assert _find(report, "not_a_real_attribute").kind is DriftKind.REVISED


# --------------------------------------------------------------------- lifecycle vs drift


def test_a_sku_in_only_one_revision_is_not_reported_as_drift(registry):
    """Discontinuation is a lifecycle event. Filing it as an attribute change would bury it."""
    before = {
        "A": _record("A", {"body_material": "Bronze C84400"}),
        "GONE": _record("GONE", {"body_material": "Bronze C84400"}),
    }
    after = {
        "A": _record("A", {"body_material": "Bronze C84400"}),
        "NEW": _record("NEW", {"body_material": "Bronze C84400"}),
    }

    reports = detect_drift_across(
        before,
        _document("old", revision=REV_C, sha="a"),
        after,
        _document("new", revision=REV_D, sha="b"),
        registry,
    )

    assert [r.sku for r in reports] == ["A"]


# --------------------------------------------------------------------- the committed corpus


def test_the_committed_revision_pair_reproduces_its_authored_changes(registry):
    """The seven changes described in `pvf_valves_revd.yaml` are asserted, not merely counted."""
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)

    assert pair.common_skus() == [
        "BA-100-025",
        "BA-100-050",
        "BA-100-075",
        "BA-100-100",
        "BA-100-125",
    ]
    assert not pair.measured, "golden records must never claim to be measured extraction"
    assert pair.before_document.revision_label == REV_C
    assert pair.after_document.revision_label == REV_D

    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    assert len(reports) == 5

    expected = {
        "pressure_rating_wog": DriftKind.TIGHTENED,
        "steam_pressure_rating": DriftKind.RELAXED,
        "temperature_range": DriftKind.TIGHTENED,
        "seat_material": DriftKind.REVISED,
        "approvals": DriftKind.TIGHTENED,
        "potable_water_approved": DriftKind.TIGHTENED,
        "body_material": DriftKind.UNCHANGED,
        "end_connection": DriftKind.UNCHANGED,
    }

    for report in reports:
        assert report.ordered, f"{report.sku} could not be ordered by revision"
        for code, kind in expected.items():
            assert _find(report, code).kind is kind, (
                f"{report.sku}.{code}: expected {kind.value}, "
                f"got {_find(report, code).kind.value}"
            )


def test_the_cv_is_the_only_attribute_that_gained_a_value(registry):
    """Rev D qualifies the Cv to the 1/2" size, so exactly one SKU may report ADDED."""
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )

    added = {
        (r.sku, d.attribute_code)
        for r in reports
        for d in r.by_kind(DriftKind.ADDED)
    }
    assert added == {("BA-100-050", "cv_flow_coefficient")}


def test_every_sku_in_the_family_is_affected_and_the_compliance_claims_are_caught(registry):
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    sweep = sweep_drift(reports, source_note=pair.source_note, measured=pair.measured)
    summary = sweep.summary()

    assert summary["skus_compared"] == 5
    assert summary["skus_affected"] == 5
    assert summary["skus_urgent"] == 5
    assert summary["measured"] is False

    # Two compliance claims per SKU: the approvals list and the potable-water flag it substantiated.
    assert summary["compliance_impacts"] == 10
    assert {d.attribute_code for d in sweep.compliance_impacts()} == {
        "approvals",
        "potable_water_approved",
    }


def test_withdrawing_nsf61_takes_the_potable_water_claim_with_it(registry):
    """The cascade worth demonstrating: one line off a datasheet, two compliance claims down."""
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    report = next(r for r in reports if r.sku == "BA-100-075")

    approvals = _find(report, "approvals")
    potable = _find(report, "potable_water_approved")

    assert approvals.kind is DriftKind.TIGHTENED
    assert potable.kind is DriftKind.TIGHTENED
    assert approvals.consequence is Consequence.WITHDRAW
    assert potable.consequence is Consequence.WITHDRAW
    assert report.requires_republication


# --------------------------------------------------------------------- corpus guards


def test_a_multi_document_newer_side_is_refused(registry):
    """The newer side stands for one reissued datasheet; a corpus is not a revision."""
    with pytest.raises(RevisionPairError, match="exactly one"):
        golden_revision_pair("pvf_valves_revd.yaml", "pvf_valves.yaml", registry)


def test_the_before_side_is_narrowed_to_the_reissued_document(registry):
    """The corpus holds 15 SKUs across three manufacturers; only the BA-100 family may appear.

    Without the narrowing, the ten SKUs the reissued sheet never covered would each be reported
    as wholly withdrawn — three untouched manufacturers turned into spurious findings.
    """
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)

    assert len(pair.before) == 5
    assert all(sku.startswith("BA-100") for sku in pair.before)
    assert pair.only_before() == []
    assert pair.only_after() == []


# --------------------------------------------------------------------- rendering


def test_the_report_names_the_direction_and_flags_the_urgent_rows(registry):
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    rendered = format_drift(next(r for r in reports if r.sku == "BA-100-075"))

    assert "SPEC DRIFT" in rendered
    assert "TIGHTENED" in rendered
    assert "600 psi -> 400 psi" in rendered
    assert "COMPLIANCE CLAIMS AFFECTED" in rendered
    assert REV_C in rendered and REV_D in rendered


def test_the_sweep_states_that_golden_records_are_not_measured(registry):
    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    rendered = format_drift_sweep(
        sweep_drift(reports, source_note=pair.source_note, measured=pair.measured)
    )

    assert "hand-authored" in rendered
    assert "5 SKUs compared" in rendered


def test_an_unordered_report_says_so_prominently(registry):
    report = _drift(
        registry,
        {"pressure_rating_wog": Quantity(magnitude=600.0, unit="psi")},
        {"pressure_rating_wog": Quantity(magnitude=400.0, unit="psi")},
        after_rev=None,
    )
    assert "REVISION ORDER NOT ESTABLISHED" in format_drift(report)


def test_the_payload_round_trips_as_json(registry):
    import json

    pair = golden_revision_pair("pvf_valves.yaml", "pvf_valves_revd.yaml", registry)
    reports = detect_drift_across(
        pair.before, pair.before_document, pair.after, pair.after_document, registry
    )
    sweep = sweep_drift(reports, source_note=pair.source_note, measured=pair.measured)

    payload = json.loads(json.dumps(sweep.to_dict(), default=str))
    assert payload["skus_affected"] == 5
    assert payload["by_kind"]["tightened"] > 0
    assert payload["reports"][0]["drifts"]
