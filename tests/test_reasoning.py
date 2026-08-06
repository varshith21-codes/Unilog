"""Tests for validation layer L6: formal verification via Bedrock Automated Reasoning.

The solver runs in AWS, so these tests stub the client and verify the two halves this codebase
actually owns: that premises are rendered from a record **honestly**, and that a solver verdict is
mapped to a validation result **without softening it**.

The live behaviour was confirmed separately and is recorded here because it is the point of the
layer: against a Bronze C84400 body with NSF-61 but not NSF-372, the deployed policy returns
``invalid`` and names ``RLEADEDALLOY`` for a lead-free claim, ``RSOLDERMATCH`` for a solder claim
on a threaded valve, and ``RPOTABLELEAD`` for a potable-water claim — the last being a compound
inference across three premises. A valid claim returns ``satisfiable``.

What is deliberately *not* tested here is the solver itself. It is AWS's, it is sound, and
re-testing it would be testing the wrong thing.
"""

from __future__ import annotations

import pytest
import yaml
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.validation import ValidationLayer, Verdict
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.validate import (
    ReasoningChecker,
    ReasoningConfig,
    ReasoningFinding,
    ReasoningReport,
    describe_record,
    split_claims,
)

SHA = "9f2c" + "0" * 60
CLASS_CODE = "PLB.VLV.BALL.2PC"


def value(
    code: str,
    canonical,
    *,
    status: ValueStatus = ValueStatus.AUTO_ACCEPTED,
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.95,
        status=status,
        evidence=[
            EvidenceSpan(
                span_id=f"sp-{code}",
                document_id="ba100",
                document_sha256=SHA,
                quote="q",
                page=1,
                bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
                quote_verified=True,
                match_score=1.0,
            )
        ],
    )


def record(**overrides) -> ProductRecord:
    product = ProductRecord(
        tenant_id="demo", sku="BA-100-075", class_code=CLASS_CODE, schema_version="v1"
    )
    values = {
        "body_material": "Bronze C84400",
        "end_connection": "NPT Threaded",
        "approvals": ["UL listed", "CSA certified", "NSF-61"],
    }
    values.update(overrides)
    for code, canonical in values.items():
        if canonical is not None:
            product.add_value(value(code, canonical))
    return product


class StubRuntime:
    """Returns a canned ApplyGuardrail response and records what it was asked."""

    def __init__(self, findings: list[dict]) -> None:
        self._findings = findings
        self.calls: list[dict] = []

    def apply_guardrail(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "action": "NONE",
            "assessments": [{"automatedReasoningPolicy": {"findings": self._findings}}],
        }


def invalid(rule: str) -> dict:
    return {
        "invalid": {
            "translation": {
                "claims": [{"naturalLanguage": "lead_free_claim is equal to Asserted"}],
                "premises": [],
                "confidence": 1.0,
            },
            "contradictingRules": [{"identifier": rule}],
        }
    }


SATISFIABLE = {"satisfiable": {"translation": {"claims": [], "confidence": 1.0}}}


@pytest.fixture
def config():
    return ReasoningConfig(
        region="us-east-2",
        policy_arn="arn:aws:bedrock:us-east-2:1:automated-reasoning-policy/x",
        guardrail_id="gid",
        guardrail_version="DRAFT",
    )


# ===================================================================== premises


def test_premises_are_rendered_from_the_record(config):
    text = describe_record(record())
    assert "Bronze C84400" in text
    assert "NPT threaded" in text
    assert "NSF-61 certification is held" in text
    assert "NSF-372 certification is not held" in text


def test_a_queued_value_is_not_stated_as_an_established_premise():
    """A formal proof resting on an unverified assumption is worse than no proof.

    If a value the system has not accepted were fed in as a premise, the solver could prove a
    claim from it — and the verdict would look every bit as sound as one built on real facts.
    """
    product = ProductRecord(tenant_id="t", sku="S", class_code=CLASS_CODE)
    product.add_value(
        value("body_material", "Bronze C89833", status=ValueStatus.QUEUED_FOR_REVIEW)
    )
    text = describe_record(product)

    assert "C89833" not in text
    assert "not established" in text


def test_a_missing_alloy_is_stated_as_unestablished():
    """Silence would let the solver assume any alloy, including a lead-free one."""
    product = ProductRecord(tenant_id="t", sku="S", class_code=CLASS_CODE)
    assert "not established" in describe_record(product)


def test_nsf372_is_distinguished_from_nsf61():
    """The distinction the whole policy turns on: 61 is drinking-water components, 372 is lead."""
    with_372 = describe_record(record(approvals=["NSF-372", "NSF-61"]))
    assert "NSF-372 certification is held" in with_372
    assert "NSF-61 certification is held" in with_372

    without = describe_record(record(approvals=["NSF-61"]))
    assert "NSF-372 certification is not held" in without


def test_a_lead_free_certification_is_stated_when_held():
    text = describe_record(record(lead_free_compliant=True))
    assert "certified lead-free" in text


# ===================================================================== verdict mapping


def test_a_contradiction_is_a_blocking_failure(config):
    """A solver verdict is not a probability. There is no threshold at which publishing a
    proven contradiction becomes acceptable."""
    checker = ReasoningChecker(StubRuntime([invalid("RLEADEDALLOY")]), config)
    results = checker.check(record(), "This valve is lead-free.")

    assert len(results) == 1
    result = results[0]
    assert result.layer is ValidationLayer.L6_FORMAL
    assert result.verdict is Verdict.FAIL
    assert result.is_blocking is True
    assert result.rule_id == "RLEADEDALLOY", "the violated rule is the finding, not a generic id"
    assert "lead-free" in result.counterexample


def test_a_consistent_claim_passes(config):
    checker = ReasoningChecker(StubRuntime([SATISFIABLE]), config)
    result = checker.check(record(), "This valve has NPT threaded ends.")[0]

    assert result.verdict is Verdict.PASS
    assert result.is_blocking is False


@pytest.mark.parametrize("kind", ["translationAmbiguous", "tooComplex", "noTranslations"])
def test_no_verdict_is_skipped_not_passed(config, kind):
    """The solver failing to form an opinion is not the claim being verified.

    Recording it as PASS would let an unchecked claim through wearing a verified badge, and the
    confidence features deliberately count a skipped check differently from a passing one.
    """
    checker = ReasoningChecker(StubRuntime([{kind: {}}]), config)
    result = checker.check(record(), "Something ambiguous.")[0]

    assert result.verdict is Verdict.SKIPPED
    assert result.verdict is not Verdict.PASS
    assert result.rule_id == "AR_NO_VERDICT"
    assert "not verified" in result.reason


def test_impossible_is_treated_as_a_contradiction(config):
    """`impossible` means the premises themselves cannot hold. That is not a passing state."""
    checker = ReasoningChecker(StubRuntime([{"impossible": {}}]), config)
    assert checker.check(record(), "x")[0].is_blocking is True


# ===================================================================== the request


def test_premises_and_claim_are_sent_as_guarded_content(config):
    """The API detail that silently breaks this layer.

    Facts passed with a `grounding_source` qualifier are ignored by an Automated Reasoning
    policy — coverage metrics show them excluded and every claim comes back trivially
    satisfiable. Both halves must be guarded.
    """
    stub = StubRuntime([SATISFIABLE])
    ReasoningChecker(stub, config).check(record(), "This valve is lead-free.")

    content = stub.calls[0]["content"]
    assert len(content) == 1, "premises and claim travel together in one guarded block"

    block = content[0]["text"]
    assert block["qualifiers"] == ["guard_content"]
    assert "grounding_source" not in str(content)
    assert "Bronze C84400" in block["text"], "premises must be in the guarded text"
    assert "lead-free" in block["text"], "so must the claim"


def test_the_configured_guardrail_and_version_are_used(config):
    stub = StubRuntime([SATISFIABLE])
    ReasoningChecker(stub, config).check(record(), "x")

    assert stub.calls[0]["guardrailIdentifier"] == "gid"
    assert stub.calls[0]["guardrailVersion"] == "DRAFT"
    assert stub.calls[0]["source"] == "OUTPUT"


# ===================================================================== config


def test_missing_config_loads_as_none(tmp_path):
    """L6 is optional. A pipeline must run on an account with no deployed policy."""
    assert ReasoningConfig.load(tmp_path / "absent.yaml") is None


def test_a_config_without_a_guardrail_is_none(tmp_path):
    path = tmp_path / "reasoning.yaml"
    path.write_text(yaml.safe_dump({"region": "us-east-2"}), encoding="utf-8")
    assert ReasoningConfig.load(path) is None


def test_the_deployed_config_is_present_and_complete():
    """The checked-in config is generated by scripts/deploy_reasoning_policy.py."""
    config = ReasoningConfig.load()
    if config is None:
        pytest.skip("no reasoning policy deployed; run scripts/deploy_reasoning_policy.py")

    assert config.guardrail_id
    assert config.policy_arn.startswith("arn:aws:bedrock:")
    assert config.rules, "the config should record which rules were deployed"
    assert all(len(rule) == 12 for rule in config.rules), (
        "Automated Reasoning rule ids are exactly twelve characters"
    )


def test_deployed_rules_match_the_declarative_source():
    """The generated config must not drift from the YAML it was compiled from."""
    config = ReasoningConfig.load()
    if config is None:
        pytest.skip("no reasoning policy deployed")

    from pathlib import Path

    source = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "schema" / "reasoning_policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    declared = {rule["id"] for rule in source["rules"]}
    assert set(config.rules) == declared, "re-run the deploy script"


# ===================================================================== report


def test_a_report_summarises_contradictions():
    report = ReasoningReport(
        findings=[
            ("lead-free", ReasoningFinding(kind="invalid", rules=("RLEADEDALLOY",))),
            ("threaded", ReasoningFinding(kind="satisfiable")),
        ]
    )
    summary = report.summary()

    assert report.passed is False
    assert summary["contradictions"] == 1
    assert summary["violated_rules"] == ["RLEADEDALLOY"]


def test_a_clean_report_passes():
    report = ReasoningReport(findings=[("x", ReasoningFinding(kind="satisfiable"))])
    assert report.passed is True


# ===================================================================== claim splitting


def test_a_standard_reference_is_not_split_at_its_dot():
    """The bug this splitter exists to avoid.

    "ASME B16.34" is one token. Splitting on every period would cut it in half and send "34" to
    a solver as though it were a sentence.
    """
    claims = split_claims("Rated to ASME B16.34 standards. Full port design.")

    assert claims == ["Rated to ASME B16.34 standards.", "Full port design."]


def test_a_decimal_after_a_period_is_not_a_sentence_boundary():
    """"approx. 600 psi" is followed by a digit, so it stays whole."""
    assert split_claims("Torque approx. 600 psi rating.") == ["Torque approx. 600 psi rating."]


def test_bullets_are_split_per_line_without_losing_leading_numbers():
    """A bullet reading "600 psi WOG" must keep its figure — that is the checkable part."""
    claims = split_claims("- 600 psi WOG rating\n- Bronze C84400 body\n\u2022 NPT threaded ends")

    assert claims == ["600 psi WOG rating", "Bronze C84400 body", "NPT threaded ends"]


def test_repeated_sentences_are_verified_once():
    """A headline is routinely restated in the description. Each solver call costs money."""
    claims = split_claims("This valve is lead-free.\nThis valve is lead-free.")
    assert claims == ["This valve is lead-free."]


def test_fragments_too_short_to_assert_anything_are_dropped():
    assert split_claims("Yes. No. 3/4\".") == []


# ===================================================================== verifying copy


def test_every_claim_in_copy_is_verified_against_the_same_premises(config):
    """Premises are rendered once. Two sentences judged against different worlds would make a
    formal verdict meaningless."""
    stub = StubRuntime([SATISFIABLE])
    report = ReasoningChecker(stub, config).verify_copy(
        record(),
        {"headline": "Bronze ball valve.", "bullets": "NPT threaded ends are provided"},
    )

    assert report.checked == 2
    assert len(stub.calls) == 2
    premises = [call["content"][0]["text"]["text"] for call in stub.calls]
    assert all("Bronze C84400" in text for text in premises)
    assert report.passed is True
    assert report.conclusive is True


def test_a_contradiction_in_copy_names_the_claim_and_the_rule(config):
    stub = StubRuntime([invalid("RLEADEDALLOY")])
    report = ReasoningChecker(stub, config).verify_copy(
        record(), {"headline": "This valve is certified lead-free."}
    )

    assert report.passed is False
    assert report.summary()["violated_rules"] == ["RLEADEDALLOY"]
    claim, finding = report.contradictions[0]
    assert claim == "This valve is certified lead-free."
    assert finding.rules == ("RLEADEDALLOY",)


def test_l6_results_are_produced_as_validation_results(config):
    """The layer has to speak the same language as the rest of the validation stack."""
    report = ReasoningChecker(StubRuntime([invalid("RLEADEDALLOY")]), config).verify_copy(
        record(), {"headline": "This valve is certified lead-free."}
    )

    assert [r.layer for r in report.results] == [ValidationLayer.L6_FORMAL]
    assert report.results[0].is_blocking is True


def test_an_unreachable_policy_fails_closed(config):
    """The failure mode that matters. If the solver cannot be reached, the answer is "not
    verified" and publication is withheld — never "verified"."""

    class Broken:
        def apply_guardrail(self, **kwargs):
            raise RuntimeError("ExpiredTokenException")

    report = ReasoningChecker(Broken(), config).verify_copy(
        record(), {"headline": "This valve is bronze bodied."}
    )

    assert report.error is not None
    assert "ExpiredTokenException" in report.error
    assert report.passed is False, "an unreachable solver must not read as assent"
    assert report.conclusive is False


def test_a_response_with_no_finding_is_indeterminate_not_a_pass(config):
    """An empty findings list is indistinguishable from agreement, so it cannot be treated
    as agreement."""

    class Empty:
        def apply_guardrail(self, **kwargs):
            return {"assessments": [{"automatedReasoningPolicy": {"findings": []}}]}

    report = ReasoningChecker(Empty(), config).verify_copy(
        record(), {"headline": "This valve is bronze bodied."}
    )

    assert report.checked == 1
    assert len(report.indeterminate) == 1
    assert report.results[0].verdict is Verdict.SKIPPED


def test_all_claims_indeterminate_passes_but_is_not_conclusive(config):
    """The distinction a "formally verified" badge must respect.

    Nothing was disproven, so publication is not blocked. But nothing was established either,
    so the UI cannot claim verification.
    """
    report = ReasoningChecker(StubRuntime([{"tooComplex": {}}]), config).verify_copy(
        record(), {"headline": "This valve is bronze bodied."}
    )

    assert report.passed is True
    assert report.conclusive is False


def test_a_report_serialises_per_claim_for_the_console(config):
    report = ReasoningChecker(StubRuntime([invalid("RPOTABLELEAD")]), config).verify_copy(
        record(), {"headline": "Suitable for potable water systems."}
    )
    payload = report.to_dict()

    assert payload["passed"] is False
    assert payload["premises"], "the premises must travel with the verdict"
    assert payload["claims"][0]["claim"] == "Suitable for potable water systems."
    assert payload["claims"][0]["rules"] == ["RPOTABLELEAD"]
    assert payload["claims"][0]["contradiction"] is True
