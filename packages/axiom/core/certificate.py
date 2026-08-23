"""The Enrichment Certificate — the audit artifact that makes explainability tangible.

One signed JSON document per SKU, showing for every value: the source, the method, the
model and prompt version, the validation results, and who reviewed it. Plus the gaps, with
what was searched. Plus the cost and the quality index.

This is the object you hand to a judge, an auditor, or a compliance officer. It answers
"how do I know this is right" in a form that survives scrutiny.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from axiom.core.product import ProductRecord
from axiom.core.values import ValueStatus


@dataclass(frozen=True)
class RichnessComponents:
    """What the blueprint means by richness: "assets, relationships, copy depth, channel
    readiness" (Part 5, M11).

    Each component is ``None`` when this build cannot observe it, and that is deliberately not the
    same as ``0.0``. Two of the four depend on modules that do not exist yet — there is no asset
    intelligence and no relationship graph — and scoring them zero would report a data-quality
    deficit where the truth is a missing feature. It is the same distinction the validation layers
    draw between SKIPPED and FAIL, for the same reason.
    """

    channel_readiness: float | None = None
    """Share of output channels that passed pre-flight. Always observable once exports are built."""

    copy_depth: float | None = None
    """Share of copy fields populated, once copy has cleared its gates.

    ``None`` when copy was never attempted, ``0.0`` when it was attempted and produced nothing
    publishable. Those are different facts: the first is a pipeline option nobody selected, the
    second is a real absence of usable prose.
    """

    relationships: float | None = None
    """Parent-child and cross-reference density. ``None`` until M9 exists."""

    assets: float | None = None
    """Image and document coverage. ``None`` until M10 exists."""

    def score(self) -> float | None:
        """Mean of the observable components, or None when none of them are.

        An unweighted mean over what is present, rather than fixed weights over four slots. With
        two of four permanently unobservable, fixed weights would cap richness at half regardless
        of how good the data was.
        """
        present = [
            value
            for value in (
                self.channel_readiness,
                self.copy_depth,
                self.relationships,
                self.assets,
            )
            if value is not None
        ]
        if not present:
            return None
        return round(sum(present) / len(present), 4)

    def observed(self) -> list[str]:
        return [
            name
            for name, value in (
                ("channel_readiness", self.channel_readiness),
                ("copy_depth", self.copy_depth),
                ("relationships", self.relationships),
                ("assets", self.assets),
            )
            if value is not None
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_readiness": self.channel_readiness,
            "copy_depth": self.copy_depth,
            "relationships": self.relationships,
            "assets": self.assets,
            "score": self.score(),
            "observed": self.observed(),
        }


class QualityIndex(BaseModel):
    """The four scored dimensions plus a composite. See blueprint Part 5, module M11."""

    model_config = ConfigDict(frozen=True)

    completeness: float = Field(ge=0.0, le=1.0)
    verifiability: float = Field(ge=0.0, le=1.0)
    consistency: float = Field(ge=0.0, le=1.0)
    richness: float | None = Field(default=None, ge=0.0, le=1.0)
    """None when nothing about richness could be observed — not zero.

    This distinction is the whole point. Richness carries a tenth of the composite weight, so
    treating an unmeasured dimension as a zero understated every composite this system reported by
    up to ten points, for a reason that had nothing to do with the data.
    """

    self_declared: float | None = Field(default=None, ge=0.0, le=1.0)
    """Share of required attributes the *client's own input* suggests a value for.

    Diagnostic, and deliberately **not** part of the composite. It exists so a zero completeness
    can be read correctly: "nothing established" and "nothing there" are very different states, and
    without this number they are indistinguishable. A SKU at ``completeness 0.0`` with
    ``self_declared 0.45`` has a rich description and no retrieved document — the work item is
    retrieval. At ``self_declared 0.0`` the description is uninformative too, and the work item is
    a supplier request.

    Scoring it would defeat the purpose. The composite is what a distributor is asked to trust, and
    folding the input into it would let a catalogue raise its own score by restating itself.
    """

    corroborated: float | None = Field(default=None, ge=0.0, le=1.0)
    """Share of required attributes an independent source *confirmed* the input's suggestion on.

    Also unscored, and the most useful of the three for telling whether retrieval is working rather
    than merely running: high ``self_declared`` with near-zero ``corroborated`` means documents are
    arriving that do not speak to what the descriptions claim.
    """

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "completeness": 0.35,
            "verifiability": 0.30,
            "consistency": 0.25,
            "richness": 0.10,
        }
    )

    @property
    def measured(self) -> dict[str, float]:
        """The dimensions the composite is computed over.

        ``self_declared`` and ``corroborated`` are excluded by construction, not by omission from
        ``weights``: they are provenance diagnostics rather than quality dimensions, and a future
        edit that gave them a weight would silently reintroduce input-as-enrichment into the one
        number the console leads with.
        """
        scored = {
            "completeness": self.completeness,
            "verifiability": self.verifiability,
            "consistency": self.consistency,
        }
        if self.richness is not None:
            scored["richness"] = self.richness
        return scored

    @computed_field
    @property
    def measured_dimensions(self) -> list[str]:
        """Which dimensions the composite spans.

        Serialised so a reader can tell a score over three dimensions from one over four.
        """
        return sorted(self.measured)

    # A computed field rather than a plain property, so `model_dump` carries it.
    #
    # This is the fix for a real duplication: because `composite` was a bare property, pydantic
    # left it out of the serialised bundle, and the console reimplemented the weighting in
    # TypeScript to get it back. The formula therefore existed in two languages and could disagree
    # with itself — which it would have, the moment richness became optionally unmeasured.
    @computed_field
    @property
    def composite(self) -> float:
        """Weighted mean over the dimensions that were measured, renormalised.

        Renormalisation is what makes an absent dimension harmless. Multiplying an unmeasured
        richness by 0.10 and adding zero does not leave the composite alone — it drags it down by
        a tenth, which is indistinguishable from a product with genuinely no assets, no copy and
        no channel readiness. Dividing by the weight actually applied says instead: this is the
        score across what we could see.
        """
        scored = self.measured
        total = sum(self.weights.get(name, 0.0) for name in scored)
        if total <= 0:
            return 0.0
        weighted = sum(value * self.weights.get(name, 0.0) for name, value in scored.items())
        return round(weighted / total, 4)

    def to_dict(self) -> dict[str, float | None | list[str]]:
        return {
            "completeness": round(self.completeness, 4),
            "verifiability": round(self.verifiability, 4),
            "consistency": round(self.consistency, 4),
            "richness": None if self.richness is None else round(self.richness, 4),
            "self_declared": (
                None if self.self_declared is None else round(self.self_declared, 4)
            ),
            "corroborated": (
                None if self.corroborated is None else round(self.corroborated, 4)
            ),
            "composite": self.composite,
            # So a reader can tell a composite over four dimensions from one over three.
            "measured_dimensions": sorted(self.measured),
        }


class CertificateSummary(BaseModel):
    """Headline counts. These are the numbers that go on the scoreboard slide."""

    model_config = ConfigDict(frozen=True)

    attributes_required: int
    attributes_populated: int
    attributes_with_evidence: int
    attributes_inferred: int
    auto_accepted: int
    queued_for_review: int
    gaps_total: int
    gaps_required: int
    quality_index: QualityIndex
    cost_usd: float | None = None
    wall_clock_seconds: float | None = None

    def to_dict(self) -> dict:
        out: dict = {
            "attributes_required": self.attributes_required,
            "attributes_populated": self.attributes_populated,
            "attributes_with_evidence": self.attributes_with_evidence,
            "attributes_inferred": self.attributes_inferred,
            "auto_accepted": self.auto_accepted,
            "queued_for_review": self.queued_for_review,
            "gaps_total": self.gaps_total,
            "gaps_required": self.gaps_required,
            "quality_index": self.quality_index.to_dict(),
        }
        if self.cost_usd is not None:
            out["cost_usd"] = round(self.cost_usd, 4)
        if self.wall_clock_seconds is not None:
            out["wall_clock_seconds"] = round(self.wall_clock_seconds, 2)
        return out


class EnrichmentCertificate(BaseModel):
    """Signed, machine-readable provenance record for one SKU."""

    model_config = ConfigDict(frozen=True)

    certificate_id: str
    sku: str
    tenant_id: str
    generated_at: datetime
    schema_version: str | None
    pipeline_version: str
    classifications: list[dict]
    attributes: list[dict]
    gaps: list[dict]
    summary: CertificateSummary
    signature: str

    def to_json(self, *, indent: int = 2) -> str:
        payload = self._payload()
        payload["signature"] = self.signature
        return json.dumps(payload, indent=indent, default=str)

    def _payload(self) -> dict:
        return {
            "certificate_id": self.certificate_id,
            "sku": self.sku,
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at.isoformat(),
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "classifications": self.classifications,
            "attributes": self.attributes,
            "gaps": self.gaps,
            "summary": self.summary.to_dict(),
        }

    def verify_signature(self) -> bool:
        """Recompute the content hash and compare. Detects post-hoc tampering."""
        return self.signature == _sign(self._payload())


def _sign(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def richness_for(
    record: ProductRecord,
    *,
    exports: Mapping[str, object] | None = None,
    copy: Mapping[str, object] | None = None,
) -> RichnessComponents:
    """Observe what can be observed about richness.

    ``exports`` is the channel map from ``axiom.syndicate.export_all``; ``copy`` is a serialised
    :class:`axiom.generate.GeneratedCopy`. Both are passed as plain mappings rather than imported
    types so that ``core`` stays at the bottom of the dependency graph — syndicate and generate both
    sit above it, and importing either here would invert the layering.
    """
    channel_readiness: float | None = None
    if exports:
        published = sum(1 for export in exports.values() if getattr(export, "published", False))
        channel_readiness = round(published / len(exports), 4)

    copy_depth: float | None = None
    if copy is not None:
        if copy.get("published"):
            fields = ("headline", "short_description", "long_description", "bullets")
            filled = sum(1 for name in fields if copy.get(name))
            copy_depth = round(filled / len(fields), 4)
        else:
            # Attempted and withheld. A real absence of publishable prose, unlike never trying.
            copy_depth = 0.0

    del record  # reserved for relationship density once M9 exists

    return RichnessComponents(
        channel_readiness=channel_readiness,
        copy_depth=copy_depth,
        # Left unobserved rather than zeroed. A standalone product legitimately has no parent, so
        # `parent_sku is None` is not evidence of thin data, and there is no cross-reference graph
        # to measure density against until M9 exists.
        relationships=None,
        assets=None,
    )


def quality_index_for(
    record: ProductRecord,
    required_attribute_codes: list[str],
    *,
    richness: float | None = None,
) -> QualityIndex:
    """Score one record's quality index.

    Split out of :func:`build_certificate` so the before/after cohort scores an un-enriched item
    master through *this* function rather than a parallel one. Two implementations of the same
    index would drift, and a cohort comparison whose two arms were scored by different code would
    be measuring the code rather than the enrichment.
    """
    current = record.current_values()

    consistency = 1.0
    if current:
        clean = sum(1 for v in current if not v.failed_validations())
        consistency = clean / len(current)

    return QualityIndex(
        completeness=record.fill_rate(required_attribute_codes),
        verifiability=record.verifiability(),
        consistency=consistency,
        richness=richness,
        self_declared=record.self_declared_rate(required_attribute_codes),
        corroborated=record.corroborated_rate(required_attribute_codes),
    )


def build_certificate(
    record: ProductRecord,
    *,
    required_attribute_codes: list[str],
    pipeline_version: str,
    cost_usd: float | None = None,
    wall_clock_seconds: float | None = None,
    richness: float | None = None,
    exports: Mapping[str, object] | None = None,
    copy: Mapping[str, object] | None = None,
) -> EnrichmentCertificate:
    """Assemble a certificate from a product record.

    Only *publishable* values are certified. Candidates and queued values are counted in
    the summary but are not presented as certified facts, which is the whole point.

    Pass ``exports`` and ``copy`` to have richness observed from them. Passing ``richness``
    directly overrides that, which is what the cohort does — it has neither, and needs the
    dimension left unmeasured rather than inferred from their absence.
    """
    current = record.current_values()
    publishable = record.publishable_values()

    if richness is None and (exports is not None or copy is not None):
        richness = richness_for(record, exports=exports, copy=copy).score()

    quality = quality_index_for(record, required_attribute_codes, richness=richness)

    summary = CertificateSummary(
        attributes_required=len(required_attribute_codes),
        attributes_populated=len(publishable),
        attributes_with_evidence=sum(1 for v in publishable if v.has_verified_evidence),
        attributes_inferred=sum(1 for v in publishable if v.method.is_inference),
        auto_accepted=sum(1 for v in current if v.status == ValueStatus.AUTO_ACCEPTED),
        queued_for_review=len(record.values_needing_review()),
        gaps_total=len(record.gaps),
        gaps_required=sum(1 for g in record.gaps if g.is_required),
        quality_index=quality,
        cost_usd=cost_usd,
        wall_clock_seconds=wall_clock_seconds,
    )

    generated_at = datetime.now(UTC)
    certificate_id = "ec_" + hashlib.sha256(
        f"{record.tenant_id}:{record.sku}:{generated_at.isoformat()}".encode()
    ).hexdigest()[:12]

    payload_core = {
        "certificate_id": certificate_id,
        "sku": record.sku,
        "tenant_id": record.tenant_id,
        "generated_at": generated_at.isoformat(),
        "schema_version": record.schema_version,
        "pipeline_version": pipeline_version,
        "classifications": [
            {
                "scheme": c.scheme.value,
                "code": c.code,
                "path": c.path,
                "confidence": round(c.confidence, 4),
                "method": c.method,
                "status": (
                    "requires_human_confirmation"
                    if not c.is_publishable
                    else "confirmed"
                    if c.reviewed_by
                    else "auto"
                ),
                "alternatives": c.alternatives,
            }
            for c in record.classifications
        ],
        "attributes": [v.to_certificate_entry() for v in publishable],
        "gaps": [g.to_certificate_entry() for g in record.gaps],
        "summary": summary.to_dict(),
    }

    return EnrichmentCertificate(
        certificate_id=certificate_id,
        sku=record.sku,
        tenant_id=record.tenant_id,
        generated_at=generated_at,
        schema_version=record.schema_version,
        pipeline_version=pipeline_version,
        classifications=payload_core["classifications"],
        attributes=payload_core["attributes"],
        gaps=payload_core["gaps"],
        summary=summary,
        signature=_sign(payload_core),
    )
