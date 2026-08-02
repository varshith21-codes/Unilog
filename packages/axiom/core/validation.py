"""Validation layers L0–L6 and their results.

Each layer catches a different class of error and produces a human-readable reason.
Layers are ordered cheapest-and-most-deterministic first, so bad candidates die early
before they cost any model tokens.

The important detail for explainability: a `ValidationResult` carries not just a verdict
but a `reason`, and where the layer can produce one, a `counterexample` and a
`suggested_fix`. That is what turns "this failed" into something a reviewer can act on in
a couple of seconds.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ValidationLayer(str, Enum):
    """The seven validation layers. See blueprint Part 5, module M7."""

    L0_TYPE_FORMAT = "L0"
    """Datatype, regex, check digits (GTIN), enum membership."""

    L1_DIMENSION = "L1"
    """Unit's quantity kind must match the attribute's declared quantity kind."""

    L2_DOMAIN_RULE = "L2"
    """Deterministic cross-field rules: ID < OD, packaging arithmetic, thread sets."""

    L3_STATISTICAL = "L3"
    """Plausibility against the learned per-class distribution. Flags, does not reject."""

    L4_CROSS_SOURCE = "L4"
    """Agreement between independent sources, with learned per-supplier trust."""

    L5_GROUNDEDNESS = "L5"
    """Every value traces to a verifiable evidence span; copy asserts nothing unsupported."""

    L6_FORMAL = "L6"
    """Formal verification against a logic policy (Bedrock Automated Reasoning checks)."""

    @property
    def is_deterministic(self) -> bool:
        """Layers that need no model call at all."""
        return self in {
            ValidationLayer.L0_TYPE_FORMAT,
            ValidationLayer.L1_DIMENSION,
            ValidationLayer.L2_DOMAIN_RULE,
        }


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    """Suspicious but not disqualifying — surfaces in review, does not block publication."""

    AMBIGUOUS = "ambiguous"
    """Formal check could not determine validity: the record is underdetermined rather
    than wrong. Extremely common in catalog data and normally invisible."""

    SKIPPED = "skipped"
    """Layer not applicable, e.g. no second source available for L4."""


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationResult(BaseModel):
    """Outcome of one validation layer against one target."""

    model_config = ConfigDict(frozen=True)

    layer: ValidationLayer
    rule_id: str
    verdict: Verdict
    severity: Severity = Severity.ERROR
    reason: str = Field(description="Human-readable, shown directly in the review UI")
    counterexample: str | None = Field(
        default=None,
        description="Concrete case demonstrating the violation. Automated Reasoning checks "
        "produce these; deterministic rules can too.",
    )
    suggested_fix: str | None = None
    detail: str | None = Field(
        default=None, description="Supporting numbers, e.g. 'z=3.8 vs class mean 585 psi'"
    )

    @property
    def is_blocking(self) -> bool:
        """Only an ERROR-severity failure blocks publication."""
        return self.verdict == Verdict.FAIL and self.severity == Severity.ERROR

    @property
    def needs_review(self) -> bool:
        return self.verdict in {Verdict.FAIL, Verdict.WARN, Verdict.AMBIGUOUS}

    def to_certificate_entry(self) -> dict[str, str | None]:
        entry: dict[str, str | None] = {
            "layer": self.layer.value,
            "rule": self.rule_id,
            "verdict": self.verdict.value,
        }
        if self.detail:
            entry["detail"] = self.detail
        if self.counterexample:
            entry["counterexample"] = self.counterexample
        if self.suggested_fix:
            entry["suggested_fix"] = self.suggested_fix
        return entry

    @classmethod
    def passed(
        cls, layer: ValidationLayer, rule_id: str, reason: str = "", detail: str | None = None
    ) -> ValidationResult:
        return cls(
            layer=layer,
            rule_id=rule_id,
            verdict=Verdict.PASS,
            reason=reason or f"{rule_id} satisfied",
            detail=detail,
        )

    @classmethod
    def failed(
        cls,
        layer: ValidationLayer,
        rule_id: str,
        reason: str,
        *,
        severity: Severity = Severity.ERROR,
        counterexample: str | None = None,
        suggested_fix: str | None = None,
        detail: str | None = None,
    ) -> ValidationResult:
        return cls(
            layer=layer,
            rule_id=rule_id,
            verdict=Verdict.FAIL,
            severity=severity,
            reason=reason,
            counterexample=counterexample,
            suggested_fix=suggested_fix,
            detail=detail,
        )

    @classmethod
    def warned(
        cls, layer: ValidationLayer, rule_id: str, reason: str, detail: str | None = None
    ) -> ValidationResult:
        return cls(
            layer=layer,
            rule_id=rule_id,
            verdict=Verdict.WARN,
            severity=Severity.WARNING,
            reason=reason,
            detail=detail,
        )
