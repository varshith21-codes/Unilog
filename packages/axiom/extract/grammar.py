"""Part-number grammar induction (blueprint Tier 3, item 19).

An industrial part number is not an opaque key. `BA-100-075` is a ball valve from the BA-100
series in a 3/4" size, and a merchandiser reads all three facts off the string without opening a
datasheet. This module learns to do the same from examples, rather than from a hand-written regex
per supplier — a distributor carries thousands of series, and nobody is going to write thousands
of regexes.

### Where the danger is

A grammar is the cheapest attribute source in the system: no model call, no document, no network.
It is also the least trustworthy, because it produces values by pattern rather than by reading
anything. So the design question is not "can rules be induced" — that part is easy, and a naive
implementation induces hundreds. It is **which induced rules are real**.

Two failure modes matter, and both are silent:

*   **The coincidental bijection.** Given four part numbers with four distinct size codes, "the
    third segment determines the size" is a perfect rule. So is "the third segment determines the
    carton quantity", and so is "the third segment determines the price". With one observation per
    key, every attribute is trivially a function of every varying segment. Such a rule fits the
    training data exactly and predicts nothing — and its held-out accuracy is not low, it is
    *undefined*, because the key is simply absent. It has to be refused at induction time, which
    is what ``min_support`` does.
*   **Memorisation dressed as a law.** `77C-103` is a 1/2" valve and `77C-104` is 3/4". That is a
    catalogue sequence, not an encoding: nothing about `104` says three quarters of an inch, and
    `77C-107` could never be predicted. Contrast `BA-100-075`, where `075` genuinely *is* 0.75
    inches in hundredths — a rule that extends to a code never seen. Both look identical in a
    lookup table, and only one of them generalises.

So a rule carries its kind and its corroboration, :attr:`SegmentRule.generalises` is a property
rather than a claim, and the held-out harness in :mod:`axiom.evaluation.grammar` measures the
difference instead of arguing about it.

### The three rule kinds

*   :attr:`RuleKind.CONSTANT` — the segment never varies within its shape group, and neither does
    the value. Every observed `77C-…` has a bronze body.
*   :attr:`RuleKind.LOOKUP` — a memorised token → value table. Predicts only tokens it has seen,
    and is admitted only when at least one key is corroborated by two independent observations.
*   :attr:`RuleKind.LINEAR` — the token, read as a number, scaled to the canonical magnitude. The
    only kind that can produce a value for an unseen token, and so the only one that deserves to
    be called an encoding rather than a record.

### What it will never do

Compliance attributes are refused outright. `lead_free_compliant` is a legal claim, this method
sits in `DerivationMethod`'s inference family, and the schema already marks every such attribute
``evidence_requirement: strict``. A part number is not a declaration of conformity no matter how
many examples agree, so the refusal is structural rather than a threshold somebody could tune.

The same reasoning is why every value emitted here is ``QUEUED_FOR_REVIEW``: `AttributeValue`
refuses to construct an inferred value as ``AUTO_ACCEPTED`` at all, so this cannot quietly become
an auto-publish path.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException
from enum import Enum
from typing import Any

from pydantic import BaseModel

from axiom.core import compare
from axiom.core.values import AttributeValue, DerivationMethod, Quantity, ValueStatus
from axiom.schema.models import AttributeDefinition, EvidenceRequirement
from axiom.schema.registry import SchemaRegistry

# Maximal runs of letters, digits, or anything else. Splitting on alpha/numeric transitions as
# well as on separators is what lets `77C` decompose into a numeric family and an alpha suffix
# without a per-supplier rule about where the boundary sits.
_TOKEN = re.compile(r"[A-Za-z]+|[0-9]+|[^A-Za-z0-9]+")

# Scale agreement for a LINEAR rule is tested on exact decimals, quantised to this many places.
#
# Float division would defeat the rule entirely. `nominal_size` declares `tolerance: 0.0`, so a
# predicted magnitude must match ground truth exactly, and `6.35 / 25 * 75` in binary floating
# point is 19.049999999999997 rather than 19.05 — a correct rule scored as a wrong value. Decimal
# keeps the 0.254 scale exact across every code in this corpus.
_SCALE_QUANTUM = Decimal("1.000000000000")

# A LINEAR rule needs enough distinct tokens to have actually been tested. One point through the
# origin defines a scale and cannot contradict it; two can agree by luck on a two-member series.
_MIN_LINEAR_TOKENS = 3

# Confidence ceiling for an inferred value. Deliberately below any threshold the risk policy has
# ever selected (the measured operating points are 0.685 and 0.719), so a grammar value cannot
# reach auto-accept even before `AttributeValue`'s own refusal to auto-accept inference. The
# number orders the review queue; it is not a calibrated probability and is not treated as one.
_CONFIDENCE_CEILING = 0.70


class RuleKind(str, Enum):
    """How a segment rule turns a token into a value."""

    CONSTANT = "constant"
    """The segment does not vary within its shape group, and neither does the value."""

    LOOKUP = "lookup"
    """A memorised token -> value table. Cannot predict a token it has never seen."""

    LINEAR = "linear"
    """The token read as a number and scaled. The only kind that extends to an unseen token."""

    @property
    def generalises(self) -> bool:
        """Whether this kind can produce a value for a token absent from the training set."""
        return self is RuleKind.LINEAR


class SegmentKind(str, Enum):
    ALPHA = "alpha"
    NUMERIC = "numeric"


class RejectionReason(str, Enum):
    """Why a candidate rule was not admitted.

    Recorded rather than discarded. "No rule was found" and "a rule was found and refused
    because it could not be told apart from a coincidence" are different statements about a
    catalogue, and only the second one tells a merchandiser their part numbers carry no
    recoverable structure at that position.
    """

    COMPLIANCE_CLAIM = "compliance_claim"
    """A legal claim. Never derivable from a part number, at any support."""

    TOO_FEW_OBSERVATIONS = "too_few_observations"
    """Fewer than two examples in this shape group state the attribute at all."""

    NOT_A_FUNCTION = "not_a_function"
    """One token maps to two different values, so the position cannot determine the attribute."""

    UNCORROBORATED = "uncorroborated"
    """Every token was seen exactly once — a bijection indistinguishable from coincidence."""


@dataclass(frozen=True)
class Segment:
    """One token of a part number, with the position it occupies."""

    position: int
    text: str
    kind: SegmentKind

    @property
    def as_int(self) -> int | None:
        return int(self.text) if self.kind is SegmentKind.NUMERIC else None


def segment(part_number: str) -> tuple[Segment, ...]:
    """Split a part number into positional alpha and numeric tokens.

    Separators are consumed rather than returned: they carry no attribute information on their
    own, so `BA-100-075` and `BA.100.075` index identically and a rule learned from one applies
    to the other. They do survive in :func:`shape`, which is what keeps a catalogue that writes
    `BA/100/075` in a different group from one that writes `BA-100-075` — the layouts are
    different conventions and their positions should not be pooled on the strength of a coincidence.

    **A run with no separator cannot be subdivided.** `BA100075` yields two tokens, not three,
    because nothing in the string says whether the size code is two digits or three. Recovering
    that needs a grammar already known for the series, which is the problem this module is trying
    to solve rather than one it can assume away. Unseparated catalogues are therefore a documented
    limitation: the shape groups still form, but a position spanning two concatenated fields will
    usually fail the functional test and be refused, which is the correct outcome.
    """
    segments: list[Segment] = []
    for match in _TOKEN.finditer(part_number.strip()):
        text = match.group()
        if text[0].isalpha():
            kind = SegmentKind.ALPHA
        elif text[0].isdigit():
            kind = SegmentKind.NUMERIC
        else:
            continue
        segments.append(Segment(len(segments), text, kind))
    return tuple(segments)


def shape(part_number: str) -> str:
    """A structural signature: `A` per alpha run, `N` per numeric run, separators verbatim.

    `BA-100-075` and `T-113-025` are both `A-N-N`, which is the point — they come from different
    manufacturers and share an encoding, so grouping by shape lets one rule be corroborated by
    two independent catalogues. `77C-105` is `NA-N` while `77C-105R` is `NA-NA`, so a reduced-port
    suffix separates into its own group instead of contaminating its full-port twin.
    """
    parts: list[str] = []
    for match in _TOKEN.finditer(part_number.strip()):
        text = match.group()
        if text[0].isalpha():
            parts.append("A")
        elif text[0].isdigit():
            parts.append("N")
        else:
            parts.append(text)
    return "".join(parts)


@dataclass(frozen=True)
class Observation:
    """One part number and the canonical values known to be true for it.

    ``values`` must already be canonical — the same form
    :attr:`axiom.core.values.AttributeValue.value_canonical` holds — because induction compares
    them through :func:`axiom.core.compare.agree` and a LINEAR rule divides into their magnitudes.
    Handing this source-form strings would induce rules against `'3/4"'` instead of 19.05 mm.
    """

    sku: str
    values: Mapping[str, Any]
    class_code: str | None = None
    supplier_id: str | None = None

    @property
    def segments(self) -> tuple[Segment, ...]:
        return segment(self.sku)

    @property
    def shape(self) -> str:
        return shape(self.sku)


@dataclass(frozen=True)
class SegmentRule:
    """One induced rule: at this position, in this shape, the token implies this value."""

    shape: str
    position: int
    attribute_code: str
    kind: RuleKind
    mapping: Mapping[str, Any] = field(default_factory=dict)
    scale: float | None = None
    unit: str | None = None
    support: int = 0
    """Observations the rule was induced from."""

    corroboration: int = 1
    """The most independent observations agreeing on any single token.

    This is the number that carries the argument, not ``support``. Nine observations spread over
    nine distinct tokens corroborate nothing; two observations agreeing that `025` means a
    quarter inch are evidence.
    """

    contributing_skus: tuple[str, ...] = ()

    @property
    def generalises(self) -> bool:
        return self.kind.generalises

    @property
    def confidence(self) -> float:
        """A queue-ordering score, not a calibrated probability.

        Capped well below every threshold the risk policy has selected, and inference cannot
        auto-accept regardless. A generalising rule outranks a memorised one at equal
        corroboration because it was tested against tokens it had to extrapolate to.
        """
        base = 0.55 if self.generalises else 0.45
        return round(min(_CONFIDENCE_CEILING, base + 0.03 * min(self.corroboration, 5)), 4)

    def predict(self, token: str) -> Any | None:
        """The value this rule implies for a token, or None if it does not apply."""
        if self.kind is RuleKind.LINEAR:
            if self.scale is None or not token.isdigit():
                return None
            magnitude = float(Decimal(str(self.scale)) * Decimal(int(token)))
            return Quantity(magnitude=magnitude, unit=self.unit) if self.unit else magnitude
        return self.mapping.get(token)

    def describe(self) -> str:
        """A one-line human reading of the rule."""
        where = f"{self.shape}[{self.position}]"
        if self.kind is RuleKind.LINEAR:
            unit = f" {self.unit}" if self.unit else ""
            return f"{where} as a number x {self.scale:g}{unit} -> {self.attribute_code}"
        if self.kind is RuleKind.CONSTANT:
            token, value = next(iter(sorted(self.mapping.items())))
            return f"{where} == {token!r} -> {self.attribute_code} = {_render(value)}"
        shown = ", ".join(f"{k}->{_render(v)}" for k, v in sorted(self.mapping.items())[:3])
        more = ", …" if len(self.mapping) > 3 else ""
        return f"{where} {{{shown}{more}}} -> {self.attribute_code}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "position": self.position,
            "attribute_code": self.attribute_code,
            "kind": self.kind.value,
            "generalises": self.generalises,
            "support": self.support,
            "corroboration": self.corroboration,
            "confidence": self.confidence,
            "scale": self.scale,
            "unit": self.unit,
            "mapping": {k: _render(v) for k, v in sorted(self.mapping.items())},
            "contributing_skus": list(self.contributing_skus),
            "description": self.describe(),
        }


@dataclass(frozen=True)
class RejectedRule:
    """A candidate that was considered and refused, with the reason."""

    shape: str
    attribute_code: str
    reason: RejectionReason
    position: int | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "position": self.position,
            "attribute_code": self.attribute_code,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Prediction:
    """A value the grammar is willing to propose for one attribute."""

    attribute_code: str
    value_canonical: Any
    rule: SegmentRule
    agreeing_rules: int = 1
    """How many independent positions implied this same value. More than one is corroboration
    from within the part number itself."""

    @property
    def generalises(self) -> bool:
        return self.rule.generalises

    @property
    def confidence(self) -> float:
        return self.rule.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_code": self.attribute_code,
            "value_canonical": _render(self.value_canonical),
            "confidence": self.confidence,
            "generalises": self.generalises,
            "agreeing_rules": self.agreeing_rules,
            "rule": self.rule.describe(),
            "rule_kind": self.rule.kind.value,
        }


@dataclass(frozen=True)
class Conflict:
    """Two rules implied different values for one attribute, so nothing is proposed."""

    attribute_code: str
    candidates: tuple[tuple[str, Any], ...]
    """(rule description, value) for each disagreeing rule."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_code": self.attribute_code,
            "candidates": [
                {"rule": rule, "value": _render(value)} for rule, value in self.candidates
            ],
        }


@dataclass(frozen=True)
class GrammarReading:
    """Everything the grammar concluded about one part number."""

    sku: str
    shape: str
    matched: bool
    """False when no rule was induced for this shape at all — an unrecognised catalogue."""

    predictions: Mapping[str, Prediction] = field(default_factory=dict)
    conflicts: tuple[Conflict, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "shape": self.shape,
            "matched": self.matched,
            "predictions": [p.to_dict() for _, p in sorted(self.predictions.items())],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


@dataclass(frozen=True)
class PartNumberGrammar:
    """A set of induced rules, grouped by part-number shape."""

    rules: tuple[SegmentRule, ...] = ()
    rejected: tuple[RejectedRule, ...] = ()
    tolerances: Mapping[str, float] = field(default_factory=dict)
    """Per-attribute comparison tolerance, captured at induction so :meth:`predict` needs no
    registry. Prediction must not depend on schema availability — the grammar is a serialisable
    artifact and has to behave identically wherever it is loaded."""

    observations: int = 0
    shapes: Mapping[str, int] = field(default_factory=dict)

    @property
    def generalising_rules(self) -> tuple[SegmentRule, ...]:
        return tuple(r for r in self.rules if r.generalises)

    def rules_for(self, shape_signature: str) -> tuple[SegmentRule, ...]:
        return tuple(r for r in self.rules if r.shape == shape_signature)

    def attribute_codes(self) -> list[str]:
        return sorted({r.attribute_code for r in self.rules})

    def predict(self, part_number: str) -> GrammarReading:
        """Apply the grammar to a part number.

        Where several positions imply the same attribute, they must agree. Disagreement produces
        a :class:`Conflict` and no value: two positions of one part number contradicting each
        other is exactly the case where guessing would be least defensible, and the grammar has
        no evidence with which to break the tie.
        """
        signature = shape(part_number)
        segments = segment(part_number)
        applicable = self.rules_for(signature)
        if not applicable:
            return GrammarReading(sku=part_number, shape=signature, matched=False)

        hits: dict[str, list[tuple[SegmentRule, Any]]] = defaultdict(list)
        for rule in applicable:
            if rule.position >= len(segments):
                continue
            value = rule.predict(segments[rule.position].text)
            if value is None:
                continue
            hits[rule.attribute_code].append((rule, value))

        predictions: dict[str, Prediction] = {}
        conflicts: list[Conflict] = []
        for code, candidates in sorted(hits.items()):
            tolerance = self.tolerances.get(code, 0.0)
            first = candidates[0][1]
            if all(compare.agree(first, value, tolerance=tolerance) for _, value in candidates):
                # Prefer a rule that had to extrapolate, then one with more corroboration. Both
                # agree on the value here; this only decides which rule is cited as the reason.
                rule, value = max(
                    candidates,
                    key=lambda c: (c[0].generalises, c[0].corroboration, c[0].support),
                )
                predictions[code] = Prediction(
                    attribute_code=code,
                    value_canonical=value,
                    rule=rule,
                    agreeing_rules=len(candidates),
                )
            else:
                conflicts.append(
                    Conflict(
                        attribute_code=code,
                        candidates=tuple(
                            (rule.describe(), value) for rule, value in candidates
                        ),
                    )
                )

        return GrammarReading(
            sku=part_number,
            shape=signature,
            matched=True,
            predictions=predictions,
            conflicts=tuple(conflicts),
        )

    def values_for(
        self,
        part_number: str,
        *,
        registry: SchemaRegistry,
        class_code: str | None = None,
        only_codes: Iterable[str] | None = None,
    ) -> list[AttributeValue]:
        """Render a reading as reviewable attribute values.

        Every value comes back ``QUEUED_FOR_REVIEW``. That is not caution for its own sake:
        `AttributeValue` refuses to construct an inferred value as ``AUTO_ACCEPTED``, so the only
        way a grammar value can publish is for a human to approve it.
        """
        reading = self.predict(part_number)
        if not reading.matched:
            return []

        allowed: set[str] | None = None
        if class_code is not None:
            allowed = {a.code for a in registry.attributes_for(class_code)}
        wanted = set(only_codes) if only_codes is not None else None

        values: list[AttributeValue] = []
        for code, prediction in sorted(reading.predictions.items()):
            if allowed is not None and code not in allowed:
                continue
            if wanted is not None and code not in wanted:
                continue
            definition = registry.attribute(code)
            if _refuses_inference(definition):
                # Belt and braces. Induction never builds such a rule, and this makes a
                # hand-assembled grammar unable to smuggle one through either.
                continue
            values.append(
                AttributeValue(
                    attribute_code=code,
                    # The token genuinely is where the value came from, so it belongs in
                    # value_raw. There is no evidence span, because a part number is not a
                    # document — which is precisely why this method is in the inference family.
                    value_raw=_token_at(part_number, prediction.rule.position),
                    value_canonical=prediction.value_canonical,
                    method=DerivationMethod.PART_NUMBER_GRAMMAR,
                    confidence=prediction.confidence,
                    status=ValueStatus.QUEUED_FOR_REVIEW,
                    schema_version=(
                        registry.product_class(class_code).schema_version if class_code else None
                    ),
                )
            )
        return values

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, int] = defaultdict(int)
        for rule in self.rules:
            by_kind[rule.kind.value] += 1
        by_reason: dict[str, int] = defaultdict(int)
        for rejection in self.rejected:
            by_reason[rejection.reason.value] += 1
        return {
            "observations": self.observations,
            "shapes": dict(sorted(self.shapes.items())),
            "rules": len(self.rules),
            "generalising_rules": len(self.generalising_rules),
            "rules_by_kind": dict(sorted(by_kind.items())),
            "attributes_covered": self.attribute_codes(),
            "rejected": len(self.rejected),
            "rejected_by_reason": dict(sorted(by_reason.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "rule_detail": [r.to_dict() for r in self.rules],
            "rejected_detail": [r.to_dict() for r in self.rejected],
        }


def induce(
    observations: Iterable[Observation],
    registry: SchemaRegistry,
    *,
    min_support: int = 2,
) -> PartNumberGrammar:
    """Induce a grammar from part numbers whose attribute values are already known.

    ``min_support`` is the corroboration a memorised rule needs before it is believed: how many
    independent observations must agree on a single token. Two is the smallest number that means
    anything, because one is the coincidental bijection described in the module docstring. A
    LINEAR rule is exempt, since agreement across three distinct tokens is itself the test.
    """
    members: list[Observation] = list(observations)
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for observation in members:
        grouped[observation.shape].append(observation)

    rules: list[SegmentRule] = []
    rejected: list[RejectedRule] = []
    tolerances: dict[str, float] = {}

    for signature, group in sorted(grouped.items()):
        codes = sorted({code for member in group for code in member.values})
        positions = max((len(member.segments) for member in group), default=0)

        for code in codes:
            try:
                definition = registry.attribute(code)
            except KeyError:
                # An observation naming an attribute the schema does not define is a corpus bug,
                # not a rule candidate. Skipped rather than raised so one bad row cannot void a
                # whole induction run.
                continue

            tolerances[code] = definition.tolerance or 0.0

            if _refuses_inference(definition):
                rejected.append(
                    RejectedRule(
                        shape=signature,
                        attribute_code=code,
                        reason=RejectionReason.COMPLIANCE_CLAIM,
                        detail=(
                            "a compliance claim cannot be derived from a part number at any "
                            "level of support; the schema marks it evidence_requirement: strict"
                        ),
                    )
                )
                continue

            for position in range(positions):
                outcome = _induce_position(
                    signature, position, definition, group, min_support=min_support
                )
                if isinstance(outcome, SegmentRule):
                    rules.append(outcome)
                elif outcome is not None:
                    rejected.append(outcome)

    return PartNumberGrammar(
        rules=tuple(rules),
        rejected=tuple(rejected),
        tolerances=tolerances,
        observations=len(members),
        shapes={signature: len(group) for signature, group in sorted(grouped.items())},
    )


def _induce_position(
    signature: str,
    position: int,
    definition: AttributeDefinition,
    group: Sequence[Observation],
    *,
    min_support: int,
) -> SegmentRule | RejectedRule | None:
    """Try to explain one attribute from one segment position. None means "nothing to say"."""
    pairs: list[tuple[str, Any, str]] = []
    for member in group:
        if definition.code not in member.values:
            continue
        segments = member.segments
        if position >= len(segments):
            continue
        pairs.append((segments[position].text, member.values[definition.code], member.sku))

    if len(pairs) < 2:
        # Not reported as a rejection: "only one example mentions this" is an absence of data
        # rather than a refused candidate, and recording it would bury the real refusals.
        return None

    tolerance = definition.tolerance or 0.0
    by_token: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for token, value, sku in pairs:
        by_token[token].append((value, sku))

    for token, entries in by_token.items():
        first = entries[0][0]
        if not all(compare.agree(first, value, tolerance=tolerance) for value, _ in entries):
            distinct = sorted({_display(value) for value, _ in entries})
            return RejectedRule(
                shape=signature,
                position=position,
                attribute_code=definition.code,
                reason=RejectionReason.NOT_A_FUNCTION,
                detail=(
                    f"token {token!r} appears with {len(distinct)} different values "
                    f"({', '.join(distinct)}), so this position cannot determine "
                    f"{definition.code}"
                ),
            )

    support = len(pairs)
    corroboration = max(len(entries) for entries in by_token.values())
    skus = tuple(sorted(sku for _, _, sku in pairs))

    linear = _try_linear(signature, position, definition, by_token, support, corroboration, skus)
    if linear is not None:
        return linear

    mapping = {token: entries[0][0] for token, entries in by_token.items()}

    if len(mapping) == 1 and corroboration >= min_support:
        return SegmentRule(
            shape=signature,
            position=position,
            attribute_code=definition.code,
            kind=RuleKind.CONSTANT,
            mapping=mapping,
            support=support,
            corroboration=corroboration,
            contributing_skus=skus,
        )

    if corroboration < min_support:
        return RejectedRule(
            shape=signature,
            position=position,
            attribute_code=definition.code,
            reason=RejectionReason.UNCORROBORATED,
            detail=(
                f"{len(mapping)} tokens over {support} observations, every one seen "
                f"{corroboration} time(s). A mapping with no repeated key fits perfectly and "
                f"predicts nothing, so it cannot be told apart from coincidence"
            ),
        )

    return SegmentRule(
        shape=signature,
        position=position,
        attribute_code=definition.code,
        kind=RuleKind.LOOKUP,
        mapping=mapping,
        support=support,
        corroboration=corroboration,
        contributing_skus=skus,
    )


def _try_linear(
    signature: str,
    position: int,
    definition: AttributeDefinition,
    by_token: Mapping[str, Sequence[tuple[Any, str]]],
    support: int,
    corroboration: int,
    skus: tuple[str, ...],
) -> SegmentRule | None:
    """Look for `magnitude = token x scale`, the one rule kind that extrapolates.

    Requires every token to be numeric, at least :data:`_MIN_LINEAR_TOKENS` distinct tokens, and
    an exact decimal scale shared by all of them. Exactness is not fussiness — see
    :data:`_SCALE_QUANTUM`.
    """
    if len(by_token) < _MIN_LINEAR_TOKENS:
        return None
    if not all(token.isdigit() and int(token) != 0 for token in by_token):
        return None

    unit: str | None = None
    scales: set[Decimal] = set()
    for token, entries in by_token.items():
        value = entries[0][0]
        if isinstance(value, Quantity):
            if unit is not None and unit != value.unit:
                return None
            unit = value.unit
            magnitude: float | int = value.magnitude
        elif isinstance(value, int | float) and not isinstance(value, bool):
            if unit is not None:
                return None
            magnitude = value
        else:
            return None
        try:
            scale = (Decimal(str(magnitude)) / Decimal(int(token))).quantize(_SCALE_QUANTUM)
        except (DecimalException, ZeroDivisionError):
            return None
        scales.add(scale)
        if len(scales) > 1:
            return None

    scale = scales.pop()
    if scale == 0:
        return None

    return SegmentRule(
        shape=signature,
        position=position,
        attribute_code=definition.code,
        kind=RuleKind.LINEAR,
        mapping={},
        scale=float(scale),
        unit=unit,
        support=support,
        corroboration=corroboration,
        contributing_skus=skus,
    )


def _refuses_inference(definition: AttributeDefinition) -> bool:
    """Whether the schema forbids satisfying this attribute without evidence."""
    return (
        definition.compliance_claim
        or definition.evidence_requirement is EvidenceRequirement.STRICT
    )


def _token_at(part_number: str, position: int) -> str | None:
    segments = segment(part_number)
    return segments[position].text if position < len(segments) else None


def _render(value: Any) -> Any:
    """Plain-JSON rendering of a canonical value, for reports and serialisation."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_render(v) for v in value]
    return value


def _display(value: Any) -> str:
    """A short, hashable rendering for messages. ``Quantity`` already formats itself."""
    if isinstance(value, list | tuple):
        return ", ".join(_display(v) for v in value)
    return str(value)
