"""Confidence features.

A model saying "high" is not confidence. Self-reported certainty is weakly correlated with
correctness at best, and it is the one signal that is *systematically* optimistic. So
confidence is estimated from signals that can be checked independently of the model's opinion:
how well the quote matched, whether the citation resolved to a table row, how many validation
layers agreed, how hard this attribute has historically been, and how reliable this supplier
has been for this attribute.

Every feature is bounded to [0, 1] so the calibrator's weights are directly comparable and a
reviewer can be told which signal dominated a decision.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from axiom.core.validation import ValidationResult, Verdict
from axiom.core.values import AttributeValue

# Cheap-to-expensive, so the index doubles as a cost signal. A value that only the frontier
# tier could produce is, empirically, a harder value.
TIER_RANK = {"micro": 0.0, "volume": 0.25, "mid": 0.5, "frontier": 0.75, "vision": 1.0}

NEUTRAL_PRIOR = 0.5
"""Used when no history exists yet. Deliberately uninformative rather than optimistic."""


@dataclass
class Priors:
    """Learned per-attribute and per-source reliability.

    Empty until the backtest harness and real review outcomes exist. Returning a neutral 0.5
    in that state is the honest default: an unmeasured attribute is not a trustworthy one, and
    seeding optimistic priors would inflate coverage before there is any evidence for it.
    """

    attribute_accuracy: dict[str, float] = field(default_factory=dict)
    source_accuracy: dict[str, float] = field(default_factory=dict)
    sample_counts: dict[str, int] = field(default_factory=dict)

    MIN_SAMPLES: int = 20
    """Below this, a measured rate is noise. Shrunk toward neutral rather than trusted."""

    @classmethod
    def load(cls, path: Path | str) -> Priors:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            attribute_accuracy=payload.get("attribute_accuracy", {}),
            source_accuracy=payload.get("source_accuracy", {}),
            sample_counts=payload.get("sample_counts", {}),
        )

    def save(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(
                {
                    "attribute_accuracy": self.attribute_accuracy,
                    "source_accuracy": self.source_accuracy,
                    "sample_counts": self.sample_counts,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _shrunk(self, table: dict[str, float], key: str) -> float:
        """Shrink a measured rate toward neutral in proportion to how little data backs it.

        A single correct observation is not evidence of 100% accuracy. This is what stops a
        thinly-sampled attribute from being handed a confident prior it has not earned.
        """
        if key not in table:
            return NEUTRAL_PRIOR
        observed = table[key]
        n = self.sample_counts.get(key, 0)
        weight = min(1.0, n / self.MIN_SAMPLES)
        return NEUTRAL_PRIOR + weight * (observed - NEUTRAL_PRIOR)

    def attribute_prior(self, attribute_code: str) -> float:
        return self._shrunk(self.attribute_accuracy, attribute_code)

    def source_prior(self, supplier_id: str | None, attribute_code: str) -> float:
        if not supplier_id:
            return NEUTRAL_PRIOR
        return self._shrunk(self.source_accuracy, f"{supplier_id}:{attribute_code}")

    def observe(self, key: str, table: str, correct: bool) -> None:
        """Fold one review outcome into the running rate. Feeds the learning flywheel."""
        target = self.attribute_accuracy if table == "attribute" else self.source_accuracy
        n = self.sample_counts.get(key, 0)
        previous = target.get(key, NEUTRAL_PRIOR)
        target[key] = (previous * n + (1.0 if correct else 0.0)) / (n + 1)
        self.sample_counts[key] = n + 1


@dataclass(frozen=True)
class ConfidenceFeatures:
    """Independently checkable signals about one value."""

    evidence_match_score: float
    evidence_verified: float
    citation_precision: float
    """1.0 for a table cell, 0.7 for a table row, 0.4 for a line. A cell pins the exact
    value; a row still requires trusting that the right column was read."""

    quote_specificity: float
    """How tightly the quote brackets the value. A quote far longer than the value it
    supports is weaker evidence, because it would match many candidate values."""

    self_reported_certainty: float
    validation_pass_rate: float
    validation_clean: float
    is_extraction: float
    is_inference: float
    normalized: float
    attribute_prior: float
    source_prior: float
    tier_cost: float

    FEATURE_ORDER = (
        "evidence_match_score",
        "evidence_verified",
        "citation_precision",
        "quote_specificity",
        "self_reported_certainty",
        "validation_pass_rate",
        "validation_clean",
        "is_extraction",
        "is_inference",
        "normalized",
        "attribute_prior",
        "source_prior",
        "tier_cost",
    )

    def vector(self) -> list[float]:
        data = asdict(self)
        return [float(data[name]) for name in self.FEATURE_ORDER]

    def explain(self) -> dict[str, float]:
        data = asdict(self)
        return {name: round(float(data[name]), 4) for name in self.FEATURE_ORDER}


def extract_features(
    value: AttributeValue,
    *,
    validations: list[ValidationResult] | None = None,
    priors: Priors | None = None,
    supplier_id: str | None = None,
) -> ConfidenceFeatures:
    """Build the feature vector for one attribute value."""
    priors = priors or Priors()
    checks = list(validations or value.validations)

    evaluated = [c for c in checks if c.verdict is not Verdict.SKIPPED]
    passed = sum(1 for c in evaluated if c.verdict is Verdict.PASS)
    pass_rate = passed / len(evaluated) if evaluated else NEUTRAL_PRIOR
    clean = 0.0 if any(c.is_blocking for c in checks) else 1.0

    return ConfidenceFeatures(
        evidence_match_score=_best_match_score(value),
        evidence_verified=1.0 if value.has_verified_evidence else 0.0,
        citation_precision=_citation_precision(value),
        quote_specificity=_quote_specificity(value),
        self_reported_certainty=min(1.0, max(0.0, value.confidence)),
        validation_pass_rate=pass_rate,
        validation_clean=clean,
        is_extraction=1.0 if value.method.requires_evidence else 0.0,
        is_inference=1.0 if value.method.is_inference else 0.0,
        normalized=1.0 if value.value_canonical is not None else 0.0,
        attribute_prior=priors.attribute_prior(value.attribute_code),
        source_prior=priors.source_prior(supplier_id, value.attribute_code),
        tier_cost=TIER_RANK.get(value.model_tier or "", NEUTRAL_PRIOR),
    )


def _best_match_score(value: AttributeValue) -> float:
    scores = [s.match_score for s in value.evidence if s.match_score is not None]
    return max(scores) if scores else 0.0


def _citation_precision(value: AttributeValue) -> float:
    best = 0.0
    for span in value.evidence:
        if not span.quote_verified:
            continue
        ref = span.table_ref or ""
        if ref.count(":") >= 2:  # t1:r3:c0 — an exact cell
            best = max(best, 1.0)
        elif ref:  # t1:r3 — a row
            best = max(best, 0.7)
        else:
            best = max(best, 0.4)
    return best


def _quote_specificity(value: AttributeValue) -> float:
    """Ratio of value length to quote length, capped at 1.0.

    A three-character value backed by a two-hundred-character quote is technically cited and
    practically unverified: that quote would equally support many other values on the same
    line. Short, tight quotes are stronger evidence.
    """
    if not value.value_raw:
        return 0.0
    quotes = [s.quote for s in value.evidence if s.quote]
    if not quotes:
        return 0.0
    tightest = min(len(q) for q in quotes)
    if tightest == 0:
        return 0.0
    return min(1.0, len(value.value_raw) / tightest)
