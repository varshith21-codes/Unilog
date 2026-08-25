"""Enriching one SKU from a part number, a manufacturer name, and whatever else was typed.

Every other entry point into this system needs a file. ``POST /api/delivery/export`` filters rows
already present in an uploaded item master; ``scripts/run_pipeline.py`` returns 2 without a source
document. Someone holding a part number, a manufacturer name and a link to the datasheet had to
build a six-column CSV first. This is the path that removes that step.

The interesting decisions are all about what this refuses to do.

**Description or URL is required, even though only the part number and the manufacturer are.** A
part number identifies a product but does not describe one. With neither field, ``classify_row``'s
reasoning applies exactly — ``(None, "no_description")`` — a row with no class has no attribute for
an extracted token to bind to, and extraction has nothing to read. The only honest output is an
identity-only row, produced *after* paying for two model calls. So the submission is refused with
that explanation rather than run. Cheaper, and consistent with how the CLI refuses ``--dry-run
--save-session``.

**A description is read, and a document supersedes it.** Both run when both are present. The
description pass is deterministic, class-scoped, and cites the substring it read from; the document
pass then supersedes it, because a datasheet *states* a fact where a description only implies it.
:meth:`ProductRecord.add_value` supersedes rather than appends, so the description reading stays in
history rather than colliding with the stronger one. This is the ordering
:mod:`axiom.delivery.batch` already documents as load-bearing, and it is enforced inside
:func:`~axiom.pipeline.stages.run_stages` rather than left to a caller to remember.

**A typed manufacturer name is a proposal, not a verified fact.** It is screened by
:func:`resolve_manufacturer` like any other ``Part_Manuf`` value, so ``Appliance Dealers
Cooperative`` comes back flagged rather than published, and the flag travels in the result.

**This spends real money.** Two model calls per run, three with copy generation. That is the whole
point — the offline path exists and produces a structurally complete, specification-poor row — but
it is the reason the caller above this one needs caps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from axiom.confidence import DEFAULT_EPSILON
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue
from axiom.delivery.source import ManufacturerResolution, resolve_manufacturer
from axiom.docintel import title_block
from axiom.extract import ModelCascade
from axiom.extract.description import AbbreviationTable, extract_from_description
from axiom.extract.description import to_attribute_values as description_values
from axiom.ingest.fabric import IngestedArtifact
from axiom.ingest.store import ArtifactStore
from axiom.pipeline.retrieval import RetrievalAttempt, retrieve_documents
from axiom.pipeline.source import ResolvedSource, ingest_submission, resolve_source
from axiom.pipeline.stages import PipelineRun, SecondarySource, run_stages
from axiom.retrieve.library import DocumentEntry
from axiom.retrieve.policy import SourcePolicy, SourceTier
from axiom.retrieve.policy import load_default as load_source_policy
from axiom.review import queue_summary
from axiom.schema import SchemaRegistry


def _artifact_for(parsed, retrieval: RetrievalAttempt) -> IngestedArtifact:
    """Present a retrieved document as an :class:`IngestedArtifact`.

    The pipeline downstream takes an artifact, because that is what every other arrival channel
    produces. A library entry holds the same facts under different names, and the bytes are already
    in the same content-addressed store — so this is a rename, not a re-ingest. Nothing is fetched
    and nothing is written.

    ``was_already_stored`` is True by definition here: the document is in the library, which is an
    index *over* the store.
    """
    entry = next(
        (e for e in retrieval.entries if e.sha256 == parsed.document.sha256),
        None,
    )
    return IngestedArtifact(
        document=parsed.document,
        storage_uri=entry.storage_uri if entry else parsed.document.uri,
        size_bytes=entry.size_bytes if entry else 0,
        original_filename=entry.filename if entry else None,
        was_already_stored=True,
    )


def _source_authority(
    source: ResolvedSource,
    retrieval: RetrievalAttempt | None,
    policy: SourcePolicy | None,
    manufacturer: ManufacturerResolution,
    brand: str | None,
) -> tuple[str, bool]:
    """Return the persisted source tier and whether it may support manufacturer-named claims."""
    if not source.from_url:
        return "submission", False

    effective = policy or load_source_policy()
    entry = None
    if retrieval is not None:
        entry = next(
            (
                candidate
                for candidate in retrieval.entries
                if candidate.sha256 == source.artifact.document.sha256
            ),
            None,
        )
    if entry is not None:
        expected_id = retrieval.manufacturer.id if retrieval and retrieval.manufacturer else None
        citable = (
            entry.tier == SourceTier.MANUFACTURER.value
            and entry.manufacturer_id is not None
            and expected_id is not None
            and entry.manufacturer_id == expected_id
        )
        return entry.tier, citable

    verdict = effective.classify(source.artifact.document.uri)
    expected = effective.manufacturer_for(
        vendor_code=manufacturer.supplier_code,
        vendor_name=manufacturer.name,
        brand=brand,
    )
    citable = (
        verdict.citable_as_manufacturer
        and expected is not None
        and verdict.manufacturer_id == expected.id
    )
    return verdict.tier.value, citable


def _entry_citable_as_manufacturer(
    entry: DocumentEntry | None, retrieval: RetrievalAttempt
) -> bool:
    """Whether one retrieved document may support manufacturer-named claims.

    The same rule ``_source_authority`` applies to the primary, factored out so a secondary
    document is judged on its own tier and manufacturer id rather than inheriting the primary's.
    A manufacturer's own product page earns citable manufacturer specifications; a retailer listing
    fetched in the same run does not, even when it is read in the same pass.
    """
    if entry is None:
        return False
    expected_id = retrieval.manufacturer.id if retrieval.manufacturer else None
    return (
        entry.tier == SourceTier.MANUFACTURER.value
        and entry.manufacturer_id is not None
        and expected_id is not None
        and entry.manufacturer_id == expected_id
    )


class InsufficientInputError(ValueError):
    """The submission cannot produce anything worth paying for.

    A typed exception rather than a message, because the caller that can act on this is a form
    telling someone which field to fill in, and re-parsing a sentence to recover that would be
    silly.
    """

    def __init__(self, message: str, *, missing: Sequence[str] = ()) -> None:
        self.missing = tuple(missing)
        super().__init__(message)


@dataclass(frozen=True)
class EnrichmentRequest:
    """One product to enrich, as somebody would type it.

    Frozen because it travels from an HTTP handler through several functions and nothing downstream
    has any business editing what was asked for.
    """

    mpn: str
    manufacturer: str | None = None
    description: str | None = None
    source_url: str | None = None
    brand: str | None = None
    retrieve: bool = True
    """Look for the manufacturer's own document when no URL was supplied.

    On by default, because it is what makes a part number and a manufacturer name sufficient on
    their
    own — which is the whole point of the form. Turn it off for a deterministic, offline run: with
    it
    off, a submission with neither a description nor a URL is refused, because then there really is
    nothing to read. See :mod:`axiom.pipeline.retrieval`.

    It makes no model call either way. It makes HTTP requests to the manufacturer's site, gated by
    :mod:`axiom.retrieve.policy` and ``robots.txt``.
    """
    refresh_sources: bool = False
    """Bypass stored SKU coverage once to look for a richer live product source and datasheet."""

    merge_sources: bool = True
    """Read every retrieved document, not only the strongest one.

    On by default: when retrieval returns a manufacturer product page, its datasheet PDF and a
    third-party listing for the same part, the primary is only one vantage point, and the others
    carry the "Technical details" and datasheet values a single source omits. Each secondary is
    extracted on its own authority and merged as candidate values plus deduplicated manufacturer
    specifications, so a conflict stays visible rather than being resolved by which source sorted
    first. Turn it off for a strictly single-document run — the behaviour before this existed —
    when reproducibility against one exact source matters more than coverage."""

    class_code: str | None = None
    """Forced class, bypassing classification. The fallback when classification abstains, too — the
    same double duty ``run_pipeline``'s ``--class-code`` has."""

    supplier_id: str | None = None
    tenant_id: str = "demo"
    include_optional: bool = False
    risk_budget: float = DEFAULT_EPSILON
    generate_copy: bool = False
    tier: str = "volume"

    def __post_init__(self) -> None:
        if not self.mpn or not self.mpn.strip():
            raise InsufficientInputError(
                "a manufacturer part number is required: it is the identity of the record and "
                "the token extraction looks for in the source document",
                missing=["mpn"],
            )
        # Only enforced with retrieval off. With it on, the manufacturer name *is* the lead: the
        # library is consulted, and then the manufacturer's own site is searched using the search
        # form it published. So a part number and a manufacturer are genuinely sufficient, and
        # refusing them would be refusing the thing this form is for.
        if not self.retrieve and not self.has_content:
            raise InsufficientInputError(
                "with retrieval off, a description or a manufacturer URL is required. A part "
                "number identifies a product but does not describe one: with neither field and "
                "nowhere to look, classification abstains (there is nothing to classify), "
                "extraction has nothing to read, and the only honest output is an identity-only "
                "row — after paying for two model calls. Type a description, paste a link to the "
                "datasheet, or leave retrieval on and let it find the document.",
                missing=["description", "source_url"],
            )

    @property
    def has_content(self) -> bool:
        return bool((self.description or "").strip() or (self.source_url or "").strip())

    @property
    def clean_mpn(self) -> str:
        return self.mpn.strip()

    def summary(self) -> dict[str, object]:
        return {
            "mpn": self.clean_mpn,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "source_url": self.source_url,
            "brand": self.brand,
            "class_code": self.class_code,
            "include_optional": self.include_optional,
            "refresh_sources": self.refresh_sources,
            "risk_budget": self.risk_budget,
            "generate_copy": self.generate_copy,
        }


@dataclass
class EnrichmentResult:
    """What one enrichment run produced, before anything is written or rendered."""

    request: EnrichmentRequest
    run: PipelineRun
    source: ResolvedSource
    submission: ResolvedSource
    """The typed fields as a stored document. The same object as ``source`` with no URL given."""

    manufacturer: ManufacturerResolution
    from_description: int = 0
    description_refused: list[tuple[str, str]] = field(default_factory=list)
    retrieval: RetrievalAttempt | None = None
    """What retrieval tried, when no URL was supplied. None when it was not attempted."""

    @property
    def record(self) -> ProductRecord:
        return self.run.record

    @property
    def sku(self) -> str:
        return self.run.record.sku

    def summary(self) -> dict[str, object]:
        """The run, as numbers. What the screen reports and what the response carries."""
        run = self.run
        certificate = run.certificate
        quality = certificate.summary.quality_index
        return {
            "sku": self.sku,
            "class_code": run.class_code,
            "class_from_fallback": run.class_code_from_fallback,
            "classification": run.classification.summary(),
            "extraction": run.extraction.summary(),
            "from_description": {
                "extracted": self.from_description,
                "refused": len(self.description_refused),
            },
            "values": {
                "total": len(run.record.current_values()),
                "publishable": len(run.record.publishable_values()),
                "needing_review": len(run.record.values_needing_review()),
            },
            "manufacturer_specifications": {
                "total": len(run.record.manufacturer_specifications),
                "mapped": sum(
                    1
                    for specification in run.record.manufacturer_specifications
                    if specification.mapped_attribute_code is not None
                ),
                "unmapped": sum(
                    1
                    for specification in run.record.manufacturer_specifications
                    if specification.mapped_attribute_code is None
                ),
            },
            "gaps": {
                "total": len(run.record.gaps),
                "required": sum(1 for gap in run.record.gaps if gap.is_required),
            },
            "validation": run.validation.summary(),
            "certificate": {
                "certificate_id": certificate.certificate_id,
                "signature_verified": certificate.verify_signature(),
                "pipeline_version": certificate.pipeline_version,
                "generated_at": str(certificate.generated_at),
                "quality_index": quality.to_dict(),
                "attributes_populated": certificate.summary.attributes_populated,
                "attributes_with_evidence": certificate.summary.attributes_with_evidence,
            },
            "cost": {
                "usd": run.cost_usd,
                "calls": run.usage.calls,
                "escalations": run.usage.escalations,
                "input_tokens": run.usage.input_tokens,
                "output_tokens": run.usage.output_tokens,
                "latency_ms": run.usage.latency_ms,
            },
            "policy": run.policy.summary(),
            "calibrator": "trained" if run.calibrator.is_trained else "untrained-heuristic",
            "retrieval": (
                self.retrieval.summary()
                if self.retrieval is not None
                else {"attempted": False, "found": False}
            ),
            "source": self.source.summary(),
            "manufacturer": self.manufacturer.summary(),
            "brand": {
                "requested": self.request.brand,
                "resolved": run.brand.brand.name if run.brand and run.brand.resolved else None,
                "method": run.brand.method if run.brand else None,
            },
            "channels": [
                {
                    "name": name,
                    "published": export.published,
                    "value_count": export.value_count,
                    "withheld": list(export.withheld),
                }
                for name, export in run.exports.items()
            ],
            "notes": list(run.notes),
        }

    def queue(self, registry: SchemaRegistry) -> dict[str, object]:
        """The review queue this run produced, in the shape the console already renders."""
        from axiom.pipeline.persist import session_for

        return queue_summary(session_for(self.run, self.source.parsed, registry))


def enrich_one(
    request: EnrichmentRequest,
    *,
    registry: SchemaRegistry,
    client,
    cascade: ModelCascade | None = None,
    store: ArtifactStore,
    calibration_dir: Path | str,
    fetcher=None,
    abbreviations: AbbreviationTable | None = None,
    library_path: Path | str | None = None,
    library=None,
    source_policy=None,
    search=None,
    renderer=None,
) -> EnrichmentResult:
    """Enrich one SKU end to end. Makes real model calls through ``client``.

    ``client`` is injected rather than constructed, which is what lets the whole of this be tested
    with :class:`~axiom.extract.StubModelClient` against the same code the Bedrock path runs.

    ``library_path`` turns retrieval on. Without it — or with ``request.retrieve`` False — the only
    routes to a document are a supplied URL and the submission itself, which is the behaviour before
    retrieval was wired in. ``search`` supplies the open-web arm; there is deliberately no default,
    because search is an external service with a key and a bill.
    """
    cascade = cascade or ModelCascade.load()
    mpn = request.clean_mpn
    description = (request.description or "").strip() or None
    source_url = (request.source_url or "").strip() or None

    # `Part_Manuf` screening, on a typed value, for the same reason it exists for a supplied one:
    # most values in this field are manufacturers and a substantial minority are distributors and
    # buying co-ops, and writing one of those into MANUFACTURER_NAME would be confidently wrong.
    manufacturer = resolve_manufacturer(request.manufacturer)

    # --- find the document ---------------------------------------------------------
    #
    # Three routes, in the order their answers are worth. A supplied URL wins because somebody
    # already knew the answer. Failing that, retrieval looks: the library first (no request), then
    # the manufacturer's own site read through its own search form. Failing *that*, the typed fields
    # become the source, which is honest and weak.
    #
    # Retrieval before the fallback, not after, and the reason is cost asymmetry: retrieval spends
    # bandwidth, extraction spends tokens. Reading a real datasheet is the difference between
    # thirteen gaps and thirteen cited values, and it is the cheaper half of the run.
    retrieval: RetrievalAttempt | None = None
    source: ResolvedSource | None = None

    if not source_url and request.retrieve and library_path is not None:
        retrieval = retrieve_documents(
            mpn,
            store=store,
            library_path=library_path,
            manufacturer=manufacturer.name,
            vendor_code=manufacturer.supplier_code,
            brand=request.brand,
            policy=source_policy,
            search=search,
            renderer=renderer,
            fetcher=fetcher,
            refresh_sources=request.refresh_sources,
            library=library,
        )
        if retrieval.primary is not None:
            parsed = retrieval.primary
            source = ResolvedSource(
                artifact=_artifact_for(parsed, retrieval),
                parsed=parsed,
                from_url=True,
            )

    if source is None:
        source = resolve_source(
            store,
            mpn=mpn,
            manufacturer=manufacturer.name,
            description=description,
            brand=request.brand,
            source_url=source_url,
            supplier_id=request.supplier_id,
            fetcher=fetcher,
        )

    source_tier, citable_as_manufacturer = _source_authority(
        source, retrieval, source_policy, manufacturer, request.brand
    )
    source = replace(
        source,
        source_tier=source_tier,
        citable_as_manufacturer=citable_as_manufacturer,
    )

    # The other retrieved documents, read alongside the primary rather than discarded. Two groups:
    # the remaining coverage (documents[1:] — a second body-text source for the part), and the
    # manufacturer's supplementary reading (its product page and datasheet, admitted on the maker's
    # authority when the SKU renders client-side or lives in a drawing find_sku cannot see). Each is
    # judged for manufacturer authority on its own entry, so a manufacturer page contributes citable
    # specifications even when the primary is an untrusted listing. Skipped when merging is off or a
    # URL was supplied by hand.
    extra_documents: list[SecondarySource] = []
    if retrieval is not None and request.merge_sources and source.from_url:
        seen_sha = {source.artifact.document.sha256}
        candidates = [*retrieval.documents, *retrieval.supplementary]
        for document in candidates:
            sha = document.document.sha256
            if sha in seen_sha:
                continue
            seen_sha.add(sha)
            entry = next(
                (e for e in retrieval.entries if e.sha256 == sha), None
            )
            extra_documents.append(
                SecondarySource(
                    parsed=document,
                    document_id=document.document.document_id,
                    citable_as_manufacturer=_entry_citable_as_manufacturer(
                        entry, retrieval
                    ),
                )
            )

    # When a datasheet is the primary source, the typed fields are stored as a second document so a
    # description-derived value cites text that actually contains it. Free: the bytes are hashed and
    # deduplicated, and no model reads them.
    submission = source
    if source.from_url and description:
        submission = ingest_submission(
            store,
            mpn=mpn,
            manufacturer=manufacturer.name,
            description=description,
            brand=request.brand,
            supplier_id=request.supplier_id,
        )

    counted: dict[str, int] = {"from_description": 0}
    refused: list[tuple[str, str]] = []

    def seed(record: ProductRecord) -> Sequence[AttributeValue]:
        """The description pass, run before any document value so the document supersedes it."""
        if not description or not record.class_code:
            return []
        extraction = extract_from_description(
            description,
            registry=registry,
            class_code=record.class_code,
            abbreviations=abbreviations if abbreviations is not None else AbbreviationTable.load(),
        )
        values = description_values(
            extraction,
            document_id=submission.artifact.document.document_id,
            document_sha256=submission.artifact.document.sha256,
            schema_version=registry.product_class(record.class_code).schema_version,
            # False, and this is the difference between this path and the offline delivery run.
            # There a description value publishes on its own provenance because no calibrated
            # policy is available; here one is, and letting the policy decide is what the flag is
            # documented for.
            accept=False,
        )
        counted["from_description"] = len(values)
        refused.extend(extraction.refused)
        if submission.artifact.document.document_id not in record.source_document_ids:
            record.source_document_ids.append(submission.artifact.document.document_id)
        return values

    run = run_stages(
        source.parsed,
        source.artifact,
        registry=registry,
        client=client,
        cascade=cascade,
        sku=mpn,
        mpn=mpn,
        tenant_id=request.tenant_id,
        brand=request.brand,
        supplier_id=request.supplier_id,
        class_code_fallback=request.class_code,
        calibration_dir=calibration_dir,
        risk_budget=request.risk_budget,
        include_optional=request.include_optional,
        tier=request.tier,
        generate_copy=request.generate_copy,
        seed_values=seed,
        # What the classifier should read, most specific first.
        #
        # The description when there is one: it is a direct statement of what the product *is*
        # ("Kichler Pendant Lt"), which is what `axiom.delivery.batch` classifies on. Otherwise the
        # retrieved document's title block, which is what `tests/test_minimal_input.py` established
        # as the link that was missing — a blank description made classification abstain, so no
        # attributes were requested, so a perfectly good retrieved datasheet went unread.
        #
        # None falls through to the whole document, which is correct for a submission (three lines)
        # and for a single-product datasheet.
        classify_text=description or (
            title_block(source.parsed) if source.from_url else None
        ),
        manufacturer_source_verified=source.citable_as_manufacturer,
        extra_documents=extra_documents,
    )

    if manufacturer.looks_like_a_distributor:
        run.notes.append(
            f"{manufacturer.name!r} looks like a distributor or buying co-op rather than a "
            f"manufacturer. It is recorded and echoed, and MANUFACTURER_NAME still needs the "
            f"approved master or the manufacturer URL to confirm it."
        )
    if source.from_url and description and counted["from_description"] == 0:
        run.notes.append(
            "the description evidenced no attribute for this class, so every value came from the "
            "document"
        )
    if retrieval is not None:
        run.notes.extend(retrieval.notes)
    if source.from_url and not source.citable_as_manufacturer:
        run.notes.append(
            "the fetched source was not verified as manufacturer-owned. Typed values remain "
            "cited, but source-native manufacturer specifications were withheld"
        )
    if not source.from_url:
        run.notes.append(
            "no manufacturer document was found, so the submission itself is the source. Every "
            "citation resolves to a field you typed, at its hash — a real provenance claim, and a "
            "weaker one than a datasheet."
        )

    return EnrichmentResult(
        request=request,
        run=run,
        source=source,
        submission=submission,
        manufacturer=manufacturer,
        from_description=counted["from_description"],
        description_refused=refused,
        retrieval=retrieval,
    )


__all__ = ["EnrichmentRequest", "EnrichmentResult", "InsufficientInputError", "enrich_one"]
