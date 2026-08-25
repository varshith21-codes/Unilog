"""The document library: fetch a datasheet once, use it for every part it covers.

The artifact store already made *re-fetching the same bytes* free — identical content hashes to an
identical key, so the second write is a no-op. That is not the expensive question. The expensive
question is the one it cannot answer:

    *We are about to look for a datasheet for 49-94-0013. Do we already have a document that
    covers it?*

Answering that needs an index, because the store is addressed by hash and nothing about a hash says
which part numbers are inside. Without one, a thousand-row batch fetches a thousand times against
a catalogue that covers a hundred parts per file — and the wasted requests are the least of it. The
real cost is that the same document gets re-parsed and re-searched per row.

So this module records, per stored document: where it came from, what tier that host was, and
**which part numbers it was found to cover** — plus, just as importantly, which part numbers were
checked and found *absent*. Both halves matter:

*   ``covers`` turns one Milwaukee accessory catalogue into a source for all 108 Milwaukee rows.
*   ``absent`` stops the next run re-parsing a 200-page PDF to re-learn that a part is not in it.
    A negative result is a result, and this is the same argument the gap records make.

Coverage is decided by :func:`axiom.docintel.find_sku`, not by string matching, so a part listed in
an ordering row is distinguished from one merely mentioned, and a part the document *withdraws* is
recorded as withdrawn rather than as covered. Enriching a discontinued part from the note that
discontinues it would be worse than finding nothing.

The index is a plain JSON file beside the store. It is derived data and safe to delete: every fact
in it can be recomputed from the artifacts, at the cost of re-parsing them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from axiom.docintel import ParsedDocument, find_sku, parse_artifact
from axiom.ingest import IngestedArtifact, LocalArtifactStore
from axiom.retrieve.policy import SourceTier

INDEX_VERSION = 1


@dataclass
class DocumentEntry:
    """One stored document, and everything learned about what is in it."""

    sha256: str
    storage_uri: str
    document_id: str
    source_uri: str
    """Where the bytes came from — a URL for a fetched document, a ``file://`` path otherwise.
    This is the value that becomes the citation, so it is the URL and never the store path."""

    host: str = ""
    tier: str = SourceTier.UNKNOWN.value
    manufacturer_id: str | None = None
    doc_type: str = "unknown"
    filename: str | None = None
    size_bytes: int = 0
    fetched_at: str = ""
    revision_label: str | None = None
    license_note: str | None = None

    covers: dict[str, str] = field(default_factory=dict)
    """mpn -> how it was found: ``table`` for an ordering row, ``text`` for a mention.

    ``table`` is the strong signal. It means the document *offers* the part rather than referring
    to it, which is what separates a catalogue that can be extracted from for this SKU from one
    that happens to name it in a compatibility note.
    """

    absent: list[str] = field(default_factory=list)
    """Part numbers checked against this document and not found.

    Cached so the check is paid once rather than on every run."""

    withdrawn: dict[str, str] = field(default_factory=dict)
    """mpn -> the quote that retires it. Not coverage: the remedy is to delist, not to enrich."""

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "storage_uri": self.storage_uri,
            "document_id": self.document_id,
            "source_uri": self.source_uri,
            "host": self.host,
            "tier": self.tier,
            "manufacturer_id": self.manufacturer_id,
            "doc_type": self.doc_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "fetched_at": self.fetched_at,
            "revision_label": self.revision_label,
            "license_note": self.license_note,
            "covers": dict(sorted(self.covers.items())),
            "absent": sorted(self.absent),
            "withdrawn": dict(sorted(self.withdrawn.items())),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> DocumentEntry:
        return cls(
            sha256=str(payload["sha256"]),
            storage_uri=str(payload["storage_uri"]),
            document_id=str(payload.get("document_id") or ""),
            source_uri=str(payload.get("source_uri") or ""),
            host=str(payload.get("host") or ""),
            tier=str(payload.get("tier") or SourceTier.UNKNOWN.value),
            manufacturer_id=payload.get("manufacturer_id"),
            doc_type=str(payload.get("doc_type") or "unknown"),
            filename=payload.get("filename"),
            size_bytes=int(payload.get("size_bytes") or 0),
            fetched_at=str(payload.get("fetched_at") or ""),
            revision_label=payload.get("revision_label"),
            license_note=payload.get("license_note"),
            covers={str(k): str(v) for k, v in (payload.get("covers") or {}).items()},
            absent=[str(m) for m in payload.get("absent") or []],
            withdrawn={str(k): str(v) for k, v in (payload.get("withdrawn") or {}).items()},
        )


@dataclass(frozen=True)
class Coverage:
    """A document that covers a part number, and how strongly."""

    entry: DocumentEntry
    mpn: str
    how: str
    """``table``, ``text`` or ``manufacturer``. See :attr:`DocumentEntry.covers`.

    ``manufacturer`` is the one value not written into :attr:`DocumentEntry.covers`. It marks a
    document admitted to coverage on *authority* rather than on a body-text match: the
    manufacturer's own page or datasheet for this part, whose SKU lives in a JS-rendered tab or a
    PDF drawing that ``find_sku`` cannot see. The bytes really are the manufacturer's statement
    about this part, so withholding them would drop the richest source on the page — but the claim
    is weaker than a located mention, so it sorts last and is never cached as a found mention.
    """

    @property
    def is_ordering_row(self) -> bool:
        return self.how == "table"

    @property
    def is_body_match(self) -> bool:
        """Whether ``find_sku`` located the part in this document's text, as opposed to admitting
        it on manufacturer authority alone."""
        return self.how in {"table", "text"}


class DocumentLibrary:
    """An index over the artifact store, recording what each document covers.

    Not a cache in front of the store — the store is already content-addressed and needs no help.
    This is the *catalogue* of the store: which documents exist, where they came from, and which
    part numbers they answer for.
    """

    def __init__(self, store: LocalArtifactStore, index_path: Path | str) -> None:
        self._store = store
        self._path = Path(index_path)
        self._entries: dict[str, DocumentEntry] = {}
        self._generated_at: str | None = None
        self._parsed: dict[str, ParsedDocument] = {}
        """Parsed documents, memoised for this process. A 200-page PDF is parsed once per run
        however many of the thousand rows it turns out to cover."""

    # ------------------------------------------------------------------ persistence

    @classmethod
    def load(cls, store: LocalArtifactStore, index_path: Path | str) -> DocumentLibrary:
        library = cls(store, index_path)
        path = Path(index_path)
        if not path.is_file():
            return library
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Derived data. A corrupt index must not stop a run — it costs re-parsing, not
            # correctness, and refusing to start would be a worse failure than rebuilding.
            return library
        for entry in payload.get("documents") or []:
            try:
                record = DocumentEntry.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                continue
            library._entries[record.sha256] = record
        library._generated_at = payload.get("generated_at")
        return library

    def save(self) -> Path:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "documents": [
                entry.to_dict()
                for entry in sorted(self._entries.values(), key=lambda e: e.sha256)
            ],
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self._path

    # ------------------------------------------------------------------ contents

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[DocumentEntry, ...]:
        return tuple(sorted(self._entries.values(), key=lambda e: e.sha256))

    def get(self, sha256: str) -> DocumentEntry | None:
        return self._entries.get(sha256)

    def has(self, sha256: str) -> bool:
        return sha256 in self._entries

    def seen_url(self, url: str) -> DocumentEntry | None:
        """A document already fetched from this URL, if any.

        Checked before fetching, so a URL that several rows resolve to is requested once. Matched on
        the URL as recorded, which is the post-redirect one — the value that is actually the source.
        """
        key = url.rstrip("/")
        for entry in self._entries.values():
            if entry.source_uri.rstrip("/") == key:
                return entry
        return None

    # ------------------------------------------------------------------ registration

    def register(
        self,
        artifact: IngestedArtifact,
        *,
        host: str = "",
        tier: str = SourceTier.UNKNOWN.value,
        manufacturer_id: str | None = None,
    ) -> DocumentEntry:
        """Record an ingested artifact, or return the entry already describing those bytes.

        Idempotent on the hash, which is what makes two rows resolving to the same PDF cost one
        entry. Coverage learned under the previous registration is preserved: the bytes have not
        changed, so what is inside them has not either.
        """
        existing = self._entries.get(artifact.sha256)
        if existing is not None:
            return existing

        entry = DocumentEntry(
            sha256=artifact.sha256,
            storage_uri=artifact.storage_uri,
            document_id=artifact.document.document_id,
            source_uri=artifact.document.uri,
            host=host,
            tier=tier,
            manufacturer_id=manufacturer_id,
            doc_type=artifact.document.doc_type.value,
            filename=artifact.original_filename,
            size_bytes=artifact.size_bytes,
            fetched_at=artifact.document.fetched_at.isoformat(),
            revision_label=artifact.document.revision_label,
            license_note=artifact.document.license_note,
        )
        self._entries[entry.sha256] = entry
        return entry

    # ------------------------------------------------------------------ parsing

    def raw(self, entry: DocumentEntry) -> bytes | None:
        """The stored bytes, or None when they are gone.

        Separate from :meth:`parsed` because two callers want different things from the same
        document. Extraction wants the *rendered* form, where markup has been thrown away on the way
        to citable prose. Discovery wants the markup itself — the renderer discarded every ``href``
        it needs. Serving both from one method would mean one of them re-parsing to recover what the
        other deliberately dropped.
        """
        try:
            return self._store.get(entry.storage_uri)
        except (FileNotFoundError, OSError):
            return None

    def text(self, entry: DocumentEntry) -> str | None:
        """The stored bytes decoded, for reading structure. None when they are not text at all."""
        data = self.raw(entry)
        if data is None or data.startswith(b"%PDF-"):
            return None
        for encoding in ("utf-8", "cp1252"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def parsed(self, entry: DocumentEntry) -> ParsedDocument | None:
        """The parsed form of a stored document, memoised per process.

        Returns None when the bytes are gone or unparseable. Either is survivable and neither should
        end a batch: the index is derived, so a missing artifact means this document contributes
        nothing to this run rather than that the run is invalid.
        """
        if entry.sha256 in self._parsed:
            return self._parsed[entry.sha256]
        try:
            data = self._store.get(entry.storage_uri)
        except (FileNotFoundError, OSError):
            return None
        try:
            document = _source_document(entry)
            parsed = parse_artifact(data, document)
        except Exception:  # noqa: BLE001 - an unparseable stored file is not a fatal condition
            return None
        self._parsed[entry.sha256] = parsed
        return parsed

    # ------------------------------------------------------------------ the point

    def coverage_for(
        self, mpn: str, *, variants: set[str] | None = None, recheck: bool = False
    ) -> list[Coverage]:
        """Every stored document that covers this part number, best first.

        This is the method the whole module exists for. Ordering is by strength of evidence then by
        tier: an ordering row on the manufacturer's own site beats a passing mention on an unknown
        host, and a reviewer looking at the citation should see the strongest one first.

        Documents already known to lack the part are skipped without being re-parsed, and documents
        that *withdraw* it are not returned as coverage at all.

        Coverage is a body-text question, deliberately: it gates retrieval's escalation to richer
        PDFs and rendered pages, so a manufacturer's JavaScript shell that only *links* to the
        datasheet must not count here or the search would stop before fetching it. Reading the
        manufacturer's own page on authority alone is :meth:`supplementary_for`'s job, run after
        the search is over.
        """
        found: list[Coverage] = []
        for entry in self._entries.values():
            if mpn in entry.withdrawn:
                continue
            if mpn in entry.covers:
                found.append(Coverage(entry=entry, mpn=mpn, how=entry.covers[mpn]))
                continue
            if not recheck and mpn in entry.absent:
                continue
            how = self._check(entry, mpn, variants=variants)
            if how:
                found.append(Coverage(entry=entry, mpn=mpn, how=how))

        found.sort(
            key=lambda c: (
                0 if c.is_ordering_row else 1,
                0 if c.entry.tier == SourceTier.MANUFACTURER.value else 1,
                c.entry.sha256,
            )
        )
        return found

    def supplementary_for(
        self,
        mpn: str,
        *,
        manufacturer_id: str,
        among: set[str],
        exclude: set[str] | None = None,
    ) -> list[Coverage]:
        """Manufacturer-owned documents worth reading that no body-text match found.

        The counterpart to :meth:`coverage_for`, kept separate on purpose. ``coverage_for`` answers
        "which stored document *is* a source for this part", and its answer gates retrieval's
        escalation to richer PDFs and rendered pages — so admitting a JavaScript shell there would
        stop the search before it fetched the datasheet the shell only links to. That must stay a
        body-text question.

        This answers a different one: once the search is over, which of the documents already
        fetched are the manufacturer's own statement about this part and therefore worth *reading*,
        even though the part number lives in a tab or a drawing ``find_sku`` cannot see. Those are
        read alongside the primary, never instead of it, so they enrich without suppressing.

        ``among`` scopes the search to the shas fetched *for this part in this run*, and it is
        required rather than optional. Without it the method would scan the whole library and pull
        in the manufacturer's documents for unrelated parts — a catalogue for a different product
        line shares the manufacturer id — which is exactly the misattribution the body-text gate in
        ``coverage_for`` exists to prevent. Authority admits a document the search already tied to
        this part; it does not go looking for new ones.

        ``exclude`` drops shas already returned as coverage, so a document is never read twice.
        """
        expected = (manufacturer_id or "").strip()
        if not expected or not among:
            return []
        skip = exclude or set()
        supplementary: list[Coverage] = []
        for sha in among:
            if sha in skip:
                continue
            entry = self._entries.get(sha)
            if entry is None:
                continue
            if mpn in entry.withdrawn or mpn in entry.covers:
                continue
            if (
                entry.tier == SourceTier.MANUFACTURER.value
                and entry.manufacturer_id is not None
                and entry.manufacturer_id == expected
            ):
                supplementary.append(Coverage(entry=entry, mpn=mpn, how="manufacturer"))
        supplementary.sort(
            key=lambda c: (
                # A datasheet before a product page: the technical values live in the PDF.
                0 if c.entry.doc_type == "spec_sheet" else 1,
                c.entry.sha256,
            )
        )
        return supplementary

    def _check(
        self, entry: DocumentEntry, mpn: str, *, variants: set[str] | None = None
    ) -> str | None:
        """Look for a part in a document and remember the answer either way."""
        parsed = self.parsed(entry)
        if parsed is None:
            return None

        presence = find_sku(parsed, mpn, variants=variants)
        if not presence.checked:
            # Too short to search for safely. Recorded as neither present nor absent, because
            # `found` is meaningless here and caching it as absent would be a false negative.
            return None

        if presence.withdrawn and presence.withdrawal_quote:
            entry.withdrawn[mpn] = presence.withdrawal_quote
            return None
        if not presence.found:
            if mpn not in entry.absent:
                entry.absent.append(mpn)
            return None

        how = "table" if presence.listed_in_table else "text"
        entry.covers[mpn] = how
        return how

    # ------------------------------------------------------------------ reporting

    def stats(self) -> dict[str, object]:
        by_tier: dict[str, int] = {}
        for entry in self._entries.values():
            by_tier[entry.tier] = by_tier.get(entry.tier, 0) + 1
        covered = {mpn for entry in self._entries.values() for mpn in entry.covers}
        ordering = {
            mpn
            for entry in self._entries.values()
            for mpn, how in entry.covers.items()
            if how == "table"
        }
        return {
            "documents": len(self._entries),
            "by_tier": dict(sorted(by_tier.items())),
            "bytes": sum(e.size_bytes for e in self._entries.values()),
            "skus_covered": len(covered),
            "skus_in_an_ordering_row": len(ordering),
            "skus_checked_absent": len(
                {mpn for entry in self._entries.values() for mpn in entry.absent}
            ),
            "skus_withdrawn": len(
                {mpn for entry in self._entries.values() for mpn in entry.withdrawn}
            ),
        }


def _source_document(entry: DocumentEntry):
    """Rebuild the SourceDocument a stored entry describes, so citations keep their identity.

    Reconstructed from the index rather than re-derived, because ``document_id`` and ``fetched_at``
    are part of every citation already written against this document. Re-deriving them would mint a
    new identity for the same bytes and orphan those citations.
    """
    from datetime import datetime as _dt

    from axiom.core.evidence import DocumentType, SourceDocument

    try:
        fetched = _dt.fromisoformat(entry.fetched_at) if entry.fetched_at else datetime.now(UTC)
    except ValueError:
        fetched = datetime.now(UTC)

    try:
        doc_type = DocumentType(entry.doc_type)
    except ValueError:
        doc_type = DocumentType.UNKNOWN

    return SourceDocument(
        document_id=entry.document_id or f"{entry.sha256[:12]}@{entry.sha256[:8]}",
        uri=entry.source_uri or entry.storage_uri,
        sha256=entry.sha256,
        doc_type=doc_type,
        fetched_at=fetched,
        revision_label=entry.revision_label,
        license_note=entry.license_note,
    )


__all__ = ["INDEX_VERSION", "Coverage", "DocumentEntry", "DocumentLibrary"]
