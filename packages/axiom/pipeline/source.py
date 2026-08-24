"""Turning a typed submission into a document the pipeline can cite.

The pipeline is single-document by construction: :meth:`Extractor.extract` and
:func:`axiom.review.build_session` each take one :class:`ParsedDocument`, so a run has exactly one
primary source. A part number is not a document and cannot find one — ``axiom.ingest.web`` states
plainly that it is "not a crawler. One URL, one artifact, no link following." So a submission
arrives one of two ways, and they are not equivalent.

**With a URL**, the fetched bytes are the source. That is an ordinary datasheet run: hashed on the
way in, cited by page and line, and the evidence viewer renders the real document.

**Without one, the submission itself becomes the source.** The typed fields are laid out as text,
hashed, and stored through :func:`ingest_bytes` with a ``submission:`` URI — exactly as
:func:`axiom.delivery.batch.parse_documents` does with ``upload:``. Classification then reads the
description, extraction cites into it, and the citation says something true: *you told us this, at
this hash, at this time.* It is a real provenance claim. It is also a weaker one than a datasheet —
a description implies where a specification states — which is why the two are labelled differently
everywhere they surface rather than being flattened into "a source".

The layout below is stable and deliberately boring. Two things depend on it: quote location, which
is a substring check against ``full_text``, and the line-based evidence viewer, which highlights by
line index. Reordering the lines would move every citation in every previously stored submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiom.core.evidence import DocumentType
from axiom.core.naming import sku_slug
from axiom.docintel import ParsedDocument, parse_artifact
from axiom.ingest import IngestedArtifact, ingest_bytes, ingest_url
from axiom.ingest.store import ArtifactStore

SUBMISSION_MAX_BYTES = 8 * 1024 * 1024
"""A tighter ceiling than the ingest default of 32 MiB.

The 32 MiB default is sized for a supplier sending a catalogue. This path is one datasheet named by
someone typing into a form on an endpoint with no authentication, so the ceiling is the one that
bounds what an unauthenticated caller can make this process buffer, not the one that accommodates
the largest legitimate document. A datasheet over 8 MiB exists; the answer for it is the CLI, which
streams and has no request timeout."""

SUBMISSION_TIMEOUT = 20.0
"""Shorter than the ingest default for the same reason: this sits inside an HTTP request that a
browser is waiting on, and a fetch that takes half a minute has already failed as an interaction."""


@dataclass(frozen=True)
class ResolvedSource:
    """The document a submission will be enriched from, and which of the two shapes it is."""

    artifact: IngestedArtifact
    parsed: ParsedDocument

    from_url: bool
    """True when the bytes came off the internet, rather than from the submitted fields."""

    source_tier: str = "submission"
    citable_as_manufacturer: bool = False
    """Set only after source policy establishes that the final publisher is the manufacturer."""

    @property
    def kind(self) -> str:
        return "document" if self.from_url else "submission"

    def summary(self) -> dict[str, object]:
        document = self.artifact.document
        return {
            "kind": self.kind,
            "document_id": document.document_id,
            "sha256": document.sha256,
            "uri": document.uri,
            "doc_type": document.doc_type.value,
            "size_bytes": self.artifact.size_bytes,
            "pages": self.parsed.page_count,
            "tables": len(self.parsed.all_tables()),
            "parser": self.parsed.parser,
            "was_already_stored": self.artifact.was_already_stored,
            "warnings": list(self.parsed.warnings),
            "source_tier": self.source_tier,
            "citable_as_manufacturer": self.citable_as_manufacturer,
            "evidential_weight": (
                "A manufacturer-owned document. Values cite the page and line they were read from."
                if self.citable_as_manufacturer
                else (
                    "A fetched document whose publisher was not verified as the manufacturer. "
                    "Typed values remain cited; source-native manufacturer claims are withheld."
                    if self.from_url
                    else "The submission itself. Values cite the field they were read from, which "
                    "records what was supplied rather than what a manufacturer published."
                )
            ),
        }


def submission_text(
    *,
    mpn: str,
    manufacturer: str | None = None,
    description: str | None = None,
    brand: str | None = None,
) -> str:
    """The typed fields as a document.

    Field-per-line with a ``Label: value`` shape, because that is what the text parser's dot-leader
    and label heuristics already read well, and because a quote lifted from it reads as a citation
    rather than as a fragment.

    No timestamp in the bytes, deliberately. Adding one would change the hash on every submission
    of identical facts, which throws away the only thing content addressing buys — resubmitting the
    same product is then a no-op that reuses the stored artifact and its citations. *When* it was
    submitted is recorded on the document's ``fetched_at``, where it belongs.
    """
    lines = [f"Manufacturer Part Number: {mpn}"]
    if manufacturer:
        lines.append(f"Manufacturer: {manufacturer}")
    if brand:
        lines.append(f"Brand: {brand}")
    if description:
        lines.append(f"Description: {description}")
    # Trailing newline so the last line is terminated like every other document the parser sees.
    return "\n".join(lines) + "\n"


def ingest_submission(
    store: ArtifactStore,
    *,
    mpn: str,
    manufacturer: str | None = None,
    description: str | None = None,
    brand: str | None = None,
    supplier_id: str | None = None,
) -> ResolvedSource:
    """Store the typed fields as a document in their own right.

    Called for the primary source when no URL was given, and *also* alongside a fetched datasheet
    whenever a description was typed. The second case is the one worth explaining: a value read out
    of the description has to cite something, and citing the datasheet would be a false citation —
    the text is not in it. So the submission is stored either way and the description's values cite
    the submission, exactly as :func:`axiom.delivery.batch.run_batch` has description values cite
    the item master rather than the attached datasheet.

    Storing it twice costs nothing. ``ingest_bytes`` is content-addressed, so the second call finds
    identical bytes already present and reports ``was_already_stored``.
    """
    text = submission_text(
        mpn=mpn, manufacturer=manufacturer, description=description, brand=brand
    )
    artifact = ingest_bytes(
        text.encode("utf-8"),
        store,
        # `.txt` so the store key carries a suffix a reader can make sense of. Parsing does not
        # depend on it — `parse_artifact` dispatches on content — but the artifact endpoint's
        # media-type allowlist does.
        filename=f"{sku_slug(mpn)}.submission.txt",
        # Not the store path. This is the field that makes the citation say where the bytes came
        # from, and "someone submitted them for this part number" is the honest answer.
        source_uri=f"submission:{mpn}",
        supplier_id=supplier_id,
        # Declared rather than sniffed. Magic bytes would call this a spec sheet, which is precisely
        # the overclaim this whole module exists to avoid.
        doc_type=DocumentType.SUPPLIER_FEED,
        license_note="submitted through the enrichment form; not a manufacturer publication",
    )
    raw = store.get(artifact.storage_uri)
    return ResolvedSource(
        artifact=artifact, parsed=parse_artifact(raw, artifact.document), from_url=False
    )


def resolve_source(
    store: ArtifactStore,
    *,
    mpn: str,
    manufacturer: str | None = None,
    description: str | None = None,
    brand: str | None = None,
    source_url: str | None = None,
    supplier_id: str | None = None,
    license_note: str | None = None,
    max_bytes: int = SUBMISSION_MAX_BYTES,
    timeout: float = SUBMISSION_TIMEOUT,
    fetcher=None,
) -> ResolvedSource:
    """Resolve a submission to the one hashed, parsed document extraction will read.

    ``fetcher`` is injectable for the same reason :func:`ingest_url` makes it injectable: a test
    that needs the internet to check this branch is a test that gets skipped. Injecting one also
    turns off the resolver guard, because an injected fetcher does not necessarily connect to
    anything.
    """
    if not source_url:
        return ingest_submission(
            store,
            mpn=mpn,
            manufacturer=manufacturer,
            description=description,
            brand=brand,
            supplier_id=supplier_id,
        )

    artifact = ingest_url(
        source_url,
        store,
        supplier_id=supplier_id,
        fetcher=fetcher,
        timeout=timeout,
        max_bytes=max_bytes,
        license_note=license_note,
        # The URL came from a caller, so the hostname is resolved and every answer checked. See
        # axiom.ingest.web._check_resolved for what this does and does not buy.
        verify_public_address=fetcher is None,
    )
    raw = store.get(artifact.storage_uri)
    return ResolvedSource(
        artifact=artifact, parsed=parse_artifact(raw, artifact.document), from_url=True
    )


__all__ = [
    "SUBMISSION_MAX_BYTES",
    "SUBMISSION_TIMEOUT",
    "ResolvedSource",
    "ingest_submission",
    "resolve_source",
    "submission_text",
]
