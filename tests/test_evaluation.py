"""Tests for the backtest harness and its metrics.

The five-outcome model is the point. A system that fabricates freely must not be able to
benchmark identically to one that abstains honestly, which is exactly what happens if
extraction is scored as binary correct/incorrect.
"""

from __future__ import annotations

import json

import pytest
from axiom.core.values import Quantity, ValueRange
from axiom.evaluation import (
    GoldenSet,
    MetricSet,
    Outcome,
    compare_value,
    format_report,
    mask,
    match_kind,
    run_backtest,
    write_calibration_artifacts,
)
from axiom.evaluation.backtest import BacktestResult
from axiom.extract import ModelCascade, StubModelClient
from axiom.schema import load_default

BALL_VALVE = "PLB.VLV.BALL.2PC"


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def golden():
    return GoldenSet.load_default()


@pytest.fixture
def cascade():
    return ModelCascade(region="us-east-2", tiers={"volume": "stub"})


def definition(registry, code: str):
    return registry.attribute(code)


# ===================================================================== golden set


def test_golden_set_loads(golden: GoldenSet):
    assert len(golden) == 9
    assert golden.comparison_count == 180
    assert golden.absent_count == 55


def test_absent_attributes_are_a_substantial_share(golden: GoldenSet):
    """Without known-absent rows, abstention cannot be scored at all."""
    assert golden.absent_count / golden.comparison_count > 0.25


def test_both_classes_are_represented(golden: GoldenSet):
    assert len(golden.by_class(BALL_VALVE)) == 5
    assert len(golden.by_class("PLB.VLV.GATE.BRZ")) == 4


def test_source_documents_resolve(golden: GoldenSet):
    for product in golden.products:
        assert golden.document_for(product).is_file()


def test_unknown_source_is_rejected(golden: GoldenSet):
    from axiom.evaluation import GoldenProduct

    stray = GoldenProduct(sku="X", class_code=BALL_VALVE, source="nonexistent")
    with pytest.raises(KeyError, match="does not declare"):
        golden.document_for(stray)


def test_ambiguous_attributes_are_scored_in_neither_direction(golden: GoldenSet):
    """Scoring a case where careful humans disagree measures the annotator, not the extractor.

    BA-100-100 sits under a footnote that contradicts its own ordering row on handle type, so
    handle_type must appear in neither `attributes` nor `absent`.
    """
    product = next(p for p in golden.products if p.sku == "BA-100-100")
    assert "handle_type" not in product.attributes
    assert "handle_type" not in product.absent
    assert "handle_type" not in product.covered_codes
    assert product.notes and "contradict" in product.notes


def test_applicability_is_scored_in_both_directions(golden: GoldenSet):
    """Torque is stated for the 1/2" size only, so one SKU expects it and the rest must not."""
    present = next(p for p in golden.products if p.sku == "BA-100-050")
    absent = next(p for p in golden.products if p.sku == "BA-100-075")
    assert "operating_torque" in present.attributes
    assert "operating_torque" in absent.absent


def test_lead_free_trap_is_encoded_both_ways(golden: GoldenSet):
    """NSF-61 does not substantiate lead-free; NSF-372 does. Both cases appear."""
    ball = next(p for p in golden.products if p.sku == "BA-100-075")
    gate = next(p for p in golden.products if p.sku == "T-113-025")
    assert "lead_free_compliant" in ball.absent
    assert gate.attributes["lead_free_compliant"] == "true"


def test_masking_hides_the_answers(golden: GoldenSet):
    """Extraction must not be able to read ground truth out of its own input."""
    product = next(p for p in golden.products if p.sku == "BA-100-075")
    masked = mask(product)
    assert set(masked) == {"sku", "class_code", "brand"}
    for value in product.attributes.values():
        assert value not in masked.values()


# ===================================================================== outcomes


def test_correct_value(registry):
    result = compare_value(
        "X", definition(registry, "body_material"), "Bronze C84400", "Bronze C84400"
    )
    assert result.outcome is Outcome.CORRECT
    assert result.match_kind == "exact"


def test_wrong_value_is_distinguished_from_a_miss(registry):
    """A wrong number is acted on; a missing one is not. They must not aggregate together."""
    wrong = compare_value(
        "X", definition(registry, "body_material"), "Bronze C84400", "Brass C36000"
    )
    missed = compare_value("X", definition(registry, "body_material"), "Bronze C84400", None)
    assert wrong.outcome is Outcome.WRONG_VALUE
    assert missed.outcome is Outcome.MISSED
    assert wrong.outcome is not missed.outcome


def test_correct_abstention_is_a_success(registry):
    result = compare_value("X", definition(registry, "gtin"), None, None)
    assert result.outcome is Outcome.CORRECTLY_ABSTAINED
    assert result.outcome.is_success


def test_hallucination_is_its_own_outcome(registry):
    result = compare_value("X", definition(registry, "gtin"), None, "012345678905")
    assert result.outcome is Outcome.HALLUCINATED
    assert not result.outcome.is_success
    assert "no value is present" in result.detail


# ===================================================================== match kinds


def test_exact_quantity_match(registry):
    assert (
        match_kind(
            definition(registry, "pressure_rating_wog"),
            Quantity(magnitude=600.0, unit="psi"),
            Quantity(magnitude=600.0, unit="psi"),
        )
        == "exact"
    )


def test_different_unit_is_not_a_match(registry):
    assert (
        match_kind(
            definition(registry, "pressure_rating_wog"),
            Quantity(magnitude=600.0, unit="psi"),
            Quantity(magnitude=600.0, unit="bar"),
        )
        is None
    )


def test_tolerance_match_is_labelled_separately(registry):
    """A tolerance match must never be reported as exact, or a loosely-toleranced attribute
    would inflate the headline accuracy figure."""
    kind = match_kind(definition(registry, "each_weight"), 1.00, 1.01)
    assert kind == "tolerance"


def test_zero_tolerance_attribute_demands_exactness(registry):
    assert definition(registry, "pressure_rating_wog").tolerance == 0.0
    assert (
        match_kind(
            definition(registry, "pressure_rating_wog"),
            Quantity(magnitude=600.0, unit="psi"),
            Quantity(magnitude=601.0, unit="psi"),
        )
        is None
    )


def test_multi_enum_order_does_not_matter(registry):
    assert (
        match_kind(definition(registry, "approvals"), ["UL", "CSA"], ["CSA", "UL"]) == "exact"
    )


def test_range_match(registry):
    a = ValueRange(minimum=-28.9, maximum=185.6, unit="degC")
    assert match_kind(definition(registry, "temperature_range"), a, a) == "exact"


def test_normalized_text_match(registry):
    kind = match_kind(definition(registry, "country_of_origin"), "United States", "united  states")
    assert kind == "normalized"


def test_booleans_do_not_tolerance_match(registry):
    """bool is an int subclass, so an unguarded numeric comparison would call True close to 1."""
    assert match_kind(definition(registry, "lead_free_compliant"), True, 1.0) is None


# ===================================================================== metric aggregation


def _metrics(outcomes: list[Outcome], registry) -> MetricSet:
    metrics = MetricSet()
    defn = definition(registry, "body_material")
    for index, outcome in enumerate(outcomes):
        expected = None if outcome in {Outcome.CORRECTLY_ABSTAINED, Outcome.HALLUCINATED} else "a"
        actual = None if outcome in {Outcome.CORRECTLY_ABSTAINED, Outcome.MISSED} else "a"
        if outcome is Outcome.WRONG_VALUE:
            actual = "b"
        if outcome is Outcome.HALLUCINATED:
            actual = "b"
        metrics.comparisons.append(
            compare_value(f"sku{index}", defn, expected, actual, confidence=0.8)
        )
    return metrics


def test_precision_counts_hallucinations_against_it(registry):
    metrics = _metrics([Outcome.CORRECT] * 8 + [Outcome.HALLUCINATED] * 2, registry)
    assert metrics.precision == pytest.approx(0.8)


def test_recall_counts_misses_against_it(registry):
    metrics = _metrics([Outcome.CORRECT] * 8 + [Outcome.MISSED] * 2, registry)
    assert metrics.recall == pytest.approx(0.8)


def test_abstention_correctness_is_reported_separately(registry):
    metrics = _metrics(
        [Outcome.CORRECT] * 5 + [Outcome.CORRECTLY_ABSTAINED] * 3 + [Outcome.HALLUCINATED],
        registry,
    )
    assert metrics.abstention_correctness == pytest.approx(0.75)


def test_a_fabricating_system_cannot_score_like_an_honest_one(registry):
    """The reason there are five outcomes rather than two."""
    honest = _metrics([Outcome.CORRECT] * 5 + [Outcome.CORRECTLY_ABSTAINED] * 5, registry)
    fabricator = _metrics([Outcome.CORRECT] * 5 + [Outcome.HALLUCINATED] * 5, registry)

    assert honest.abstention_correctness == 1.0
    assert fabricator.abstention_correctness == 0.0
    assert honest.precision > fabricator.precision
    assert fabricator.hallucination_rate == 0.5


def test_empty_metrics_do_not_divide_by_zero():
    metrics = MetricSet()
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
    assert metrics.abstention_correctness == 1.0
    assert metrics.citation_coverage == 0.0


def test_calibration_pairs_exclude_abstentions(registry):
    """Only produced values can be labelled correct or not."""
    metrics = _metrics(
        [Outcome.CORRECT] * 3 + [Outcome.CORRECTLY_ABSTAINED] * 2 + [Outcome.WRONG_VALUE],
        registry,
    )
    scores, labels = metrics.calibration_pairs()
    assert len(scores) == 4
    assert labels == [True, True, True, False]


def test_by_attribute_grouping(registry):
    metrics = _metrics([Outcome.CORRECT] * 3, registry)
    assert set(metrics.by_attribute()) == {"body_material"}


# ===================================================================== the harness


def _contract(items: list[dict]) -> str:
    return json.dumps(items)


def test_backtest_runs_offline_with_a_stub(registry, golden, cascade):
    """The harness itself must be testable without hitting a model."""
    responses = [
        _contract(
            [
                {
                    "attribute_code": "body_material",
                    "found": True,
                    "value_raw": "Bronze C84400",
                    "evidence_quote": "Bronze C84400",
                    "evidence_page": 1,
                    "certainty": "high",
                }
            ]
        )
    ]
    result = run_backtest(
        golden,
        registry,
        StubModelClient(responses),
        cascade,
        limit=1,
    )
    assert result.products_run == 1
    by_code = {c.attribute_code: c for c in result.metrics.comparisons}
    assert by_code["body_material"].outcome is Outcome.CORRECT
    # everything else was requested and not returned, so it is a miss or a correct abstention
    assert by_code["gtin"].outcome is Outcome.CORRECTLY_ABSTAINED
    assert by_code["nominal_size"].outcome is Outcome.MISSED


def test_backtest_isolates_a_failing_product(registry, golden, cascade):
    """One bad source must not end the run."""
    from axiom.extract import ModelError

    result = run_backtest(
        golden, registry, StubModelClient([ModelError("boom")] * 4), cascade, limit=1
    )
    # the extractor converts total model failure into gaps rather than raising
    assert result.products_run == 1
    assert result.metrics.total > 0


def test_backtest_amortises_document_parsing(registry, golden, cascade):
    """Five ordering rows in one datasheet is one parse, not five."""
    responses = [_contract([{"attribute_code": "body_material", "found": False}])] * 5
    result = run_backtest(golden, registry, StubModelClient(responses), cascade, limit=5)
    assert result.products_run == 5


def test_priors_are_derived_from_produced_values_only(registry):
    result = BacktestResult(golden_set="t")
    result.metrics = _metrics(
        [Outcome.CORRECT] * 3 + [Outcome.CORRECTLY_ABSTAINED] * 5, registry
    )
    priors = result.priors()
    assert priors.sample_counts["body_material"] == 3, "abstentions are not accuracy evidence"


def test_calibration_artifacts_are_written(registry, tmp_path):
    result = BacktestResult(golden_set="t")
    result.metrics = _metrics([Outcome.CORRECT] * 5 + [Outcome.WRONG_VALUE], registry)

    written = write_calibration_artifacts(result, tmp_path)
    payload = json.loads(written["calibration_set"].read_text(encoding="utf-8"))
    assert payload["sample_size"] == 6
    assert payload["labels"].count(False) == 1
    assert written["priors"].is_file()


def test_hardest_attributes_are_ranked_worst_first(registry):
    result = BacktestResult(golden_set="t")
    metrics = MetricSet()
    good = definition(registry, "body_material")
    bad = definition(registry, "cv_flow_coefficient")
    metrics.comparisons.extend(
        compare_value("s", good, "Bronze C84400", "Bronze C84400") for _ in range(5)
    )
    metrics.comparisons.extend(compare_value("s", bad, 12.0, None) for _ in range(5))
    result.metrics = metrics

    hardest = result.hardest_attributes()
    assert hardest[0][0] == "cv_flow_coefficient"
    assert hardest[0][1] == 0.0


def test_report_renders(registry):
    result = BacktestResult(golden_set="t", products_run=2)
    result.metrics = _metrics(
        [Outcome.CORRECT] * 5 + [Outcome.CORRECTLY_ABSTAINED] * 2 + [Outcome.HALLUCINATED],
        registry,
    )
    report = format_report(result)
    assert "BACKTEST" in report
    assert "hallucinated" in report
    assert "must be zero" in report
    assert "RISK-CONTROLLED AUTO-ACCEPT" in report


def test_summary_is_json_serialisable(registry):
    result = BacktestResult(golden_set="t")
    result.metrics = _metrics([Outcome.CORRECT] * 3, registry)
    json.dumps(result.summary())
