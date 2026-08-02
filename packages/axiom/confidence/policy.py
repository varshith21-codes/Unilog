"""Risk-controlled selective automation.

The mechanism that turns a confidence score into an operating decision: pick a threshold so
that the error rate among auto-published values stays inside a chosen budget, and report how
much of the catalogue that buys.

**The threshold is chosen against an upper confidence bound, not a point estimate.** This is
the whole point. If 40 held-out values clear a threshold and one is wrong, the observed error
rate is 2.5% — but with a sample that small the true rate could easily be 8%. Thresholding on
the observed rate would produce a guarantee that is an artifact of sample size, which is worse
than no guarantee because it invites reliance. A one-sided Wilson bound makes small samples
*behave* small: they force a stricter threshold and lower coverage until more data arrives.

That yields the property that matters commercially: coverage rises as review data accumulates,
so unit cost falls with volume rather than being asserted to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

from axiom.core.values import AttributeValue, ValueStatus

DEFAULT_EPSILON = 0.02
"""Default error budget on auto-published values: 2%."""

DEFAULT_CONFIDENCE = 0.95
"""Confidence level for the one-sided upper bound on the error rate."""

MIN_ACCEPTED_FOR_GUARANTEE = 10
"""Below this many accepted samples, no threshold is treated as validated.

A bound computed from a handful of points is technically valid and practically meaningless;
requiring a floor stops the policy from claiming a guarantee it cannot support.
"""


def wilson_upper_bound(errors: int, total: int, confidence: float = DEFAULT_CONFIDENCE) -> float:
    """One-sided upper bound on an error rate, by the Wilson score interval.

    Closed form, so there is no dependency on a statistics package, and it behaves sensibly at
    the boundaries where the normal approximation to a binomial does not — including the
    ``errors == 0`` case, which is exactly where a naive estimate would claim zero risk.
    """
    if total <= 0:
        return 1.0
    if errors < 0 or errors > total:
        raise ValueError(f"errors ({errors}) must lie in [0, total] ({total})")

    z = NormalDist().inv_cdf(confidence)
    proportion = errors / total
    denominator = 1.0 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z
        / denominator
        * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
    )
    return min(1.0, centre + margin)


@dataclass(frozen=True)
class RiskCoveragePoint:
    threshold: float
    coverage: float
    accepted: int
    observed_error_rate: float
    error_upper_bound: float

    def meets(self, epsilon: float) -> bool:
        return self.error_upper_bound <= epsilon


@dataclass
class RiskPolicy:
    """A threshold plus the evidence that it satisfies the error budget."""

    epsilon: float
    confidence_level: float
    threshold: float
    coverage: float
    accepted: int
    accepted_errors: int
    observed_error_rate: float
    error_upper_bound: float
    calibration_size: int
    achievable: bool
    reason: str = ""
    curve: list[RiskCoveragePoint] = field(default_factory=list)

    def decide(self, score: float) -> bool:
        """Whether a value scoring this high may be auto-published."""
        return self.achievable and score >= self.threshold

    def summary(self) -> dict[str, object]:
        return {
            "epsilon": self.epsilon,
            "confidence_level": self.confidence_level,
            "threshold": round(self.threshold, 4),
            "coverage": round(self.coverage, 4),
            "accepted": self.accepted,
            "accepted_errors": self.accepted_errors,
            "observed_error_rate": round(self.observed_error_rate, 4),
            "error_upper_bound": round(self.error_upper_bound, 4),
            "calibration_size": self.calibration_size,
            "achievable": self.achievable,
            "reason": self.reason,
        }


def risk_coverage_curve(
    scores: list[float],
    labels: list[bool],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    max_points: int = 40,
) -> list[RiskCoveragePoint]:
    """Trace coverage against risk across candidate thresholds.

    The artifact behind the risk dial: for every threshold, how much of the catalogue is
    publishable and what the worst-case error rate on that portion is.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")
    if not scores:
        return []

    total = len(scores)
    paired = sorted(zip(scores, labels, strict=True), key=lambda p: -p[0])

    candidates = sorted({round(s, 4) for s in scores}, reverse=True)
    if len(candidates) > max_points:
        # Thin evenly so the curve stays readable, always keeping the extremes.
        step = len(candidates) / max_points
        sampled = [candidates[int(i * step)] for i in range(max_points)]
        candidates = sorted({*sampled, candidates[0], candidates[-1]}, reverse=True)

    points: list[RiskCoveragePoint] = []
    for threshold in candidates:
        accepted = [label for score, label in paired if score >= threshold]
        if not accepted:
            continue
        errors = sum(1 for label in accepted if not label)
        points.append(
            RiskCoveragePoint(
                threshold=threshold,
                coverage=round(len(accepted) / total, 4),
                accepted=len(accepted),
                observed_error_rate=round(errors / len(accepted), 4),
                error_upper_bound=round(wilson_upper_bound(errors, len(accepted), confidence), 4),
            )
        )
    return points


def select_threshold(
    scores: list[float],
    labels: list[bool],
    *,
    epsilon: float = DEFAULT_EPSILON,
    confidence: float = DEFAULT_CONFIDENCE,
    min_accepted: int = MIN_ACCEPTED_FOR_GUARANTEE,
) -> RiskPolicy:
    """Choose the most permissive threshold whose error upper bound stays within budget.

    Maximising coverage subject to the bound is the correct objective: a stricter threshold is
    always safe but throws away automation the data supports.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")

    curve = risk_coverage_curve(scores, labels, confidence=confidence)

    if not scores:
        return RiskPolicy(
            epsilon=epsilon,
            confidence_level=confidence,
            threshold=1.01,
            coverage=0.0,
            accepted=0,
            accepted_errors=0,
            observed_error_rate=0.0,
            error_upper_bound=1.0,
            calibration_size=0,
            achievable=False,
            reason="no calibration data; nothing can be auto-accepted",
            curve=curve,
        )

    viable = [
        point
        for point in curve
        if point.error_upper_bound <= epsilon and point.accepted >= min_accepted
    ]

    if not viable:
        tightest = min(curve, key=lambda p: p.error_upper_bound) if curve else None
        if tightest is not None and tightest.accepted < min_accepted:
            reason = (
                f"no threshold reached {min_accepted} accepted samples while staying inside "
                f"the {epsilon:.1%} budget; the calibration set is too small to support a "
                f"guarantee at this risk level"
            )
        else:
            best = tightest.error_upper_bound if tightest else 1.0
            reason = (
                f"the tightest achievable upper bound is {best:.1%}, above the {epsilon:.1%} "
                f"budget; either widen the budget or improve extraction quality"
            )
        return RiskPolicy(
            epsilon=epsilon,
            confidence_level=confidence,
            threshold=1.01,
            coverage=0.0,
            accepted=0,
            accepted_errors=0,
            observed_error_rate=0.0,
            error_upper_bound=tightest.error_upper_bound if tightest else 1.0,
            calibration_size=len(scores),
            achievable=False,
            reason=reason,
            curve=curve,
        )

    # Most permissive means lowest threshold, which is the highest coverage.
    chosen = max(viable, key=lambda p: (p.coverage, -p.threshold))
    errors = round(chosen.observed_error_rate * chosen.accepted)
    return RiskPolicy(
        epsilon=epsilon,
        confidence_level=confidence,
        threshold=chosen.threshold,
        coverage=chosen.coverage,
        accepted=chosen.accepted,
        accepted_errors=errors,
        observed_error_rate=chosen.observed_error_rate,
        error_upper_bound=chosen.error_upper_bound,
        calibration_size=len(scores),
        achievable=True,
        reason=(
            f"at threshold {chosen.threshold:.3f}, {chosen.coverage:.1%} of values are "
            f"publishable with an error rate of at most {chosen.error_upper_bound:.1%} at "
            f"{confidence:.0%} confidence"
        ),
        curve=curve,
    )


@dataclass
class AcceptanceDecision:
    """What the policy decided about one value, and why."""

    attribute_code: str
    score: float
    accepted: bool
    status: ValueStatus
    reason_code: str
    detail: str = ""


# Reason codes drive the review queue's grouping. Batching by failure mode is dramatically
# faster for a reviewer than context-switching per SKU.
REASON_AUTO_ACCEPTED = "auto_accepted"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_BLOCKING_VALIDATION = "blocking_validation"
REASON_UNVERIFIED_EVIDENCE = "unverified_evidence"
REASON_INFERRED = "inferred_value"
REASON_NO_POLICY = "no_validated_policy"


def apply_policy(
    values: list[AttributeValue],
    scores: dict[str, float],
    policy: RiskPolicy,
) -> list[AcceptanceDecision]:
    """Set publication status on each value according to the policy.

    Hard constraints are checked before the score, because no confidence number should be able
    to override them: a blocking validation failure, unverified evidence, or an inferred value
    stays out of automatic publication regardless of how it scored.
    """
    decisions: list[AcceptanceDecision] = []

    for value in values:
        score = scores.get(value.attribute_code, 0.0)

        if value.failed_validations():
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                False,
                ValueStatus.QUEUED_FOR_REVIEW,
                REASON_BLOCKING_VALIDATION,
                "; ".join(v.rule_id for v in value.failed_validations()),
            )
        elif value.method.requires_evidence and not value.has_verified_evidence:
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                False,
                ValueStatus.QUEUED_FOR_REVIEW,
                REASON_UNVERIFIED_EVIDENCE,
                "no evidence span could be verified against the source",
            )
        elif value.method.is_inference:
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                False,
                ValueStatus.QUEUED_FOR_REVIEW,
                REASON_INFERRED,
                f"derived by {value.method.value}; inference requires human approval",
            )
        elif not policy.achievable:
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                False,
                ValueStatus.QUEUED_FOR_REVIEW,
                REASON_NO_POLICY,
                policy.reason,
            )
        elif policy.decide(score):
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                True,
                ValueStatus.AUTO_ACCEPTED,
                REASON_AUTO_ACCEPTED,
                f"score {score:.3f} >= threshold {policy.threshold:.3f}",
            )
        else:
            decision = AcceptanceDecision(
                value.attribute_code,
                score,
                False,
                ValueStatus.QUEUED_FOR_REVIEW,
                REASON_BELOW_THRESHOLD,
                f"score {score:.3f} < threshold {policy.threshold:.3f}",
            )

        value.status = decision.status
        decisions.append(decision)

    return decisions


def coverage_at_risk(
    scores: list[float],
    labels: list[bool],
    epsilons: list[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[float, RiskPolicy]:
    """Policies across several error budgets — the operating table behind the risk dial."""
    return {
        epsilon: select_threshold(scores, labels, epsilon=epsilon, confidence=confidence)
        for epsilon in epsilons
    }
