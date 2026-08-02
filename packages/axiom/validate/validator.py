"""The validation orchestrator: layers L0 through L3 over a product record.

Ordered cheapest-and-most-deterministic first, so bad candidates die before they cost
anything. Every result carries a human-readable reason, and where the layer can produce one,
a counterexample and a suggested fix — that is what turns "this failed" into something a
reviewer can act on in seconds rather than investigate for minutes.

Layers L4 (cross-source), L5 (groundedness) and L6 (formal verification) are separate:
groundedness is already enforced in the extractor by discarding unverifiable quotes, and L4
and L6 arrive later in the build.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.core.product import ProductRecord
from axiom.core.validation import (
    Severity,
    ValidationLayer,
    ValidationResult,
    Verdict,
)
from axiom.core.values import AttributeValue, UnitMismatchError
from axiom.schema import ClassDefinition, SchemaRegistry
from axiom.schema.models import Severity as RuleSeverity
from axiom.validate.checks import check_plausible_range, validate_gtin
from axiom.validate.constants import RuleConstants
from axiom.validate.expressions import ExpressionError, SkipRule, evaluate


@dataclass
class ValidationReport:
    """Everything validation concluded about one record."""

    results: list[ValidationResult] = field(default_factory=list)
    per_attribute: dict[str, list[ValidationResult]] = field(default_factory=dict)
    skipped_rules: dict[str, str] = field(default_factory=dict)
    """rule_id -> why it could not be evaluated. Usually a missing dependency, which is
    information rather than a failure: an unevaluated rule must never look like a passing one."""

    @property
    def failures(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_blocking]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.verdict is Verdict.WARN]

    @property
    def passed(self) -> bool:
        return not self.failures

    def consistency_score(self) -> float:
        """Share of evaluated checks that passed. Feeds the Quality Index."""
        evaluated = [r for r in self.results if r.verdict is not Verdict.SKIPPED]
        if not evaluated:
            return 1.0
        return sum(1 for r in evaluated if r.verdict is Verdict.PASS) / len(evaluated)

    def summary(self) -> dict[str, object]:
        return {
            "checks": len(self.results),
            "failures": len(self.failures),
            "warnings": len(self.warnings),
            "skipped_rules": len(self.skipped_rules),
            "consistency": round(self.consistency_score(), 4),
        }


class Validator:
    """Runs L0-L3 against a product record."""

    def __init__(
        self,
        registry: SchemaRegistry,
        constants: RuleConstants | None = None,
    ) -> None:
        self._registry = registry
        self._constants = constants or RuleConstants.load()

    def validate(self, record: ProductRecord, *, class_code: str | None = None) -> ValidationReport:
        report = ValidationReport()
        resolved_class = class_code or record.class_code
        if resolved_class is None:
            report.results.append(
                ValidationResult.failed(
                    ValidationLayer.L0_TYPE_FORMAT,
                    "no_class",
                    "record has no product class, so no rules or required attributes apply",
                )
            )
            return report

        definition = self._registry.product_class(resolved_class)
        values = record.current_values()

        self._run_attribute_checks(report, values)
        self._run_cross_field_rules(report, definition, values)
        return report

    # ------------------------------------------------------------------ L0 / L1 / L3

    def _run_attribute_checks(
        self, report: ValidationReport, values: list[AttributeValue]
    ) -> None:
        for value in values:
            try:
                definition = self._registry.attribute(value.attribute_code)
            except KeyError:
                self._add(
                    report,
                    value.attribute_code,
                    ValidationResult.failed(
                        ValidationLayer.L0_TYPE_FORMAT,
                        "unknown_attribute",
                        f"'{value.attribute_code}' is not defined in the active schema",
                    ),
                )
                continue

            # Results attached during normalization are real findings and belong in the
            # report, otherwise an L0/L1 failure discovered earlier would be invisible here.
            for existing in value.validations:
                self._add(report, value.attribute_code, existing)

            if value.value_canonical is None:
                self._add(
                    report,
                    value.attribute_code,
                    ValidationResult.warned(
                        ValidationLayer.L0_TYPE_FORMAT,
                        "not_normalized",
                        f"attribute '{value.attribute_code}' has no canonical value, so it "
                        f"cannot be compared, filtered or rule-checked",
                    ),
                )
                continue

            if value.attribute_code == "gtin" and isinstance(value.value_canonical, str):
                self._add(report, value.attribute_code, validate_gtin(value.value_canonical))

            if plausibility := check_plausible_range(value, definition):
                self._add(report, value.attribute_code, plausibility)

    # ------------------------------------------------------------------ L2

    def _run_cross_field_rules(
        self,
        report: ValidationReport,
        definition: ClassDefinition,
        values: list[AttributeValue],
    ) -> None:
        if not definition.cross_field_rules:
            return

        namespace = {v.attribute_code: v.value_canonical for v in values}
        namespace.update(self._constants.namespace())
        functions = self._constants.functions()

        for rule in definition.cross_field_rules:
            missing = [
                code
                for code in rule.references
                if code not in namespace or namespace[code] is None
            ]
            if missing:
                # A rule about values you do not have is not a violation. Recording it as
                # skipped keeps an unevaluated rule from being mistaken for a passing one.
                report.skipped_rules[rule.id] = (
                    f"missing {', '.join(sorted(missing))}"
                )
                report.results.append(
                    ValidationResult(
                        layer=ValidationLayer.L2_DOMAIN_RULE,
                        rule_id=rule.id,
                        verdict=Verdict.SKIPPED,
                        severity=Severity.INFO,
                        reason=f"not evaluated: missing {', '.join(sorted(missing))}",
                    )
                )
                continue

            severity = (
                Severity.ERROR if rule.severity is RuleSeverity.ERROR else Severity.WARNING
            )
            try:
                holds = evaluate(rule.expr, namespace, functions=functions)
            except UnitMismatchError as exc:
                # Two quantities in different units reached a comparison. That is a genuine
                # data problem — one of them was not canonicalised — so it is reported rather
                # than skipped.
                report.results.append(
                    ValidationResult.failed(
                        ValidationLayer.L1_DIMENSION,
                        rule.id,
                        f"rule '{rule.id}' compared incompatible units: {exc}",
                        counterexample=self._counterexample(rule.references, namespace),
                    )
                )
                continue
            except SkipRule as exc:
                report.skipped_rules[rule.id] = str(exc)
                report.results.append(
                    ValidationResult(
                        layer=ValidationLayer.L2_DOMAIN_RULE,
                        rule_id=rule.id,
                        verdict=Verdict.SKIPPED,
                        severity=Severity.INFO,
                        reason=f"not evaluated: {exc}",
                    )
                )
                continue
            except ExpressionError as exc:
                # A malformed rule is a schema defect, not a data defect. Reporting it as a
                # data failure would send a reviewer hunting for a problem in the product.
                report.skipped_rules[rule.id] = f"malformed rule: {exc}"
                report.results.append(
                    ValidationResult.failed(
                        ValidationLayer.L2_DOMAIN_RULE,
                        rule.id,
                        f"rule '{rule.id}' is malformed and could not be evaluated: {exc}",
                        severity=Severity.WARNING,
                        suggested_fix="fix the rule expression in the class schema",
                    )
                )
                continue

            if holds:
                report.results.append(
                    ValidationResult.passed(
                        ValidationLayer.L2_DOMAIN_RULE,
                        rule.id,
                        rule.message.strip(),
                    )
                )
            else:
                report.results.append(
                    ValidationResult.failed(
                        ValidationLayer.L2_DOMAIN_RULE,
                        rule.id,
                        rule.message.strip(),
                        severity=severity,
                        counterexample=self._counterexample(rule.references, namespace),
                        detail=f"rule: {rule.expr}",
                    )
                )
                for code in rule.references:
                    report.per_attribute.setdefault(code, []).append(report.results[-1])

    @staticmethod
    def _counterexample(references: tuple[str, ...], namespace: dict) -> str:
        """Show the actual values that violated the rule.

        The single most useful line in a validation failure: it turns "this rule failed" into
        "lead_free_compliant=True, body_material=Bronze C84400", which a reviewer can resolve
        without opening anything else.
        """
        parts = []
        for code in references:
            value = namespace.get(code)
            rendered = getattr(value, "value", value)
            parts.append(f"{code}={rendered!r}")
        return ", ".join(parts)

    @staticmethod
    def _add(report: ValidationReport, code: str, result: ValidationResult) -> None:
        report.results.append(result)
        if result.needs_review:
            report.per_attribute.setdefault(code, []).append(result)


__all__ = ["ValidationReport", "Validator"]
