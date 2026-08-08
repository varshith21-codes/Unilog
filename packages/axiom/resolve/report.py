"""Rendering cross-reference results, and the whole-catalogue sweep.

Formatting lives here rather than in ``scripts/`` for the same reason every other harness in this
repository keeps it in the package: the API, the console exporter and the CLI must not each grow
their own idea of what a verdict looks like.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from axiom.core.product import ProductRecord
from axiom.resolve.catalogue import Catalogue
from axiom.resolve.equivalence import (
    AttributeComparison,
    Compatibility,
    EquivalenceReport,
    Verdict,
    equivalence,
    rank,
)
from axiom.schema.models import Interchange
from axiom.schema.registry import SchemaRegistry


@dataclass
class CrossReference:
    """Every substitute considered for one reference part."""

    reference_sku: str
    catalogue: Catalogue
    reports: list[EquivalenceReport] = field(default_factory=list)

    @property
    def substitutable(self) -> list[EquivalenceReport]:
        return [r for r in self.reports if r.verdict.is_substitutable]

    @property
    def indeterminate(self) -> list[EquivalenceReport]:
        return [r for r in self.reports if r.verdict is Verdict.INDETERMINATE]

    def by_verdict(self) -> dict[str, int]:
        counts = Counter(r.verdict.value for r in self.reports)
        return {verdict.value: counts.get(verdict.value, 0) for verdict in Verdict}

    def best(self) -> EquivalenceReport | None:
        return self.substitutable[0] if self.substitutable else None

    def summary(self) -> dict[str, Any]:
        best = self.best()
        return {
            "reference_sku": self.reference_sku,
            "candidates": len(self.reports),
            "substitutable": len(self.substitutable),
            "indeterminate": len(self.indeterminate),
            "by_verdict": self.by_verdict(),
            "best_substitute": best.candidate_sku if best else None,
            "best_verdict": best.verdict.value if best else None,
            "cross_brand_substitutes": sum(1 for r in self.substitutable if r.cross_brand),
            **self.catalogue.summary(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "candidates_detail": [r.to_dict() for r in self.reports],
        }


def cross_reference(
    reference_sku: str,
    catalogue: Catalogue,
    registry: SchemaRegistry,
    *,
    limit: int | None = None,
) -> CrossReference:
    """Rank every other record in the catalogue as a substitute for one part."""
    reference = catalogue.get(reference_sku)
    if reference is None:
        raise KeyError(
            f"'{reference_sku}' is not in this catalogue. Available: "
            f"{', '.join(catalogue.skus()) or 'none'}"
        )

    return CrossReference(
        reference_sku=reference_sku,
        catalogue=catalogue,
        reports=rank(
            reference,
            catalogue.records,
            registry,
            include_unsubstitutable=True,
            limit=limit,
        ),
    )


@dataclass
class Sweep:
    """Every ordered pair in a catalogue, so the asymmetry is visible as a number."""

    catalogue: Catalogue
    reports: list[EquivalenceReport] = field(default_factory=list)

    @property
    def pairs(self) -> int:
        return len(self.reports)

    def by_verdict(self) -> dict[str, int]:
        counts = Counter(r.verdict.value for r in self.reports)
        return {verdict.value: counts.get(verdict.value, 0) for verdict in Verdict}

    def asymmetric_pairs(self) -> list[tuple[EquivalenceReport, EquivalenceReport]]:
        """Unordered pairs whose two directions reached different verdicts.

        The headline evidence that substitution is directional. If this were ever empty on a
        corpus with mixed pressure ratings, the engine would have quietly become symmetric.
        """
        indexed = {(r.reference_sku, r.candidate_sku): r for r in self.reports}
        seen: set[tuple[str, str]] = set()
        out: list[tuple[EquivalenceReport, EquivalenceReport]] = []
        for (left, right), forward in indexed.items():
            key = tuple(sorted((left, right)))
            if key in seen:
                continue
            backward = indexed.get((right, left))
            if backward is not None and backward.verdict is not forward.verdict:
                seen.add(key)
                out.append((forward, backward))
        return sorted(out, key=lambda pair: (pair[0].reference_sku, pair[0].candidate_sku))

    def substitutable(self) -> list[EquivalenceReport]:
        return [r for r in self.reports if r.verdict.is_substitutable]

    def cross_brand_substitutes(self) -> list[EquivalenceReport]:
        return [r for r in self.substitutable() if r.cross_brand]

    def comparable(self) -> list[EquivalenceReport]:
        """Pairs where no *defining* attribute differs, so the comparison is worth making.

        Without this the headline is meaningless. Most pairs in any catalogue are different
        sizes, and "a 1/4" valve does not replace a 2" valve" is arithmetic rather than a
        finding — reporting it alongside a real alloy mismatch would bury the interesting
        rejections under trivial ones and make the substitution rate look like a verdict on the
        engine rather than on the corpus.
        """
        return [
            r
            for r in self.reports
            if not any(
                c.interchange is Interchange.DEFINING and c.compatibility.blocks
                for c in r.deciding()
            )
        ]

    def blocking_attributes(self) -> list[tuple[str, int]]:
        """Which attributes refuse substitutions, most frequent first.

        The actionable output of a sweep. A distributor reading "body material blocked 100 pairs"
        learns that their three suppliers use three different alloys, which is a sourcing fact
        rather than a data-quality one.
        """
        counts: Counter[str] = Counter()
        for report in self.reports:
            for comparison in report.blocking():
                counts[comparison.attribute_code] += 1
        return counts.most_common()

    def unclassified(self) -> list[str]:
        found: set[str] = set()
        for report in self.reports:
            found.update(report.unclassified())
        return sorted(found)

    def summary(self) -> dict[str, Any]:
        comparable = self.comparable()
        return {
            "pairs": self.pairs,
            "comparable_pairs": len(comparable),
            "by_verdict": self.by_verdict(),
            "substitutable": len(self.substitutable()),
            "substitution_rate_of_comparable": (
                round(len(self.substitutable()) / len(comparable), 4) if comparable else 0.0
            ),
            "cross_brand_substitutes": len(self.cross_brand_substitutes()),
            "asymmetric_pairs": len(self.asymmetric_pairs()),
            "blocking_attributes": dict(self.blocking_attributes()),
            "unclassified_attributes": self.unclassified(),
            **self.catalogue.summary(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "asymmetry_detail": [
                {
                    "a": forward.reference_sku,
                    "b": forward.candidate_sku,
                    "b_replaces_a": forward.verdict.value,
                    "a_replaces_b": backward.verdict.value,
                    "b_replaces_a_reason": forward.reason,
                    "a_replaces_b_reason": backward.reason,
                }
                for forward, backward in self.asymmetric_pairs()
            ],
            "pairs_detail": [r.summary() for r in self.reports],
        }


def sweep(catalogue: Catalogue, registry: SchemaRegistry) -> Sweep:
    """Compare every ordered pair in the catalogue.

    Ordered, not unordered: `n(n-1)` comparisons rather than `n(n-1)/2`, because
    "does B replace A" and "does A replace B" are different questions. Halving the work here
    would mean silently answering one of them with the other's verdict.
    """
    records: Sequence[ProductRecord] = catalogue.records
    reports = [
        equivalence(reference, candidate, registry)
        for reference in records
        for candidate in records
        if reference.sku != candidate.sku
    ]
    return Sweep(catalogue=catalogue, reports=reports)


# ---------------------------------------------------------------- formatting

_VERDICT_LABEL = {
    Verdict.IDENTICAL: "identical",
    Verdict.DROP_IN: "drop-in",
    Verdict.FUNCTIONAL_EQUIVALENT: "functional equiv",
    Verdict.NOT_EQUIVALENT: "not equivalent",
    Verdict.INDETERMINATE: "indeterminate",
}


def format_cross_reference(result: CrossReference, *, show_detail: int = 3) -> str:
    """Human-readable substitute list for one reference part."""
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"CROSS-REFERENCE — substitutes for {result.reference_sku}")
    add("=" * 78)
    add(
        f"  {len(result.reports)} candidate(s) from {len(result.catalogue)} records | "
        f"{len(result.substitutable)} substitutable | "
        f"{len(result.indeterminate)} indeterminate | 0 model calls | $0.00"
    )
    add(f"  source: {result.catalogue.source.value}")
    for line in _wrap(result.catalogue.source.note, width=74):
        add(f"    {line}")

    add("")
    add("  RANKED")
    add(f"    {'candidate':<14} {'verdict':<17} {'brand':<18} {'basis':>7}  why")
    for report in result.reports:
        add(
            f"    {report.candidate_sku:<14} {_VERDICT_LABEL[report.verdict]:<17} "
            f"{(report.candidate_brand or '-')[:18]:<18} "
            f"{report.compared:>3}/{len(report.deciding()):<3} "
            f"{_first_clause(report.reason)}"
        )

    shown = [r for r in result.reports if r.verdict.is_substitutable][:show_detail]
    for report in shown:
        add("")
        add(f"  {report.candidate_sku} — {_VERDICT_LABEL[report.verdict].upper()}")
        for line in _wrap(report.reason, width=72):
            add(f"    {line}")
        add(f"    {report.coverage_note}")
        _add_groups(add, report)

    if result.indeterminate:
        add("")
        add("  INDETERMINATE — a data gap, not a rejection")
        for report in result.indeterminate:
            missing = ", ".join(c.attribute_code for c in report.unknown())
            add(f"    {report.candidate_sku:<14} needs {missing}")

    return "\n".join(lines)


def format_equivalence(report: EquivalenceReport) -> str:
    """One directional verdict, in full."""
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"EQUIVALENCE — can {report.candidate_sku} replace {report.reference_sku}?")
    add("=" * 78)
    add(f"  verdict: {_VERDICT_LABEL[report.verdict].upper()}")
    for line in _wrap(report.reason, width=74):
        add(f"    {line}")
    add("")
    add(
        f"  {report.reference_sku} ({report.reference_brand or 'unknown brand'}, "
        f"{report.reference_class}) <- {report.candidate_sku} "
        f"({report.candidate_brand or 'unknown brand'}, {report.candidate_class})"
    )
    add(f"  {report.coverage_note}")
    if not report.same_class:
        add(
            "  NOTE: different product classes. The behaviour that distinguishes them is not "
            "modelled"
        )
        add("        by any attribute here, so a drop-in claim is deliberately withheld.")

    _add_groups(add, report)

    if report.inapplicable():
        add("")
        add("  NOT APPLICABLE TO BOTH CLASSES (never compared)")
        for line in _wrap(", ".join(report.inapplicable()), width=70):
            add(f"    {line}")

    if report.unclassified():
        add("")
        add("  UNCLASSIFIED — no interchange level declared in the schema")
        for line in _wrap(", ".join(report.unclassified()), width=70):
            add(f"    {line}")
        add("    Excluded from the verdict and reported, because an attribute nobody has")
        add("    classified is a schema gap rather than a silent pass.")

    return "\n".join(lines)


def format_sweep(result: Sweep) -> str:
    """The whole-catalogue sweep, with the asymmetry evidence."""
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("CROSS-REFERENCE SWEEP — every ordered pair")
    add("=" * 78)
    add(
        f"  {len(result.catalogue)} records | {result.pairs} ordered pairs | "
        f"0 model calls | $0.00"
    )
    add(f"  source: {result.catalogue.source.value}")
    for line in _wrap(result.catalogue.source.note, width=74):
        add(f"    {line}")

    comparable = result.comparable()
    add("")
    add("  WHAT IS EVEN WORTH COMPARING")
    add(f"    ordered pairs            {result.pairs:>5}")
    add(
        f"    share a nominal size     {len(comparable):>5}"
        f"   <- the rest differ on a defining attribute"
    )
    add(
        "    A 1/4\" valve not replacing a 2\" valve is arithmetic, not a finding. Every rate "
        "below is"
    )
    add("    quoted against the pairs that got past that gate.")

    add("")
    add("  VERDICTS")
    for verdict, count in result.by_verdict().items():
        share = count / result.pairs if result.pairs else 0.0
        add(f"    {verdict:<22} {count:>5}  {share:>6.1%} of all pairs")

    add("")
    add("  SUBSTITUTABILITY")
    add(
        f"    substitutable pairs      {len(result.substitutable()):>5}"
        f"   {result.summary()['substitution_rate_of_comparable']:.1%} of comparable pairs"
    )
    add(
        f"    across manufacturers     {len(result.cross_brand_substitutes()):>5}"
        f"   <- the case a text-similarity match cannot find"
    )

    blockers = result.blocking_attributes()
    if blockers:
        add("")
        add("  WHAT REFUSES A SUBSTITUTION (a sourcing fact, not a data-quality one)")
        for code, count in blockers:
            add(f"    {code:<24} {count:>5}")

    asymmetric = result.asymmetric_pairs()
    add("")
    add("  DIRECTIONAL ASYMMETRY")
    add(
        f"    {len(asymmetric)} unordered pair(s) reach different verdicts depending on which "
        f"part is the reference."
    )
    add("    This is the property the engine exists to preserve: a 600 psi valve substitutes")
    add("    for a 400 psi one and the reverse is a downgrade.")
    if asymmetric:
        add("")
        add(f"    {'a':<12} {'b':<12} {'b replaces a':<18} {'a replaces b':<18}")
        for forward, backward in asymmetric[:12]:
            add(
                f"    {forward.reference_sku:<12} {forward.candidate_sku:<12} "
                f"{_VERDICT_LABEL[forward.verdict]:<18} {_VERDICT_LABEL[backward.verdict]:<18}"
            )
        if len(asymmetric) > 12:
            add(f"    … and {len(asymmetric) - 12} more")

    if result.unclassified():
        add("")
        add("  UNCLASSIFIED ATTRIBUTES (schema gap)")
        for line in _wrap(", ".join(result.unclassified()), width=70):
            add(f"    {line}")
    else:
        add("")
        add("  Every compared attribute declares an interchange level.")

    if result.catalogue.failures:
        add("")
        add("  CATALOGUE NOTES")
        for failure in result.catalogue.failures:
            for line in _wrap(failure, width=70):
                add(f"    {line}")

    return "\n".join(lines)


def _add_groups(add, report: EquivalenceReport) -> None:
    """The four groups a reader needs: blocked, unknown, upgraded, cosmetic."""
    groups: list[tuple[str, Sequence[AttributeComparison]]] = [
        ("DIFFERS", report.blocking()),
        ("NOT ESTABLISHED", report.unknown()),
        ("CANDIDATE EXCEEDS", report.satisfied()),
        ("DIFFERS, DOES NOT BLOCK", report.cosmetic_differences()),
    ]
    for label, group in groups:
        if not group:
            continue
        add(f"    {label}")
        for item in group:
            left = item.reference_display or "not established"
            right = item.candidate_display or "not established"
            level = item.interchange.value if item.interchange else "unclassified"
            add(f"      {item.attribute_code:<24} {left:>22}  ->  {right:<22} [{level}]")

    agreed = report.agreed()
    if agreed:
        add(f"    AGREES ({len(agreed)})")
        for line in _wrap(", ".join(c.attribute_code for c in agreed), width=66):
            add(f"      {line}")


def _first_clause(reason: str) -> str:
    clause = reason.split(",")[0].split(";")[0]
    return clause if len(clause) <= 44 else clause[:41] + "…"


def _wrap(text: str, *, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


__all__ = [
    "Compatibility",
    "CrossReference",
    "Sweep",
    "cross_reference",
    "format_cross_reference",
    "format_equivalence",
    "format_sweep",
    "sweep",
]
