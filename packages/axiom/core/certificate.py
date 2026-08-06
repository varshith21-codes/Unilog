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
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from axiom.core.product import ProductRecord
from axiom.core.values import ValueStatus


class QualityIndex(BaseModel):
    """The four scored dimensions plus a composite. See blueprint Part 5, module M11."""

    model_config = ConfigDict(frozen=True)

    completeness: float = Field(ge=0.0, le=1.0)
    verifiability: float = Field(ge=0.0, le=1.0)
    consistency: float = Field(ge=0.0, le=1.0)
    richness: float = Field(ge=0.0, le=1.0)

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "completeness": 0.35,
            "verifiability": 0.30,
            "consistency": 0.25,
            "richness": 0.10,
        }
    )

    @property
    def composite(self) -> float:
        return round(
            self.completeness * self.weights["completeness"]
            + self.verifiability * self.weights["verifiability"]
            + self.consistency * self.weights["consistency"]
            + self.richness * self.weights["richness"],
            4,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "completeness": round(self.completeness, 4),
            "verifiability": round(self.verifiability, 4),
            "consistency": round(self.consistency, 4),
            "richness": round(self.richness, 4),
            "composite": self.composite,
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


def quality_index_for(
    record: ProductRecord,
    required_attribute_codes: list[str],
    *,
    richness: float = 0.0,
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
    )


def build_certificate(
    record: ProductRecord,
    *,
    required_attribute_codes: list[str],
    pipeline_version: str,
    cost_usd: float | None = None,
    wall_clock_seconds: float | None = None,
    richness: float = 0.0,
) -> EnrichmentCertificate:
    """Assemble a certificate from a product record.

    Only *publishable* values are certified. Candidates and queued values are counted in
    the summary but are not presented as certified facts, which is the whole point.
    """
    current = record.current_values()
    publishable = record.publishable_values()

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
