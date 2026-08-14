"""Assembling a revision pair for the drift report.

Drift compares two revisions of one document, and — exactly as with the cross-reference in
:mod:`axiom.resolve.catalogue` — the records on each side can come from two places that support
very different claims:

*   **Pipeline bundles.** Real extraction, verified citations, policy-accepted. What a production
    drift run would compare, and limited to the SKUs that have actually been through the pipeline.
*   **The golden corpus.** Ground truth for each revision, hand-authored by reading the datasheets.
    The values are not measured, so a report built on them exercises the *drift logic* rather than
    the extraction that would feed it.

The distinction travels in the payload rather than being left for a reader to infer, which is the
same contract the L4 artifact and the equivalence sweep carry.

**The revision label is read off the page.** Not from the filename, not from the file's
modification time — both describe when a copy was obtained rather than when the specification was
written, and ordering two revisions by retrieval time would let the order an operator happened to
download them decide which specification wins. When no marker can be found the label is left
``None``, which makes the pair unorderable and the report says so instead of guessing a direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiom.core.evidence import DocumentType, SourceDocument
from axiom.core.product import ProductRecord
from axiom.docintel.revision import find_revision
from axiom.ingest.store import sha256_file
from axiom.resolve.catalogue import CatalogueSource, records_from_golden
from axiom.schema.registry import SchemaRegistry


class RevisionPairError(Exception):
    """Raised when two golden sets cannot be read as a revision pair."""


@dataclass
class RevisionPair:
    """Two revisions of one document, with the records each revision supports."""

    before_document: SourceDocument
    after_document: SourceDocument
    before: dict[str, ProductRecord]
    after: dict[str, ProductRecord]
    source: CatalogueSource
    failures: list[str]

    @property
    def measured(self) -> bool:
        return self.source.is_measured

    @property
    def source_note(self) -> str:
        return self.source.note

    def common_skus(self) -> list[str]:
        return sorted(set(self.before) & set(self.after))

    def only_before(self) -> list[str]:
        """SKUs the old revision listed and the new one does not — discontinued, not drifted."""
        return sorted(set(self.before) - set(self.after))

    def only_after(self) -> list[str]:
        """SKUs the new revision introduces."""
        return sorted(set(self.after) - set(self.before))

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "measured": self.measured,
            "source_note": self.source_note,
            "before_document": self.before_document.document_id,
            "after_document": self.after_document.document_id,
            "before_revision": self.before_document.revision_label,
            "after_revision": self.after_document.revision_label,
            "common_skus": self.common_skus(),
            "discontinued_skus": self.only_before(),
            "introduced_skus": self.only_after(),
            "failures": list(self.failures),
        }


def golden_revision_pair(
    before_name: str,
    after_name: str,
    registry: SchemaRegistry,
) -> RevisionPair:
    """Read two golden sets as a revision pair, scoped to the document that was reissued.

    The two sides are deliberately asymmetric. The *after* set describes one reissued datasheet and
    must name exactly one source document. The *before* set is normally the full ground-truth
    corpus spanning several manufacturers, so it is **narrowed** to the single source the reissued
    SKUs came from.

    That narrowing is what keeps the report honest. Comparing a whole corpus against one reissued
    datasheet would file every SKU that datasheet never covered as "withdrawn", turning three
    untouched manufacturers into hundreds of spurious findings.
    """
    from axiom.evaluation.golden import GoldenSet

    before_set = GoldenSet.load_default(before_name)
    after_set = GoldenSet.load_default(after_name)

    after_document = _single_document(after_set, after_name)
    after_skus = {p.sku for p in after_set.products}

    origins = {p.source for p in before_set.products if p.sku in after_skus}
    if not origins:
        raise RevisionPairError(
            f"'{after_name}' names {len(after_skus)} SKU(s), none of which appear in "
            f"'{before_name}', so there is no earlier revision to compare against"
        )
    if len(origins) > 1:
        raise RevisionPairError(
            f"the SKUs in '{after_name}' come from {len(origins)} different source documents in "
            f"'{before_name}' ({', '.join(sorted(origins))}); a revision pair compares one "
            f"document against its own past, and spanning several would mix a reissue with "
            f"unrelated changes elsewhere"
        )

    origin = next(iter(origins))
    before_document = source_document(
        before_set.documents[origin], document_id=origin
    )

    # Only the products the reissued datasheet is the source for. Everything else in the corpus is
    # untouched by this revision and must not appear in its report.
    family = {p.sku for p in before_set.products if p.source == origin}

    before_catalogue = records_from_golden(registry, name=before_name)
    after_catalogue = records_from_golden(registry, name=after_name)

    return RevisionPair(
        before_document=before_document,
        after_document=after_document,
        before={r.sku: r for r in before_catalogue.records if r.sku in family},
        after={r.sku: r for r in after_catalogue.records},
        source=CatalogueSource.GOLDEN,
        failures=[*before_catalogue.failures, *after_catalogue.failures],
    )


def _single_document(golden_set, name: str) -> SourceDocument:
    """Build a content-addressed SourceDocument for a golden set's single source."""
    if len(golden_set.documents) != 1:
        raise RevisionPairError(
            f"golden set '{name}' declares {len(golden_set.documents)} source documents; the "
            f"newer side of a revision pair must name exactly one, since it stands for the "
            f"datasheet that was reissued"
        )

    source_id, path = next(iter(golden_set.documents.items()))
    return source_document(path, document_id=source_id)


def source_document(
    path: Path,
    *,
    document_id: str | None = None,
    supplier_id: str | None = None,
) -> SourceDocument:
    """Hash a file and read its revision marker off the page.

    The hash is real — it is the same :func:`~axiom.ingest.store.sha256_file` the ingest fabric
    uses, so a citation minted here resolves against the same bytes the pipeline would have stored.

    Built in two passes because the marker lives *inside* the document: a provisional record is
    needed to parse the bytes, and the label can only be attached once the parse has found it.
    """
    digest = sha256_file(path)
    provisional = SourceDocument(
        document_id=document_id or f"{path.stem}@{digest[:8]}",
        uri=path.as_uri(),
        sha256=digest,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime.now(UTC),
        supplier_id=supplier_id,
    )

    marker = find_revision(_text_of(path, provisional))
    if marker is None:
        return provisional
    return provisional.model_copy(update={"revision_label": marker.label})


def _text_of(path: Path, document: SourceDocument) -> str:
    """Best-effort text for revision scanning.

    A failure here is not fatal and must not be: it yields no revision marker, which makes the
    pair unorderable, which makes the report decline to claim a direction. That is the correct
    outcome — far better than a direction derived from a filename.
    """
    data = path.read_bytes()

    try:
        from axiom.docintel import parse_artifact

        return parse_artifact(data, document).full_text
    except Exception:  # noqa: BLE001 - any parse failure degrades to "no marker found"
        pass

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""
