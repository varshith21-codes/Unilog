"""Constrained copy generation, gated on a deterministic claim check.

Generation is a separate subsystem from extraction, and the separation is load-bearing.
Specifications are extracted and proven; prose is generated and then *checked* — every number,
standard reference, material designation and regulated claim in the output must trace back to an
already-publishable attribute, verified arithmetically or by lookup rather than by asking a second
model whether the first one behaved.
"""

from axiom.generate.claims import (
    Claim,
    ClaimKind,
    ClaimReport,
    ClaimVerdict,
    check_copy,
    check_text,
)
from axiom.generate.copy import (
    FIELDS,
    PROMPT_VERSION,
    SYSTEM,
    CopyGenerator,
    GeneratedCopy,
)
from axiom.generate.facts import Fact, FactSheet, QuantityFact, build_fact_sheet
from axiom.generate.policy import CopyPolicy, RegulatedClaim, load_policy

__all__ = [
    "FIELDS",
    "PROMPT_VERSION",
    "SYSTEM",
    "Claim",
    "ClaimKind",
    "ClaimReport",
    "ClaimVerdict",
    "CopyGenerator",
    "CopyPolicy",
    "Fact",
    "FactSheet",
    "GeneratedCopy",
    "QuantityFact",
    "RegulatedClaim",
    "build_fact_sheet",
    "check_copy",
    "check_text",
    "load_policy",
]
