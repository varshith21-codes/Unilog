"""Validation layers L0-L3.

L0 type and format, L1 dimensional consistency, L2 deterministic cross-field rules executed
from the schema, L3 statistical plausibility. Groundedness (L5) is enforced upstream in the
extractor by discarding unverifiable quotes; cross-source (L4) and formal verification (L6)
come later.
"""

from axiom.validate.checks import (
    GTIN_LENGTHS,
    check_plausible_range,
    gtin_check_digit,
    validate_gtin,
)
from axiom.validate.constants import RuleConstants
from axiom.validate.expressions import (
    ExpressionError,
    Missing,
    SkipRule,
    compile_expression,
    desugar,
    evaluate,
)
from axiom.validate.reasoning import (
    ReasoningChecker,
    ReasoningConfig,
    ReasoningFinding,
    ReasoningReport,
    describe_record,
)
from axiom.validate.validator import ValidationReport, Validator

__all__ = [
    "GTIN_LENGTHS",
    "ExpressionError",
    "Missing",
    "ReasoningChecker",
    "ReasoningConfig",
    "ReasoningFinding",
    "ReasoningReport",
    "RuleConstants",
    "SkipRule",
    "ValidationReport",
    "Validator",
    "describe_record",
    "check_plausible_range",
    "compile_expression",
    "desugar",
    "evaluate",
    "gtin_check_digit",
    "validate_gtin",
]
