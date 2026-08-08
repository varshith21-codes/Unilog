"""Held-out validation for induced part-number grammars (blueprint Tier 3, item 19).

Induction always succeeds. Point it at any catalogue and rules come back, and every one of them
fits the data it was induced from perfectly — that is what induction *is*. So the rule count says
nothing at all about whether the grammar works, and a report that led with it would be measuring
its own training set.

This module asks the only question that matters: **hide a part number, induce the grammar without
it, then see whether the grammar can reconstruct that part's attributes.** Leave-one-out, so
every SKU in the corpus takes a turn as the held-out case and no split has to be argued for on a
corpus this size.

Scored through :class:`axiom.evaluation.metrics.MetricSet`, the same five-outcome scorer the
extraction backtest uses. That is deliberate and it is not merely tidy: a grammar that abstains
and a grammar that invents must be separated here exactly as they are for a model, or the cheapest
attribute source in the system would be the one held to the loosest standard.

Two properties of the result are worth reading before the headline number:

*   **Citation coverage is zero, by construction.** A grammar value has no evidence span, because
    a part number is not a document. That is why the method sits in the inference family and why
    every value it emits is queued rather than published — this harness measures whether the
    grammar is *right*, not whether it is publishable, and those are different questions.
*   **Recall is bounded by what a part number can possibly carry.** Carton quantity and country of
    origin are not encoded in `BA-100-075` and never will be, so they are counted as misses.
    Reporting recall against every scored attribute rather than against a hand-picked subset is
    what stops the number being cosmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.core.values import AttributeValue, DerivationMethod
from axiom.evaluation.golden import GoldenSet
from axiom.evaluation.metrics import Comparison, MetricSet, compare_value
from axiom.extract.grammar import Observation, PartNumberGrammar, induce, shape
from axiom.normalize import normalize_value
from axiom.schema import SchemaRegistry

DEFAULT_MIN_SUPPORT = 2


def observations_from_golden(
    golden: GoldenSet, registry: SchemaRegistry
) -> tuple[list[Observation], list[str]]:
    """Normalise a golden set into canonical observations for induction.

    Ground truth is stored in source form — the string a human reads off the datasheet — so it
    goes through the same normaliser the extractor's output does. Inducing rules against `'3/4"'`
    rather than 19.05 mm would produce a grammar that could never agree with a pipeline value.

    Returns the observations and any per-product failures, rather than raising: one malformed
    ground-truth row should not void a whole run.
    """
    observations: list[Observation] = []
    failures: list[str] = []

    for product in golden.products:
        values: dict[str, object] = {}
        for code, raw in product.attributes.items():
            try:
                definition = registry.attribute(code)
            except KeyError:
                failures.append(
                    f"{product.sku}: golden set names attribute '{code}', which the schema "
                    f"does not define"
                )
                continue
            canonical = _canonical(raw, code, definition)
            if canonical is None:
                failures.append(
                    f"{product.sku}: golden value {raw!r} for '{code}' could not be normalised"
                )
                continue
            values[code] = canonical
        observations.append(
            Observation(
                sku=product.sku,
                values=values,
                class_code=product.class_code,
                supplier_id=product.supplier_id,
            )
        )

    return observations, failures


@dataclass
class Fold:
    """One held-out SKU and what the grammar induced without it managed to say."""

    sku: str
    shape: str
    matched: bool
    """False when the training set contained no other part number of this shape, so no grammar
    for it could be induced at all."""

    training_size: int
    shape_siblings: int
    """How many other observations shared this SKU's shape in the training set."""

    predicted: int = 0
    generalising_predictions: int = 0
    conflicts: int = 0
    comparisons: list[Comparison] = field(default_factory=list)

    @property
    def metrics(self) -> MetricSet:
        return MetricSet(self.comparisons)

    def to_dict(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "shape": self.shape,
            "matched": self.matched,
            "shape_siblings": self.shape_siblings,
            "predicted": self.predicted,
            "generalising_predictions": self.generalising_predictions,
            "conflicts": self.conflicts,
            **self.metrics.summary(),
        }


@dataclass
class GrammarResult:
    """Everything one held-out validation run produced."""

    golden_set: str
    min_support: int
    grammar: PartNumberGrammar
    """Induced on the *full* corpus. Reported so the rule inventory can be read, but never
    scored — the folds are what produced the metrics."""

    metrics: MetricSet = field(default_factory=MetricSet)
    folds: list[Fold] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def arm(self) -> str:
        return "guarded" if self.min_support >= DEFAULT_MIN_SUPPORT else "unguarded"

    @property
    def folds_without_grammar(self) -> list[Fold]:
        """Folds where the held-out SKU's shape had no sibling left to learn from."""
        return [fold for fold in self.folds if not fold.matched]

    @property
    def rules_fired(self) -> int:
        """Distinct (shape, attribute) pairs that produced a value on at least one held-out SKU.

        The honest counterweight to the rule count. A rule induced but never fired on unseen
        data has demonstrated nothing, and this is the number that separates the two.
        """
        fired: set[tuple[str, str]] = set()
        for fold in self.folds:
            for comparison in fold.comparisons:
                if comparison.actual is not None:
                    fired.add((fold.shape, comparison.attribute_code))
        return len(fired)

    @property
    def covered_pairs(self) -> int:
        """Distinct (shape, attribute) pairs the full-corpus grammar has any rule for.

        Lower than the rule count, because several segment positions routinely imply the same
        attribute — in this corpus the alpha prefix and the family number co-vary perfectly, so
        the series is identified twice. Both rules are kept rather than one being picked
        arbitrarily, and this is the number to compare against ``rules_fired``.
        """
        return len({(rule.shape, rule.attribute_code) for rule in self.grammar.rules})

    def by_shape(self) -> dict[str, MetricSet]:
        grouped: dict[str, MetricSet] = {}
        for fold in self.folds:
            grouped.setdefault(fold.shape, MetricSet()).comparisons.extend(fold.comparisons)
        return grouped

    def by_attribute(self) -> dict[str, MetricSet]:
        return self.metrics.by_attribute()

    def recovered_attributes(self) -> list[tuple[str, float, int]]:
        """(code, recall, n) for attributes the grammar ever recovered, best first."""
        ranked = [
            (code, metrics.recall, metrics.expected_present)
            for code, metrics in self.by_attribute().items()
            if metrics.correct > 0
        ]
        return sorted(ranked, key=lambda row: (-row[1], row[0]))

    def summary(self) -> dict[str, object]:
        grammar = self.grammar.summary()
        return {
            "golden_set": self.golden_set,
            "arm": self.arm,
            "min_support": self.min_support,
            "observations": grammar["observations"],
            "folds": len(self.folds),
            "folds_without_grammar": len(self.folds_without_grammar),
            "shapes": grammar["shapes"],
            "rules": grammar["rules"],
            "generalising_rules": grammar["generalising_rules"],
            "rules_by_kind": grammar["rules_by_kind"],
            "covered_pairs": self.covered_pairs,
            "rules_fired": self.rules_fired,
            "rejected": grammar["rejected"],
            "rejected_by_reason": grammar["rejected_by_reason"],
            "comparisons": self.metrics.total,
            **self.metrics.summary(),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "attributes_covered": self.grammar.attribute_codes(),
            "rule_detail": [rule.to_dict() for rule in self.grammar.rules],
            "rejected_detail": [rule.to_dict() for rule in self.grammar.rejected],
            "per_shape": {
                signature: metrics.summary()
                for signature, metrics in sorted(self.by_shape().items())
            },
            "per_attribute": {
                code: metrics.summary() for code, metrics in sorted(self.by_attribute().items())
            },
            "folds_detail": [fold.to_dict() for fold in self.folds],
            "failures": list(self.failures),
        }


def run_held_out(
    golden: GoldenSet,
    registry: SchemaRegistry,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> GrammarResult:
    """Leave-one-out validation of grammar induction over a golden set.

    No model is called and no document is read. The corpus supplies the part numbers and the
    ground truth; everything in between is arithmetic and lookup, which is exactly why this
    result is reproducible bit-for-bit rather than drifting run to run the way extraction does.
    """
    observations, failures = observations_from_golden(golden, registry)
    by_sku = {observation.sku: observation for observation in observations}

    result = GrammarResult(
        golden_set=golden.name,
        min_support=min_support,
        grammar=induce(observations, registry, min_support=min_support),
        failures=failures,
    )

    for product in golden.products:
        held_out = by_sku.get(product.sku)
        if held_out is None:
            continue

        training = [o for o in observations if o.sku != product.sku]
        signature = shape(product.sku)
        siblings = sum(1 for o in training if o.shape == signature)

        try:
            grammar = induce(training, registry, min_support=min_support)
            reading = grammar.predict(product.sku)
        except Exception as exc:  # noqa: BLE001 - one fold must not end the run
            result.failures.append(f"{product.sku}: induction failed: {exc}")
            continue

        fold = Fold(
            sku=product.sku,
            shape=signature,
            matched=reading.matched,
            training_size=len(training),
            shape_siblings=siblings,
            predicted=len(reading.predictions),
            generalising_predictions=sum(
                1 for p in reading.predictions.values() if p.generalises
            ),
            conflicts=len(reading.conflicts),
        )

        for code in sorted(product.covered_codes):
            try:
                definition = registry.attribute(code)
            except KeyError:
                continue
            expected = _expected(product, code, definition, result)
            prediction = reading.predictions.get(code)
            fold.comparisons.append(
                compare_value(
                    product.sku,
                    definition,
                    expected,
                    prediction.value_canonical if prediction else None,
                    confidence=prediction.confidence if prediction else None,
                    # A grammar value never carries a citation. Reported rather than defaulted
                    # to True, so citation coverage tells the truth about this source.
                    had_verified_citation=False,
                )
            )

        result.folds.append(fold)
        result.metrics.comparisons.extend(fold.comparisons)

    return result


def _expected(
    product, code: str, definition, result: GrammarResult
) -> object | None:
    raw = product.expected_raw(code)
    if raw is None:
        return None
    canonical = _canonical(raw, code, definition)
    if canonical is None:
        result.failures.append(
            f"{product.sku}: golden value {raw!r} for '{code}' could not be normalised"
        )
    return canonical


def _canonical(raw: str, code: str, definition) -> object | None:
    """Normalise a source-form ground-truth string into its canonical value."""
    placeholder = AttributeValue(
        attribute_code=code,
        value_raw=raw,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
    )
    outcome = normalize_value(placeholder, definition)
    if not outcome.normalized or outcome.value.value_canonical is None:
        return None
    return outcome.value.value_canonical


def format_grammar_report(result: GrammarResult, *, show_rules: bool = True) -> str:
    """Human-readable held-out validation report.

    Named distinctly rather than ``format_report`` because the backtest already exports that from
    the same package, and ``axiom.evaluation.cohort`` set the precedent with ``format_study``.
    """
    metrics = result.metrics
    grammar = result.grammar
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"PART-NUMBER GRAMMAR — held-out validation — {result.golden_set}")
    add("=" * 78)
    add(
        f"  {grammar.observations} part numbers | {len(result.folds)} leave-one-out folds | "
        f"{metrics.total} comparisons | 0 model calls | $0.00"
    )
    add(f"  shapes: {_shapes(grammar.shapes)}")

    add("")
    add("  INDUCED ON THE FULL CORPUS (not scored — the folds below are)")
    add(f"    rules                    {len(grammar.rules):>4}")
    for kind, count in sorted(grammar.summary()["rules_by_kind"].items()):
        add(f"      {kind:<22} {count:>4}")
    add(
        f"    generalising             {len(grammar.generalising_rules):>4}"
        f"   <- can predict an unseen token"
    )
    add(f"    (shape, attribute) pairs {result.covered_pairs:>4}")
    add(f"    fired on held-out data   {result.rules_fired:>4}")

    add("")
    add("  REFUSED AT INDUCTION")
    for reason, count in sorted(grammar.summary()["rejected_by_reason"].items()):
        add(f"    {reason:<24} {count:>4}   {_reason_note(reason)}")

    add("")
    add("  HELD-OUT OUTCOMES")
    add(f"    correct              {metrics.correct:>4}")
    add(f"    wrong value          {metrics.wrong:>4}   <- the dangerous failure")
    add(f"    missed               {metrics.missed:>4}   <- not carried by the part number")
    add(f"    correctly abstained  {metrics.correctly_abstained:>4}")
    add(f"    hallucinated         {metrics.hallucinated:>4}   <- must be zero")

    add("")
    add("  HELD-OUT METRICS")
    add(f"    precision              {metrics.precision:.1%}")
    add(f"    recall                 {metrics.recall:.1%}")
    add(f"    F1                     {metrics.f1:.1%}")
    add(f"    exact-match share      {metrics.exact_match_rate:.1%}")
    add(f"    abstention correctness {metrics.abstention_correctness:.1%}")
    add(f"    hallucination rate     {metrics.hallucination_rate:.1%}")
    add(
        f"    citation coverage      {metrics.citation_coverage:.1%}"
        f"   <- zero by construction; a part number is not a document"
    )

    per_shape = result.by_shape()
    if per_shape:
        add("")
        add("  BY PART-NUMBER SHAPE")
        add(f"    {'shape':<10} {'n':>3} {'precision':>10} {'recall':>8}  note")
        for signature, shape_metrics in sorted(per_shape.items()):
            folds = [f for f in result.folds if f.shape == signature]
            note = ""
            if all(not f.matched for f in folds):
                note = "no sibling left when held out — nothing inducible"
            add(
                f"    {signature:<10} {len(folds):>3} {shape_metrics.precision:>9.1%} "
                f"{shape_metrics.recall:>7.1%}  {note}"
            )

    recovered = result.recovered_attributes()
    if recovered:
        add("")
        add("  ATTRIBUTES THE PART NUMBER CARRIES")
        for code, recall, n in recovered:
            add(f"    {code:<26} {recall:>6.1%}  (n={n})")

    unrecovered = sorted(
        code
        for code, attribute_metrics in result.by_attribute().items()
        if attribute_metrics.correct == 0 and attribute_metrics.expected_present > 0
    )
    if unrecovered:
        add("")
        add("  NEVER RECOVERED (correctly — these are not encoded in a part number)")
        for line in _wrap(unrecovered, width=64):
            add(f"    {line}")

    without = result.folds_without_grammar
    if without:
        add("")
        add("  FOLDS WITH NO INDUCIBLE GRAMMAR")
        for fold in without:
            add(
                f"    {fold.sku:<12} shape {fold.shape} had {fold.shape_siblings} sibling(s) "
                f"in training"
            )
        add(
            "    A shape seen once cannot be learned from itself. Reported rather than dropped, "
            "because averaging it away would overstate coverage."
        )

    if show_rules and grammar.rules:
        add("")
        add("  RULE INVENTORY (full corpus)")
        for rule in sorted(
            grammar.rules, key=lambda r: (not r.generalises, r.shape, r.attribute_code, r.position)
        ):
            mark = "*" if rule.generalises else " "
            add(
                f"   {mark} [{rule.kind.value:<8}] {rule.describe()}"
                f"  (support {rule.support}, corroboration {rule.corroboration})"
            )
        add("    * generalises to a token never seen during induction")

    if result.failures:
        add("")
        add("  FAILURES")
        for failure in result.failures:
            add(f"    {failure}")

    return "\n".join(lines)


def format_comparison(guarded: GrammarResult, unguarded: GrammarResult) -> str:
    """Contrast the corroboration guard against no guard at all.

    The guard's cost and benefit are not the same kind of quantity, which is the point of
    reporting them side by side. Dropping it does not make the grammar *wrong* on this corpus —
    an uncorroborated rule keys on a token the held-out SKU does not have, so it abstains anyway.
    What it does is let the grammar *claim* rules that have never predicted anything, and a
    coverage figure built from those would be fiction.
    """
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("CORROBORATION GUARD — min_support 2 vs 1")
    add("=" * 78)
    add(f"    {'':<26} {'guarded':>10} {'unguarded':>11} {'delta':>8}")

    rows = [
        ("rules claimed", len(guarded.grammar.rules), len(unguarded.grammar.rules)),
        ("rules fired held-out", guarded.rules_fired, unguarded.rules_fired),
        ("correct", guarded.metrics.correct, unguarded.metrics.correct),
        ("wrong value", guarded.metrics.wrong, unguarded.metrics.wrong),
        ("hallucinated", guarded.metrics.hallucinated, unguarded.metrics.hallucinated),
    ]
    for label, left, right in rows:
        add(f"    {label:<26} {left:>10} {right:>11} {right - left:>+8}")

    for label, left, right in [
        ("precision", guarded.metrics.precision, unguarded.metrics.precision),
        ("recall", guarded.metrics.recall, unguarded.metrics.recall),
    ]:
        add(f"    {label:<26} {left:>9.1%} {right:>10.1%} {(right - left) * 100:>+7.1f} pts")

    claimed = len(unguarded.grammar.rules) - len(guarded.grammar.rules)
    add("")
    add(
        f"    The guard refuses {claimed} rule(s) whose every key was seen exactly once. On "
        f"held-out\n    data those rules abstain rather than err, so accuracy barely moves — "
        f"the guard buys an\n    honest coverage claim, not a higher score. Reported this way "
        f"because the reverse\n    framing would be the easy one to oversell."
    )

    return "\n".join(lines)


def _shapes(shapes: dict[str, int]) -> str:
    return ", ".join(f"{signature} x{count}" for signature, count in sorted(shapes.items()))


def _reason_note(reason: str) -> str:
    return {
        "compliance_claim": "a legal claim is never derivable from a part number",
        "not_a_function": "one token, two different values",
        "uncorroborated": "every key seen once — fits perfectly, predicts nothing",
        "too_few_observations": "not enough examples to test anything",
    }.get(reason, "")


def _wrap(items: list[str], *, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = f"{current}, {item}" if current else item
        if len(candidate) > width and current:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


__all__ = [
    "DEFAULT_MIN_SUPPORT",
    "Fold",
    "GrammarResult",
    "format_comparison",
    "format_grammar_report",
    "observations_from_golden",
    "run_held_out",
]
