"""Masked-attribute backtesting.

Hide known-correct values, run the pipeline against the source documents alone, and measure
what comes back. Produces real accuracy numbers, the calibration set that makes risk-controlled
auto-accept possible, and per-attribute difficulty priors.
"""

from axiom.evaluation.backtest import (
    BacktestResult,
    format_report,
    run_backtest,
    write_calibration_artifacts,
)
from axiom.evaluation.cohort import (
    DIMENSIONS,
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
from axiom.evaluation.golden import (
    DEFAULT_GOLDEN_PATH,
    GoldenProduct,
    GoldenSet,
    mask,
)
from axiom.evaluation.metrics import (
    Comparison,
    MetricSet,
    Outcome,
    compare_value,
    match_kind,
)
from axiom.evaluation.regression import (
    TRACKED,
    Direction,
    GateVerdict,
    MetricComparison,
    MetricGuard,
    RegressionReport,
    check_regression,
    format_regression_report,
)

__all__ = [
    "DEFAULT_GOLDEN_PATH",
    "DIMENSIONS",
    "TRACKED",
    "Arm",
    "BacktestResult",
    "CohortMember",
    "CohortScore",
    "CohortStudy",
    "Comparison",
    "Direction",
    "GoldenProduct",
    "GoldenSet",
    "build_study",
    "format_study",
    "legacy_record",
    "load_records",
    "score",
    "MetricComparison",
    "MetricGuard",
    "MetricSet",
    "Outcome",
    "RegressionReport",
    "GateVerdict",
    "check_regression",
    "compare_value",
    "format_regression_report",
    "format_report",
    "mask",
    "match_kind",
    "run_backtest",
    "write_calibration_artifacts",
]
