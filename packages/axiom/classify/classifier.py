"""Multi-target classification with per-level confidence and abstention.

Three design decisions worth stating.

**Per-level confidence comes from candidate agreement, not from a model's self-report.** If
every shortlisted class sits under ``Plumbing > Valves``, then those levels are certain
regardless of which leaf wins; the uncertainty lives entirely at the level where the
candidates diverge. That produces an honest "confident to level 3, not level 4" instead of a
single number attached to a leaf that may be wrong.

**A clear winner needs no model call.** When retrieval already separates the top candidate
decisively, spending tokens to confirm it is waste. The model is asked only when the decision
is genuinely close, which is the only situation where judgement adds anything.

**External scheme codes are derived, not independently classified.** Once the internal class
is known, ETIM and UNSPSC come from the mapping the schema already declares. Re-deriving them
with a model would invite disagreement between two answers that must agree by definition.
Their confidence is inherited and discounted, because the mapping is itself an assertion that
could be wrong.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from axiom.classify.candidates import Candidate, CandidateIndex
from axiom.core.product import Classification, ClassificationScheme
from axiom.extract.client import ModelCascade, ModelClient, ModelError, UsageLedger
from axiom.schema import SchemaRegistry

# The top candidate is accepted without a model call when the runner-up scores at or below
# this fraction of it.
#
# Deliberately a *ratio* rather than an absolute margin. Raw retrieval scores scale with how
# much text the caller supplies, so an absolute threshold would classify the same product
# differently from a one-line ERP description than from a full datasheet. Measured on the
# valve classes: a correct match sits around 0.57-0.63, and a genuinely ambiguous one around
# 0.75, so 0.68 separates them with room on both sides.
DECISIVE_DOMINANCE = 0.68

# Weak candidates are down-weighted before agreement is computed. Cosine scores are not
# probabilities, and treating a 25%-weaker candidate as 75% as likely overstates it badly
# enough to drag a clear winner below the level-confidence floor.
AGREEMENT_SHARPENING = 3.0

# Below this, no candidate is credible enough to classify at all.
MIN_VIABLE_SCORE = 0.05

# Confidence a level must reach to be published rather than truncated away.
LEVEL_CONFIDENCE_FLOOR = 0.60

# Mapping assertions are discounted relative to the internal decision they derive from.
MAPPING_CONFIDENCE_FACTOR = 0.95

_SCHEME_BY_KEY = {
    "etim": ClassificationScheme.ETIM,
    "unspsc": ClassificationScheme.UNSPSC,
    "eclass": ClassificationScheme.ECLASS,
    "eCl@ss": ClassificationScheme.ECLASS,
    "gs1_gpc": ClassificationScheme.GS1_GPC,
    "hts": ClassificationScheme.HTS,
    "marketplace": ClassificationScheme.MARKETPLACE,
}

SYSTEM_PROMPT = """\
You classify industrial products into a distributor's product taxonomy.

RULES
1. Choose from the CANDIDATE CLASSES only. Never invent a class code.
2. Base the decision on what the product IS, not on how it is marketed.
3. If the product text does not contain enough information to distinguish between candidates, \
say so: set "code": null and explain what is missing. Abstaining is a correct answer.
4. Give a short rationale naming the specific evidence that decided it.

Return ONLY JSON:
{"code": str|null, "confidence": 0.0-1.0, "rationale": str, "runner_up": str|null}
"""


@dataclass
class ClassificationResult:
    """The outcome of classifying one product."""

    classifications: list[Classification] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None
    method: str = "unclassified"
    usage: UsageLedger = field(default_factory=UsageLedger)

    @property
    def internal(self) -> Classification | None:
        for classification in self.classifications:
            if classification.scheme is ClassificationScheme.INTERNAL:
                return classification
        return None

    @property
    def class_code(self) -> str | None:
        internal = self.internal
        return internal.code if internal else None

    def summary(self) -> dict[str, object]:
        internal = self.internal
        return {
            "class_code": self.class_code,
            "confidence": round(internal.confidence, 4) if internal else 0.0,
            "confident_depth": len(internal.path) if internal else 0,
            "schemes": [c.scheme.value for c in self.classifications],
            "candidates_considered": len(self.candidates),
            "method": self.method,
            "abstained": self.abstained,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
        }


class Classifier:
    """Classifies a product into the internal tree and every mapped external scheme."""

    def __init__(
        self,
        registry: SchemaRegistry,
        *,
        client: ModelClient | None = None,
        cascade: ModelCascade | None = None,
        index: CandidateIndex | None = None,
        tier: str = "volume",
        shortlist_size: int = 5,
    ) -> None:
        self._registry = registry
        self._client = client
        self._cascade = cascade
        self._index = index or CandidateIndex.build(registry)
        self._tier = tier
        self._shortlist_size = shortlist_size

    def classify(self, text: str, *, sku: str | None = None) -> ClassificationResult:
        candidates = self._index.search(text, limit=self._shortlist_size)
        result = ClassificationResult(candidates=candidates)

        viable = [c for c in candidates if c.score >= MIN_VIABLE_SCORE]
        if not viable:
            result.abstained = True
            result.method = "no_viable_candidate"
            result.abstain_reason = (
                "no class scored above the viability floor; the product text shares too "
                "little vocabulary with any class in the schema"
            )
            return result

        chosen, confidence, method, runner_up = self._decide(text, viable, sku, result.usage)
        result.method = method

        if chosen is None:
            result.abstained = True
            result.abstain_reason = runner_up or "model declined to choose"
            return result

        definition = self._registry.product_class(chosen.code)
        level_confidences = self._level_confidences(chosen, viable, confidence)
        depth = self._confident_depth(level_confidences)

        result.classifications.append(
            Classification(
                scheme=ClassificationScheme.INTERNAL,
                code=chosen.code,
                path=list(definition.browse_path[:depth]),
                confidence=round(confidence, 4),
                level_confidences=[round(c, 4) for c in level_confidences],
                rationale=runner_up,
                method=method,
                alternatives=[
                    {"code": c.code, "score": c.score}
                    for c in viable
                    if c.code != chosen.code
                ],
            )
        )
        result.classifications.extend(self._mapped_schemes(chosen.code, confidence))
        return result

    # ------------------------------------------------------------------ decision

    def _decide(
        self,
        text: str,
        viable: list[Candidate],
        sku: str | None,
        ledger: UsageLedger,
    ) -> tuple[Candidate | None, float, str, str | None]:
        top = viable[0]

        if len(viable) == 1:
            return top, min(0.90, 0.60 + top.score), "retrieval_only", "single viable candidate"

        # Dominance, not absolute margin: how much of the leader's score does the runner-up
        # reach? Scale-invariant, so a one-line ERP description and a full datasheet for the
        # same product reach the same conclusion.
        dominance = viable[1].score / top.score if top.score > 0 else 1.0

        if dominance <= DECISIVE_DOMINANCE:
            # Retrieval already separated them; a model call would only confirm what the
            # numbers already say.
            confidence = 0.70 + 0.25 * (1.0 - dominance / DECISIVE_DOMINANCE)
            return top, min(0.95, confidence), "retrieval_decisive", (
                f"runner-up {viable[1].code} reached only {dominance:.0%} of the leading score"
            )

        if self._client is None or self._cascade is None:
            # Without a model, an ambiguous case must not be resolved by coin flip.
            return None, 0.0, "ambiguous_no_model", (
                f"runner-up {viable[1].code} reached {dominance:.0%} of the leading score and "
                f"no model is available to adjudicate"
            )

        return self._ask_model(text, viable, sku, dominance, ledger)

    def _ask_model(
        self,
        text: str,
        viable: list[Candidate],
        sku: str | None,
        dominance: float,
        ledger: UsageLedger,
    ) -> tuple[Candidate | None, float, str, str | None]:
        lines = ["CANDIDATE CLASSES:"]
        for candidate in viable:
            lines.append(f"  {candidate.code} — {candidate.name}")
            lines.append(f"      path: {candidate.path_text}")
        lines.append("")
        if sku:
            lines.append(f"TARGET SKU: {sku}")
        lines.append("PRODUCT TEXT:")
        lines.append("<<<")
        lines.append(text)
        lines.append(">>>")

        by_code = {c.code: c for c in viable}
        try:
            response = self._client.converse(
                model_id=self._cascade.model_for(self._tier),
                tier=self._tier,
                system=SYSTEM_PROMPT,
                user="\n".join(lines),
                max_tokens=600,
            )
        except ModelError as exc:
            return None, 0.0, "model_error", f"classification model call failed: {exc}"

        # Recorded before the response is judged. A call that produced unusable output still
        # cost tokens, and a cost meter that only counts successes understates the bill.
        ledger.record(response)

        try:
            payload = _parse_decision(response.text)
        except ValueError as exc:
            return None, 0.0, "unparseable_decision", str(exc)

        code = payload.get("code")
        if not code:
            return None, 0.0, "model_abstained", payload.get("rationale") or "model abstained"

        chosen = by_code.get(str(code).strip())
        if chosen is None:
            # A code outside the shortlist is a hallucination, not a decision.
            return None, 0.0, "invented_code", (
                f"model returned {code!r}, which was not among the candidates"
            )

        stated = payload.get("confidence")
        confidence = float(stated) if isinstance(stated, int | float) else 0.70
        # Cap it. A near-tie in retrieval is objective evidence of ambiguity, and a model
        # asserting 0.99 on an inherently ambiguous case is not evidence to the contrary.
        ceiling = 0.85 if dominance > 0.90 else 0.92
        return (
            chosen,
            max(0.0, min(confidence, ceiling)),
            "model_adjudicated",
            payload.get("rationale"),
        )

    # ------------------------------------------------------------------ confidence

    @staticmethod
    def _level_confidences(
        chosen: Candidate, viable: list[Candidate], leaf_confidence: float
    ) -> list[float]:
        """Confidence per browse-path level.

        Two sources of certainty, applied where each actually applies:

        * **Above the divergence point**, every candidate agrees, so the level is settled by
          consensus and is certain no matter which leaf wins.
        * **From the divergence point down**, the level was settled by the decision, so it
          inherits the decision's confidence. Continuing to use raw agreement here would be
          wrong: agreement measures whether a decision was *needed*, not whether it was good,
          and using it would discard a correct high-confidence adjudication.

        A leaf can never exceed the confidence of the decision that selected it, so the last
        level is capped regardless of which branch produced it.
        """
        if not chosen.browse_path:
            return []

        # Sharpen before weighting. Cosine scores are similarities, not probabilities, so
        # using them raw treats a clearly weaker candidate as far more plausible than it is
        # and can drag a decisive winner below the level-confidence floor.
        weights = {c.code: c.score**AGREEMENT_SHARPENING for c in viable}
        total_score = sum(weights.values()) or 1.0

        confidences: list[float] = []
        diverged = False
        for depth, segment in enumerate(chosen.browse_path):
            agreeing = sum(
                weights[c.code]
                for c in viable
                if len(c.browse_path) > depth and c.browse_path[depth] == segment
            )
            agreement = agreeing / total_score
            if agreement < 0.999:
                diverged = True

            confidence = leaf_confidence if diverged else agreement
            if depth == len(chosen.browse_path) - 1:
                confidence = min(confidence, leaf_confidence)
            confidences.append(confidence)
        return confidences

    @staticmethod
    def _confident_depth(level_confidences: list[float]) -> int:
        """Deepest level meeting the confidence floor, counting from the root.

        Truncating is more useful than a confident wrong leaf: ``Plumbing > Valves`` is
        navigable and correct, whereas ``Plumbing > Valves > Gate Valves`` on a ball valve
        actively misleads a buyer.
        """
        depth = 0
        for confidence in level_confidences:
            if confidence < LEVEL_CONFIDENCE_FLOOR:
                break
            depth += 1
        return depth

    def _mapped_schemes(self, class_code: str, confidence: float) -> list[Classification]:
        definition = self._registry.product_class(class_code)
        out: list[Classification] = []
        for key, code in definition.mappings.items():
            scheme = _SCHEME_BY_KEY.get(key.lower())
            if scheme is None:
                continue
            out.append(
                Classification(
                    scheme=scheme,
                    code=str(code),
                    confidence=round(confidence * MAPPING_CONFIDENCE_FACTOR, 4),
                    method="schema_mapping",
                    rationale=(
                        f"derived from internal class {class_code} via the mapping declared "
                        f"in the schema, not independently classified"
                    ),
                )
            )
        return out


def _parse_decision(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("classification response was not valid JSON") from None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"classification response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("classification response was not a JSON object")
    return payload


__all__ = ["ClassificationResult", "Classifier"]
