"""Tests for calibrated confidence and risk-controlled auto-accept.

The load-bearing property: the threshold is chosen against an *upper confidence bound*, not an
observed rate. A small sample must therefore force a stricter threshold and lower coverage,
rather than producing a flattering guarantee that is really an artifact of sample size.

Also asserted: hard constraints beat scores. No confidence number may publish a value with a
blocking validation failure, unverified evidence, or an inferred origin.
"""

from __future__ import annotations

import random

import pytest
from axiom.confidence import (
    Calibrator,
    ConfidenceFeatures,
    Priors,
    apply_policy,
    coverage_at_risk,
    extract_features,
    risk_coverage_curve,
    select_threshold,
    wilson_upper_bound,
)
from axiom.confidence.policy import (
    REASON_AUTO_ACCEPTED,
    REASON_BELOW_THRESHOLD,
    REASON_BLOCKING_VALIDATION,
    REASON_INFERRED,
    REASON_NO_POLICY,
    REASON_UNVERIFIED_EVIDENCE,
)
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.validation import ValidationLayer, ValidationResult
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus

SHA = "9f2c" + "0" * 60


def span(*, verified=True, score=1.0, table_ref=None, quote="600 PSI WOG") -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp",
        document_id="doc",
        document_sha256=SHA,
        quote=quote,
        page=1,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        table_ref=table_ref,
        quote_verified=verified,
        match_score=score,
    )


def value(
    code="pressure_rating_wog",
    *,
    method=DerivationMethod.DOCUMENT_EXTRACTION,
    confidence=0.9,
    evidence=None,
    validations=None,
    canonical="600 psi",
    raw="600 PSI WOG",
    tier="volume",
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=raw,
        value_canonical=canonical,
        method=method,
        confidence=confidence,
        evidence=evidence if evidence is not None else [span()],
        validations=validations or [],
        model_tier=tier,
    )


# ===================================================================== Wilson bound


def test_zero_observed_errors_does_not_mean_zero_risk():
    """The case a naive point estimate gets exactly wrong."""
    bound = wilson_upper_bound(0, 20)
    assert bound > 0.0
    assert bound < 0.20


def test_bound_tightens_as_the_sample_grows():
    """This is what makes coverage rise with volume instead of being asserted."""
    small = wilson_upper_bound(0, 20)
    medium = wilson_upper_bound(0, 200)
    large = wilson_upper_bound(0, 2000)
    assert small > medium > large


def test_bound_exceeds_the_observed_rate():
    observed = 1 / 40
    assert wilson_upper_bound(1, 40) > observed


def test_bound_on_no_data_is_total_uncertainty():
    assert wilson_upper_bound(0, 0) == 1.0


def test_all_errors_gives_a_bound_near_one():
    assert wilson_upper_bound(10, 10) > 0.7


def test_higher_confidence_gives_a_wider_bound():
    assert wilson_upper_bound(1, 100, 0.99) > wilson_upper_bound(1, 100, 0.90)


def test_invalid_error_count_is_rejected():
    with pytest.raises(ValueError, match="must lie in"):
        wilson_upper_bound(11, 10)


# ===================================================================== threshold selection


def _synthetic(n=400, seed=7):
    """Two explicit populations: reliable high scorers and unreliable low scorers.

    Deliberately not sampled from a smooth score-to-accuracy curve. These tests are about the
    threshold *mechanism*, and a smooth curve makes the outcome depend on the exact noise model
    chosen — an earlier version put ~15% error at the top scores, which made a 10% budget
    genuinely unachievable and made the tests look like mechanism failures.
    """
    rng = random.Random(seed)
    scores: list[float] = []
    labels: list[bool] = []

    reliable = n // 2
    for _ in range(reliable):
        scores.append(round(rng.uniform(0.80, 1.0), 4))
        labels.append(rng.random() < 0.99)
    for _ in range(n - reliable):
        scores.append(round(rng.uniform(0.0, 0.60), 4))
        labels.append(rng.random() < 0.55)
    return scores, labels


def test_selected_threshold_meets_the_budget():
    scores, labels = _synthetic()
    policy = select_threshold(scores, labels, epsilon=0.10)
    assert policy.achievable
    assert policy.error_upper_bound <= 0.10
    assert 0.0 < policy.coverage <= 1.0


def test_tighter_budget_yields_lower_coverage():
    """The core tradeoff the risk dial exposes."""
    scores, labels = _synthetic()
    loose = select_threshold(scores, labels, epsilon=0.20)
    strict = select_threshold(scores, labels, epsilon=0.05)
    assert loose.coverage >= strict.coverage
    assert loose.threshold <= strict.threshold


def test_threshold_maximises_coverage_within_the_budget():
    """A stricter threshold is always safe but discards automation the data supports."""
    scores, labels = _synthetic()
    policy = select_threshold(scores, labels, epsilon=0.10)
    better = [
        point
        for point in policy.curve
        if point.error_upper_bound <= 0.10
        and point.accepted >= 10
        and point.coverage > policy.coverage
    ]
    assert not better


def test_small_sample_cannot_buy_a_guarantee():
    """A flattering observed rate on 6 samples must not become a published guarantee."""
    scores = [0.99] * 6
    labels = [True] * 6
    policy = select_threshold(scores, labels, epsilon=0.02)
    assert policy.achievable is False
    assert policy.coverage == 0.0
    assert "too small" in policy.reason


def test_unachievable_budget_is_reported_honestly():
    scores = [0.9] * 100
    labels = [i % 3 != 0 for i in range(100)]  # ~33% wrong regardless of threshold
    policy = select_threshold(scores, labels, epsilon=0.01)
    assert policy.achievable is False
    assert "above the" in policy.reason
    assert policy.error_upper_bound > 0.01


def test_no_data_is_not_achievable():
    policy = select_threshold([], [], epsilon=0.02)
    assert policy.achievable is False
    assert policy.threshold > 1.0
    assert "no calibration data" in policy.reason


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError, match="same length"):
        select_threshold([0.5], [True, False])


def test_policy_summary_is_reportable():
    scores, labels = _synthetic()
    summary = select_threshold(scores, labels, epsilon=0.10).summary()
    for key in ("epsilon", "threshold", "coverage", "error_upper_bound", "achievable"):
        assert key in summary


# ===================================================================== risk-coverage curve


def test_curve_is_monotone_in_coverage():
    scores, labels = _synthetic()
    curve = risk_coverage_curve(scores, labels)
    coverages = [p.coverage for p in curve]
    assert coverages == sorted(coverages), "lower thresholds must accept more"


def test_curve_bounds_always_exceed_observed_rates():
    scores, labels = _synthetic()
    for point in risk_coverage_curve(scores, labels):
        assert point.error_upper_bound >= point.observed_error_rate


def test_curve_is_thinned_for_readability():
    scores, labels = _synthetic(n=500)
    assert len(risk_coverage_curve(scores, labels, max_points=20)) <= 22


def test_empty_curve():
    assert risk_coverage_curve([], []) == []


def test_coverage_at_risk_builds_the_operating_table():
    scores, labels = _synthetic()
    table = coverage_at_risk(scores, labels, [0.01, 0.05, 0.10, 0.20])
    coverages = [table[e].coverage for e in (0.01, 0.05, 0.10, 0.20)]
    assert coverages == sorted(coverages), "a looser budget must never reduce coverage"


# ===================================================================== features


def test_verified_evidence_scores_above_unverified():
    verified = extract_features(value())
    unverified = extract_features(value(evidence=[span(verified=False, score=0.0)]))
    assert verified.evidence_verified == 1.0
    assert unverified.evidence_verified == 0.0
    assert Calibrator().predict(value_features(verified)) > Calibrator().predict(
        value_features(unverified)
    )


def value_features(features: ConfidenceFeatures) -> ConfidenceFeatures:
    return features


def test_cell_citation_beats_row_citation():
    """A cell pins the exact value; a row still requires trusting the column was read right."""
    cell = extract_features(value(evidence=[span(table_ref="t1:r3:c0")]))
    row = extract_features(value(evidence=[span(table_ref="t1:r3")]))
    line = extract_features(value(evidence=[span()]))
    assert cell.citation_precision > row.citation_precision > line.citation_precision


def test_a_sprawling_quote_is_weaker_evidence():
    """A short value backed by a huge quote is cited but effectively unverified."""
    tight = extract_features(value(raw="600 PSI", evidence=[span(quote="600 PSI")]))
    loose = extract_features(
        value(raw="600 PSI", evidence=[span(quote="x" * 200)])
    )
    assert tight.quote_specificity > loose.quote_specificity


def test_blocking_validation_zeroes_the_clean_signal():
    failing = value(
        validations=[
            ValidationResult.failed(
                ValidationLayer.L2_DOMAIN_RULE, "R_TEST", "contradiction"
            )
        ]
    )
    assert extract_features(failing).validation_clean == 0.0


def test_skipped_checks_do_not_count_as_passes():
    from axiom.core.validation import Severity, Verdict

    skipped = ValidationResult(
        layer=ValidationLayer.L2_DOMAIN_RULE,
        rule_id="R_SKIP",
        verdict=Verdict.SKIPPED,
        severity=Severity.INFO,
        reason="missing dependency",
    )
    passed = ValidationResult.passed(ValidationLayer.L0_TYPE_FORMAT, "R_OK")
    features = extract_features(value(validations=[skipped, passed]))
    assert features.validation_pass_rate == 1.0, "only evaluated checks form the denominator"


def test_inference_is_flagged_in_the_features():
    inferred = value(method=DerivationMethod.PART_NUMBER_GRAMMAR, evidence=[])
    features = extract_features(inferred)
    assert features.is_inference == 1.0
    assert features.is_extraction == 0.0


def test_feature_vector_matches_declared_order():
    features = extract_features(value())
    assert len(features.vector()) == len(ConfidenceFeatures.FEATURE_ORDER)
    assert set(features.explain()) == set(ConfidenceFeatures.FEATURE_ORDER)


# ===================================================================== priors


def test_unmeasured_attribute_gets_a_neutral_prior():
    """An unmeasured attribute is not a trustworthy one."""
    assert Priors().attribute_prior("anything") == 0.5


def test_thin_evidence_is_shrunk_toward_neutral():
    """One correct observation is not evidence of perfect accuracy."""
    thin = Priors(attribute_accuracy={"a": 1.0}, sample_counts={"a": 1})
    thick = Priors(attribute_accuracy={"a": 1.0}, sample_counts={"a": 100})
    assert 0.5 < thin.attribute_prior("a") < thick.attribute_prior("a")
    assert thick.attribute_prior("a") == pytest.approx(1.0)


def test_observing_outcomes_moves_the_prior():
    priors = Priors()
    for _ in range(30):
        priors.observe("pressure_rating_wog", "attribute", correct=True)
    assert priors.attribute_prior("pressure_rating_wog") > 0.9


def test_source_prior_is_keyed_by_supplier_and_attribute():
    priors = Priors()
    for _ in range(30):
        priors.observe("acme:voltage", "source", correct=False)
    assert priors.source_prior("acme", "voltage") < 0.2
    assert priors.source_prior("other", "voltage") == 0.5
    assert priors.source_prior(None, "voltage") == 0.5


def test_priors_round_trip(tmp_path):
    priors = Priors()
    priors.observe("a", "attribute", correct=True)
    priors.save(tmp_path / "priors.json")
    assert Priors.load(tmp_path / "priors.json").attribute_accuracy == priors.attribute_accuracy


# ===================================================================== calibrator


def test_untrained_calibrator_is_capped():
    """A cold-start score must not reach certainty. The primary guard is the absent policy."""
    best_case = extract_features(value(confidence=1.0, evidence=[span(table_ref="t1:r3:c0")]))
    assert Calibrator().predict(best_case) <= 0.95


def test_untrained_scores_spread_enough_to_rank_the_review_queue():
    """Regression guard: an earlier heuristic pinned nearly every value to the cap.

    Ordering is the only thing that makes a review queue better than a list, so a flat score
    is worse than a slightly wrong one.
    """
    calibrator = Calibrator()
    cell = calibrator.predict(
        extract_features(
            value(raw="600 PSI", evidence=[span(table_ref="t1:r3:c0", quote="600 PSI")])
        )
    )
    row = calibrator.predict(
        extract_features(
            value(raw="600 PSI", evidence=[span(table_ref="t1:r3", quote="600 PSI")])
        )
    )
    line = calibrator.predict(
        extract_features(value(raw="600 PSI", evidence=[span(quote="600 PSI WOG @ 73 degF")]))
    )
    weak = calibrator.predict(
        extract_features(
            value(
                raw="600 PSI",
                evidence=[span(score=0.91, quote="x" * 200)],
                validations=[
                    ValidationResult.failed(ValidationLayer.L3_STATISTICAL, "R", "outlier")
                ],
            )
        )
    )

    assert cell > row > line > weak
    assert cell - weak > 0.25, "the spread must be wide enough to order a queue"


def test_untrained_calibrator_penalises_inference():
    inferred = extract_features(
        value(method=DerivationMethod.PART_NUMBER_GRAMMAR, evidence=[])
    )
    extracted = extract_features(value())
    assert Calibrator().predict(inferred) < Calibrator().predict(extracted)


def test_training_separates_good_from_bad():
    good = [extract_features(value()) for _ in range(60)]
    bad = [
        extract_features(
            value(
                evidence=[span(verified=False, score=0.0)],
                validations=[
                    ValidationResult.failed(ValidationLayer.L2_DOMAIN_RULE, "R", "bad")
                ],
                canonical=None,
            )
        )
        for _ in range(60)
    ]
    calibrator = Calibrator().fit(good + bad, [True] * 60 + [False] * 60)

    assert calibrator.is_trained
    assert calibrator.predict(good[0]) > 0.7
    assert calibrator.predict(bad[0]) < 0.3


def test_weights_are_named_for_explanation():
    good = [extract_features(value()) for _ in range(30)]
    bad = [extract_features(value(evidence=[span(verified=False, score=0.0)])) for _ in range(30)]
    calibrator = Calibrator().fit(good + bad, [True] * 30 + [False] * 30)
    assert set(calibrator.weights) == set(ConfidenceFeatures.FEATURE_ORDER)


def test_fit_rejects_mismatched_or_empty_input():
    with pytest.raises(ValueError, match="same length"):
        Calibrator().fit([extract_features(value())], [True, False])
    with pytest.raises(ValueError, match="empty sample"):
        Calibrator().fit([], [])


def test_calibration_report_measures_the_gap():
    good = [extract_features(value()) for _ in range(80)]
    bad = [extract_features(value(evidence=[span(verified=False, score=0.0)])) for _ in range(80)]
    features = good + bad
    labels = [True] * 80 + [False] * 80

    calibrator = Calibrator().fit(features, labels)
    report = calibrator.calibration_report(features, labels)

    assert report.sample_size == 160
    assert 0.0 <= report.expected_calibration_error <= 1.0
    assert report.base_rate == pytest.approx(0.5)
    assert report.bins
    assert all(0 <= b.observed_accuracy <= 1 for b in report.bins)


def test_small_calibration_sample_is_marked_unusable():
    """A calibration measured on a handful of examples is itself uncalibrated."""
    features = [extract_features(value()) for _ in range(5)]
    report = Calibrator().calibration_report(features, [True] * 5)
    assert report.is_usable is False


def test_calibrator_round_trip(tmp_path):
    good = [extract_features(value()) for _ in range(30)]
    bad = [extract_features(value(evidence=[span(verified=False, score=0.0)])) for _ in range(30)]
    calibrator = Calibrator().fit(good + bad, [True] * 30 + [False] * 30)
    calibrator.save(tmp_path / "cal.json")

    reloaded = Calibrator.load(tmp_path / "cal.json")
    assert reloaded.is_trained
    assert reloaded.predict(good[0]) == pytest.approx(calibrator.predict(good[0]))


# ===================================================================== applying the policy


def _policy(epsilon=0.10):
    scores, labels = _synthetic()
    return select_threshold(scores, labels, epsilon=epsilon)


def test_high_scoring_clean_value_is_auto_accepted():
    policy = _policy()
    good = value()
    decisions = apply_policy([good], {good.attribute_code: 1.0}, policy)
    assert decisions[0].accepted is True
    assert decisions[0].reason_code == REASON_AUTO_ACCEPTED
    assert good.status is ValueStatus.AUTO_ACCEPTED


def test_low_scoring_value_is_queued():
    policy = _policy()
    v = value()
    decisions = apply_policy([v], {v.attribute_code: 0.01}, policy)
    assert decisions[0].reason_code == REASON_BELOW_THRESHOLD
    assert v.status is ValueStatus.QUEUED_FOR_REVIEW


def test_blocking_validation_beats_a_perfect_score():
    """No confidence number may publish a value that failed a hard rule."""
    policy = _policy()
    v = value(
        validations=[
            ValidationResult.failed(
                ValidationLayer.L2_DOMAIN_RULE, "R_LEADFREE_MATERIAL", "contradiction"
            )
        ]
    )
    decisions = apply_policy([v], {v.attribute_code: 1.0}, policy)
    assert decisions[0].accepted is False
    assert decisions[0].reason_code == REASON_BLOCKING_VALIDATION
    assert "R_LEADFREE_MATERIAL" in decisions[0].detail


def test_unverified_evidence_beats_a_perfect_score():
    policy = _policy()
    v = value(evidence=[span(verified=False, score=0.0)])
    decisions = apply_policy([v], {v.attribute_code: 1.0}, policy)
    assert decisions[0].reason_code == REASON_UNVERIFIED_EVIDENCE


def test_inferred_value_beats_a_perfect_score():
    policy = _policy()
    v = value(method=DerivationMethod.PART_NUMBER_GRAMMAR, evidence=[])
    decisions = apply_policy([v], {v.attribute_code: 1.0}, policy)
    assert decisions[0].reason_code == REASON_INFERRED
    assert v.status is ValueStatus.QUEUED_FOR_REVIEW


def test_without_a_validated_policy_nothing_is_published():
    """Cold start must queue everything rather than fall open."""
    policy = select_threshold([], [], epsilon=0.02)
    v = value()
    decisions = apply_policy([v], {v.attribute_code: 1.0}, policy)
    assert decisions[0].accepted is False
    assert decisions[0].reason_code == REASON_NO_POLICY
    assert v.status is ValueStatus.QUEUED_FOR_REVIEW


def test_missing_score_is_treated_as_zero():
    policy = _policy()
    v = value()
    decisions = apply_policy([v], {}, policy)
    assert decisions[0].accepted is False


def test_reason_codes_group_the_review_queue():
    """Batching by failure mode is much faster for a reviewer than per-SKU switching."""
    policy = _policy()
    values = [
        value(code="a"),
        value(code="b", evidence=[span(verified=False, score=0.0)]),
        value(code="c", method=DerivationMethod.PART_NUMBER_GRAMMAR, evidence=[]),
    ]
    scores = {"a": 1.0, "b": 1.0, "c": 1.0}
    codes = {d.attribute_code: d.reason_code for d in apply_policy(values, scores, policy)}
    assert codes == {
        "a": REASON_AUTO_ACCEPTED,
        "b": REASON_UNVERIFIED_EVIDENCE,
        "c": REASON_INFERRED,
    }
