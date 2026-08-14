"""Rendering DPP readiness, and aggregating it across a catalogue.

Formatting lives in the package rather than in ``scripts/`` for the same reason every other harness
here does: the API, the console exporter and the CLI must not each grow their own idea of what a
readiness verdict looks like.

The aggregate view exists because readiness is a portfolio question. One SKU's score is a curiosity;
"the same four fields are missing on all fifteen SKUs" is a plan, and "nine fields are missing
because the schema cannot express them" is a different plan aimed at different people.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from axiom.compliance.passport import (
    FieldStatus,
    PassportProfile,
    ReadinessReport,
    Requirement,
)


@dataclass
class ReadinessSweep:
    """Readiness across every record assessed."""

    reports: list[ReadinessReport] = field(default_factory=list)
    source_note: str = ""
    measured: bool = False

    def __len__(self) -> int:
        return len(self.reports)

    @property
    def profile(self) -> PassportProfile | None:
        return self.reports[0].profile if self.reports else None

    @property
    def mean_readiness(self) -> float:
        if not self.reports:
            return 0.0
        return sum(r.readiness for r in self.reports) / len(self.reports)

    @property
    def mean_addressable_readiness(self) -> float:
        if not self.reports:
            return 0.0
        return sum(r.addressable_readiness for r in self.reports) / len(self.reports)

    def registrable(self) -> list[ReadinessReport]:
        return [r for r in self.reports if r.readiness == 1.0]

    def blocking_fields(self) -> dict[str, int]:
        """Mandatory fields that are not ready, most widespread first.

        The triage list. A field failing on every SKU is a systemic gap; one failing on a single SKU
        is a data-entry problem, and the two want completely different responses.
        """
        counts = Counter(
            a.field.id
            for r in self.reports
            for a in r.scored()
            if not a.is_ready
        )
        return dict(counts.most_common())

    def schema_gap_fields(self) -> list[str]:
        """Fields no record can ever satisfy. Identical across every SKU by construction."""
        if not self.reports:
            return []
        return [a.field.id for a in self.reports[0].schema_gaps()]

    def status_counts(self) -> dict[str, int]:
        counts = Counter(
            a.status.value for r in self.reports for a in r.assessments
        )
        return {status.value: counts.get(status.value, 0) for status in FieldStatus}

    def summary(self) -> dict[str, Any]:
        profile = self.profile
        return {
            "records": len(self.reports),
            "profile": profile.name if profile else None,
            "profile_version": profile.version if profile else None,
            "category_in_scope": profile.category_in_scope if profile else None,
            "mean_readiness": round(self.mean_readiness, 4),
            "mean_addressable_readiness": round(self.mean_addressable_readiness, 4),
            "registrable": len(self.registrable()),
            "blocking_fields": self.blocking_fields(),
            "schema_gap_fields": self.schema_gap_fields(),
            "status_counts": self.status_counts(),
            "measured": self.measured,
            "source_note": self.source_note,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "reports": [r.to_dict() for r in self.reports],
        }


def sweep_readiness(
    reports: Sequence[ReadinessReport],
    *,
    source_note: str = "",
    measured: bool = False,
) -> ReadinessSweep:
    return ReadinessSweep(
        reports=list(reports), source_note=source_note, measured=measured
    )


_STATUS_LABEL: dict[FieldStatus, str] = {
    FieldStatus.VERIFIED: "ready",
    FieldStatus.PARTIAL: "partial",
    FieldStatus.UNVERIFIED: "unverified",
    FieldStatus.INFERRED: "INFERRED",
    FieldStatus.ABSENT: "absent",
    FieldStatus.UNMAPPED: "unmapped",
}


def format_readiness(report: ReadinessReport) -> str:
    """One record's readiness, field by field."""
    lines: list[str] = []
    add = lines.append
    summary = report.summary()

    add("=" * 78)
    add(f"DPP READINESS — {report.sku}")
    add("=" * 78)
    add(f"  {report.profile.regulation}  |  profile {report.profile.name}"
        f"@{report.profile.version}")

    if not report.profile.category_in_scope and report.profile.category_note:
        add("")
        for line in _wrap(report.profile.category_note, width=74):
            add(f"  {line}")

    add("")
    add(
        f"  readiness {summary['readiness']:.0%} of {summary['mandatory_fields']} mandatory "
        f"fields  |  {summary['addressable_readiness']:.0%} of those the schema can express"
    )
    add(
        f"  {summary['data_gaps']} data gap(s) the pipeline could close  |  "
        f"{summary['schema_gaps']} field(s) it never can"
    )

    for group in report.profile.groups():
        entries = [a for a in report.assessments if a.field.group == group]
        if not entries:
            continue
        add("")
        add(f"  {group.upper()}")
        for assessment in entries:
            marker = "+" if assessment.is_ready else " "
            requirement = (
                "" if assessment.field.requirement is Requirement.MANDATORY
                else f" ({assessment.field.requirement.value})"
            )
            add(
                f"  {marker} {assessment.field.id:<36}"
                f"{_STATUS_LABEL[assessment.status]}{requirement}"
            )
            if assessment.contributing:
                add(f"      from: {', '.join(assessment.contributing)}")
            if assessment.missing:
                add(f"      needs: {', '.join(assessment.missing)}")

    schema_gaps = report.schema_gaps()
    if schema_gaps:
        add("")
        add("  WHY THE UNMAPPED FIELDS CANNOT BE FIXED BY RE-EXTRACTION")
        for assessment in schema_gaps:
            add("")
            add(f"    {assessment.field.id}")
            if assessment.field.note:
                for line in _wrap(assessment.field.note, width=68):
                    add(f"      {line}")

    return "\n".join(lines)


def format_readiness_sweep(sweep: ReadinessSweep, *, show_detail: int = 1) -> str:
    """The portfolio view: which fields block registration, and who can unblock them."""
    lines: list[str] = []
    add = lines.append
    summary = sweep.summary()

    add("=" * 78)
    add("DPP READINESS SWEEP — the catalogue against the regulation")
    add("=" * 78)
    add(
        f"  {summary['records']} records | mean readiness "
        f"{summary['mean_readiness']:.0%} | {summary['registrable']} registrable"
    )
    add(
        f"  mean readiness over fields the schema can express: "
        f"{summary['mean_addressable_readiness']:.0%}"
    )

    if not summary["measured"] and sweep.source_note:
        add("")
        for line in _wrap(sweep.source_note, width=74):
            add(f"  {line}")

    add("")
    add("  FIELD STATUS ACROSS ALL RECORDS")
    for status, count in summary["status_counts"].items():
        if count:
            add(f"    {status:<14}{count}")

    schema_gaps = summary["schema_gap_fields"]
    if schema_gaps:
        add("")
        add(
            f"  {len(schema_gaps)} FIELD(S) NO RECORD CAN SATISFY — schema gaps, not data gaps."
        )
        for line in _wrap(
            "Closing these needs a new attribute and a supplier who publishes the figure. "
            "Re-running extraction will not move them.",
            width=74,
        ):
            add(f"  {line}")
        for field_id in schema_gaps:
            add(f"    {field_id}")

    blocking = {
        field_id: count
        for field_id, count in summary["blocking_fields"].items()
        if field_id not in set(schema_gaps)
    }
    add("")
    if blocking:
        add("  MANDATORY FIELDS BLOCKED BY DATA, MOST WIDESPREAD FIRST")
        for field_id, count in blocking.items():
            add(f"    {field_id:<38}{count} record(s)")
    else:
        add("  No mandatory field is blocked by anything the pipeline could fix.")

    for report in sweep.reports[:show_detail]:
        add("")
        add(format_readiness(report))

    return "\n".join(lines)


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
