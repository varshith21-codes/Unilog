"""Writing a run to the places the console and the review workspace read.

Two artifacts per run, and they are not redundant:

*   ``data/sessions/{slug}.json`` is the review session — one SKU's worth of review work, with the
    evidence beside each value. It is what a reviewer opens and what decisions are recorded against.
*   ``data/console/{slug}.bundle.json`` is the wider projection: certificate, channel readiness,
    classification candidates and the validation report, none of which a session carries.

Persisting the bundle here rather than recomputing it in the API is what keeps model calls off the
request path. A dashboard that re-ran extraction on every page load would be both slow and
non-deterministic.

**Both filenames are slugged**, and that is a fix rather than a preference. ``run_pipeline`` wrote
``{sku}.json`` with the raw part number while the API read ``{slug}.json``, so the two disagreed for
any SKU needing an escape — and ``52C3-5/8-UPC`` is a fractional size, not an edge case. The write
either landed in a directory that does not exist or created one nobody meant to. A part number
needing no escaping is its own slug, so every artifact already on disk keeps resolving and no
migration is required. See :mod:`axiom.core.naming`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from axiom.console import (
    build_bundle,
    jsonable,
    serialise_class,
    serialise_cost,
    serialise_document,
    serialise_pages,
)
from axiom.core.naming import sku_slug
from axiom.pipeline.source import ResolvedSource
from axiom.pipeline.stages import PipelineRun
from axiom.review import ReviewSession, build_session


@dataclass(frozen=True)
class PersistedPaths:
    """Where a run ended up, and under what identifier it is addressable."""

    slug: str
    session: Path
    bundle: Path

    def relative_to(self, root: Path) -> dict[str, str]:
        """Both paths relative to a root, for reporting. Falls back to the absolute path rather
        than raising when a caller passes a root the files are not under."""
        out = {}
        for name, path in (("session", self.session), ("bundle", self.bundle)):
            try:
                out[name] = str(path.relative_to(root))
            except ValueError:
                out[name] = str(path)
        return out


def session_for(run: PipelineRun, parsed, registry) -> ReviewSession:
    """The review session for a run.

    The quality index is handed over from the certificate rather than recomputed, because the
    certificate is the signed artifact and a session reporting a different composite than the
    certificate it came from would make both untrustworthy.
    """
    return build_session(
        run.record,
        parsed,
        registry,
        run.decisions,
        run.scores,
        run.policy,
        quality=run.certificate.summary.quality_index.to_dict(),
    )


def source_summaries(
    source: ResolvedSource, retrieval, *, mpn: str
) -> list[dict[str, Any]]:
    """Project every source used or discovered by one enrichment for the console.

    The primary source keeps the identity-aware authority decision made by ``enrich_one``. Secondary
    retrieval entries are citable only when their manufacturer ID matches the resolved retrieval
    manufacturer; a tier name alone is not publisher verification.
    """
    artifact = source.artifact
    entries = {
        entry.sha256: entry
        for entry in (retrieval.entries if retrieval is not None else ())
    }
    expected_manufacturer_id = (
        retrieval.manufacturer.id
        if retrieval is not None and retrieval.manufacturer is not None
        else None
    )
    ordered_hashes = [artifact.document.sha256, *entries]
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    for digest in ordered_hashes:
        if digest in seen:
            continue
        seen.add(digest)
        primary = digest == artifact.document.sha256
        entry = entries.get(digest)
        if entry is not None:
            url = entry.source_uri
            tier = source.source_tier if primary else entry.tier
            citable = (
                source.citable_as_manufacturer
                if primary
                else (
                    entry.tier == "manufacturer"
                    and entry.manufacturer_id is not None
                    and expected_manufacturer_id is not None
                    and entry.manufacturer_id == expected_manufacturer_id
                )
            )
            sources.append(
                {
                    "document_id": entry.document_id,
                    "url": url,
                    "host": entry.host,
                    "tier": tier,
                    "doc_type": entry.doc_type,
                    "sha256": entry.sha256,
                    # "manufacturer" marks a source read on the maker's authority: find_sku could
                    # not locate the part in its text (a JS tab, a datasheet drawing), but it is the
                    # manufacturer's own page for this part and was extracted anyway. Distinct from
                    # "linked", which is fetched-but-not-read, so the panel can tell a source that
                    # contributed from one that was merely referenced.
                    "covers_this_sku": entry.covers.get(
                        mpn, "manufacturer" if (not primary and citable) else "linked"
                    ),
                    "citable_as_manufacturer": citable,
                    "license_note": entry.license_note,
                    "revision_label": entry.revision_label,
                }
            )
            continue

        document = artifact.document
        url = document.uri
        submission = url.startswith("submission:")
        sources.append(
            {
                "document_id": document.document_id,
                "url": url,
                "host": urlparse(url).hostname or "",
                "tier": source.source_tier,
                "doc_type": document.doc_type.value,
                "sha256": document.sha256,
                "covers_this_sku": "submission" if submission else "document",
                "citable_as_manufacturer": source.citable_as_manufacturer,
                "license_note": document.license_note,
                "revision_label": document.revision_label,
            }
        )

    return sources


def bundle_payload(
    run: PipelineRun,
    *,
    parsed,
    artifact,
    registry,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One SKU's console bundle, as a JSON-ready dict.

    The document and class definition travel with the bundle rather than being looked up by the API.
    A bundle has to stay readable against the schema version it was produced under — if the API
    resolved the class at read time, editing a YAML file would silently rewrite the history of every
    run that came before it.
    """
    bundle = build_bundle(
        registry=registry,
        record=run.record,
        artifact=artifact,
        classification=run.classification,
        extraction=run.extraction,
        normalization_issues=run.normalization_issues,
        validation=run.validation,
        cost=serialise_cost(
            run.usage,
            cost_usd=run.cost_usd,
            cost_by_tier=run.usage.cost_by_tier(run.tier_prices),
            prices=run.prices,
        ),
        copy=run.serialised_copy,
        scores=run.scores,
        features=run.feature_explanations(),
        decisions=run.decisions,
        certificate=run.certificate,
        exports=run.exports,
        sources=sources,
    )
    return {
        "bundle": bundle,
        "document": serialise_document(artifact, parsed),
        "pages": serialise_pages(parsed),
        "class_definition": (
            serialise_class(registry, run.record.class_code) if run.record.class_code else None
        ),
        "policy": jsonable(run.policy.summary()),
        "calibrator": "trained" if run.calibrator.is_trained else "untrained-heuristic",
    }


def persist_run(
    run: PipelineRun,
    *,
    parsed,
    artifact,
    registry,
    sessions_dir: Path | str,
    console_dir: Path | str,
    sources: list[dict[str, Any]] | None = None,
) -> PersistedPaths:
    """Write the session and the console bundle. Returns where they went."""
    slug = sku_slug(run.record.sku)

    session_path = session_for(run, parsed, registry).save(Path(sessions_dir) / f"{slug}.json")

    bundle_path = Path(console_dir) / f"{slug}.bundle.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(
        json.dumps(
            bundle_payload(
                run,
                parsed=parsed,
                artifact=artifact,
                registry=registry,
                sources=sources,
            ),
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return PersistedPaths(slug=slug, session=session_path, bundle=bundle_path)


__all__ = [
    "PersistedPaths",
    "bundle_payload",
    "persist_run",
    "session_for",
    "source_summaries",
]
