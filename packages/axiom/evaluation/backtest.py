"""The masked-attribute backtest harness.

Hide values that are known to be correct, run the pipeline against the source documents alone,
and measure what comes back. That produces three things nothing else can:

1. **Real accuracy numbers** on the actual domain, rather than a vendor claim.
2. **The calibration set** — (score, was_correct) pairs — which is what lets the risk machinery
   choose a threshold with a genuine guarantee instead of refusing to automate anything.
3. **Per-attribute difficulty priors**, which feed straight back into confidence estimation.

Note the direction of the dependency: auto-accept coverage is *downstream* of measurement. The
system cannot publish anything automatically until it has been measured, which is the correct
ordering and not an accident of implementation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from axiom.confidence import Calibrator, Priors, extract_features, select_threshold
from axiom.core.values import AttributeValue, DerivationMethod
from axiom.docintel import parse_artifact
from axiom.evaluation.golden import GoldenProduct, GoldenSet
from axiom.evaluation.metrics import Comparison, MetricSet, Outcome, compare_value
from axiom.extract import Extractor, ModelCascade, ModelClient, UsageLedger
from axiom.ingest import LocalArtifactStore, ingest_file
from axiom.normalize import normalize_value
from axiom.schema import SchemaRegistry
from axiom.schema.prompts import PROMPT_VERSION


@dataclass
class BacktestResult:
    """Everything one backtest run produced."""

    golden_set: str
    metrics: MetricSet = field(default_factory=MetricSet)
    usage: UsageLedger = field(default_factory=UsageLedger)
    duration_seconds: float = 0.0
    products_run: int = 0
    failures: list[str] = field(default_factory=list)
    enforce_evidence: bool = True
    """False marks an ablation run, where the evidence contract was not enforced. Recorded on
    the result so a control-group measurement can never be mistaken for a real one."""

    prompt_version: str | None = None
    model_ids: list[str] = field(default_factory=list)
    """What produced these numbers. The regression gate blocks a change that degrades a metric,
    and its report is only actionable if it can name the prompt and the models involved —
    "recall fell" is a bug report, "recall fell when the prompt went to v3" is a diagnosis."""

    @property
    def arm(self) -> str:
        return "axiom" if self.enforce_evidence else "no-evidence-contract"

    def per_attribute(self) -> dict[str, MetricSet]:
        return self.metrics.by_attribute()

    def per_sku(self) -> dict[str, MetricSet]:
        grouped: dict[str, MetricSet] = {}
        for comparison in self.metrics.comparisons:
            grouped.setdefault(comparison.sku, MetricSet()).comparisons.append(comparison)
        return grouped

    def calibration_set(self) -> dict[str, object]:
        """Serialisable (score, label) pairs for the risk policy."""
        scores, labels = self.metrics.calibration_pairs()
        return {
            "golden_set": self.golden_set,
            "generated_by": "axiom.evaluation.backtest",
            "sample_size": len(scores),
            "scores": scores,
            "labels": labels,
        }

    def priors(self) -> Priors:
        """Per-attribute accuracy, for the confidence estimator's difficulty prior."""
        priors = Priors()
        for comparison in self.metrics.comparisons:
            if comparison.actual is None:
                continue  # priors describe produced values, not abstentions
            priors.observe(
                comparison.attribute_code,
                "attribute",
                correct=comparison.outcome is Outcome.CORRECT,
            )
        return priors

    def hardest_attributes(self, limit: int = 5) -> list[tuple[str, float, int]]:
        """(code, accuracy, sample size), worst first. Drives where to spend effort next."""
        ranked = [
            (code, metrics.recall, metrics.expected_present)
            for code, metrics in self.per_attribute().items()
            if metrics.expected_present > 0
        ]
        return sorted(ranked, key=lambda row: (row[1], -row[2]))[:limit]

    def summary(self) -> dict[str, object]:
        scores, _ = self.metrics.calibration_pairs()
        return {
            "golden_set": self.golden_set,
            "arm": self.arm,
            "enforce_evidence": self.enforce_evidence,
            "products": self.products_run,
            "comparisons": self.metrics.total,
            "duration_seconds": round(self.duration_seconds, 2),
            "calibration_samples": len(scores),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "model_calls": self.usage.calls,
            "prompt_version": self.prompt_version,
            "model_ids": list(self.model_ids),
            **self.metrics.summary(),
        }


def run_backtest(
    golden: GoldenSet,
    registry: SchemaRegistry,
    client: ModelClient,
    cascade: ModelCascade,
    *,
    calibrator: Calibrator | None = None,
    priors: Priors | None = None,
    start_tier: str = "volume",
    store_root: Path | None = None,
    limit: int | None = None,
    enforce_evidence: bool = True,
) -> BacktestResult:
    """Run the full pipeline over a golden set and score the results.

    ``enforce_evidence=False`` runs the **ablation**: the same prompt against the same model and
    the same documents, but with the evidence contract not enforced, so values the model could
    not support with a locatable quote are kept instead of discarded. That is the control group
    — what a generic enrichment pipeline would publish — and comparing the two is the only way
    to state what the trust layer actually buys rather than asserting it.
    """
    calibrator = calibrator or Calibrator()
    priors = priors or Priors()
    store = LocalArtifactStore(store_root or Path("data/cache/artifacts"))

    result = BacktestResult(
        golden_set=golden.name,
        enforce_evidence=enforce_evidence,
        prompt_version=PROMPT_VERSION,
        # Every tier that could be reached from the starting one, because the cascade escalates
        # and the gate needs to know which models were in play, not just the first.
        model_ids=[cascade.model_for(tier) for tier in cascade.escalation_path(start_tier)],
    )
    started = time.perf_counter()

    # Documents are parsed once and reused across every SKU that cites them. A datasheet with
    # five ordering rows is one parse, not five — which is exactly the amortisation that makes
    # per-SKU document cost approach zero on catalogue-style sources.
    parsed_cache: dict[str, object] = {}

    products = golden.products[:limit] if limit else golden.products
    for product in products:
        try:
            parsed = _parsed_document(product, golden, store, parsed_cache)
        except Exception as exc:  # noqa: BLE001 - one bad source must not end the run
            result.failures.append(f"{product.sku}: could not parse source: {exc}")
            continue

        try:
            comparisons, usage = _score_product(
                product,
                parsed,
                registry,
                client,
                cascade,
                calibrator,
                priors,
                start_tier,
                enforce_evidence=enforce_evidence,
            )
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"{product.sku}: extraction failed: {exc}")
            continue

        result.metrics.comparisons.extend(comparisons)
        result.usage.merge(usage)
        result.products_run += 1

    result.duration_seconds = time.perf_counter() - started
    return result


def _parsed_document(
    product: GoldenProduct, golden: GoldenSet, store: LocalArtifactStore, cache: dict
):
    if product.source in cache:
        return cache[product.source]
    path = golden.document_for(product)
    artifact = ingest_file(path, store, supplier_id=product.supplier_id)
    parsed = parse_artifact(store.get(artifact.storage_uri), artifact.document)
    cache[product.source] = parsed
    return parsed


def _score_product(
    product: GoldenProduct,
    parsed,
    registry: SchemaRegistry,
    client: ModelClient,
    cascade: ModelCascade,
    calibrator: Calibrator,
    priors: Priors,
    start_tier: str,
    *,
    enforce_evidence: bool = True,
) -> tuple[list[Comparison], UsageLedger]:
    extractor = Extractor(
        registry,
        client,
        cascade,
        start_tier=start_tier,
        enforce_evidence=enforce_evidence,
    )
    extraction = extractor.extract(
        parsed,
        class_code=product.class_code,
        target_sku=product.sku,
        only_codes=product.covered_codes,
    )

    produced: dict[str, AttributeValue] = {}
    for value in extraction.values:
        definition = registry.attribute(value.attribute_code)
        outcome = normalize_value(value, definition)
        # A value that cannot be normalised has not been recovered in any usable form, so it is
        # treated as absent rather than counted as a near-miss.
        if outcome.normalized and outcome.value.value_canonical is not None:
            produced[value.attribute_code] = outcome.value

    comparisons: list[Comparison] = []
    for code in product.covered_codes:
        definition = registry.attribute(code)
        expected = _expected_canonical(product, code, definition)
        actual_value = produced.get(code)

        confidence = None
        verified = False
        if actual_value is not None:
            features = extract_features(
                actual_value, priors=priors, supplier_id=product.supplier_id
            )
            confidence = calibrator.predict(features)
            verified = actual_value.has_verified_evidence

        comparisons.append(
            compare_value(
                product.sku,
                definition,
                expected,
                actual_value.value_canonical if actual_value else None,
                confidence=confidence,
                had_verified_citation=verified,
            )
        )

    return comparisons, extraction.usage


def _expected_canonical(product: GoldenProduct, code: str, definition):
    """Normalise source-form ground truth into a canonical value for comparison."""
    raw = product.expected_raw(code)
    if raw is None:
        return None

    placeholder = AttributeValue(
        attribute_code=code,
        value_raw=raw,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
    )
    outcome = normalize_value(placeholder, definition)
    if not outcome.normalized or outcome.value.value_canonical is None:
        raise ValueError(
            f"golden set value {raw!r} for '{code}' on {product.sku} could not be normalised; "
            f"the ground truth itself is malformed"
        )
    return outcome.value.value_canonical


def write_calibration_artifacts(result: BacktestResult, directory: Path) -> dict[str, Path]:
    """Persist what the backtest produced for the confidence and policy layers.

    Refuses an ablation result. The calibration set governs what publishes without a human, and
    an ablation deliberately keeps values whose quotes could not be located — training the
    acceptance threshold on those would raise coverage by teaching the policy that unverifiable
    values are fine. That is the exact failure this system exists to prevent, so it is blocked
    here rather than left to the caller to remember.
    """
    if not result.enforce_evidence:
        raise ValueError(
            "refusing to write calibration artifacts from an ablation run "
            f"(arm={result.arm!r}): its scores describe a pipeline with the evidence contract "
            "disabled, and using them would calibrate auto-accept against unverifiable values"
        )

    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    calibration_path = directory / "calibration_set.json"
    calibration_path.write_text(
        json.dumps(result.calibration_set(), indent=2), encoding="utf-8"
    )
    written["calibration_set"] = calibration_path

    priors_path = directory / "priors.json"
    result.priors().save(priors_path)
    written["priors"] = priors_path

    return written


def format_report(
    result: BacktestResult, *, epsilons: tuple[float, ...] = (0.02, 0.05, 0.10)
) -> str:
    """Human-readable backtest report."""
    metrics = result.metrics
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"BACKTEST — {result.golden_set}")
    add("=" * 78)
    add(
        f"  {result.products_run} products | {metrics.total} comparisons | "
        f"{result.duration_seconds:.1f}s | {result.usage.calls} model calls"
    )
    add(f"  tokens {result.usage.input_tokens}/{result.usage.output_tokens}")

    add("")
    add("  OUTCOMES")
    add(f"    correct              {metrics.correct:>4}")
    add(f"    wrong value          {metrics.wrong:>4}   <- the dangerous failure")
    add(f"    missed               {metrics.missed:>4}")
    add(f"    correctly abstained  {metrics.correctly_abstained:>4}")
    add(f"    hallucinated         {metrics.hallucinated:>4}   <- must be zero")

    add("")
    add("  METRICS")
    add(f"    precision              {metrics.precision:.1%}")
    add(f"    recall                 {metrics.recall:.1%}")
    add(f"    F1                     {metrics.f1:.1%}")
    add(f"    exact-match share      {metrics.exact_match_rate:.1%}")
    add(f"    abstention correctness {metrics.abstention_correctness:.1%}")
    add(f"    hallucination rate     {metrics.hallucination_rate:.1%}")
    add(f"    citation coverage      {metrics.citation_coverage:.1%}")

    scores, labels = metrics.calibration_pairs()
    if scores:
        add("")
        add(f"  RISK-CONTROLLED AUTO-ACCEPT ({len(scores)} calibration samples)")
        add(f"    {'budget':>8} {'threshold':>10} {'coverage':>9} {'upper bound':>12}  status")
        for epsilon in epsilons:
            policy = select_threshold(scores, labels, epsilon=epsilon)
            status = "ok" if policy.achievable else "not achievable"
            threshold = f"{policy.threshold:.3f}" if policy.achievable else "-"
            add(
                f"    {epsilon:>8.0%} {threshold:>10} {policy.coverage:>8.1%} "
                f"{policy.error_upper_bound:>11.1%}  {status}"
            )

    hardest = result.hardest_attributes()
    if hardest:
        add("")
        add("  HARDEST ATTRIBUTES (drives where to spend effort next)")
        for code, accuracy, n in hardest:
            add(f"    {code:<26} {accuracy:>6.1%}  (n={n})")

    if result.failures:
        add("")
        add("  FAILURES")
        for failure in result.failures:
            add(f"    {failure}")

    return "\n".join(lines)
