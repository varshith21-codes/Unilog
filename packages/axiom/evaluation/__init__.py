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

__all__ = [
    "DEFAULT_GOLDEN_PATH",
    "BacktestResult",
    "Comparison",
    "GoldenProduct",
    "GoldenSet",
    "MetricSet",
    "Outcome",
    "compare_value",
    "format_report",
    "mask",
    "match_kind",
    "run_backtest",
    "write_calibration_artifacts",
]
