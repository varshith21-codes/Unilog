"""Validation layer L6: formal verification of natural-language claims.

Bedrock Automated Reasoning translates a passage of prose into logic and asks an SMT solver
whether it is consistent with a policy. The policy is authored in
``schema/reasoning_policy.yaml`` and deployed by ``scripts/deploy_reasoning_policy.py``.

**How this differs from L2**, which already evaluates cross-field rules: L2 operates on
already-structured values and has real arithmetic. L6 operates on *sentences*. It is the only
layer that can look at a product description and determine that what it says is contradictory —
and it returns the identifier of the rule that was violated, which is a proof rather than a
score.

That distinction matters next to the claim checker in ``axiom.generate``. The claim checker
proves a statement *came from* a verified attribute. This proves the statement is not
*self-contradictory*. "Lead-free bronze" beside a C84400 body passes neither, but for different
reasons, and a description can fail this while passing that — a sentence assembled entirely from
real attributes can still assert something impossible.

**The limitation is structural and worth stating plainly.** Automated Reasoning policies are
enum-only; there are no numeric sorts. Every rule expressible here is propositional or
categorical, so numeric checks stay in L2 where they can be done properly. This layer is not a
replacement for the rest of the validation stack, it is the one piece the rest cannot do.

Discovered by probing the live API, because none of it is guessable:

*   Rule expressions are SMT-LIB S-expressions. ``(= x y)``, ``(not p)``, ``(and ..)``,
    ``(or ..)``, ``(=> p q)``, ``(ite c a b)``. Infix forms and ``distinct`` are rejected.
*   Premises and claims must both sit in **guarded** content. Facts passed with a
    ``grounding_source`` qualifier are ignored by this policy type — coverage metrics show them
    excluded, and every claim then comes back trivially satisfiable.
*   The guardrail needs a cross-Region profile (``us.guardrail.v1:0``) or creation is refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from axiom.core.product import ProductRecord
from axiom.core.validation import Severity, ValidationLayer, ValidationResult, Verdict
from axiom.core.values import Quantity, ValueRange

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "reasoning.yaml"

L6 = ValidationLayer.L6_FORMAL

INDETERMINATE_KINDS = frozenset({"translationAmbiguous", "tooComplex", "noTranslations"})
"""Verdicts in which the solver declined to form an opinion.

Recorded as SKIPPED, never as PASS. The distinction is the whole reason this layer is
trustworthy: a claim the solver could not translate is unverified, and reporting it as verified
would be worse than not checking it at all, because it arrives wearing a badge it did not earn.
"""


@dataclass(frozen=True)
class ReasoningConfig:
    """Pinned ARNs for the deployed policy and its guardrail."""

    region: str
    policy_arn: str
    guardrail_id: str
    guardrail_version: str
    confidence_threshold: float = 0.9
    rules: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path | str | None = None) -> ReasoningConfig | None:
        """Returns None when the policy has never been deployed.

        None rather than raising: L6 is optional, and a pipeline must run on an account where
        nobody has deployed a reasoning policy.
        """
        target = Path(path) if path else DEFAULT_CONFIG
        if not target.is_file():
            return None
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if not payload.get("guardrail_id"):
            return None
        return cls(
            region=payload.get("region", ""),
            policy_arn=payload.get("policy_arn", ""),
            guardrail_id=payload["guardrail_id"],
            guardrail_version=str(payload.get("guardrail_version", "DRAFT")),
            confidence_threshold=float(payload.get("confidence_threshold", 0.9)),
            rules=tuple(payload.get("rules") or ()),
        )

    def summary(self) -> dict[str, object]:
        return {
            "region": self.region,
            "guardrail_id": self.guardrail_id,
            "guardrail_version": self.guardrail_version,
            "confidence_threshold": self.confidence_threshold,
            "rules": list(self.rules),
        }


def guardrail_runtime(config: ReasoningConfig, *, profile: str | None = None, timeout: int = 60):
    """A ``bedrock-runtime`` client bound to the region the *policy* was deployed in.

    Deliberately not the region from ``models.yaml``. The guardrail is a regional resource and
    the model cascade may legitimately run elsewhere; resolving the client from the cascade's
    region would produce a ``ResourceNotFoundException`` that reads like a missing policy rather
    than a misrouted call.

    Imported lazily so that everything else in this module stays usable — and testable — without
    boto3 or credentials present.
    """
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=profile, region_name=config.region or None)
    return session.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=timeout),
    )


# Rendering the record into the phrasing the policy's variable descriptions were written
# against. Deterministic on purpose: if a model paraphrased the facts, a contradiction could be
# introduced or hidden by the paraphrase rather than by the product data.
_ALLOY_PHRASES = {
    "Bronze C84400": "The valve body alloy is Bronze C84400.",
    "Bronze C89833": "The valve body alloy is Bronze C89833.",
    "Brass C36000": "The valve body alloy is Brass C36000.",
    "Brass C46500": "The valve body alloy is Brass C46500.",
    "Brass C69300": "The valve body alloy is Brass C69300.",
    "Stainless Steel 316": "The valve body alloy is Stainless Steel 316.",
    "Stainless Steel 304": "The valve body alloy is Stainless Steel 304.",
}

_CONNECTION_PHRASES = {
    "NPT Threaded": "The end connection is NPT threaded.",
    "BSPT Threaded": "The end connection is BSPT threaded.",
    "BSPP Threaded": "The end connection is BSPP threaded.",
    "Solder": "The end connection is solder.",
    "Press": "The end connection is press-fit.",
    "Flanged": "The end connection is flanged.",
}


def describe_record(record: ProductRecord) -> str:
    """Render a record's publishable facts as premises the policy can bind to.

    Only publishable values are described. Feeding a queued value in as an established premise
    would let the solver prove a claim from something the system has not accepted, which would
    make a formal verdict rest on an unverified assumption.
    """
    sentences: list[str] = []
    values = {value.attribute_code: value for value in record.publishable_values()}

    alloy = values.get("body_material")
    if alloy is not None:
        canonical = str(alloy.value_canonical or alloy.value_raw or "")
        sentences.append(
            _ALLOY_PHRASES.get(canonical, f"The valve body alloy is {canonical}.")
        )
    else:
        sentences.append("The valve body alloy is not established.")

    connection = values.get("end_connection")
    if connection is not None:
        canonical = str(connection.value_canonical or connection.value_raw or "")
        sentences.append(
            _CONNECTION_PHRASES.get(canonical, f"The end connection is {canonical}.")
        )

    approvals = values.get("approvals")
    approval_text = _approval_text(approvals)
    sentences.append(
        "NSF-372 certification is held."
        if "372" in approval_text
        else "NSF-372 certification is not held."
    )
    sentences.append(
        "NSF-61 certification is held."
        if "61" in approval_text
        else "NSF-61 certification is not held."
    )

    lead_free = values.get("lead_free_compliant")
    if lead_free is not None and lead_free.value_canonical is True:
        sentences.append("The product is certified lead-free.")

    return " ".join(sentences)


def _approval_text(value) -> str:
    if value is None:
        return ""
    canonical = value.value_canonical
    if isinstance(canonical, list | tuple):
        return " ".join(str(item) for item in canonical)
    if isinstance(canonical, Quantity | ValueRange):
        return ""
    return str(canonical or value.value_raw or "")


# A sentence boundary is terminal punctuation followed by whitespace and the start of something
# new. Requiring an uppercase letter or an opening quote after the space is what keeps standards
# references intact: "ASME B16.34" has no space after its dot, and "approx. 600 psi" is followed
# by a digit, so neither is split. Getting this wrong is the same class of bug the claim checker
# already had to fix once — punctuation inside a specification is not punctuation between
# sentences.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\u201c\"])")

# Bullet and list markers only. Deliberately not digits: a bullet reading "600 psi WOG" would
# otherwise be stripped down to "psi WOG" and lose the very figure worth verifying.
_LIST_MARKER = re.compile(r"^[\s\-\u2013\u2014*\u2022]+")

MIN_CLAIM_CHARS = 12
"""Below this a fragment carries no assertion worth sending to a solver.

Tuned to drop artifacts of splitting rather than real sentences. Short industrial bullets like
"Full port design" are 16 characters and do survive.
"""


def split_claims(text: str) -> list[str]:
    """Split prose into the individual assertions to verify, in order, without duplicates.

    Per-sentence rather than whole-passage, because the solver reports *that* a passage
    contradicts the policy, not *which* clause did. One call per sentence is what turns
    "this description is invalid" into "this sentence is invalid, and here is the rule" —
    which is the difference between a verdict a reviewer can act on and one they cannot.

    That granularity costs one ``ApplyGuardrail`` call per sentence, which is the reason this
    is opt-in on the pipeline rather than always on.

    Deduplicated case-insensitively: a headline is routinely restated in the short description,
    and paying twice to prove the same sentence twice is waste. Over-splitting degrades safely —
    a fragment the solver cannot translate comes back ``noTranslations`` and is recorded as
    SKIPPED, never as a pass.
    """
    claims: list[str] = []
    seen: set[str] = set()

    for line in text.splitlines():
        stripped = _LIST_MARKER.sub("", line).strip()
        if not stripped:
            continue
        for part in _SENTENCE_BOUNDARY.split(stripped):
            claim = part.strip()
            if len(claim) < MIN_CLAIM_CHARS or not any(c.isalpha() for c in claim):
                continue
            key = claim.casefold()
            if key in seen:
                continue
            seen.add(key)
            claims.append(claim)

    return claims


@dataclass
class ReasoningFinding:
    """One verdict the solver returned."""

    kind: str
    rules: tuple[str, ...] = ()
    confidence: float | None = None
    claims: tuple[str, ...] = ()
    detail: str = ""

    @property
    def is_contradiction(self) -> bool:
        return self.kind in {"invalid", "impossible"}

    @property
    def is_proven(self) -> bool:
        return self.kind == "valid"

    @property
    def is_indeterminate(self) -> bool:
        """The solver returned no opinion. Not a pass, and not a failure either."""
        return self.kind in INDETERMINATE_KINDS

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "rules": list(self.rules),
            "confidence": self.confidence,
            "claims": list(self.claims),
            "detail": self.detail,
            "contradiction": self.is_contradiction,
            "indeterminate": self.is_indeterminate,
        }


class ReasoningChecker:
    """Applies the deployed Automated Reasoning policy to a passage of prose."""

    def __init__(self, client, config: ReasoningConfig) -> None:
        self._client = client
        self._config = config

    def check(self, record: ProductRecord, claim: str) -> list[ValidationResult]:
        """Verify one claim against a record's established facts.

        Returns L6 results. A contradiction is a **blocking** failure: unlike a low confidence
        score, a solver verdict is not a probability, and there is no threshold at which
        publishing a proven contradiction becomes acceptable.
        """
        premises = describe_record(record)
        findings = self.evaluate(f"{premises} {claim}".strip())
        return [self._to_result(finding, claim) for finding in findings]

    def verify_claims(self, premises: str, claims: Sequence[str]) -> ReasoningReport:
        """Verify many claims against one set of premises.

        The premises are rendered once and reused, so every claim is judged against exactly the
        same established facts. Building them per claim would let two sentences in one
        description be evaluated against different worlds.

        **Fails closed.** If the policy cannot be reached, the report carries the error and
        ``passed`` is False. The alternative — treating an unreachable solver as assent — would
        make the gate quietly stop working the moment credentials expired, which is precisely
        when nobody is looking.
        """
        report = ReasoningReport(premises=premises)

        for claim in claims:
            try:
                findings = self.evaluate(f"{premises} {claim}".strip())
            except Exception as exc:  # noqa: BLE001 - any failure means "not verified"
                report.error = f"the reasoning policy could not be reached: {exc}"
                return report

            if not findings:
                # A response carrying no finding is not assent. The policy may have declined to
                # translate the sentence at all, and there is no way to tell that apart from
                # agreement, so it is recorded as the indeterminate case.
                findings = [
                    ReasoningFinding(
                        kind="noTranslations",
                        detail="the policy returned no finding for this claim",
                    )
                ]

            for finding in findings:
                report.findings.append((claim, finding))
                report.results.append(self._to_result(finding, claim))

        return report

    def verify_copy(self, record: ProductRecord, fields: Mapping[str, str]) -> ReasoningReport:
        """Verify every assertion in a piece of generated copy against the record.

        This is the layer's intended production use. The claim checker in ``axiom.generate``
        already proves each statement *came from* a verified attribute; this proves the
        statement is not *self-contradictory* given everything else the record establishes. Copy
        assembled entirely from real attributes can still assert something impossible, and only
        this catches that.
        """
        premises = describe_record(record)
        claims: list[str] = []
        seen: set[str] = set()

        for text in fields.values():
            for claim in split_claims(text or ""):
                key = claim.casefold()
                if key not in seen:
                    seen.add(key)
                    claims.append(claim)

        return self.verify_claims(premises, claims)

    def evaluate(self, text: str) -> list[ReasoningFinding]:
        """Raw findings for a passage. Premises and claim must both be in ``text``."""
        response = self._client.apply_guardrail(
            guardrailIdentifier=self._config.guardrail_id,
            guardrailVersion=self._config.guardrail_version,
            source="OUTPUT",
            # Both in guarded content. A `grounding_source` block is ignored by this policy
            # type, which silently makes every claim satisfiable.
            content=[{"text": {"text": text, "qualifiers": ["guard_content"]}}],
        )
        return _parse_findings(response)

    def _to_result(self, finding: ReasoningFinding, claim: str) -> ValidationResult:
        rules = ", ".join(finding.rules) or "policy"

        if finding.is_contradiction:
            return ValidationResult.failed(
                L6,
                finding.rules[0] if finding.rules else "AR_CONTRADICTION",
                "an SMT solver proved this claim contradicts the product's established facts",
                counterexample=claim.strip()[:200],
                suggested_fix="remove the claim, or establish the attribute that would support it",
                detail=f"violated {rules}; finding={finding.kind}",
            )

        if finding.is_indeterminate:
            # Explicitly SKIPPED, not PASS. The solver could not form an opinion, and recording
            # that as success would let an unchecked claim through wearing a verified badge —
            # the confidence features count a skipped check differently from a passing one for
            # exactly this reason.
            return ValidationResult(
                layer=L6,
                rule_id="AR_NO_VERDICT",
                verdict=Verdict.SKIPPED,
                severity=Severity.INFO,
                reason=(
                    f"the policy returned no verdict ({finding.kind}); this claim was not "
                    f"verified either way"
                ),
                detail=finding.detail or None,
            )

        return ValidationResult.passed(
            L6,
            "AR_CONSISTENT",
            "no policy rule contradicts this claim",
            detail=f"finding={finding.kind}",
        )


def _parse_findings(response: dict[str, Any]) -> list[ReasoningFinding]:
    findings: list[ReasoningFinding] = []
    for assessment in response.get("assessments", ()):
        policy = assessment.get("automatedReasoningPolicy") or {}
        for entry in policy.get("findings", ()):
            for kind, body in entry.items():
                findings.append(_finding(kind, body or {}))
    return findings


def _finding(kind: str, body: dict[str, Any]) -> ReasoningFinding:
    translation = body.get("translation") or {}
    rules = tuple(
        rule.get("identifier", "")
        for rule in (body.get("contradictingRules") or ())
        if rule.get("identifier")
    )
    claims = tuple(
        claim.get("naturalLanguage", "") for claim in translation.get("claims", ())
    )
    return ReasoningFinding(
        kind=kind,
        rules=rules,
        confidence=translation.get("confidence"),
        claims=claims,
        detail=body.get("logicWarning", {}).get("kind", "") if body.get("logicWarning") else "",
    )


@dataclass
class ReasoningReport:
    """Findings across several claims, for reporting and for the publication gate."""

    findings: list[tuple[str, ReasoningFinding]] = field(default_factory=list)
    results: list[ValidationResult] = field(default_factory=list)
    premises: str = ""
    error: str | None = None
    """Set when the policy could not be consulted at all. Distinct from a clean report."""

    @property
    def contradictions(self) -> list[tuple[str, ReasoningFinding]]:
        return [(claim, f) for claim, f in self.findings if f.is_contradiction]

    @property
    def indeterminate(self) -> list[tuple[str, ReasoningFinding]]:
        """Claims the solver declined to translate. Neither proven nor disproven."""
        return [(claim, f) for claim, f in self.findings if f.is_indeterminate]

    @property
    def checked(self) -> int:
        return len(self.findings)

    @property
    def passed(self) -> bool:
        """Whether publication may proceed.

        No contradiction was proven *and* the policy was actually reachable. A confused solver
        does not block publication — there is nothing to act on — but an unreachable one does,
        because "we could not check" must never read as "we checked".
        """
        return self.error is None and not self.contradictions

    @property
    def conclusive(self) -> bool:
        """Whether the layer actually established anything.

        ``passed`` can be true while this is false: a passage in which every sentence came back
        untranslatable is not a contradiction, but nor is it verified. Any UI that shows a
        "formally verified" badge must read this, not ``passed``.
        """
        return (
            self.error is None
            and self.checked > 0
            and len(self.indeterminate) < self.checked
        )

    def summary(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "contradictions": len(self.contradictions),
            "indeterminate": len(self.indeterminate),
            "violated_rules": sorted(
                {rule for _, f in self.contradictions for rule in f.rules}
            ),
            "passed": self.passed,
            "conclusive": self.conclusive,
            "error": self.error,
        }

    def to_dict(self) -> dict[str, object]:
        """The full report, for the console bundle.

        Per-claim rather than aggregate: the value of this layer is naming the sentence and the
        rule, so a summary alone would discard the only actionable part.
        """
        return {
            **self.summary(),
            "premises": self.premises,
            "claims": [
                {
                    "claim": claim,
                    "verdict": finding.kind,
                    "rules": list(finding.rules),
                    "contradiction": finding.is_contradiction,
                    "indeterminate": finding.is_indeterminate,
                    "confidence": finding.confidence,
                    "detail": finding.detail,
                }
                for claim, finding in self.findings
            ],
        }
