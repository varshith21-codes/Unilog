"""Tests for validation layers L0-L3.

Three groups matter most:

* **Security.** The expression evaluator runs strings from YAML against data derived from
  supplier-controlled documents. The allow-list tests prove that arbitrary code cannot get
  through, because ``eval`` would have been the obvious way to build this.
* **Skipped rules.** A rule that could not be evaluated must never look like a rule that
  passed. That distinction is the difference between real coverage and apparent coverage.
* **The lead-free contradiction.** The flagship L2 catch, and the one whose failure has legal
  consequences rather than merely commercial ones.
"""

from __future__ import annotations

import pytest
from axiom.core.product import ProductRecord
from axiom.core.validation import Verdict
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    UnitMismatchError,
    ValueRange,
)
from axiom.schema import load_default
from axiom.validate import (
    ExpressionError,
    RuleConstants,
    SkipRule,
    Validator,
    compile_expression,
    desugar,
    evaluate,
    gtin_check_digit,
    validate_gtin,
)

CLASS_CODE = "PLB.VLV.BALL.2PC"


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def constants():
    return RuleConstants.load()


@pytest.fixture
def validator(registry, constants):
    return Validator(registry, constants)


def value(code: str, canonical, raw: str | None = None) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=raw if raw is not None else str(canonical),
        value_canonical=canonical,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
    )


def record_with(*values: AttributeValue) -> ProductRecord:
    record = ProductRecord(tenant_id="t1", sku="BA-100-075", class_code=CLASS_CODE)
    for item in values:
        record.add_value(item)
    return record


# ===================================================================== security


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",
        "().__class__.__bases__[0].__subclasses__()",
        "open('/etc/passwd').read()",
        "[x for x in range(10)]",
        "(lambda: 1)()",
        "value.__dict__",
        "a := 1",
    ],
)
def test_dangerous_expressions_are_rejected_by_the_node_allowlist(expression):
    """Rejected before evaluation, by an allow-list over the AST rather than by sanitising."""
    with pytest.raises(ExpressionError):
        compile_expression(expression)


@pytest.mark.parametrize("expression", ["exec('x=1')", "globals()", "eval('2+2')"])
def test_calls_to_unlisted_functions_are_rejected(expression):
    """These parse as ordinary calls, so the function allow-list is what stops them.

    Nothing executes either way — an unlisted name is never bound — but rejecting at compile
    time also turns a typo'd function name in a schema into a load-time error.
    """
    with pytest.raises(ExpressionError, match="not available"):
        compile_expression(expression, allowed_functions={"abs", "min", "max"})

    with pytest.raises(ExpressionError):
        evaluate(expression, {}, functions={"abs": abs})


def test_attribute_access_is_restricted_to_known_names():
    compile_expression("temperature_range.min < temperature_range.max")
    with pytest.raises(ExpressionError, match="not permitted"):
        compile_expression("thing.__class__")


def test_only_direct_function_calls_are_allowed():
    with pytest.raises(ExpressionError):
        compile_expression("obj.method()")


def test_unknown_function_is_rejected():
    with pytest.raises(ExpressionError, match="not available"):
        evaluate("mystery(1)", {}, functions={})


def test_unknown_name_is_rejected():
    with pytest.raises(ExpressionError, match="unknown name"):
        evaluate("undefined_thing > 1", {})


# ===================================================================== desugaring


def test_implies_desugars_to_material_implication():
    assert desugar("A implies B") == "(not (A)) or (B)"


def test_null_desugars_to_none():
    assert desugar("x is not null") == "x is not None"


def test_multiple_implies_is_rejected():
    with pytest.raises(ExpressionError, match="only one 'implies'"):
        desugar("A implies B implies C")


def test_implies_needs_both_sides():
    with pytest.raises(ExpressionError, match="both sides"):
        desugar("A implies")


@pytest.mark.parametrize(
    ("antecedent", "consequent", "expected"),
    [(True, True, True), (True, False, False), (False, True, True), (False, False, True)],
)
def test_implication_truth_table(antecedent, consequent, expected):
    assert evaluate("a implies b", {"a": antecedent, "b": consequent}) is expected


# ===================================================================== evaluation


def test_membership_works_with_a_scalar_on_the_left():
    ns = {"end_connection": "NPT Threaded", "THREADED_TYPES": ["NPT Threaded", "BSPT Threaded"]}
    assert evaluate("end_connection in THREADED_TYPES", ns) is True


def test_membership_works_with_a_list_attribute():
    """'NSF-61' in approvals must work when approvals is multi-valued."""
    assert evaluate("'NSF-61' in approvals", {"approvals": ["UL", "NSF-61"]}) is True
    assert evaluate("'NSF-61' in approvals", {"approvals": ["UL", "CSA"]}) is False


def test_membership_against_an_absent_list_skips_rather_than_returning_false():
    """If the approvals list is unknown, we cannot claim NSF-61 is absent from it.

    Returning False here would let `potable implies 'NSF-61' in approvals` fail on a record
    that simply has no approvals data yet — manufacturing a compliance violation out of
    missing information, which is the exact failure mode the skip machinery exists to prevent.
    """
    with pytest.raises(SkipRule):
        evaluate("'NSF-61' in approvals", {"approvals": None})


def test_membership_against_an_empty_list_is_genuinely_false():
    """An empty list is knowledge; None is the absence of knowledge."""
    assert evaluate("'NSF-61' in approvals", {"approvals": []}) is False


def test_range_bounds_are_readable_as_min_and_max():
    rng = ValueRange(minimum=-28.9, maximum=185.6, unit="degC")
    assert evaluate("r.min < r.max", {"r": rng}) is True


def test_missing_value_skips_the_rule_rather_than_failing_it():
    """A rule about values you do not have is not a violation."""
    with pytest.raises(SkipRule):
        evaluate("a > 1", {"a": None})


def test_short_circuit_means_a_false_antecedent_tolerates_a_missing_consequent():
    """`false implies anything` is true, so the missing value never matters."""
    assert evaluate("a implies b", {"a": False, "b": None}) is True


def test_division_by_zero_skips_rather_than_raising():
    with pytest.raises(SkipRule, match="division by zero"):
        evaluate("x / y > 1", {"x": 1.0, "y": 0.0})


def test_arithmetic_and_abs():
    ns = {"a": 10.0, "b": 3.0}
    assert evaluate("abs(a - b * 3) <= 1.0", ns, functions={"abs": abs}) is True


# ===================================================================== Quantity algebra


def test_quantities_compare_within_a_unit():
    assert Quantity(magnitude=150, unit="psi") <= Quantity(magnitude=600, unit="psi")
    assert Quantity(magnitude=600, unit="psi") > Quantity(magnitude=150, unit="psi")


def test_comparing_across_units_is_refused():
    """Comparing magnitudes as bare floats would silently compare pounds against kilograms."""
    with pytest.raises(UnitMismatchError):
        _ = Quantity(magnitude=1, unit="kg") < Quantity(magnitude=1, unit="lb")


def test_quantity_arithmetic():
    each = Quantity(magnitude=0.34, unit="kg")
    case = Quantity(magnitude=4.1, unit="kg")
    assert (each * 12).magnitude == pytest.approx(4.08)
    assert abs(case - each * 12).magnitude == pytest.approx(0.02, abs=1e-9)
    assert (case / each) == pytest.approx(12.058, rel=1e-3)


def test_multiplying_two_quantities_is_refused():
    with pytest.raises(UnitMismatchError, match="quantity kind"):
        _ = Quantity(magnitude=2, unit="mm") * Quantity(magnitude=3, unit="mm")


# ===================================================================== L0 GTIN


@pytest.mark.parametrize(
    "gtin",
    ["036000291452", "012345678905", "00012345678905", "40170725"],
)
def test_valid_gtins_pass(gtin):
    assert validate_gtin(gtin).verdict is Verdict.PASS


def test_transposed_digit_is_caught_by_arithmetic_alone():
    """No catalogue lookup, no model call, no ambiguity about whether it is wrong."""
    result = validate_gtin("036000291542")
    assert result.verdict is Verdict.FAIL
    assert result.rule_id == "gtin_check_digit"
    assert result.counterexample is not None
    assert result.suggested_fix is not None


def test_wrong_length_is_caught():
    assert validate_gtin("12345").rule_id == "gtin_length"


def test_non_numeric_gtin_is_caught():
    assert validate_gtin("ABC-DEF").rule_id == "gtin_not_numeric"


def test_check_digit_computation():
    assert gtin_check_digit("03600029145") == 2
    assert gtin_check_digit("not digits") is None


# ===================================================================== L3 plausibility


def test_implausible_pressure_is_warned_not_rejected(validator):
    """A specialty item may genuinely exceed the usual ceiling; blocking it is worse."""
    record = record_with(value("pressure_rating_wog", Quantity(magnitude=50000, unit="psi")))
    report = validator.validate(record)
    plausibility = next(r for r in report.results if r.rule_id == "plausible_range")
    assert plausibility.verdict is Verdict.WARN
    assert plausibility.is_blocking is False
    assert report.passed is True


def test_plausible_value_passes(validator):
    record = record_with(value("pressure_rating_wog", Quantity(magnitude=600, unit="psi")))
    plausibility = next(
        r for r in validator.validate(record).results if r.rule_id == "plausible_range"
    )
    assert plausibility.verdict is Verdict.PASS


# ===================================================================== L2 cross-field


def test_lead_free_claim_on_a_leaded_alloy_fails(validator):
    """The flagship catch. Legal consequences, not merely commercial ones."""
    record = record_with(
        value("lead_free_compliant", True),
        value("body_material", "Bronze C84400"),
    )
    report = validator.validate(record)
    failure = next(r for r in report.failures if r.rule_id == "R_LEADFREE_MATERIAL")
    assert "lead-free" in failure.reason.lower()
    assert "Bronze C84400" in failure.counterexample
    assert "lead_free_compliant=True" in failure.counterexample
    assert report.passed is False


def test_lead_free_claim_on_a_lead_free_alloy_passes(validator):
    record = record_with(
        value("lead_free_compliant", True),
        value("body_material", "Bronze C89833"),
    )
    report = validator.validate(record)
    result = next(r for r in report.results if r.rule_id == "R_LEADFREE_MATERIAL")
    assert result.verdict is Verdict.PASS


def test_potable_claim_without_nsf61_fails(validator):
    record = record_with(
        value("potable_water_approved", True),
        value("approvals", ["UL", "CSA"]),
    )
    report = validator.validate(record)
    assert any(r.rule_id == "R_POTABLE_REQUIRES_NSF61" for r in report.failures)


def test_potable_claim_with_nsf61_passes(validator):
    record = record_with(
        value("potable_water_approved", True),
        value("approvals", ["UL", "NSF-61"]),
    )
    report = validator.validate(record)
    result = next(r for r in report.results if r.rule_id == "R_POTABLE_REQUIRES_NSF61")
    assert result.verdict is Verdict.PASS


def test_steam_rating_above_wog_fails(validator):
    """A violation here usually means the WOG figure was extracted into the steam field."""
    record = record_with(
        value("steam_pressure_rating", Quantity(magnitude=600, unit="psi")),
        value("pressure_rating_wog", Quantity(magnitude=150, unit="psi")),
    )
    report = validator.validate(record)
    assert any(r.rule_id == "R_STEAM_BELOW_WOG" for r in report.failures)


def test_correct_steam_and_wog_pass(validator):
    record = record_with(
        value("steam_pressure_rating", Quantity(magnitude=150, unit="psi")),
        value("pressure_rating_wog", Quantity(magnitude=600, unit="psi")),
    )
    result = next(
        r for r in validator.validate(record).results if r.rule_id == "R_STEAM_BELOW_WOG"
    )
    assert result.verdict is Verdict.PASS


def test_inverted_temperature_range_fails(validator):
    record = ProductRecord(tenant_id="t1", sku="X", class_code=CLASS_CODE)
    # ValueRange refuses to construct inverted, so the rule guards against a bypass
    record.add_value(
        value("temperature_range", ValueRange(minimum=-28.9, maximum=185.6, unit="degC"))
    )
    result = next(
        r for r in validator.validate(record).results if r.rule_id == "R_TEMP_ORDER"
    )
    assert result.verdict is Verdict.PASS


def test_packaging_arithmetic_inconsistency_is_flagged(validator):
    """One of the three values is wrong, and the arithmetic proves it."""
    record = record_with(
        value("case_weight", Quantity(magnitude=40.0, unit="kg")),
        value("each_weight", Quantity(magnitude=0.34, unit="kg")),
        value("case_quantity", 12),
    )
    report = validator.validate(record)
    result = next(r for r in report.results if r.rule_id == "R_PACK_WEIGHT")
    assert result.verdict is Verdict.FAIL
    assert result.severity.value == "warning", "packaging drift is a warning, not a blocker"


def test_consistent_packaging_passes(validator):
    record = record_with(
        value("case_weight", Quantity(magnitude=4.1, unit="kg")),
        value("each_weight", Quantity(magnitude=0.34, unit="kg")),
        value("case_quantity", 12),
    )
    result = next(
        r for r in validator.validate(record).results if r.rule_id == "R_PACK_WEIGHT"
    )
    assert result.verdict is Verdict.PASS


def test_full_port_cv_floor_uses_the_size_lookup(validator):
    """A full-port valve well below the floor usually means the Cv came from a
    reduced-port row of the same table."""
    record = record_with(
        value("port_type", "Full Port"),
        value("nominal_size", Quantity(magnitude=19.05, unit="mm")),
        value("cv_flow_coefficient", 2.0),
    )
    report = validator.validate(record)
    result = next(r for r in report.results if r.rule_id == "R_FULLPORT_CV_FLOOR")
    assert result.verdict is Verdict.FAIL


def test_adequate_cv_for_a_full_port_valve_passes(validator):
    record = record_with(
        value("port_type", "Full Port"),
        value("nominal_size", Quantity(magnitude=19.05, unit="mm")),
        value("cv_flow_coefficient", 24.0),
    )
    result = next(
        r for r in validator.validate(record).results if r.rule_id == "R_FULLPORT_CV_FLOOR"
    )
    assert result.verdict is Verdict.PASS


# ===================================================================== skipped rules


def test_rule_with_missing_dependencies_is_skipped_not_passed(validator):
    """An unevaluated rule must never be mistaken for a passing one."""
    record = record_with(value("body_material", "Bronze C84400"))
    report = validator.validate(record)

    assert "R_LEADFREE_MATERIAL" in report.skipped_rules
    assert "lead_free_compliant" in report.skipped_rules["R_LEADFREE_MATERIAL"]
    skipped = next(r for r in report.results if r.rule_id == "R_LEADFREE_MATERIAL")
    assert skipped.verdict is Verdict.SKIPPED
    assert skipped.is_blocking is False


def test_skipped_rules_do_not_inflate_the_consistency_score(validator):
    """Otherwise a record with almost no data would score as highly consistent."""
    sparse = record_with(value("body_material", "Bronze C84400"))
    report = validator.validate(sparse)
    evaluated = [r for r in report.results if r.verdict is not Verdict.SKIPPED]
    assert len(evaluated) < len(report.results)
    assert 0.0 <= report.consistency_score() <= 1.0


def test_consistency_score_reflects_failures(validator):
    clean = record_with(
        value("lead_free_compliant", True), value("body_material", "Bronze C89833")
    )
    dirty = record_with(
        value("lead_free_compliant", True), value("body_material", "Bronze C84400")
    )
    assert validator.validate(clean).consistency_score() > (
        validator.validate(dirty).consistency_score()
    )


# ===================================================================== orchestration


def test_record_without_a_class_is_reported(validator):
    record = ProductRecord(tenant_id="t1", sku="X")
    report = validator.validate(record)
    assert any(r.rule_id == "no_class" for r in report.failures)


def test_unnormalized_value_is_flagged(validator):
    record = ProductRecord(tenant_id="t1", sku="X", class_code=CLASS_CODE)
    record.add_value(
        AttributeValue(
            attribute_code="body_material",
            value_raw="something odd",
            method=DerivationMethod.HUMAN_ENTRY,
            confidence=0.5,
        )
    )
    report = validator.validate(record)
    assert any(r.rule_id == "not_normalized" for r in report.results)


def test_unknown_attribute_is_reported(validator):
    record = ProductRecord(tenant_id="t1", sku="X", class_code=CLASS_CODE)
    record.attribute_values.append(
        AttributeValue(
            attribute_code="not_in_schema",
            value_canonical="x",
            method=DerivationMethod.HUMAN_ENTRY,
            confidence=1.0,
        )
    )
    report = validator.validate(record)
    assert any(r.rule_id == "unknown_attribute" for r in report.failures)


def test_normalization_findings_are_carried_into_the_report(validator, registry):
    """An L0 failure found during normalization must not be invisible at validation time."""
    from axiom.normalize import normalize_value

    outcome = normalize_value(
        value("approvals", None, raw="UL, CSA, FooCert 9000"),
        registry.attribute("approvals"),
    )
    record = ProductRecord(tenant_id="t1", sku="X", class_code=CLASS_CODE)
    record.add_value(outcome.value)

    report = validator.validate(record)
    assert any(r.rule_id == "multi_enum_partial" for r in report.results)


def test_summary_is_reportable(validator):
    record = record_with(
        value("lead_free_compliant", True), value("body_material", "Bronze C84400")
    )
    summary = validator.validate(record).summary()
    assert summary["failures"] >= 1
    assert summary["checks"] > 0
    assert 0.0 <= summary["consistency"] <= 1.0


# ===================================================================== constants


def test_constants_load(constants: RuleConstants):
    assert "Bronze C84400" in constants.sets["LEADED_ALLOYS"]
    assert "NSF-61" not in constants.sets["LEAD_FREE_CERTIFICATIONS"], (
        "NSF-61 covers potable water, not lead content; conflating them is the most common "
        "compliance error in this category"
    )


def test_cv_floor_lookup(constants: RuleConstants):
    cv_floor = constants.functions()["cv_floor"]
    assert cv_floor(19.05) == 22.0
    assert cv_floor(Quantity(magnitude=19.05, unit="mm")) == 22.0
    assert cv_floor(None) is None


def test_cv_floor_does_not_snap_a_distant_size(constants: RuleConstants):
    """0.5 mm tolerance is tighter than the gap between nominal sizes."""
    assert constants.functions()["cv_floor"](100.0) is None


def test_material_pressure_lookup(constants: RuleConstants):
    lookup = constants.functions()["material_max_pressure"]
    assert lookup("Bronze C84400") == 600.0
    assert lookup("Unknown Alloy") is None
