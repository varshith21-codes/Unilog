"""The regression gate: block a change that makes the measured numbers worse.

Module M15. The blueprint rates this above an extra feature, and it is right to: every accuracy
claim in this repository is one careless prompt edit away from being false, and nothing else in
the system would notice. A test suite proves the code does what it says. This proves the *model
pipeline* still performs as well as it did, which no unit test can.

Three decisions shape it.

**Rates are compared, counts are not — unless the corpus is identical.** A golden set that grows
from 15 SKUs to 40 legitimately changes every count, so comparing ``correct: 222`` across
different corpora would fail on an improvement. Counts are therefore only checked when the
comparison total matches exactly, and a different golden set is a hard stop that demands a
rebaseline rather than a silent pass.

**Tolerances are derived from measured variance, not chosen for comfort.** The README records
recall moving between 96.5% and 97.8% across two runs of identical code, as the model resolved
``handle_type`` differently. A zero-tolerance recall gate would fail half the time on no change at
all, and a gate that cries wolf gets disabled — which is strictly worse than no gate. So recall
carries a two-point band that covers the observed drift, while precision and citation coverage,
which have been stable, carry almost none.

**Some bounds are absolute and need no baseline.** A hallucination is unacceptable at any rate,
so it is checked against zero rather than against "no worse than last time". Making it relative
would let one fabrication become the new normal, and then two.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class GateVerdict(str, Enum):
    PASS = "pass"
    REGRESSED = "regressed"
    IMPROVED = "improved"
    NOT_COMPARABLE = "not_comparable"
    """The metric is absent from one side, or the corpora differ. Never silently a pass."""

    @property
    def blocks(self) -> bool:
        return self in {GateVerdict.REGRESSED, GateVerdict.NOT_COMPARABLE}


@dataclass(frozen=True)
class MetricGuard:
    """One tracked metric, and how much it is allowed to move."""

    metric: str
    direction: Direction
    tolerance: float
    rationale: str
    absolute_bound: float | None = None
    """A fixed limit checked without reference to the baseline. Set only where "no worse than
    last time" is the wrong standard."""

    is_count: bool = False
    """Counts scale with the corpus, so they are only compared when the corpus is identical."""


# The tracked set. Deliberately small: a gate on thirty metrics is a gate nobody reads, and each
# entry here has to justify why its movement means something.
TRACKED: tuple[MetricGuard, ...] = (
    MetricGuard(
        "hallucinated",
        Direction.LOWER_IS_BETTER,
        tolerance=0.0,
        absolute_bound=0.0,
        is_count=True,
        rationale=(
            "a value invented where the source has none. Checked against zero rather than "
            "against the baseline, because a relative gate would let the first fabrication "
            "become the new normal"
        ),
    ),
    MetricGuard(
        "hallucination_rate",
        Direction.LOWER_IS_BETTER,
        tolerance=0.0,
        absolute_bound=0.0,
        rationale="the corpus-independent form of the same invariant",
    ),
    MetricGuard(
        "precision",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.01,
        rationale=(
            "of the values produced, how many were right. Measured stable at 99% or better "
            "across runs, so a one-point band is generous"
        ),
    ),
    MetricGuard(
        "recall",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.02,
        rationale=(
            "run-to-run drift of 1.3 points is documented on this corpus, driven by handle_type. "
            "Two points covers it without hiding a real loss"
        ),
    ),
    MetricGuard(
        "f1",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.015,
        rationale="between the precision and recall bands, since it is their harmonic mean",
    ),
    MetricGuard(
        "abstention_correctness",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.0,
        rationale=(
            "the mirror of the hallucination count: it can only fall if something was invented, "
            "and that is already a zero-tolerance failure"
        ),
    ),
    MetricGuard(
        "citation_coverage",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.0,
        rationale=(
            "enforced structurally by the evidence contract, which discards a value whose quote "
            "cannot be located. A drop here means the contract stopped working, not that the "
            "model had an off day"
        ),
    ),
    MetricGuard(
        "exact_match_rate",
        Direction.HIGHER_IS_BETTER,
        tolerance=0.05,
        rationale=(
            "guards against tolerance matches quietly replacing exact ones, which would hold "
            "precision flat while the values got looser"
        ),
    ),
    MetricGuard(
        "wrong_value",
        Direction.LOWER_IS_BETTER,
        tolerance=0.0,
        is_count=True,
        rationale=(
            "the dangerous failure — a wrong number is acted on, where a missing one is not. "
            "Compared only when the corpus is identical"
        ),
    ),
)


@dataclass(frozen=True)
class MetricComparison:
    """One metric's verdict."""

    guard: MetricGuard
    baseline: float | None
    candidate: float | None
    verdict: GateVerdict
    reason: str

    @property
    def delta(self) -> float | None:
        if self.baseline is None or self.candidate is None:
            return None
        return self.candidate - self.baseline

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.guard.metric,
            "direction": self.guard.direction.value,
            "tolerance": self.guard.tolerance,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": None if self.delta is None else round(self.delta, 6),
            "verdict": self.verdict.value,
            "reason": self.reason,
        }


@dataclass
class RegressionReport:
    """The gate's decision, and the evidence for it."""

    comparisons: list[MetricComparison] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)
    """Reasons the two runs cannot be compared at all, e.g. a different golden set."""

    baseline_meta: dict[str, object] = field(default_factory=dict)
    candidate_meta: dict[str, object] = field(default_factory=dict)
    counts_compared: bool = True

    @property
    def regressed(self) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.verdict is GateVerdict.REGRESSED]

    @property
    def improved(self) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.verdict is GateVerdict.IMPROVED]

    @property
    def not_comparable(self) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.verdict is GateVerdict.NOT_COMPARABLE]

    @property
    def passed(self) -> bool:
        """Whether the change may proceed.

        A metric that could not be compared blocks, exactly like one that regressed. The whole
        value of a gate is that it cannot be defeated by removing a number from the report.
        """
        return not self.fatal and not any(c.verdict.blocks for c in self.comparisons)

    def summary(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checked": len(self.comparisons),
            "regressed": len(self.regressed),
            "improved": len(self.improved),
            "not_comparable": len(self.not_comparable),
            "counts_compared": self.counts_compared,
            "fatal": list(self.fatal),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "baseline": self.baseline_meta,
            "candidate": self.candidate_meta,
            "metrics": [c.to_dict() for c in self.comparisons],
        }


def _number(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _meta(payload: Mapping[str, object]) -> dict[str, object]:
    """The fields that identify *which* run produced a report."""
    return {
        key: payload[key]
        for key in (
            "golden_set",
            "arm",
            "products",
            "comparisons",
            "prompt_version",
            "schema_version",
            "model_ids",
            "measured_at",
            "git_sha",
        )
        if key in payload
    }


def check_regression(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    guards: tuple[MetricGuard, ...] = TRACKED,
) -> RegressionReport:
    """Compare a fresh backtest summary against the committed baseline.

    Both arguments are ``BacktestResult.summary()`` shaped. Returning a report rather than
    raising lets a caller print every finding at once — a gate that stops at the first failure
    makes a contributor iterate through them one CI run at a time.
    """
    report = RegressionReport(
        baseline_meta=_meta(baseline), candidate_meta=_meta(candidate)
    )

    # A different corpus invalidates the comparison outright. Passing here would be the single
    # easiest way to sneak a regression through: swap the golden set, and every count moves for
    # a reason the gate cannot distinguish from a code change.
    base_set, cand_set = baseline.get("golden_set"), candidate.get("golden_set")
    if base_set != cand_set:
        report.fatal.append(
            f"golden set changed ({base_set!r} -> {cand_set!r}); these runs are not comparable. "
            f"Re-measure the baseline on the new corpus before judging a change against it."
        )

    # The ablation arm is a control group. Scoring it as though it were a real run would report
    # the deliberately-worse arm as a regression on every single run.
    base_arm, cand_arm = baseline.get("arm"), candidate.get("arm")
    if base_arm != cand_arm:
        report.fatal.append(
            f"arm changed ({base_arm!r} -> {cand_arm!r}); the ablation arm is a control group "
            f"and must not be compared against a treatment baseline."
        )

    base_n, cand_n = baseline.get("comparisons"), candidate.get("comparisons")
    report.counts_compared = base_n == cand_n

    for guard in guards:
        report.comparisons.append(_compare(guard, baseline, candidate, report.counts_compared))

    return report


def _compare(
    guard: MetricGuard,
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    counts_compared: bool,
) -> MetricComparison:
    base = _number(baseline, guard.metric)
    cand = _number(candidate, guard.metric)

    if cand is None:
        return MetricComparison(
            guard,
            base,
            None,
            GateVerdict.NOT_COMPARABLE,
            f"{guard.metric!r} is missing from the candidate report, so it cannot be checked. "
            f"A gate that treats an absent metric as a pass can be defeated by deleting it.",
        )

    # An absolute bound is checked first and needs no baseline, which is what makes it useful on
    # a new corpus where nothing else can be compared.
    if guard.absolute_bound is not None:
        over = (
            cand > guard.absolute_bound
            if guard.direction is Direction.LOWER_IS_BETTER
            else cand < guard.absolute_bound
        )
        if over:
            return MetricComparison(
                guard,
                base,
                cand,
                GateVerdict.REGRESSED,
                f"{guard.metric} is {cand:g}, outside the absolute bound of "
                f"{guard.absolute_bound:g}. {guard.rationale}",
            )

    if guard.is_count and not counts_compared:
        return MetricComparison(
            guard,
            base,
            cand,
            GateVerdict.PASS,
            f"{guard.metric} is a count and the corpus size changed, so it was not compared. "
            f"Its rate-based equivalent still was.",
        )

    if base is None:
        return MetricComparison(
            guard,
            None,
            cand,
            GateVerdict.NOT_COMPARABLE,
            f"{guard.metric!r} is missing from the baseline. Record it before gating on it.",
        )

    if guard.direction is Direction.HIGHER_IS_BETTER:
        shortfall = base - cand
        if shortfall > guard.tolerance:
            return MetricComparison(
                guard,
                base,
                cand,
                GateVerdict.REGRESSED,
                f"{guard.metric} fell {shortfall:.4g} below the baseline of {base:g}, past the "
                f"{guard.tolerance:g} tolerance. {guard.rationale}",
            )
        improved = cand > base + guard.tolerance
    else:
        excess = cand - base
        if excess > guard.tolerance:
            return MetricComparison(
                guard,
                base,
                cand,
                GateVerdict.REGRESSED,
                f"{guard.metric} rose {excess:.4g} above the baseline of {base:g}, past the "
                f"{guard.tolerance:g} tolerance. {guard.rationale}",
            )
        improved = cand < base - guard.tolerance

    if improved:
        return MetricComparison(
            guard,
            base,
            cand,
            GateVerdict.IMPROVED,
            f"{guard.metric} moved from {base:g} to {cand:g}. Update the baseline to hold the "
            f"gain, or it will be given back silently.",
        )

    return MetricComparison(
        guard, base, cand, GateVerdict.PASS, f"{guard.metric} is within tolerance of {base:g}"
    )


def format_regression_report(report: RegressionReport) -> str:
    """Render the gate's decision for a CI log."""
    lines = ["=" * 78, "REGRESSION GATE", "=" * 78]

    base, cand = report.baseline_meta, report.candidate_meta
    lines.append(
        f"  golden set   {base.get('golden_set', '?')} "
        f"({cand.get('comparisons', '?')} comparisons, {cand.get('products', '?')} products)"
    )
    if base.get("measured_at"):
        lines.append(f"  baseline     measured {base['measured_at']}")
    if cand.get("prompt_version") or base.get("prompt_version"):
        lines.append(
            f"  prompt       {base.get('prompt_version', '?')} -> "
            f"{cand.get('prompt_version', '?')}"
        )
    if not report.counts_compared:
        lines.append(
            "  NOTE         corpus size changed; count metrics were skipped and only rates "
            "were compared"
        )

    for reason in report.fatal:
        lines += ["", f"  CANNOT COMPARE: {reason}"]

    lines += [
        "",
        f"  {'metric':<24} {'baseline':>10} {'candidate':>10} {'delta':>10}  verdict",
        "  " + "-" * 74,
    ]
    for comparison in report.comparisons:
        base_text = "—" if comparison.baseline is None else f"{comparison.baseline:.4g}"
        cand_text = "—" if comparison.candidate is None else f"{comparison.candidate:.4g}"
        delta = comparison.delta
        delta_text = "—" if delta is None else f"{delta:+.4g}"
        mark = {
            GateVerdict.PASS: "ok",
            GateVerdict.IMPROVED: "IMPROVED",
            GateVerdict.REGRESSED: "REGRESSED",
            GateVerdict.NOT_COMPARABLE: "NOT COMPARABLE",
        }[comparison.verdict]
        lines.append(
            f"  {comparison.guard.metric:<24} {base_text:>10} {cand_text:>10} "
            f"{delta_text:>10}  {mark}"
        )

    blocking = [c for c in report.comparisons if c.verdict.blocks]
    if blocking:
        lines += ["", "  WHY THIS FAILED:"]
        for comparison in blocking:
            lines.append(f"    {comparison.guard.metric}: {comparison.reason}")

    if report.improved:
        lines += ["", "  IMPROVED — rebaseline to hold these:"]
        for comparison in report.improved:
            lines.append(f"    {comparison.guard.metric}: {comparison.reason}")

    lines += ["", "  PASS" if report.passed else "  FAIL", ""]
    return "\n".join(lines)
