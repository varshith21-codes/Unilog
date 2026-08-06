"""Projection of pipeline output into the shape the console renders.

This is a **presentation** layer and nothing more. It performs no extraction, no validation and
no scoring; it takes the artifacts a pipeline run already produced and flattens them into JSON.
Keeping it here rather than in a script is what lets the batch fixture exporter and the HTTP API
serve byte-identical shapes — when the projection lived in `scripts/`, the API had no way to
reuse it and would have grown a second, quietly divergent copy.

Two joins happen here on purpose, because doing them in the browser would be worse:

*   **Attribute bindings are joined to dictionary definitions.** A class binding carries
    requirement and weight; the dictionary carries name, datatype, unit and permitted values.
    The console needs both halves on one object, and resolving that client-side would mean
    shipping the whole attribute dictionary to every page.
*   **Values are joined to their score, features and acceptance decision.** These are computed
    in three different places in the pipeline and are useless apart: a score without the
    decision it produced cannot explain itself.

Documents and class definitions are **normalized into keyed maps** at the dataset level rather
than embedded per SKU. Page geometry is by far the largest thing here, and five SKUs cut from
one datasheet would otherwise carry five copies of it. Each bundle references its document by
id and its class by code.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


def jsonable(obj: Any) -> Any:
    """Recursively convert Pydantic models, dataclasses and enums to plain JSON types."""
    if isinstance(obj, BaseModel):
        return jsonable(obj.model_dump(mode="json"))
    if is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value") and type(obj).__mro__[1] is not object:
        return obj.value
    return obj


def serialise_pages(parsed) -> list[dict[str, Any]]:
    """Pages with line and cell geometry, so the evidence viewer can position highlights.

    Coordinates are PDF points with the origin at top-left (see
    ``axiom.core.evidence.BoundingBox``). Page width and height travel with them so the client
    can scale into CSS pixels without guessing at the page size.
    """
    pages = []
    for page in parsed.pages:
        pages.append(
            {
                "number": page.number,
                "width": page.width,
                "height": page.height,
                "lines": [
                    {
                        "line_index": line.line_index,
                        "text": line.text,
                        "bbox": line.bbox.as_list(),
                    }
                    for line in page.lines
                ],
                "tables": [
                    {
                        "table_id": table.table_id,
                        "page": table.page,
                        "row_count": table.row_count,
                        "col_count": table.col_count,
                        "bbox": table.bbox.as_list(),
                        "rows": table.rows(),
                        "cells": [
                            {
                                "row": cell.row,
                                "col": cell.col,
                                "text": cell.text,
                                "bbox": cell.bbox.as_list(),
                            }
                            for cell in table.cells
                        ],
                    }
                    for table in page.tables
                ],
            }
        )
    return pages


def serialise_document(artifact, parsed) -> dict[str, Any]:
    """The source document joined to what parsing discovered about it."""
    return {
        **jsonable(artifact.document),
        "parser": parsed.parser,
        "page_count": parsed.page_count,
        "line_count": len(parsed.all_lines()),
        "table_count": len(parsed.all_tables()),
        "warnings": list(parsed.warnings),
        "size_bytes": artifact.size_bytes,
        "storage_uri": artifact.storage_uri,
    }


def serialise_class(registry, class_code: str) -> dict[str, Any]:
    """A class definition with each attribute binding joined to its dictionary entry."""
    cls = registry.product_class(class_code)
    attributes = []
    for binding in cls.attributes:
        definition = registry.attribute(binding.code)
        attributes.append(
            {
                "code": binding.code,
                "requirement": binding.requirement.value,
                "weight": binding.weight,
                "name": definition.name,
                "datatype": definition.datatype.value,
                "description": definition.description,
                "canonical_unit": definition.canonical_unit,
                "display_preference": definition.display_preference,
                "quantity_kind": definition.quantity_kind,
                "multivalued": definition.multivalued,
                "compliance_claim": definition.compliance_claim,
                "evidence_requirement": definition.evidence_requirement.value,
                "example_values": list(definition.example_values),
                "allowed_values": [
                    {"value": a.value, "aliases": list(a.aliases), "note": a.note}
                    for a in definition.allowed_values
                ],
                "plausible_range": (
                    list(definition.plausible_range) if definition.plausible_range else None
                ),
            }
        )
    return {
        "code": cls.code,
        "name": cls.name,
        "version": cls.version,
        "schema_version": cls.schema_version,
        "browse_path": list(cls.browse_path),
        "mappings": dict(cls.mappings),
        "required_codes": list(cls.required_codes),
        "attributes": attributes,
        "cross_field_rules": [
            {
                "id": rule.id,
                "expr": rule.expr,
                "severity": rule.severity.value,
                "message": " ".join(rule.message.split()),
                "references": list(rule.references),
            }
            for rule in cls.cross_field_rules
        ],
        "channel_profiles": [
            {
                "name": profile.name,
                "title_template": profile.title_template,
                "max_title_chars": profile.max_title_chars,
                "unit_system": profile.unit_system,
                "required": list(profile.required),
            }
            for profile in cls.channel_profiles
        ],
    }


def serialise_values(
    record,
    *,
    scores: dict[str, float],
    features: dict[str, dict[str, float]],
    decisions,
) -> list[dict[str, Any]]:
    """Current values, each joined to the score, features and decision that judged it."""
    by_code = {d.attribute_code: d for d in decisions}
    out = []
    for value in sorted(record.current_values(), key=lambda v: v.attribute_code):
        decision = by_code.get(value.attribute_code)
        out.append(
            {
                **jsonable(value),
                "score": round(scores.get(value.attribute_code, 0.0), 4),
                "decision": jsonable(decision) if decision else None,
                "features": features.get(value.attribute_code, {}),
                "is_publishable": value.is_publishable,
                "has_verified_evidence": value.has_verified_evidence,
                "citation_summary": value.citation_summary(),
            }
        )
    return out


def serialise_copy(generated) -> dict[str, Any] | None:
    """Generated marketing copy together with the claim check that gated it.

    The verdict travels with the prose, always. Copy shown without its claim check is just text,
    and the entire argument for generating it at all is that every assertion in it was verified
    against an already-publishable attribute. A UI that displayed one without the other would be
    making a claim this system does not support.
    """
    if generated is None:
        return None
    return generated.to_dict()


def serialise_cost(
    usage,
    *,
    cost_usd: float | None,
    cost_by_tier: dict[str, float] | None,
    prices=None,
) -> dict[str, Any]:
    """Token spend for one SKU, with the price table's provenance attached.

    ``cost_usd`` is None whenever a tier that was actually used has no published price. The
    console must render that as "not available" rather than as zero — a free-looking SKU would
    be read as a result rather than as a missing input.
    """
    return {
        "calls": usage.calls,
        "escalations": usage.escalations,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": usage.latency_ms,
        "calls_by_tier": dict(usage.by_tier),
        "input_by_tier": dict(usage.input_by_tier),
        "output_by_tier": dict(usage.output_by_tier),
        "cost_usd": cost_usd,
        "cost_by_tier": cost_by_tier,
        "priced": cost_usd is not None,
        "price_source": prices.summary() if prices is not None else None,
    }


def build_bundle(
    *,
    registry,
    record,
    artifact,
    classification,
    extraction,
    normalization_issues,
    validation,
    scores: dict[str, float],
    features: dict[str, dict[str, float]],
    decisions,
    certificate,
    exports,
    cost: dict[str, Any] | None = None,
    copy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one SKU's pipeline run.

    Everything passed in is already computed. This function must not run a model, hit the
    network, or change a verdict — if it did, the console would be showing something the
    pipeline never actually produced, which defeats the point of the whole system.
    """
    class_code = record.class_code
    required = registry.required_codes(class_code) if class_code else frozenset()

    return {
        "sku": record.sku,
        "document_id": artifact.document.document_id,
        "class_code": class_code,
        "record": {
            "tenant_id": record.tenant_id,
            "sku": record.sku,
            "mpn": record.mpn,
            "mpn_normalized": record.mpn_normalized,
            "gtin": record.gtin,
            "brand": record.brand,
            "brand_id": record.brand_id,
            "supplier_id": record.supplier_id,
            "lifecycle_status": record.lifecycle_status.value,
            "class_code": class_code,
            "schema_version": record.schema_version,
            "source_document_ids": list(record.source_document_ids),
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        },
        "classifications": jsonable(record.classifications),
        "classification_summary": jsonable(classification.summary()),
        "classification_candidates": [
            {"code": c.code, "score": round(c.score, 4), "path_text": c.path_text}
            for c in classification.candidates
        ],
        "values": serialise_values(
            record, scores=scores, features=features, decisions=decisions
        ),
        "gaps": jsonable(record.gaps),
        "extraction": jsonable(extraction.summary()),
        "normalization_issues": jsonable(normalization_issues),
        "validation": {
            **jsonable(validation.summary()),
            "results": jsonable(validation.results),
            "per_attribute": {k: jsonable(v) for k, v in validation.per_attribute.items()},
        },
        "certificate": {
            **jsonable(certificate),
            "signature_verified": certificate.verify_signature(),
        },
        "channels": [
            {
                "name": name,
                "published": export.published,
                "value_count": export.value_count,
                "withheld": list(export.withheld),
                "readiness": jsonable(export.readiness.summary()),
                "title": getattr(export, "title", None),
            }
            for name, export in exports.items()
        ],
        "metrics": {
            "fill_rate": round(record.fill_rate(required), 4),
            "verifiability": round(record.verifiability(), 4),
            "values_total": len(record.current_values()),
            "values_publishable": len(record.publishable_values()),
            "values_needing_review": len(record.values_needing_review()),
            "gaps_total": len(record.gaps),
            "gaps_required": sum(1 for g in record.gaps if g.is_required),
            "conflicts": sorted(record.conflicts().keys()),
        },
        "cost": cost,
        "copy": copy,
    }


PUBLISHABLE_STATUSES = frozenset({"auto_accepted", "human_approved"})

# The weights a certificate written before richness was observable used.
_LEGACY_WEIGHTS = {
    "completeness": 0.35,
    "verifiability": 0.30,
    "consistency": 0.25,
    "richness": 0.10,
}


def normalise_quality_index(bundle: dict[str, Any]) -> dict[str, Any]:
    """Bring an older certificate's quality index up to the current shape, on read.

    A bundle written before richness became observable recorded ``richness: 0.0`` and no
    ``composite``. That zero was never a measurement — nothing computed it, the field simply
    defaulted — so it is reinterpreted here as *unmeasured*, and the composite is renormalised over
    the three dimensions that were genuinely scored.

    Done at read time, deliberately, and only to the projection. The certificate's signature covers
    its summary, so rewriting the stored bytes would invalidate the very attestation that makes the
    document worth having. The signed original stays exactly as it was; what the console renders is
    a view of it.

    A bundle already carrying ``measured_dimensions`` is returned untouched.
    """
    certificate = bundle.get("certificate")
    if not isinstance(certificate, dict):
        return bundle

    summary = certificate.get("summary")
    if not isinstance(summary, dict):
        return bundle

    quality = summary.get("quality_index")
    if not isinstance(quality, dict) or "measured_dimensions" in quality:
        return bundle

    weights = quality.get("weights") or _LEGACY_WEIGHTS
    scored = {
        name: float(quality[name])
        for name in ("completeness", "verifiability", "consistency")
        if isinstance(quality.get(name), int | float)
    }
    total = sum(weights.get(name, 0.0) for name in scored)
    composite = (
        round(sum(value * weights.get(name, 0.0) for name, value in scored.items()) / total, 4)
        if total > 0
        else 0.0
    )

    return {
        **bundle,
        "certificate": {
            **certificate,
            "summary": {
                **summary,
                "quality_index": {
                    **quality,
                    "richness": None,
                    "composite": composite,
                    "measured_dimensions": sorted(scored),
                    # So a reader is not left wondering why this certificate looks different.
                    "reinterpreted": (
                        "written before richness was observable; its recorded 0.0 was a default "
                        "rather than a measurement, so the composite is renormalised over the "
                        "three dimensions that were scored"
                    ),
                },
            },
        },
    }


def overlay_review_decisions(bundle: dict[str, Any], session) -> dict[str, Any]:
    """Fold a review session's human decisions onto a projected bundle.

    The two artifacts are deliberately separate. A bundle is the immutable record of what the
    *pipeline* produced for a given schema and model version — rewriting it when a reviewer
    clicks accept would destroy the ability to ask "what did the machine actually say?", which
    is the question every accuracy metric depends on. A session holds what *humans* decided.

    So the dataset is the join, computed at read time. A reviewer sees their decision
    immediately, the pipeline's own output stays intact underneath it, and nothing has to be
    written twice.

    Returns the bundle unchanged when no human decision has been recorded.
    """
    reviewed = {
        item.attribute_code: item
        for item in session.items
        if item.reason_code.startswith("human_")
    }
    if not reviewed:
        return bundle

    values: list[dict[str, Any]] = []
    for value in bundle["values"]:
        item = reviewed.get(value["attribute_code"])
        if item is None:
            values.append(value)
            continue
        values.append(
            {
                **value,
                "status": item.status,
                "value_raw": item.value_raw,
                "value_display": item.value_display,
                "value_canonical": item.value_canonical,
                "method": item.method,
                "is_publishable": item.status in PUBLISHABLE_STATUSES,
                # Kept so the UI can distinguish "the model got this right and a human
                # confirmed it" from "a human had to fix it".
                "review_reason": item.reason_code,
                "review_detail": item.detail,
            }
        )

    needing_review = sum(1 for v in values if v["status"] not in PUBLISHABLE_STATUSES)
    return {
        **bundle,
        "values": values,
        "metrics": {
            **bundle["metrics"],
            "values_publishable": len(values) - needing_review,
            "values_needing_review": needing_review,
            "values_reviewed": len(reviewed),
        },
        "decisions": list(session.decisions),
    }


def overlay_cross_source(bundle: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Fold validation layer L4's findings onto a projected bundle.

    Separate from the bundle for the same reason a review session is: the bundle records what a
    *single-source* run produced, and a later multi-source analysis rewriting it would destroy the
    ability to ask what that run said on its own. So the join is computed at read time.

    Two things are attached. A ``cross_source`` block carrying the whole report, and a per-value
    ``cross_source`` verdict so the review workspace can mark an individual attribute as
    corroborated by a second document, superseded by a newer one, or in unresolved conflict —
    which is the level a reviewer actually acts at.
    """
    report = payload.get("report")
    if not isinstance(report, dict):
        return bundle

    corroborated = set(report.get("corroborated_attributes") or ())
    single_source = set(report.get("single_source_attributes") or ())

    conflicts: dict[str, dict[str, Any]] = {}
    for conflict in report.get("conflicts") or ():
        if isinstance(conflict, dict) and conflict.get("attribute_code"):
            conflicts[str(conflict["attribute_code"])] = conflict

    values: list[dict[str, Any]] = []
    for value in bundle.get("values", []):
        code = value.get("attribute_code")
        verdict: dict[str, Any] | None = None

        if code in conflicts:
            conflict = conflicts[code]
            verdict = {
                "state": "superseded" if conflict.get("resolved") else "conflict",
                "reason": conflict.get("reason"),
                "winner": conflict.get("winner"),
                "observations": conflict.get("observations") or [],
            }
        elif code in corroborated:
            verdict = {"state": "corroborated", "reason": None, "observations": []}
        elif code in single_source:
            # Recorded rather than omitted. "Only one source mentions this" is a weaker position
            # than "two sources agree", and a reviewer deciding what to trust needs to see which.
            verdict = {"state": "single_source", "reason": None, "observations": []}

        values.append({**value, "cross_source": verdict} if verdict else value)

    return {
        **bundle,
        "values": values,
        "cross_source": {
            "generated_at": payload.get("generated_at"),
            "dry_run": bool(payload.get("dry_run")),
            "sources": payload.get("sources") or [],
            **report,
        },
    }


def build_dataset(
    bundles: list[dict[str, Any]],
    *,
    documents: dict[str, dict[str, Any]],
    class_definitions: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full console dataset.

    ``policy`` is genuinely global — one calibration governs the whole run, so a per-SKU copy
    would invite the idea that thresholds vary by product, which they do not.
    """
    return {
        "meta": {"generated_at": datetime.now(UTC).isoformat(), **meta},
        "documents": documents,
        "class_definitions": class_definitions,
        "policy": policy,
        "skus": bundles,
    }


def dataset_stats(dataset: dict[str, Any]) -> dict[str, int | float]:
    """Counts for a CLI summary or a health endpoint."""
    skus = dataset.get("skus", [])
    values = [v for s in skus for v in s["values"]]
    return {
        "skus": len(skus),
        "documents": len(dataset.get("documents", {})),
        "classes": len(dataset.get("class_definitions", {})),
        "values": len(values),
        "verified": sum(1 for v in values if v["has_verified_evidence"]),
        "auto_accepted": sum(1 for v in values if v["status"] == "auto_accepted"),
        "queued": sum(1 for v in values if v["status"] == "queued_for_review"),
        "gaps": sum(len(s["gaps"]) for s in skus),
    }
