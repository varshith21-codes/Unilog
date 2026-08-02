"""Calibrated confidence and risk-controlled selective automation.

Confidence is estimated from independently checkable signals rather than a model's self-report,
calibrated against real review outcomes, and converted into a publication threshold chosen so
the error rate on auto-published values stays inside a stated budget.
"""

from axiom.confidence.calibration import (
    CalibrationReport,
    Calibrator,
    ReliabilityBin,
    sigmoid,
)
from axiom.confidence.features import (
    NEUTRAL_PRIOR,
    TIER_RANK,
    ConfidenceFeatures,
    Priors,
    extract_features,
)
from axiom.confidence.policy import (
    DEFAULT_CONFIDENCE,
    DEFAULT_EPSILON,
    MIN_ACCEPTED_FOR_GUARANTEE,
    REASON_AUTO_ACCEPTED,
    REASON_BELOW_THRESHOLD,
    REASON_BLOCKING_VALIDATION,
    REASON_INFERRED,
    REASON_NO_POLICY,
    REASON_UNVERIFIED_EVIDENCE,
    AcceptanceDecision,
    RiskCoveragePoint,
    RiskPolicy,
    apply_policy,
    coverage_at_risk,
    risk_coverage_curve,
    select_threshold,
    wilson_upper_bound,
)

__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_EPSILON",
    "MIN_ACCEPTED_FOR_GUARANTEE",
    "NEUTRAL_PRIOR",
    "REASON_AUTO_ACCEPTED",
    "REASON_BELOW_THRESHOLD",
    "REASON_BLOCKING_VALIDATION",
    "REASON_INFERRED",
    "REASON_NO_POLICY",
    "REASON_UNVERIFIED_EVIDENCE",
    "TIER_RANK",
    "AcceptanceDecision",
    "CalibrationReport",
    "Calibrator",
    "ConfidenceFeatures",
    "Priors",
    "ReliabilityBin",
    "RiskCoveragePoint",
    "RiskPolicy",
    "apply_policy",
    "coverage_at_risk",
    "extract_features",
    "risk_coverage_curve",
    "select_threshold",
    "sigmoid",
    "wilson_upper_bound",
]
