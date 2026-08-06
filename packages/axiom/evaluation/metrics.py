"""Comparison outcomes and metric definitions.

The distinction that makes these numbers meaningful: **there are five outcomes, not two.**
Treating extraction as binary correct/incorrect hides the two that matter most in this domain.

A value the system *declined* to produce when none existed is a success, and a value it
*invented* when none existed is the worst possible failure. Collapsing both into "not a match"
would let a system that fabricates freely score the same as one that abstains honestly, which
is precisely the confusion this project exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from axiom.core import compare
from axiom.schema.models import AttributeDefinition

DEFAULT_TOLERANCE = 0.0


class Outcome(str, Enum):
    CORRECT = "correct"
    """Expected a value, got a matching one."""

    WRONG_VALUE = "wrong_value"
    """Expected a value, got a different one. The dangerous failure: a wrong number is worse
    than a missing one, because it is acted on."""

    MISSED = "missed"
    """Expected a value, the system abstained. Costs a sale, not a returned order."""

    CORRECTLY_ABSTAINED = "correctly_abstained"
    """No value existed and none was produced. A success, and easy to forget to count."""

    HALLUCINATED = "hallucinated"
    """No value existed and one was produced anyway. Unacceptable at any rate."""

    @property
    def is_success(self) -> bool:
        return self in {Outcome.CORRECT, Outcome.CORRECTLY_ABSTAINED}

    @property
    def expected_a_value(self) -> bool:
        return self in {Outcome.CORRECT, Outcome.WRONG_VALUE, Outcome.MISSED}


@dataclass(frozen=True)
class Comparison:
    """One (sku, attribute) judgement."""

    sku: str
    attribute_code: str
    outcome: Outcome
    expected: object | None = None
    actual: object | None = None
    match_kind: str | None = None
    """How it matched: exact, normalized, tolerance. Reported separately so a tolerance match
    is never passed off as an exact one."""

    confidence: float | None = None
    had_verified_citation: bool = False
    detail: str | None = None


def compare_value(
    sku: str,
    definition: AttributeDefinition,
    expected: object | None,
    actual: object | None,
    *,
    confidence: float | None = None,
    had_verified_citation: bool = False,
) -> Comparison:
    """Judge one extracted value against ground truth.

    ``expected is None`` means the ground truth says no value exists in the source, so
    abstaining is the correct behaviour.
    """
    if expected is None:
        if actual is None:
            return Comparison(
                sku, definition.code, Outcome.CORRECTLY_ABSTAINED, None, None,
                confidence=confidence,
            )
        return Comparison(
            sku,
            definition.code,
            Outcome.HALLUCINATED,
            None,
            actual,
            confidence=confidence,
            had_verified_citation=had_verified_citation,
            detail="ground truth states no value is present in the source",
        )

    if actual is None:
        return Comparison(
            sku, definition.code, Outcome.MISSED, expected, None, confidence=confidence
        )

    kind = match_kind(definition, expected, actual)
    if kind:
        return Comparison(
            sku,
            definition.code,
            Outcome.CORRECT,
            expected,
            actual,
            match_kind=kind,
            confidence=confidence,
            had_verified_citation=had_verified_citation,
        )
    return Comparison(
        sku,
        definition.code,
        Outcome.WRONG_VALUE,
        expected,
        actual,
        confidence=confidence,
        had_verified_citation=had_verified_citation,
    )


def match_kind(
    definition: AttributeDefinition, expected: object, actual: object
) -> str | None:
    """Strongest match between two canonical values, or None.

    A thin wrapper over :func:`axiom.core.compare.match`, which validation layer L4 also uses to
    decide whether two independent sources agree. Sharing the primitive is deliberate: if
    "matches ground truth" and "agrees with the other source" could drift apart, a value could be
    correct against the golden set and in conflict between two sources that both stated it.
    """
    tolerance = definition.tolerance if definition.tolerance is not None else DEFAULT_TOLERANCE
    kind = compare.match(expected, actual, tolerance=tolerance)
    return kind.value if kind is not None else None


@dataclass
class MetricSet:
    """Aggregated metrics over a set of comparisons."""

    comparisons: list[Comparison] = field(default_factory=list)

    def _count(self, outcome: Outcome) -> int:
        return sum(1 for c in self.comparisons if c.outcome is outcome)

    @property
    def total(self) -> int:
        return len(self.comparisons)

    @property
    def correct(self) -> int:
        return self._count(Outcome.CORRECT)

    @property
    def wrong(self) -> int:
        return self._count(Outcome.WRONG_VALUE)

    @property
    def missed(self) -> int:
        return self._count(Outcome.MISSED)

    @property
    def correctly_abstained(self) -> int:
        return self._count(Outcome.CORRECTLY_ABSTAINED)

    @property
    def hallucinated(self) -> int:
        return self._count(Outcome.HALLUCINATED)

    @property
    def expected_present(self) -> int:
        return self.correct + self.wrong + self.missed

    @property
    def expected_absent(self) -> int:
        return self.correctly_abstained + self.hallucinated

    @property
    def produced(self) -> int:
        """Values the system actually emitted."""
        return self.correct + self.wrong + self.hallucinated

    @property
    def precision(self) -> float:
        """Of the values produced, how many were right."""
        return self.correct / self.produced if self.produced else 0.0

    @property
    def recall(self) -> float:
        """Of the values that existed, how many were recovered correctly."""
        return self.correct / self.expected_present if self.expected_present else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def abstention_correctness(self) -> float:
        """Of the cases where nothing existed, how often the system stayed quiet.

        The metric that separates an honest extractor from a plausible one.
        """
        return (
            self.correctly_abstained / self.expected_absent if self.expected_absent else 1.0
        )

    @property
    def hallucination_rate(self) -> float:
        """Share of produced values that had no basis in the source. Target is zero."""
        return self.hallucinated / self.produced if self.produced else 0.0

    @property
    def exact_match_rate(self) -> float:
        """Of correct values, how many matched exactly rather than within tolerance."""
        if not self.correct:
            return 0.0
        exact = sum(
            1 for c in self.comparisons if c.outcome is Outcome.CORRECT and c.match_kind == "exact"
        )
        return exact / self.correct

    @property
    def citation_coverage(self) -> float:
        """Share of produced values carrying a verified citation."""
        produced = [c for c in self.comparisons if c.actual is not None]
        if not produced:
            return 0.0
        return sum(1 for c in produced if c.had_verified_citation) / len(produced)

    def by_attribute(self) -> dict[str, MetricSet]:
        grouped: dict[str, MetricSet] = {}
        for comparison in self.comparisons:
            grouped.setdefault(comparison.attribute_code, MetricSet()).comparisons.append(
                comparison
            )
        return grouped

    def calibration_pairs(self) -> tuple[list[float], list[bool]]:
        """(score, was_correct) for every produced value.

        This is what turns a backtest into an auto-accept policy: the labels the risk
        machinery needs in order to choose a threshold with a real guarantee.
        """
        scores: list[float] = []
        labels: list[bool] = []
        for comparison in self.comparisons:
            if comparison.actual is None or comparison.confidence is None:
                continue
            scores.append(comparison.confidence)
            labels.append(comparison.outcome is Outcome.CORRECT)
        return scores, labels

    def summary(self) -> dict[str, object]:
        return {
            "total": self.total,
            "correct": self.correct,
            "wrong_value": self.wrong,
            "missed": self.missed,
            "correctly_abstained": self.correctly_abstained,
            "hallucinated": self.hallucinated,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "abstention_correctness": round(self.abstention_correctness, 4),
            "hallucination_rate": round(self.hallucination_rate, 4),
            "exact_match_rate": round(self.exact_match_rate, 4),
            "citation_coverage": round(self.citation_coverage, 4),
        }
