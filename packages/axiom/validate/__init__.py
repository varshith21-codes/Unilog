"""Validation layers L0-L6.

L0 type and format, L1 dimensional consistency, L2 deterministic cross-field rules executed
from the schema, L3 statistical plausibility, L4 agreement between independent sources, L6
formal verification of prose against a logic policy. Groundedness (L5) is enforced upstream in
the extractor by discarding unverifiable quotes rather than by a validator here.

L0-L3 run on every record through :class:`Validator`. L4 runs only where a SKU has more than one
independent source, and L6 only where a reasoning policy has been deployed — both report
SKIPPED rather than PASS when their preconditions are absent, because a layer that could not run
has established nothing.
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
    INDETERMINATE_KINDS,
    ReasoningChecker,
    ReasoningConfig,
    ReasoningFinding,
    ReasoningReport,
    describe_record,
    guardrail_runtime,
    split_claims,
)
from axiom.validate.validator import ValidationReport, Validator

__all__ = [
    "GTIN_LENGTHS",
    "INDETERMINATE_KINDS",
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
    "guardrail_runtime",
    "split_claims",
    "validate_gtin",
]
