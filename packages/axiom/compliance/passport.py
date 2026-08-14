"""Digital Product Passport readiness, and the payload it would carry.

Blueprint Tier 3, item 22, and Part 3.3's urgency argument: the ESPR central registry was
scheduled for July 2026, batteries carry mandatory passports from February 2027, and further
product groups phase in through 2030.

### Why a readiness panel rather than a passport generator

The tempting build is a DPP exporter: read the record, emit a passport-shaped document, declare
compliance. It would demo well and it would be dishonest, because a passport assembled from fields
nobody can source is not a passport — it is a form with confident-looking blanks.

So the output here is an **audit of the distance to the regulation**, and its most important
feature is that it keeps two different failures apart:

*   A **data gap** — the profile maps this field to an attribute, and this record has no value for
    it. Re-extraction might fix it. Chase the pipeline.
*   A **schema gap** — nothing in the attribute dictionary can express this field at all. No amount
    of re-extraction will ever fill it. Chase the supplier, or the schema.

Collapsing those into one percentage is what makes most compliance dashboards useless. "You are 46%
ready" sends a team to re-run extraction. "46% ready, and nine of the eleven missing fields are ones
your schema cannot represent" sends them to talk to suppliers, which is the only action that helps.

That distinction is the same one :class:`~axiom.core.certificate.RichnessComponents` already makes
when it records a dimension as *unobserved* rather than zero, and for the same reason: a gap you
have not measured and a gap you have measured as empty are different facts, and averaging them
understates the first and slanders the second.

### Why verified evidence is the bar, not presence

A DPP field is a regulatory assertion. Every attribute behind one of these fields is already
declared ``compliance_claim: true`` and therefore ``evidence_requirement: strict``, which forbids
satisfying it by inference. This module holds the same line one step later: a value that exists but
carries no verified citation counts as **unverified**, not as ready, and an inferred value is
reported as inferred however confident it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue
from axiom.schema.registry import SchemaRegistry, default_schema_root


class Requirement(str, Enum):
    """How strongly the regulation asks for a field."""

    MANDATORY = "mandatory"
    CONDITIONAL = "conditional"
    """Required once a delegated act names this product group. Scored, but separately."""

    RECOMMENDED = "recommended"

    @property
    def is_scored(self) -> bool:
        """Whether the field counts toward the headline readiness figure.

        Only mandatory fields do. Including conditional ones would let a score fall because of a
        requirement that does not yet apply to this category, and a number that moves for reasons
        outside the team's control is a number they will learn to ignore.
        """
        return self is Requirement.MANDATORY


class FieldStatus(str, Enum):
    """What this record can currently supply for one passport field."""

    VERIFIED = "verified"
    """A publishable value with at least one verified citation. Passport-ready."""

    UNVERIFIED = "unverified"
    """A value exists but is not publishable, or carries no verified citation. Not ready — a
    regulatory assertion resting on an unverified value is the case this whole system exists to
    refuse."""

    INFERRED = "inferred"
    """A value exists and was inferred. Never acceptable for a passport field, however high its
    confidence, because the attributes behind these fields all forbid inference by declaration."""

    ABSENT = "absent"
    """The profile maps this field to attributes the schema defines, and this record has no value
    for them. A data gap: re-extraction may fix it."""

    UNMAPPED = "unmapped"
    """No attribute in the dictionary can express this field. A schema gap: re-extraction can
    never fix it, and counting it as a zero would misdirect the effort to close it."""

    PARTIAL = "partial"
    """A field requiring several attributes has some but not all of them."""

    @property
    def is_ready(self) -> bool:
        return self is FieldStatus.VERIFIED

    @property
    def is_data_gap(self) -> bool:
        """Fixable by the pipeline."""
        return self in {
            FieldStatus.ABSENT,
            FieldStatus.UNVERIFIED,
            FieldStatus.INFERRED,
            FieldStatus.PARTIAL,
        }

    @property
    def is_schema_gap(self) -> bool:
        """Not fixable by the pipeline, at any effort."""
        return self is FieldStatus.UNMAPPED


@dataclass(frozen=True)
class PassportField:
    """One declared DPP field and how it maps onto the attribute dictionary."""

    id: str
    name: str
    group: str
    requirement: Requirement
    satisfied_by: tuple[str, ...] = ()
    satisfaction: str = "all"
    requires_verified_evidence: bool = True
    note: str | None = None

    @property
    def is_mapped(self) -> bool:
        return bool(self.satisfied_by)

    @property
    def needs_all(self) -> bool:
        return self.satisfaction == "all"


@dataclass(frozen=True)
class FieldAssessment:
    """What one record can supply for one field, and why not more."""

    field: PassportField
    status: FieldStatus
    reason: str
    contributing: tuple[str, ...] = ()
    """Attribute codes that supplied a verified value."""

    missing: tuple[str, ...] = ()
    """Attribute codes the field needs and this record cannot verify."""

    @property
    def is_ready(self) -> bool:
        return self.status.is_ready

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.field.id,
            "name": self.field.name,
            "group": self.field.group,
            "requirement": self.field.requirement.value,
            "status": self.status.value,
            "ready": self.is_ready,
            "data_gap": self.status.is_data_gap,
            "schema_gap": self.status.is_schema_gap,
            "reason": self.reason,
            "contributing_attributes": list(self.contributing),
            "missing_attributes": list(self.missing),
            "note": self.field.note,
        }


@dataclass
class PassportProfile:
    """The declared DPP field set, loaded from YAML."""

    name: str
    version: str
    regulation: str
    fields: tuple[PassportField, ...]
    description: str | None = None
    category_in_scope: bool = False
    category_note: str | None = None

    def __len__(self) -> int:
        return len(self.fields)

    @property
    def mandatory(self) -> tuple[PassportField, ...]:
        return tuple(f for f in self.fields if f.requirement.is_scored)

    def groups(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.group not in seen:
                seen.append(f.group)
        return seen

    def referenced_codes(self) -> set[str]:
        return {code for f in self.fields for code in f.satisfied_by}

    @classmethod
    def load(cls, path: Path | str) -> PassportProfile:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        fields = tuple(
            PassportField(
                id=raw["id"],
                name=raw["name"],
                group=raw["group"],
                requirement=Requirement(raw.get("requirement", "mandatory")),
                satisfied_by=tuple(raw.get("satisfied_by") or ()),
                satisfaction=raw.get("satisfaction", "all"),
                requires_verified_evidence=bool(raw.get("requires_verified_evidence", True)),
                note=raw.get("note"),
            )
            for raw in payload.get("fields", [])
        )
        duplicates = {f.id for f in fields if [g.id for g in fields].count(f.id) > 1}
        if duplicates:
            raise ValueError(f"DPP profile declares duplicate field ids: {sorted(duplicates)}")

        return cls(
            name=payload.get("name", "dpp"),
            version=str(payload.get("version", "0")),
            regulation=payload.get("regulation", ""),
            fields=fields,
            description=payload.get("description"),
            category_in_scope=bool(payload.get("category_in_scope", False)),
            category_note=payload.get("category_note"),
        )

    @classmethod
    def load_default(cls) -> PassportProfile:
        return cls.load(default_schema_root() / "dpp_profile.yaml")

    def check_against(self, registry: SchemaRegistry) -> list[str]:
        """Attribute codes the profile references that the schema does not define.

        Same contract the schema registry applies to its own cross-file references: a typo in a
        mapping must fail loudly, because the alternative is a field silently scored as an
        unfixable schema gap when in fact it was mapped to a misspelling.
        """
        return sorted(
            code for code in self.referenced_codes() if code not in set(registry.attribute_codes)
        )


@dataclass
class ReadinessReport:
    """One record's distance from a registrable passport."""

    sku: str
    profile: PassportProfile
    assessments: list[FieldAssessment] = field(default_factory=list)

    def ready(self) -> list[FieldAssessment]:
        return [a for a in self.assessments if a.is_ready]

    def scored(self) -> list[FieldAssessment]:
        return [a for a in self.assessments if a.field.requirement.is_scored]

    def data_gaps(self) -> list[FieldAssessment]:
        return [a for a in self.assessments if a.status.is_data_gap]

    def schema_gaps(self) -> list[FieldAssessment]:
        return [a for a in self.assessments if a.status.is_schema_gap]

    @property
    def readiness(self) -> float:
        """Share of mandatory fields that are passport-ready.

        Denominated over mandatory fields only, and *not* renormalised to exclude the unmapped
        ones. That is the deliberate choice: a field this schema cannot express is still a field
        the regulation will want, and quietly dropping it from the denominator would report a
        catalogue as nearly ready when most of the work has not been started.
        """
        scored = self.scored()
        if not scored:
            return 0.0
        return sum(1 for a in scored if a.is_ready) / len(scored)

    @property
    def addressable_readiness(self) -> float:
        """Readiness over the mandatory fields the schema *can* express.

        Reported alongside :attr:`readiness`, never instead of it. This is the number that tells a
        pipeline team how they are doing; the other is the number that tells a compliance officer
        how exposed the business is. Both are true and they answer different questions.
        """
        addressable = [a for a in self.scored() if not a.status.is_schema_gap]
        if not addressable:
            return 0.0
        return sum(1 for a in addressable if a.is_ready) / len(addressable)

    def by_group(self) -> dict[str, dict[str, int]]:
        grouped: dict[str, dict[str, int]] = {}
        for assessment in self.assessments:
            bucket = grouped.setdefault(
                assessment.field.group, {"total": 0, "ready": 0, "schema_gap": 0}
            )
            bucket["total"] += 1
            bucket["ready"] += int(assessment.is_ready)
            bucket["schema_gap"] += int(assessment.status.is_schema_gap)
        return grouped

    def summary(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "profile": self.profile.name,
            "profile_version": self.profile.version,
            "regulation": self.profile.regulation,
            "category_in_scope": self.profile.category_in_scope,
            "category_note": self.profile.category_note,
            "fields": len(self.assessments),
            "mandatory_fields": len(self.scored()),
            "ready": len(self.ready()),
            "readiness": round(self.readiness, 4),
            "addressable_readiness": round(self.addressable_readiness, 4),
            "data_gaps": len(self.data_gaps()),
            "schema_gaps": len(self.schema_gaps()),
            "by_group": self.by_group(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "assessments": [a.to_dict() for a in self.assessments],
        }

    def payload(self) -> dict[str, Any]:
        """The passport payload as far as it can honestly be assembled.

        Every field appears, including the ones with nothing behind them, and each carries its own
        status. A consumer therefore cannot mistake an absent field for an asserted one, which is
        the failure mode a payload that simply omitted its gaps would invite.
        """
        return {
            "profile": f"{self.profile.name}@{self.profile.version}",
            "regulation": self.profile.regulation,
            "sku": self.sku,
            "registrable": self.readiness == 1.0,
            "fields": {
                a.field.id: {
                    "name": a.field.name,
                    "group": a.field.group,
                    "status": a.status.value,
                    "values": list(a.contributing),
                }
                for a in self.assessments
            },
        }


def assess_readiness(
    record: ProductRecord,
    registry: SchemaRegistry,
    profile: PassportProfile | None = None,
) -> ReadinessReport:
    """Score one record against the DPP profile."""
    profile = profile or PassportProfile.load_default()
    unknown = profile.check_against(registry)
    if unknown:
        raise KeyError(
            f"DPP profile '{profile.name}' references attributes the schema does not define: "
            f"{', '.join(unknown)}. A misspelled mapping would otherwise be scored as an "
            f"unfixable schema gap."
        )

    report = ReadinessReport(sku=record.sku, profile=profile)
    for declared in profile.fields:
        report.assessments.append(_assess(declared, record))
    return report


def _assess(declared: PassportField, record: ProductRecord) -> FieldAssessment:
    if not declared.is_mapped:
        return FieldAssessment(
            field=declared,
            status=FieldStatus.UNMAPPED,
            reason=(
                "no attribute in the dictionary expresses this field, so no amount of "
                "re-extraction will supply it"
            ),
        )

    verified: list[str] = []
    present_but_unusable: list[str] = []
    inferred: list[str] = []
    absent: list[str] = []

    for code in declared.satisfied_by:
        value = record.get(code)
        if value is None:
            absent.append(code)
        elif value.method.is_inference:
            inferred.append(code)
        elif _is_usable(value, declared):
            verified.append(code)
        else:
            present_but_unusable.append(code)

    satisfied = (
        len(verified) == len(declared.satisfied_by) if declared.needs_all else bool(verified)
    )
    missing = tuple(absent + present_but_unusable + inferred)

    if satisfied:
        return FieldAssessment(
            field=declared,
            status=FieldStatus.VERIFIED,
            reason=(
                "every attribute this field needs carries a publishable, verified value"
                if declared.needs_all and len(declared.satisfied_by) > 1
                else "a publishable, verified value is available"
            ),
            contributing=tuple(verified),
        )

    if inferred and not verified and not present_but_unusable:
        return FieldAssessment(
            field=declared,
            status=FieldStatus.INFERRED,
            reason=(
                "the only available value was inferred, and a regulatory assertion may never "
                "rest on inference"
            ),
            missing=missing,
        )

    if verified:
        return FieldAssessment(
            field=declared,
            status=FieldStatus.PARTIAL,
            reason=(
                f"{len(verified)} of {len(declared.satisfied_by)} required attributes are "
                f"verified; this field is not satisfiable until the rest are"
            ),
            contributing=tuple(verified),
            missing=missing,
        )

    if present_but_unusable:
        return FieldAssessment(
            field=declared,
            status=FieldStatus.UNVERIFIED,
            reason=(
                "a value exists but is not publishable or carries no verified citation, so it "
                "cannot support a regulatory assertion"
            ),
            missing=missing,
        )

    return FieldAssessment(
        field=declared,
        status=FieldStatus.ABSENT,
        reason="the schema can express this field but this record holds no value for it",
        missing=missing,
    )


def _is_usable(value: AttributeValue, declared: PassportField) -> bool:
    if not value.is_publishable:
        return False
    if declared.requires_verified_evidence and not value.has_verified_evidence:
        # A human-entered value is an accountable source and satisfies the evidence requirement
        # the same way it does everywhere else in this system: a named person standing behind a
        # figure is provenance, even without a document span.
        return value.method.is_human
    return True
