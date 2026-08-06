"""Validation layer L4: agreement between independent sources.

The layer that answers "do two documents that both describe this part actually say the same
thing?" — and it is the only one that can tell a *confidently wrong* value from a merely
unconfirmed one. L0 through L3 all reason about a single observation: whether it parses, whether
its unit is coherent, whether it contradicts a sibling field, whether it is plausible for the
class. All four can pass on a value that is simply, verifiably wrong, because a wrong figure
printed in a datasheet is a well-formed figure.

Two sources are the cheapest way to catch that. Nothing else in the stack can.

### Why this arrived last

Not difficulty — corpus. L4 needs two independent documents describing the same SKU, and until
there were two it could only ever have returned SKIPPED. Shipping a validator whose every verdict
is "not applicable" would have been worse than shipping none, because the layer would appear in
reports and mean nothing.

### The three verdicts, and why disagreement is not automatically a failure

*   **PASS** — the sources agree. This is genuinely new information, not a formality: an
    attribute corroborated by two independent documents is the strongest evidence this system can
    produce, stronger than any single citation.
*   **WARN (superseded)** — they disagree, but one document is demonstrably a newer revision. That
    is not a data-quality problem, it is a data-*currency* one: an older catalogue printing last
    year's pressure rating is behaving correctly. The newer value wins and the older is recorded
    as superseded rather than wrong.
*   **FAIL (unresolved)** — they disagree and precedence cannot be established. This blocks
    publication, because the one thing the system must never do is pick a side silently. Two
    sources contradicting each other with no way to order them is exactly the case a human should
    see.

### On per-supplier trust

The blueprint pairs L4 with "learned per-supplier trust", and trust is accepted here as an
injected mapping rather than read from :mod:`axiom.confidence`. That keeps the layer testable
without a calibration file, and it keeps a deliberate limit in place: trust is used only to
*order* two sources when revision dates cannot, and never to resolve a conflict outright. A
supplier being historically reliable is a reason to prefer their number, not evidence that the
other number is wrong — and letting a prior silently overrule a printed specification would
reintroduce exactly the unaccountable guessing this system exists to remove.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from axiom.core import compare
from axiom.core.evidence import SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.validation import Severity, ValidationLayer, ValidationResult, Verdict
from axiom.core.values import AttributeValue
from axiom.schema import SchemaRegistry

L4 = ValidationLayer.L4_CROSS_SOURCE

# A four-digit year with an optional month, anywhere in a revision label. Deliberately not a full
# date parser: "Rev C 2024-08" and "2024-08-15" and "Rev 3 (Aug 2024)" all need to yield an
# orderable value, and a label that yields nothing must fall back rather than guess.
_REVISION_DATE = re.compile(r"(20\d{2}|19\d{2})(?:[-/\s]?(0[1-9]|1[0-2]))?")

# A trailing revision letter: "Rev C", "Revision B". Ordered alphabetically, which is the
# convention manufacturers actually use.
_REVISION_LETTER = re.compile(r"\brev(?:ision)?\.?\s*([A-Z])\b", re.IGNORECASE)


@dataclass(frozen=True)
class Observation:
    """One source's statement about one attribute."""

    value: AttributeValue
    document: SourceDocument

    @property
    def attribute_code(self) -> str:
        return self.value.attribute_code

    @property
    def canonical(self) -> object:
        return (
            self.value.value_canonical
            if self.value.value_canonical is not None
            else self.value.value_raw
        )

    def describe(self) -> str:
        shown = self.value.value_display or self.value.value_raw or str(self.canonical)
        revision = f" {self.document.revision_label}" if self.document.revision_label else ""
        return f"{shown!r} ({self.document.document_id}{revision})"


def revision_rank(document: SourceDocument) -> tuple[int, int, str] | None:
    """An orderable recency key for a document, or None when it cannot be established.

    Order of preference is deliberate. A manufacturer's printed revision marker beats a retrieval
    timestamp, because when a document was *downloaded* says nothing about when it was *written* —
    a datasheet crawled today may be a decade old, and preferring `fetched_at` would let the order
    in which an operator happened to collect two documents decide which specification wins.

    Returns None rather than falling back to `fetched_at` when no marker exists at all. That is
    what turns an unresolvable conflict into a FAIL instead of a coin flip.
    """
    label = document.revision_label or ""

    if match := _REVISION_DATE.search(label):
        year = int(match.group(1))
        month = int(match.group(2) or 0)
        return (2, year * 100 + month, label)

    if match := _REVISION_LETTER.search(label):
        return (1, ord(match.group(1).upper()), label)

    return None


@dataclass
class Disagreement:
    """Two or more sources stating different values for one attribute."""

    attribute_code: str
    observations: tuple[Observation, ...]
    winner: Observation | None = None
    reason: str = ""
    """How precedence was established, or why it could not be."""

    @property
    def resolved(self) -> bool:
        return self.winner is not None

    def losers(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o is not self.winner)

    def to_dict(self) -> dict[str, object]:
        return {
            "attribute_code": self.attribute_code,
            "resolved": self.resolved,
            "reason": self.reason,
            "winner": self.winner.describe() if self.winner else None,
            "observations": [
                {
                    "document_id": o.document.document_id,
                    "revision_label": o.document.revision_label,
                    "supplier_id": o.document.supplier_id,
                    "value_display": o.value.value_display or o.value.value_raw,
                }
                for o in self.observations
            ],
        }


@dataclass
class CrossSourceReport:
    """What L4 concluded across every attribute."""

    results: list[ValidationResult] = field(default_factory=list)
    corroborated: list[str] = field(default_factory=list)
    """Attributes two or more independent sources agreed on."""

    disagreements: list[Disagreement] = field(default_factory=list)
    single_source: list[str] = field(default_factory=list)
    """Attributes only one document spoke to. SKIPPED, never PASS."""

    documents: int = 0

    @property
    def per_attribute(self) -> dict[str, list[ValidationResult]]:
        grouped: dict[str, list[ValidationResult]] = {}
        for result in self.results:
            # The attribute is carried in `detail` rather than on ValidationResult, which has no
            # attribute field. Grouping is done here so callers can attach results to values.
            code = result.detail or ""
            grouped.setdefault(code, []).append(result)
        return grouped

    @property
    def unresolved(self) -> list[Disagreement]:
        return [d for d in self.disagreements if not d.resolved]

    @property
    def passed(self) -> bool:
        """L4 blocks only on a conflict it could not order."""
        return not self.unresolved

    @property
    def applicable(self) -> bool:
        """Whether L4 could say anything at all.

        False with a single document. Reporting a clean L4 on one source would claim corroboration
        the corpus cannot provide, which is the specific dishonesty this layer must avoid.
        """
        return self.documents > 1

    def summary(self) -> dict[str, object]:
        return {
            "applicable": self.applicable,
            "documents": self.documents,
            "corroborated": len(self.corroborated),
            "disagreements": len(self.disagreements),
            "unresolved": len(self.unresolved),
            "single_source": len(self.single_source),
            "passed": self.passed,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "corroborated_attributes": list(self.corroborated),
            "single_source_attributes": list(self.single_source),
            "conflicts": [d.to_dict() for d in self.disagreements],
        }


class CrossSourceValidator:
    """Validation layer L4.

    ``trust`` maps supplier id to a reliability weight in [0, 1]. Used only to order two sources
    whose revisions cannot be compared, never to overrule a revision marker.
    """

    def __init__(
        self,
        registry: SchemaRegistry,
        *,
        trust: Mapping[str, float] | None = None,
    ) -> None:
        self._registry = registry
        self._trust = dict(trust or {})

    def validate(
        self,
        record: ProductRecord,
        documents: Mapping[str, SourceDocument],
    ) -> CrossSourceReport:
        """Compare every attribute across the documents that spoke to it.

        ``documents`` maps document id to its provenance record. A value whose evidence points at a
        document not present here is skipped rather than guessed at: without the revision metadata
        there is no basis on which to order it.
        """
        report = CrossSourceReport(documents=len({d.document_id for d in documents.values()}))
        grouped = self._group(record, documents)

        for code in sorted(grouped):
            observations = grouped[code]
            sources = {o.document.document_id for o in observations}

            if len(sources) < 2:
                report.single_source.append(code)
                report.results.append(
                    ValidationResult(
                        layer=L4,
                        rule_id="L4_SINGLE_SOURCE",
                        verdict=Verdict.SKIPPED,
                        severity=Severity.INFO,
                        reason=(
                            "only one source states this value, so cross-source agreement could "
                            "not be checked. This is not corroboration."
                        ),
                        detail=code,
                    )
                )
                continue

            report.results.append(self._compare(code, observations, report))

        return report

    def _group(
        self,
        record: ProductRecord,
        documents: Mapping[str, SourceDocument],
    ) -> dict[str, list[Observation]]:
        grouped: dict[str, list[Observation]] = {}
        for value in record.current_values():
            for span in value.evidence:
                document = documents.get(span.document_id)
                if document is None:
                    continue
                grouped.setdefault(value.attribute_code, []).append(
                    Observation(value=value, document=document)
                )
                # One observation per (value, document). A value cited twice from the same page is
                # still one source saying one thing.
                break
        return grouped

    def _compare(
        self,
        code: str,
        observations: list[Observation],
        report: CrossSourceReport,
    ) -> ValidationResult:
        tolerance = self._tolerance(code)

        # Partition into agreement groups. More than one group means a genuine disagreement; the
        # comparison is the same primitive the backtest scores ground truth with, so a value
        # cannot be "correct" there and "in conflict" here.
        groups: list[list[Observation]] = []
        for observation in observations:
            for group in groups:
                if compare.agree(
                    group[0].canonical, observation.canonical, tolerance=tolerance
                ):
                    group.append(observation)
                    break
            else:
                groups.append([observation])

        if len(groups) == 1:
            report.corroborated.append(code)
            sources = sorted({o.document.document_id for o in observations})
            return ValidationResult.passed(
                L4,
                "L4_CORROBORATED",
                f"{len(sources)} independent sources state the same value",
                detail=code,
            )

        disagreement = self._resolve(code, observations, groups)
        report.disagreements.append(disagreement)

        rendered = "; ".join(o.describe() for o in observations)

        if disagreement.resolved:
            return ValidationResult(
                layer=L4,
                rule_id="L4_SUPERSEDED",
                verdict=Verdict.WARN,
                severity=Severity.WARNING,
                reason=(
                    f"sources disagree, and the newer one was preferred: {disagreement.reason}. "
                    f"An older catalogue printing a previous specification is out of date rather "
                    f"than wrong."
                ),
                counterexample=rendered[:200],
                suggested_fix=(
                    f"publish {disagreement.winner.describe()} and mark the others superseded"
                    if disagreement.winner
                    else None
                ),
                detail=code,
            )

        return ValidationResult.failed(
            L4,
            "L4_UNRESOLVED_CONFLICT",
            (
                "independent sources state different values and neither carries a revision "
                "marker that would order them, so the system will not choose between them"
            ),
            counterexample=rendered[:200],
            suggested_fix=(
                "confirm which source is current, or record a revision label on the documents so "
                "precedence can be established automatically"
            ),
            detail=code,
        )

    def _resolve(
        self,
        code: str,
        observations: list[Observation],
        groups: list[list[Observation]],
    ) -> Disagreement:
        """Order competing observations by revision, then by supplier trust."""
        ranked = [(revision_rank(o.document), o) for o in observations]
        with_rank = [(rank, o) for rank, o in ranked if rank is not None]

        # Every source must be orderable. If even one carries no revision marker, the newest
        # *known* document might not be the newest document, and preferring it would be a guess
        # dressed as precedence.
        if len(with_rank) == len(observations) and len(observations) > 1:
            with_rank.sort(key=lambda pair: pair[0], reverse=True)
            best_rank, winner = with_rank[0]
            runner_rank = with_rank[1][0]

            if best_rank != runner_rank:
                return Disagreement(
                    attribute_code=code,
                    observations=tuple(observations),
                    winner=winner,
                    reason=(
                        f"{winner.document.document_id} is the newer revision "
                        f"({winner.document.revision_label})"
                    ),
                )

        trusted = self._by_trust(observations)
        if trusted is not None:
            return Disagreement(
                attribute_code=code,
                observations=tuple(observations),
                winner=trusted,
                reason=(
                    f"revisions could not be ordered, so the more reliable supplier was preferred "
                    f"({trusted.document.supplier_id}, trust "
                    f"{self._trust.get(trusted.document.supplier_id or '', 0.0):.2f})"
                ),
            )

        missing = sorted(
            {
                o.document.document_id
                for rank, o in ranked
                if rank is None
            }
        )
        reason = (
            f"no revision marker on {', '.join(missing)}"
            if missing
            else "the sources carry equivalent revisions"
        )
        return Disagreement(
            attribute_code=code,
            observations=tuple(observations),
            winner=None,
            reason=reason,
        )

    def _by_trust(self, observations: Sequence[Observation]) -> Observation | None:
        """The single most-trusted source, or None when trust cannot decide.

        Requires a *strict* winner. Two suppliers with equal trust, an unknown supplier, or no
        trust data at all all mean the same thing: this tie-break has nothing to say, and saying
        nothing is correct.
        """
        if not self._trust:
            return None

        scored = [
            (self._trust.get(o.document.supplier_id or "", None), o) for o in observations
        ]
        known = [(score, o) for score, o in scored if score is not None]
        if len(known) != len(observations) or len(known) < 2:
            return None

        known.sort(key=lambda pair: pair[0], reverse=True)
        if known[0][0] <= known[1][0]:
            return None
        return known[0][1]

    def _tolerance(self, code: str) -> float:
        try:
            definition = self._registry.attribute(code)
        except KeyError:
            return 0.0
        return definition.tolerance if definition.tolerance is not None else 0.0


def promote_resolved(record: ProductRecord, report: CrossSourceReport) -> int:
    """Supersede the losing side of every *resolved* disagreement.

    Only the resolved ones. An unresolved conflict must keep both candidates current so it stays
    visible in :meth:`ProductRecord.conflicts` and in the review queue — collapsing it would hide
    the very thing L4 exists to surface.

    Returns how many values were superseded.
    """
    superseded = 0
    for disagreement in report.disagreements:
        if not disagreement.resolved or disagreement.winner is None:
            continue
        winner = disagreement.winner.value
        for observation in disagreement.losers():
            if observation.value is winner:
                continue
            if observation.value.superseded_by is None:
                observation.value.supersede(winner.version)
                superseded += 1
    return superseded
