"""Tests for the before/after cohort study.

This is the one measurement in the repository whose output is a *sales* claim, which makes it the
one most worth attacking. So what these tests guard is not that the arithmetic works — it is that
the comparison cannot be rigged:

* both arms must pass through the same scorer and the same required-attribute set,
* an item master row must not be credited with verifiability it did not earn,
* and a SKU that was never enriched must not be counted as a zero-improvement treatment, because
  padding the denominator with untouched rows moves the mean in whichever direction is convenient.
"""

from __future__ import annotations

import pytest
from axiom.core.certificate import QualityIndex
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.evaluation import (
    Arm,
    CohortMember,
    CohortScore,
    CohortStudy,
    build_study,
    format_study,
    legacy_record,
    load_records,
    score,
)
from axiom.schema import load_default

BALL_VALVE = "PLB.VLV.BALL.2PC"
GATE_VALVE = "PLB.VLV.GATE.BRZ"
SHA = "9f2c" + "0" * 60

# What an ERP row actually looks like: abbreviations, no units, no source.
ERP_ROW = {
    "nominal_size": '3/4"',
    "body_material": "Bronze C84400",
    "pressure_rating_wog": "600",
    "case_quantity": "12",
    "selling_uom": "EA",
    "each_weight": "0.75",
}


@pytest.fixture(scope="module")
def registry():
    return load_default()


def enriched_value(code: str, raw: str, canonical=None) -> AttributeValue:
    """A value as the pipeline produces it: cited, verified, accepted."""
    return AttributeValue(
        attribute_code=code,
        value_raw=raw,
        value_canonical=canonical if canonical is not None else raw,
        value_display=raw,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.95,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[
            EvidenceSpan(
                span_id=f"sp-{code}",
                document_id="ba100",
                document_sha256=SHA,
                quote=raw,
                page=1,
                bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
                quote_verified=True,
                match_score=1.0,
            )
        ],
    )


def enriched_record(sku: str, codes: dict[str, str], class_code: str = BALL_VALVE):
    record = ProductRecord(
        tenant_id="demo", sku=sku, mpn=sku, class_code=class_code, schema_version="v1"
    )
    for code, raw in codes.items():
        record.add_value(enriched_value(code, raw))
    return record


# ===================================================================== the before state


def test_a_legacy_value_is_present_but_not_publishable(registry):
    """The thesis, expressed as a scoring rule. A value nobody can source is a gap wearing a
    value's clothing, however solid it looks in the item master."""
    record = legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)

    assert len(record.current_values()) == len(ERP_ROW)
    assert record.publishable_values() == []


def test_a_legacy_value_earns_no_verifiability(registry):
    """The trap this had to avoid. `verifiability()` credits human-entry values on the grounds
    that a named person is an accountable source — and the defining property of a legacy row is
    that nobody is named. Scoring it as a human entry would report an untraceable catalogue as
    fully verified."""
    record = legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)

    assert record.verifiability() == 0.0


def test_field_presence_and_completeness_are_different_numbers(registry):
    """Reporting only completeness would let "unsourced" read as "empty", which is a claim a
    distributor would rightly reject about their own data."""
    record = legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)
    result = score(record, registry, class_code=BALL_VALVE)

    assert result.field_presence > 0.0, "the fields do hold values"
    assert result.quality.completeness == 0.0, "none of them is publishable"


def test_legacy_values_are_normalised_through_the_same_engine(registry):
    """Half of what enrichment delivers is canonical units. If the before-state were left
    unnormalised, that half would be credited to extraction instead."""
    record = legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)
    size = record.get("nominal_size")

    assert size is not None
    assert size.value_canonical is not None, "3/4\" should have canonicalised"


def test_an_unparseable_row_is_still_recorded(registry):
    """Dropping the rows that will not parse would raise the before-state's consistency by
    discarding its worst data, which is precisely the data worth measuring."""
    record = legacy_record(
        "X-1", {"pressure_rating_wog": "see catalog", "nominal_size": "??"}, registry,
        class_code=BALL_VALVE,
    )

    assert len(record.current_values()) == 2


def test_an_unknown_attribute_code_is_skipped(registry):
    record = legacy_record(
        "X-1", {"nominal_size": '3/4"', "not_an_attribute": "x"}, registry,
        class_code=BALL_VALVE,
    )

    assert {v.attribute_code for v in record.current_values()} == {"nominal_size"}


# ===================================================================== scoring parity


def test_both_arms_are_scored_against_the_same_required_set(registry):
    """The methodological flaw this guards against is subtle and real: the item master's category
    column is unreliable, so if the before-state were scored against the class *it* claims and the
    after-state against the class the pipeline determined, the two arms would be measured against
    different denominators — on exactly the SKUs whose category was wrong.
    """
    # A gate valve that the item master mis-filed as a ball valve.
    before = legacy_record("T-113-100", ERP_ROW, registry, class_code=BALL_VALVE)
    after = enriched_record("T-113-100", {"nominal_size": '1"'}, class_code=GATE_VALVE)

    study = build_study(before={"T-113-100": before}, after={"T-113-100": after}, registry=registry)
    member = study.members[0]

    assert member.before.required_total == member.after.required_total
    assert member.before.required_total == len(registry.required_codes(GATE_VALVE))


def test_consistency_is_computed_after_validation_runs(registry):
    """Scoring before validating would report every arm as perfectly consistent, including an
    item master whose case weight contradicts its each-weight."""
    record = legacy_record(
        "X-1",
        {"each_weight": "1.0", "case_quantity": "12", "case_weight": "2.0"},
        registry,
        class_code=BALL_VALVE,
    )
    scored = score(record, registry, class_code=BALL_VALVE)

    assert scored.checks_run > 0, "validation actually ran"


def test_failing_rules_are_named_not_just_counted(registry):
    """"Consistency fell 7 points" is a worrying number. A rule id is a work item."""
    record = legacy_record(
        "X-1",
        {"each_weight": "1.0", "case_quantity": "12", "case_weight": "999"},
        registry,
        class_code=BALL_VALVE,
    )
    scored = score(record, registry, class_code=BALL_VALVE)

    if scored.validation_failures:
        assert scored.failed_rules, "a failure count with no rule id is not actionable"


# ===================================================================== the study


def test_enrichment_shows_up_as_lift(registry):
    before = {"BA-100-075": legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)}
    after = {
        "BA-100-075": enriched_record(
            "BA-100-075",
            {code: str(raw) for code, raw in ERP_ROW.items()},
        )
    }
    study = build_study(before=before, after=after, registry=registry)

    assert study.lift("verifiability") == pytest.approx(1.0)
    assert study.lift("completeness") > 0
    assert study.lift("composite") > 0


def test_an_unenriched_sku_is_excluded_not_counted_as_zero(registry):
    """Padding the treatment arm with untouched rows would drag the mean toward zero and make
    every improvement look smaller. A run that never happened is not a null result."""
    before = {
        "BA-100-075": legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE),
        "BA-100-100": legacy_record("BA-100-100", ERP_ROW, registry, class_code=BALL_VALVE),
    }
    after = {"BA-100-075": enriched_record("BA-100-075", {"nominal_size": '3/4"'})}
    study = build_study(before=before, after=after, registry=registry)

    assert [m.sku for m in study.treatment] == ["BA-100-075"]
    assert any("not a null result" in note for note in study.notes)


def test_the_excluded_note_is_aggregated_not_one_line_per_sku(registry):
    """On a real catalogue the unenriched set is the large majority, and a line each would bury
    every other finding in the report."""
    before = {
        f"SKU-{i}": legacy_record(f"SKU-{i}", ERP_ROW, registry, class_code=BALL_VALVE)
        for i in range(30)
    }
    after = {"SKU-0": enriched_record("SKU-0", {"nominal_size": '3/4"'})}
    study = build_study(before=before, after=after, registry=registry)

    assert len(study.notes) == 1
    assert "29 item-master row(s)" in study.notes[0]
    assert "and 23 more" in study.notes[0], "the SKU list is abbreviated"


def test_an_enriched_sku_with_no_before_state_is_excluded(registry):
    """There is nothing to improve on, so including it would invent a baseline."""
    before = {"BA-100-075": legacy_record("BA-100-075", ERP_ROW, registry, class_code=BALL_VALVE)}
    after = {
        "BA-100-075": enriched_record("BA-100-075", {"nominal_size": '3/4"'}),
        "NEW-SKU": enriched_record("NEW-SKU", {"nominal_size": '1"'}),
    }
    study = build_study(before=before, after=after, registry=registry)

    assert [m.sku for m in study.members] == ["BA-100-075"]
    assert any("no before-state" in note for note in study.notes)


# ===================================================================== the control arm


def test_a_control_sku_is_scored_from_its_before_state_twice(registry):
    """That is what makes the drift guard meaningful: identical input through the same scorer
    twice must produce an identical number, so any difference is the scorer's."""
    before = {
        "A": legacy_record("A", ERP_ROW, registry, class_code=BALL_VALVE),
        "B": legacy_record("B", ERP_ROW, registry, class_code=BALL_VALVE),
    }
    after = {
        "A": enriched_record("A", {"nominal_size": '3/4"'}),
        "B": enriched_record("B", {"nominal_size": '1"'}),
    }
    study = build_study(before=before, after=after, registry=registry, control_skus=["B"])

    control = study.control[0]
    assert control.sku == "B"
    assert control.deltas()["composite"] == 0.0
    assert study.control_drift("composite") == 0.0
    assert study.trustworthy is True


def test_a_drifted_control_invalidates_the_study():
    """The guard's actual job. If an untouched SKU appears to have moved, the measurement changed
    between the two scorings — someone edited the required set, the weights or a rule — and the
    treatment deltas cannot be attributed to enrichment.
    """
    def scored(composite_driver: float) -> CohortScore:
        return CohortScore(
            quality=QualityIndex(
                completeness=composite_driver,
                verifiability=0.0,
                consistency=1.0,
                richness=0.0,
            ),
            field_presence=0.5,
            values_present=5,
            values_publishable=0,
            values_with_evidence=0,
            validation_failures=0,
            required_total=10,
        )

    study = CohortStudy(
        members=[
            CohortMember("A", Arm.TREATMENT, scored(0.0), scored(0.8)),
            # The control moved, which it cannot legitimately do.
            CohortMember("B", Arm.CONTROL, scored(0.2), scored(0.5)),
        ]
    )

    assert "completeness" in study.drifted
    assert study.trustworthy is False
    assert "NOT VALID" in format_study(study)


def test_a_study_with_no_control_reports_but_does_not_vouch(registry):
    """Without a holdout there is nothing separating "the pipeline improved the data" from
    "somebody adjusted the index weights"."""
    before = {"A": legacy_record("A", ERP_ROW, registry, class_code=BALL_VALVE)}
    after = {"A": enriched_record("A", {"nominal_size": '3/4"'})}
    study = build_study(before=before, after=after, registry=registry)

    assert study.control == []
    assert study.trustworthy is False
    assert "NO CONTROL ARM" in format_study(study)


# ===================================================================== loading rows


def test_rows_are_joined_on_the_manufacturer_part_number_by_default(registry):
    """An item master's own sku column is the distributor's internal id; the pipeline is driven by
    the manufacturer part number. Joining on the wrong one produces an empty cohort."""
    rows = [
        {"sku": "MIL-BA100-075", "mpn": "BA-100-075", "attributes": dict(ERP_ROW)},
    ]
    by_mpn = load_records(rows, registry, class_code=BALL_VALVE)
    by_sku = load_records(rows, registry, class_code=BALL_VALVE, key="sku")

    assert list(by_mpn) == ["BA-100-075"]
    assert list(by_sku) == ["MIL-BA100-075"]


def test_a_duplicate_row_keeps_the_first_rather_than_merging(registry):
    """A duplicate SKU under a variant spelling is a defect in the source. Merging them would
    repair the before-state for free and understate what enrichment fixed."""
    rows = [
        {"mpn": "BA-100-075", "attributes": {"nominal_size": '3/4"'}},
        {"mpn": "BA-100-075", "attributes": {"nominal_size": "WRONG"}},
    ]
    records = load_records(rows, registry, class_code=BALL_VALVE)

    assert len(records) == 1
    assert records["BA-100-075"].get("nominal_size").value_raw == '3/4"'


def test_a_row_with_no_identifier_is_skipped(registry):
    records = load_records(
        [{"attributes": dict(ERP_ROW)}], registry, class_code=BALL_VALVE
    )
    assert records == {}
