"""Calibrated confidence estimation.

Logistic regression over the feature vector, trained on real review outcomes. Deliberately
hand-rolled: the model has thirteen features and a few hundred examples, so a dependency on a
machine-learning framework would add install weight and a version surface for no accuracy.

The part that matters is not the classifier, it is the **calibration report**. A confidence
score is itself a claim, and a claim that has not been checked against outcomes is decoration.
:meth:`Calibrator.calibration_report` produces the expected calibration error and the
reliability bins behind it, so the number can be inspected rather than trusted.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from axiom.confidence.features import ConfidenceFeatures


def sigmoid(z: float) -> float:
    # Split by sign to avoid overflow on large-magnitude inputs.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


@dataclass
class ReliabilityBin:
    """One bucket of the reliability diagram."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_accuracy: float

    @property
    def gap(self) -> float:
        return abs(self.mean_predicted - self.observed_accuracy)


@dataclass
class CalibrationReport:
    """How trustworthy the confidence numbers themselves are."""

    sample_size: int
    expected_calibration_error: float
    max_calibration_error: float
    brier_score: float
    base_rate: float
    bins: list[ReliabilityBin] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Whether these numbers are backed by enough data to act on.

        A calibration measured on a handful of examples is itself uncalibrated, and treating
        it as a guarantee is exactly the mistake the risk machinery exists to avoid.
        """
        return self.sample_size >= 50 and self.expected_calibration_error <= 0.15

    def summary(self) -> dict[str, object]:
        return {
            "sample_size": self.sample_size,
            "ece": round(self.expected_calibration_error, 4),
            "max_calibration_error": round(self.max_calibration_error, 4),
            "brier_score": round(self.brier_score, 4),
            "base_rate": round(self.base_rate, 4),
            "usable": self.is_usable,
        }


class Calibrator:
    """Logistic model mapping features to a calibrated probability of correctness."""

    def __init__(
        self,
        weights: list[float] | None = None,
        bias: float = 0.0,
        feature_order: tuple[str, ...] = ConfidenceFeatures.FEATURE_ORDER,
    ) -> None:
        self._weights = list(weights) if weights else []
        self._bias = bias
        self._feature_order = feature_order
        self._trained = bool(weights)

    @property
    def is_trained(self) -> bool:
        return self._trained

    @property
    def weights(self) -> dict[str, float]:
        """Named weights, so a reviewer can be told which signal drove a score."""
        return dict(zip(self._feature_order, self._weights, strict=False))

    # ------------------------------------------------------------------ training

    def fit(
        self,
        features: list[ConfidenceFeatures],
        labels: list[bool],
        *,
        epochs: int = 400,
        learning_rate: float = 0.5,
        l2: float = 0.01,
    ) -> Calibrator:
        """Fit by gradient descent with L2 regularisation."""
        if len(features) != len(labels):
            raise ValueError("features and labels must be the same length")
        if not features:
            raise ValueError("cannot fit on an empty sample")

        vectors = [f.vector() for f in features]
        targets = [1.0 if label else 0.0 for label in labels]
        dimension = len(vectors[0])
        self._weights = [0.0] * dimension

        # Initialise the bias at the log-odds of the base rate so training starts from the
        # correct marginal instead of a 50/50 assumption the data may not support.
        positive_rate = sum(targets) / len(targets)
        clamped = min(max(positive_rate, 1e-6), 1 - 1e-6)
        self._bias = math.log(clamped / (1 - clamped))

        n = len(vectors)
        for _ in range(epochs):
            gradient = [0.0] * dimension
            bias_gradient = 0.0
            for vector, target in zip(vectors, targets, strict=True):
                error = sigmoid(self._dot(vector)) - target
                for index, feature_value in enumerate(vector):
                    gradient[index] += error * feature_value
                bias_gradient += error
            for index in range(dimension):
                step = gradient[index] / n + l2 * self._weights[index]
                self._weights[index] -= learning_rate * step
            self._bias -= learning_rate * bias_gradient / n

        self._trained = True
        return self

    def _dot(self, vector: list[float]) -> float:
        return self._bias + sum(w * v for w, v in zip(self._weights, vector, strict=True))

    # ------------------------------------------------------------------ inference

    def predict(self, features: ConfidenceFeatures) -> float:
        """Calibrated probability that this value is correct.

        Falls back to a transparent heuristic when untrained, rather than refusing to score.
        The pipeline has to run before any review outcomes exist, and an untrained scorer that
        says so is more useful than one that cannot start.
        """
        if not self._trained:
            return self._heuristic(features)
        return sigmoid(self._dot(features.vector()))

    @staticmethod
    def _heuristic(features: ConfidenceFeatures) -> float:
        """Cold-start score, before any labelled outcomes exist.

        Weighted toward independently checkable evidence and away from the model's own
        certainty. Two things it must do:

        **Rank usefully.** The review queue is ordered by this score, so it has to spread. An
        earlier version leaned on near-constant terms and a low cap, which pinned almost every
        value to the same number and left the queue in arbitrary order — the worst outcome,
        since ordering is the only thing making a queue better than a list. Weight therefore
        sits on the features that genuinely vary: citation precision (a cell beats a row beats
        a line) and quote specificity.

        **Not clear a strict threshold on its own.** The real guard is that an uncalibrated
        system has no validated policy, so nothing is publishable at all. The cap here is
        secondary, for the odd case where a policy is loaded without its calibrator.
        """
        score = (
            0.22 * features.evidence_verified
            + 0.18 * features.evidence_match_score
            + 0.25 * features.citation_precision
            + 0.15 * features.validation_clean
            + 0.10 * features.validation_pass_rate
            + 0.10 * features.quote_specificity
        )
        if features.is_inference:
            score *= 0.6
        return min(0.95, round(score, 4))

    # ------------------------------------------------------------------ evaluation

    def calibration_report(
        self,
        features: list[ConfidenceFeatures],
        labels: list[bool],
        *,
        bin_count: int = 10,
    ) -> CalibrationReport:
        """Measure whether the predicted probabilities match observed accuracy."""
        if len(features) != len(labels):
            raise ValueError("features and labels must be the same length")
        if not features:
            return CalibrationReport(0, 0.0, 0.0, 0.0, 0.0, [])

        predictions = [self.predict(f) for f in features]
        targets = [1.0 if label else 0.0 for label in labels]
        n = len(predictions)

        bins: list[ReliabilityBin] = []
        weighted_gap = 0.0
        max_gap = 0.0
        for index in range(bin_count):
            lower = index / bin_count
            upper = (index + 1) / bin_count
            members = [
                (p, t)
                for p, t in zip(predictions, targets, strict=True)
                # Include 1.0 in the final bin rather than dropping it.
                if (lower <= p < upper) or (index == bin_count - 1 and p == 1.0)
            ]
            if not members:
                continue
            mean_predicted = sum(p for p, _ in members) / len(members)
            observed = sum(t for _, t in members) / len(members)
            reliability = ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_predicted=round(mean_predicted, 4),
                observed_accuracy=round(observed, 4),
            )
            bins.append(reliability)
            weighted_gap += (len(members) / n) * reliability.gap
            max_gap = max(max_gap, reliability.gap)

        brier = sum((p - t) ** 2 for p, t in zip(predictions, targets, strict=True)) / n

        return CalibrationReport(
            sample_size=n,
            expected_calibration_error=round(weighted_gap, 4),
            max_calibration_error=round(max_gap, 4),
            brier_score=round(brier, 4),
            base_rate=round(sum(targets) / n, 4),
            bins=bins,
        )

    # ------------------------------------------------------------------ persistence

    def save(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(
                {
                    "bias": self._bias,
                    "weights": self._weights,
                    "feature_order": list(self._feature_order),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | str) -> Calibrator:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            weights=payload["weights"],
            bias=payload["bias"],
            feature_order=tuple(payload.get("feature_order", ConfidenceFeatures.FEATURE_ORDER)),
        )
