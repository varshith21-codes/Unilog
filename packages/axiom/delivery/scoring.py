"""Scoring a delivery file against the client's ground truth.

The guide names the metrics judges will look for: *"Field-level accuracy against the 200
known-good rows, character-limit compliance, and percentage of values found in the LOV."* So the
scorer is a deliverable, not a convenience, and it is library code rather than a script so it can
be tested and run in CI.

Three decisions worth stating, because each one makes the number *worse* and more truthful.

**Over-filling is a defect, not extra credit.** 173 of the client's 252 columns are empty in ground
truth. A row that populates a column they left blank has invented data, and a metric that only
counted missing cells would reward exactly that. So :class:`Verdict` distinguishes ``MISSED`` from
``OVERFILLED`` and both count against accuracy.

**Exact and normalized match are reported separately.** The client requires brand names to match
"exactly, symbols and all" — ``FRIGIDAIRE(R)`` is not ``Frigidaire``. Folding the symbol away
inside a single fuzzy score would hide a real failure. Normalized match exists only as a
*diagnostic*: it tells you whether a miss is a casing/symbol problem or a wrong-value problem.

**Small denominators are printed as fractions.** Two ground-truth rows means every percentage has
a denominator of 2, and "93.3%" from two samples is a lie of precision. :func:`format_ratio`
refuses to render a percentage below :data:`PERCENTAGE_FLOOR` observations.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from axiom.delivery.format import DeliveryColumn, DeliveryFormat, Provenance

PERCENTAGE_FLOOR = 20
"""Minimum observations before a ratio is rendered as a percentage.

Below this the fraction is printed instead. Chosen because a percentage implies a precision that
a handful of samples cannot support, and the single most common way to mislead with an accuracy
report is to quote one to a decimal place off a denominator of two.
"""

_SYMBOLS = re.compile(r"[\u00ae\u2122\u00a9]")
_WHITESPACE = re.compile(r"\s+")


class Verdict(str, Enum):
    """How one cell compared with ground truth."""

    EXACT = "exact"
    """Byte-identical. The only verdict that fully satisfies the contract."""

    NORMALIZED = "normalized"
    """Equal after folding case, whitespace and (R)/(TM) symbols. Right value, wrong form."""

    BOTH_EMPTY = "both_empty"
    """Neither populated. Correct, and the majority verdict on this format by design."""

    MISSED = "missed"
    """Ground truth has a value and we do not. An enrichment gap."""

    OVERFILLED = "overfilled"
    """We have a value and ground truth does not. Invented data — worse than missing."""

    WRONG = "wrong"
    """Both populated and different even after folding."""

    @property
    def is_correct(self) -> bool:
        return self in {Verdict.EXACT, Verdict.BOTH_EMPTY}

    @property
    def is_acceptable(self) -> bool:
        """Correct, or right-value-wrong-form. Reported alongside strict correctness."""
        return self.is_correct or self is Verdict.NORMALIZED

    @property
    def is_populated_comparison(self) -> bool:
        """Whether ground truth expected a value here.

        The denominator for "of the cells that should have had a value, how many did we get".
        """
        return self in {Verdict.EXACT, Verdict.NORMALIZED, Verdict.MISSED, Verdict.WRONG}


def normalize_for_match(text: str) -> str:
    """Fold a value to its comparable core.

    Symbols are stripped **before** NFKC, and the order is not cosmetic: NFKC gives U+2122 the
    compatibility decomposition ``TM``, so normalising first turns ``CleanBoost(TM)`` into
    ``cleanboosttm`` and the symbol can no longer be removed. Stripping first means ``(R)`` and
    ``(TM)`` disappear as symbols, and NFKC then does its real job of folding full-width and
    composed characters onto their canonical forms.

    Deliberately does *not* strip punctuation: ``50-1/4`` and ``50 1 4`` are different
    measurements and collapsing them would manufacture agreement.
    """
    folded = _SYMBOLS.sub("", text)
    folded = unicodedata.normalize("NFKC", folded)
    folded = _WHITESPACE.sub(" ", folded)
    return folded.strip().casefold()


def compare(expected: str, actual: str) -> Verdict:
    """Verdict for one cell."""
    expected, actual = (expected or "").strip(), (actual or "").strip()
    if not expected and not actual:
        return Verdict.BOTH_EMPTY
    if not expected:
        return Verdict.OVERFILLED
    if not actual:
        return Verdict.MISSED
    if expected == actual:
        return Verdict.EXACT
    if normalize_for_match(expected) == normalize_for_match(actual):
        return Verdict.NORMALIZED
    return Verdict.WRONG


@dataclass(frozen=True)
class CellScore:
    """One column of one row, compared."""

    column: str
    group: str
    provenance: Provenance
    expected: str
    actual: str
    verdict: Verdict

    def summary(self) -> dict[str, object]:
        return {
            "column": self.column,
            "group": self.group,
            "provenance": self.provenance.value,
            "verdict": self.verdict.value,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class RowScore:
    """One row, compared cell by cell."""

    key: str
    cells: list[CellScore] = field(default_factory=list)
    constraint_violations: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[Verdict, int]:
        out = dict.fromkeys(Verdict, 0)
        for cell in self.cells:
            out[cell.verdict] += 1
        return out

    def failures(self) -> list[CellScore]:
        """Cells worth a human's attention, worst first."""
        order = {
            Verdict.OVERFILLED: 0,
            Verdict.WRONG: 1,
            Verdict.MISSED: 2,
            Verdict.NORMALIZED: 3,
        }
        return sorted(
            (c for c in self.cells if not c.verdict.is_correct),
            key=lambda c: (order.get(c.verdict, 9), c.column),
        )


@dataclass
class ScoreReport:
    """A batch, scored."""

    format_name: str
    rows: list[RowScore] = field(default_factory=list)
    unmatched_expected: list[str] = field(default_factory=list)
    unmatched_actual: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ totals

    def counts(self) -> dict[Verdict, int]:
        out = dict.fromkeys(Verdict, 0)
        for row in self.rows:
            for verdict, count in row.counts().items():
                out[verdict] += count
        return out

    @property
    def cells(self) -> int:
        return sum(len(row.cells) for row in self.rows)

    def exact_of_expected(self) -> tuple[int, int]:
        """Exact matches over the cells ground truth actually populated.

        The headline accuracy figure, and the strict one: it ignores the 173 columns that are
        empty on both sides, which would otherwise inflate any score on this format to ~90%
        before a single value was enriched.
        """
        counts = self.counts()
        denominator = sum(c for v, c in counts.items() if v.is_populated_comparison)
        return counts[Verdict.EXACT], denominator

    def acceptable_of_expected(self) -> tuple[int, int]:
        counts = self.counts()
        denominator = sum(c for v, c in counts.items() if v.is_populated_comparison)
        return counts[Verdict.EXACT] + counts[Verdict.NORMALIZED], denominator

    def fill_discipline(self) -> tuple[int, int]:
        """Cells where our populated/empty decision agreed with theirs, either way."""
        counts = self.counts()
        agreed = (
            counts[Verdict.EXACT]
            + counts[Verdict.NORMALIZED]
            + counts[Verdict.WRONG]
            + counts[Verdict.BOTH_EMPTY]
        )
        return agreed, self.cells

    @property
    def overfilled(self) -> int:
        return self.counts()[Verdict.OVERFILLED]

    @property
    def constraint_violations(self) -> int:
        return sum(len(row.constraint_violations) for row in self.rows)

    @property
    def compliant(self) -> bool:
        return self.constraint_violations == 0

    # ------------------------------------------------------------------ breakdowns

    def by_group(self) -> dict[str, dict[Verdict, int]]:
        out: dict[str, dict[Verdict, int]] = {}
        for row in self.rows:
            for cell in row.cells:
                bucket = out.setdefault(cell.group, dict.fromkeys(Verdict, 0))
                bucket[cell.verdict] += 1
        return out

    def by_provenance(self) -> dict[str, dict[Verdict, int]]:
        out: dict[str, dict[Verdict, int]] = {}
        for row in self.rows:
            for cell in row.cells:
                bucket = out.setdefault(cell.provenance.value, dict.fromkeys(Verdict, 0))
                bucket[cell.verdict] += 1
        return out

    def summary(self) -> dict[str, object]:
        exact, expected = self.exact_of_expected()
        acceptable, _ = self.acceptable_of_expected()
        agreed, total = self.fill_discipline()
        counts = self.counts()
        return {
            "format": self.format_name,
            "rows_scored": len(self.rows),
            "cells_compared": self.cells,
            "exact_of_expected": [exact, expected],
            "acceptable_of_expected": [acceptable, expected],
            "fill_discipline": [agreed, total],
            "verdicts": {v.value: counts[v] for v in Verdict},
            "constraint_violations": self.constraint_violations,
            "unmatched_expected": list(self.unmatched_expected),
            "unmatched_actual": list(self.unmatched_actual),
        }


def format_ratio(numerator: int, denominator: int) -> str:
    """Render a ratio honestly for its sample size.

    Below :data:`PERCENTAGE_FLOOR` observations the fraction is returned alone. Two ground-truth
    rows cannot support "93.3%", and printing it anyway is the most common way an accuracy report
    misleads.
    """
    if denominator <= 0:
        return "0/0 (n/a)"
    if denominator < PERCENTAGE_FLOOR:
        return f"{numerator}/{denominator}"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def score_rows(
    fmt: DeliveryFormat,
    expected: Sequence[dict[str, str]],
    actual: Sequence[dict[str, str]],
    *,
    key: str | None = None,
    columns: Iterable[str] | None = None,
) -> ScoreReport:
    """Compare two sets of delivery rows, joined on ``key``.

    Rows are joined on a key rather than zipped by position, because a batch driver that skips an
    unclassifiable row would otherwise silently compare row *n* against row *n+1* and report
    catastrophic, entirely fictional inaccuracy.
    """
    join_key = key or fmt.join_key
    if join_key not in fmt:
        raise KeyError(f"join key {join_key!r} is not a delivery-format column")

    selected: list[DeliveryColumn] = (
        [fmt.column(name) for name in columns] if columns is not None else list(fmt.columns)
    )

    expected_by_key = {(row.get(join_key) or "").strip(): row for row in expected}
    actual_by_key = {(row.get(join_key) or "").strip(): row for row in actual}

    report = ScoreReport(format_name=f"{fmt.name}@{fmt.version}")
    report.unmatched_expected = sorted(set(expected_by_key) - set(actual_by_key))
    report.unmatched_actual = sorted(set(actual_by_key) - set(expected_by_key))

    for identifier in sorted(set(expected_by_key) & set(actual_by_key)):
        expected_row, actual_row = expected_by_key[identifier], actual_by_key[identifier]
        scored = RowScore(key=identifier)
        for column in selected:
            expected_value = (expected_row.get(column.name) or "").strip()
            actual_value = (actual_row.get(column.name) or "").strip()
            scored.cells.append(
                CellScore(
                    column=column.name,
                    group=column.group,
                    provenance=column.provenance,
                    expected=expected_value,
                    actual=actual_value,
                    verdict=compare(expected_value, actual_value),
                )
            )
            if problems := column.violations(actual_value):
                scored.constraint_violations[column.name] = problems
        report.rows.append(scored)

    return report


def render_report(report: ScoreReport, *, max_failures: int = 25) -> str:
    """A human-readable report. The thing that goes in the pitch, so it must not flatter."""
    exact, expected = report.exact_of_expected()
    acceptable, _ = report.acceptable_of_expected()
    agreed, total = report.fill_discipline()
    counts = report.counts()

    # ASCII only in the report's own chrome. This is read in a terminal, and the Windows console
    # renders an em-dash as mojibake, which makes a report about data fidelity look careless.
    lines: list[str] = []
    lines.append(f"Delivery-format score - {report.format_name}")
    lines.append("=" * 78)
    lines.append(f"rows scored          {len(report.rows)}")
    lines.append(f"cells compared       {report.cells}")
    lines.append("")
    lines.append("ACCURACY (denominator = cells ground truth populated)")
    lines.append(f"  exact match        {format_ratio(exact, expected)}")
    lines.append("  right value but")
    lines.append(f"    wrong form       {format_ratio(acceptable, expected)}")
    lines.append("")
    lines.append("FILL DISCIPLINE (agreement on populated-vs-empty, both directions)")
    lines.append(f"  agreed             {format_ratio(agreed, total)}")
    lines.append(
        f"  over-filled        {counts[Verdict.OVERFILLED]}"
        f"   <- cells the client left blank; invented data"
    )
    lines.append(f"  missed             {counts[Verdict.MISSED]}")
    lines.append("")
    lines.append("CHARACTER-LIMIT COMPLIANCE")
    verdict = "PASS" if report.compliant else f"FAIL ({report.constraint_violations})"
    lines.append(f"  declared limits    {verdict}")
    for row in report.rows:
        for column, problems in sorted(row.constraint_violations.items()):
            lines.append(f"    {row.key} {column}: {'; '.join(problems)}")
    lines.append("")

    lines.append("BY COLUMN GROUP")
    for group, bucket in report.by_group().items():
        relevant = sum(c for v, c in bucket.items() if v.is_populated_comparison)
        if not relevant:
            continue
        lines.append(
            f"  {group:18s} exact {format_ratio(bucket[Verdict.EXACT], relevant):>12s}"
            f"   missed {bucket[Verdict.MISSED]:3d}"
            f"   wrong {bucket[Verdict.WRONG]:3d}"
            f"   over {bucket[Verdict.OVERFILLED]:3d}"
        )
    lines.append("")

    if report.unmatched_expected:
        lines.append(
            f"NOT PRODUCED: {len(report.unmatched_expected)} ground-truth rows had no output "
            f"({', '.join(report.unmatched_expected[:8])})"
        )
    if report.unmatched_actual:
        lines.append(
            f"NOT IN GROUND TRUTH: {len(report.unmatched_actual)} output rows could not be scored"
        )
    if report.unmatched_expected or report.unmatched_actual:
        lines.append("")

    failures = [cell for row in report.rows for cell in row.failures()]
    if failures:
        shown = min(len(failures), max_failures)
        lines.append(f"FAILURES (worst first, showing {shown} of {len(failures)})")
        for cell in failures[:max_failures]:
            lines.append(f"  [{cell.verdict.value:10s}] {cell.column}")
            lines.append(f"      expected {cell.expected[:88]!r}")
            lines.append(f"      actual   {cell.actual[:88]!r}")
    return "\n".join(lines)


__all__ = [
    "PERCENTAGE_FLOOR",
    "CellScore",
    "RowScore",
    "ScoreReport",
    "Verdict",
    "compare",
    "format_ratio",
    "normalize_for_match",
    "render_report",
    "score_rows",
]
