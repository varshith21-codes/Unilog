"""Rendering and aggregating spec-drift findings.

Formatting lives here rather than in ``scripts/`` for the same reason every other harness in this
repository keeps it in the package: the API, the console exporter and the CLI must not each grow
their own idea of what a drift verdict looks like.

The aggregate view exists because the per-SKU report is the wrong unit for the decision drift
actually drives. A supplier reissues one datasheet and it lands on every SKU in the family at
once, so the operator's question is never "what happened to BA-100-075" — it is "how many records
are now overclaiming, and which attribute caused it".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from axiom.drift.detector import AttributeDrift, Consequence, DriftKind, DriftReport


@dataclass
class DriftSweep:
    """Drift across every SKU affected by one reissued document."""

    reports: list[DriftReport] = field(default_factory=list)
    source_note: str = ""
    """Where the records came from, and therefore what these findings may claim. Carried in the
    payload rather than left to a reader, the same contract the L4 artifact and the cross-reference
    sweep use."""

    measured: bool = False
    """False when the records are hand-authored ground truth rather than pipeline output."""

    def __len__(self) -> int:
        return len(self.reports)

    @property
    def affected(self) -> list[DriftReport]:
        return [r for r in self.reports if r.has_drift]

    @property
    def unordered(self) -> list[DriftReport]:
        return [r for r in self.reports if not r.ordered and not r.identical_source]

    def urgent(self) -> list[DriftReport]:
        """SKUs with at least one published value that is now an overclaim."""
        return [r for r in self.reports if r.urgent()]

    def all_changes(self) -> list[AttributeDrift]:
        return [d for report in self.reports for d in report.changes()]

    def by_kind(self) -> dict[str, int]:
        counts = Counter(d.kind.value for d in self.all_changes())
        return {kind.value: counts.get(kind.value, 0) for kind in DriftKind if kind.is_change}

    def by_consequence(self) -> dict[str, int]:
        counts = Counter(d.consequence.value for d in self.all_changes())
        return {
            consequence.value: counts.get(consequence.value, 0)
            for consequence in Consequence
            if consequence is not Consequence.NONE
        }

    def by_attribute(self) -> dict[str, int]:
        """Which attributes moved, most-affected first. The column an operator triages on."""
        counts = Counter(d.attribute_code for d in self.all_changes())
        return dict(counts.most_common())

    def compliance_impacts(self) -> list[AttributeDrift]:
        return [d for d in self.all_changes() if d.compliance_claim]

    def summary(self) -> dict[str, Any]:
        return {
            "skus_compared": len(self.reports),
            "skus_affected": len(self.affected),
            "skus_urgent": len(self.urgent()),
            "skus_unordered": len(self.unordered),
            "changes": len(self.all_changes()),
            "by_kind": self.by_kind(),
            "by_consequence": self.by_consequence(),
            "by_attribute": self.by_attribute(),
            "compliance_impacts": len(self.compliance_impacts()),
            "measured": self.measured,
            "source_note": self.source_note,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "reports": [r.to_dict() for r in self.reports],
        }


def sweep_drift(
    reports: Sequence[DriftReport],
    *,
    source_note: str = "",
    measured: bool = False,
) -> DriftSweep:
    return DriftSweep(reports=list(reports), source_note=source_note, measured=measured)


_KIND_LABEL: dict[DriftKind, str] = {
    DriftKind.UNCHANGED: "unchanged",
    DriftKind.TIGHTENED: "TIGHTENED",
    DriftKind.RELAXED: "relaxed",
    DriftKind.REVISED: "revised",
    DriftKind.ADDED: "added",
    DriftKind.WITHDRAWN: "WITHDRAWN",
}
"""Only the two consequential kinds are capitalised. A report where everything shouts is a report
nobody reads twice."""


def format_drift(report: DriftReport, *, show_unchanged: bool = False) -> str:
    """One SKU's drift, in full."""
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"SPEC DRIFT — {report.sku}")
    add("=" * 78)
    add(
        f"  {report.before_document} ({report.before_revision or 'no marker'})  ->  "
        f"{report.after_document} ({report.after_revision or 'no marker'})"
    )

    if report.identical_source:
        add("")
        add(f"  {report.order_note}.")
        return "\n".join(lines)

    if not report.ordered:
        add("")
        add("  REVISION ORDER NOT ESTABLISHED")
        for line in _wrap(report.order_note, width=72):
            add(f"    {line}")
    elif report.order_note:
        for line in _wrap(report.order_note, width=72):
            add(f"  {line}")

    summary = report.summary()
    add("")
    add(
        f"  {summary['attributes_compared']} attributes compared | "
        f"{summary['changed']} changed | {summary['urgent']} urgent"
    )

    shown = report.drifts if show_unchanged else report.changes()
    if not shown:
        add("")
        add("  No change on any attribute either revision states.")
        return "\n".join(lines)

    add("")
    add(f"  {'ATTRIBUTE':<26}{'CHANGE':<12}{'ACTION':<12}BEFORE -> AFTER")
    add(f"  {'-' * 74}")
    for drift in _ordered_for_display(shown):
        flag = "!" if drift.consequence.is_urgent and drift.was_published else " "
        transition = _transition(drift)
        add(
            f" {flag}{drift.attribute_code:<26}"
            f"{_KIND_LABEL[drift.kind]:<12}"
            f"{drift.consequence.value:<12}{transition}"
        )

    urgent = report.urgent()
    if urgent:
        add("")
        add("  WHY THE FLAGGED ROWS MATTER")
        for drift in urgent:
            add("")
            add(f"    {drift.attribute_code} — {drift.consequence.value}")
            for line in _wrap(drift.reason, width=68):
                add(f"      {line}")

    compliance = report.compliance_impacts()
    if compliance:
        add("")
        add("  COMPLIANCE CLAIMS AFFECTED")
        for drift in compliance:
            add(
                f"    {drift.attribute_code}: {_KIND_LABEL[drift.kind]} "
                f"-> {drift.consequence.value}"
            )

    if report.unclassified:
        add("")
        add("  ATTRIBUTES THE SCHEMA DOES NOT DEFINE (no direction applied)")
        add(f"    {', '.join(report.unclassified)}")

    return "\n".join(lines)


def format_drift_sweep(sweep: DriftSweep, *, show_detail: int = 3) -> str:
    """The whole-family view: how far one reissued datasheet reaches."""
    lines: list[str] = []
    add = lines.append

    summary = sweep.summary()

    add("=" * 78)
    add("SPEC DRIFT SWEEP — one reissued document across a product family")
    add("=" * 78)
    add(
        f"  {summary['skus_compared']} SKUs compared | "
        f"{summary['skus_affected']} affected | {summary['changes']} attribute changes"
    )

    if not summary["measured"]:
        add("")
        for line in _wrap(sweep.source_note, width=74):
            add(f"  {line}")

    if sweep.unordered:
        add("")
        add(
            f"  {len(sweep.unordered)} SKU(s) could not be ordered by revision; no direction "
            f"is claimed for those."
        )

    add("")
    add("  CHANGES BY KIND")
    for kind, count in summary["by_kind"].items():
        if count:
            add(f"    {kind:<14}{count}")

    add("")
    add("  ACTION REQUIRED")
    for consequence, count in summary["by_consequence"].items():
        if count:
            marker = "!" if Consequence(consequence).is_urgent else " "
            add(f"  {marker} {consequence:<14}{count}")

    add("")
    add("  ATTRIBUTES THAT MOVED")
    for code, count in summary["by_attribute"].items():
        add(f"    {code:<28}{count} SKU(s)")

    urgent = sweep.urgent()
    add("")
    if urgent:
        add(
            f"  {len(urgent)} SKU(s) carry a published value that the current revision no "
            f"longer supports:"
        )
        add(f"    {', '.join(r.sku for r in urgent)}")
    else:
        add("  No published value is contradicted by the current revision.")

    compliance = sweep.compliance_impacts()
    if compliance:
        add("")
        add(f"  {len(compliance)} compliance claim(s) affected:")
        # Grouped by attribute rather than listed per SKU. A reissued datasheet lands on the whole
        # family at once, so the per-SKU list is the same two lines repeated — which buries the one
        # fact that matters, namely which claim lost its basis.
        for (code, kind, consequence), count in _grouped(compliance).items():
            add(f"    {code}: {_KIND_LABEL[kind]} -> {consequence.value}  ({count} SKUs)")

    for report in sweep.affected[:show_detail]:
        add("")
        add(format_drift(report))

    return "\n".join(lines)


def _grouped(
    drifts: Sequence[AttributeDrift],
) -> dict[tuple[str, DriftKind, Consequence], int]:
    """Collapse identical findings across SKUs, preserving first-seen order."""
    counts: dict[tuple[str, DriftKind, Consequence], int] = {}
    for drift in drifts:
        key = (drift.attribute_code, drift.kind, drift.consequence)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _ordered_for_display(drifts: Sequence[AttributeDrift]) -> list[AttributeDrift]:
    """Urgent first, then by kind, then alphabetically. Stable so output is diffable."""
    order = {
        DriftKind.TIGHTENED: 0,
        DriftKind.WITHDRAWN: 1,
        DriftKind.REVISED: 2,
        DriftKind.RELAXED: 3,
        DriftKind.ADDED: 4,
        DriftKind.UNCHANGED: 5,
    }
    return sorted(
        drifts,
        key=lambda d: (
            not (d.consequence.is_urgent and d.was_published),
            order[d.kind],
            d.attribute_code,
        ),
    )


def _transition(drift: AttributeDrift) -> str:
    before = drift.before_display or "(not stated)"
    after = drift.after_display or "(not stated)"
    return f"{before} -> {after}"


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
