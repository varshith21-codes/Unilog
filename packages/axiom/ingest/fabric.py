"""The Source Fabric: everything arriving becomes one internal event.

Whatever the channel — a supplier spreadsheet, a datasheet PDF, a crawled page, an ERP
export — the pipeline downstream sees the same thing: *a hashed artifact with a timestamp,
a provenance record and a supplier attribution*.

Normalising the entry point is what makes provenance possible at all. If some paths record
where bytes came from and others do not, the Enrichment Certificate becomes a document you
cannot fully trust, which defeats its purpose.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiom.core.evidence import DocumentType, SourceDocument
from axiom.ingest.store import ArtifactStore, sha256_bytes

# Magic bytes are checked before extensions. A file named .pdf that is actually a
# spreadsheet is a real occurrence in supplier feeds, and trusting the name silently
# routes it to the wrong parser.
_MAGIC: tuple[tuple[bytes, DocumentType], ...] = (
    (b"%PDF-", DocumentType.SPEC_SHEET),
    (b"PK\x03\x04", DocumentType.SUPPLIER_FEED),  # xlsx/docx are zip containers
    (b"\x89PNG\r\n\x1a\n", DocumentType.PRODUCT_IMAGE),
    (b"\xff\xd8\xff", DocumentType.PRODUCT_IMAGE),
    (b"GIF8", DocumentType.PRODUCT_IMAGE),
)

_EXTENSION_TYPES: dict[str, DocumentType] = {
    ".pdf": DocumentType.SPEC_SHEET,
    ".csv": DocumentType.SUPPLIER_FEED,
    ".tsv": DocumentType.SUPPLIER_FEED,
    ".xlsx": DocumentType.SUPPLIER_FEED,
    ".xls": DocumentType.SUPPLIER_FEED,
    ".txt": DocumentType.SPEC_SHEET,
    ".md": DocumentType.SPEC_SHEET,
    ".html": DocumentType.WEB_PAGE,
    ".htm": DocumentType.WEB_PAGE,
    ".xml": DocumentType.SUPPLIER_FEED,
    ".png": DocumentType.PRODUCT_IMAGE,
    ".jpg": DocumentType.PRODUCT_IMAGE,
    ".jpeg": DocumentType.PRODUCT_IMAGE,
}

FLAT_FILE_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xls"})


class IngestError(Exception):
    """Raised when an artifact cannot be ingested at all."""


def detect_document_type(data: bytes, filename: str | None = None) -> DocumentType:
    """Classify by content first, falling back to the extension."""
    for magic, doc_type in _MAGIC:
        if data.startswith(magic):
            # A zip container could be xlsx or docx; the extension disambiguates.
            if magic == b"PK\x03\x04" and filename:
                suffix = Path(filename).suffix.lower()
                if suffix in {".docx", ".doc"}:
                    return DocumentType.SPEC_SHEET
            return doc_type
    if filename:
        return _EXTENSION_TYPES.get(Path(filename).suffix.lower(), DocumentType.UNKNOWN)
    return DocumentType.UNKNOWN


def pdf_page_count(data: bytes) -> int | None:
    """Cheap page count without a full parse. None when it cannot be determined."""
    if not data.startswith(b"%PDF-"):
        return None
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception:  # noqa: BLE001 - page count is informational, never fatal
        return None


@dataclass(frozen=True)
class IngestedArtifact:
    """A stored artifact and the provenance record describing it."""

    document: SourceDocument
    storage_uri: str
    size_bytes: int
    original_filename: str | None
    was_already_stored: bool
    """True when identical bytes were already present. Re-ingesting a file is a no-op,
    which matters because suppliers resend unchanged files constantly."""

    @property
    def sha256(self) -> str:
        return self.document.sha256

    @property
    def is_flat_file(self) -> bool:
        if not self.original_filename:
            return False
        return Path(self.original_filename).suffix.lower() in FLAT_FILE_SUFFIXES


def ingest_bytes(
    data: bytes,
    store: ArtifactStore,
    *,
    filename: str | None = None,
    source_uri: str | None = None,
    supplier_id: str | None = None,
    doc_type: DocumentType | None = None,
    revision_label: str | None = None,
    license_note: str | None = None,
    fetched_at: datetime | None = None,
) -> IngestedArtifact:
    """Hash, store and describe an artifact.

    The document ID is derived from the hash rather than the filename, so two suppliers
    sending the same datasheet share one document and one set of citations.
    """
    if not data:
        raise IngestError("refusing to ingest zero bytes")

    digest = sha256_bytes(data)
    suffix = Path(filename).suffix.lower() if filename else ""
    already = store.exists("local://" + _key_preview(digest, suffix))
    storage_uri = store.put(data, suffix=suffix)

    resolved_type = doc_type or detect_document_type(data, filename)
    document = SourceDocument(
        document_id=(Path(filename).stem if filename else digest[:12]) + f"@{digest[:8]}",
        uri=source_uri or storage_uri,
        sha256=digest,
        doc_type=resolved_type,
        fetched_at=fetched_at or datetime.now(UTC),
        page_count=pdf_page_count(data),
        revision_label=revision_label,
        supplier_id=supplier_id,
        license_note=license_note,
    )
    return IngestedArtifact(
        document=document,
        storage_uri=storage_uri,
        size_bytes=len(data),
        original_filename=filename,
        was_already_stored=already,
    )


def _key_preview(digest: str, suffix: str) -> str:
    from axiom.ingest.store import artifact_key

    return artifact_key(digest, suffix=suffix)


def ingest_file(
    path: Path | str,
    store: ArtifactStore,
    *,
    supplier_id: str | None = None,
    **kwargs,
) -> IngestedArtifact:
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"not a file: {path}")
    return ingest_bytes(
        path.read_bytes(),
        store,
        filename=path.name,
        supplier_id=supplier_id,
        **kwargs,
    )


# --------------------------------------------------------------------------- flat files


@dataclass(frozen=True)
class FlatFile:
    """A parsed tabular source: headers plus rows as dictionaries."""

    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    sheet_name: str | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def column(self, header: str) -> tuple[str, ...]:
        return tuple(row.get(header, "") for row in self.rows)

    def non_empty_ratio(self, header: str) -> float:
        """Share of rows with a value. A header mapped to an empty column is a trap: it
        looks like coverage and delivers nothing."""
        if not self.rows:
            return 0.0
        return sum(1 for v in self.column(header) if v.strip()) / len(self.rows)


def read_flat_file(data: bytes, *, filename: str | None = None) -> FlatFile:
    """Read CSV, TSV or XLSX into a uniform shape."""
    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix in {".xlsx", ".xls"}:
        return _read_xlsx(data)
    return _read_delimited(data, suffix=suffix)


def _read_delimited(data: bytes, *, suffix: str = "") -> FlatFile:
    # Supplier CSVs arrive in cp1252 as often as utf-8, and a decode error should not
    # discard the file. Replacement is preferable to rejection here because the row still
    # carries a usable part number.
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    if suffix == ".tsv":
        dialect: type[csv.Dialect] | csv.Dialect = csv.excel_tab
    else:
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise IngestError("flat file has no header row") from exc

    headers = _dedupe_headers(raw_headers)
    rows = tuple(
        {headers[i]: (cell or "").strip() for i, cell in enumerate(row[: len(headers)])}
        for row in reader
        if any((cell or "").strip() for cell in row)
    )
    return FlatFile(tuple(headers), rows)


def _read_xlsx(data: bytes) -> FlatFile:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = workbook.active
    if sheet is None:
        raise IngestError("workbook has no active sheet")

    iterator = sheet.iter_rows(values_only=True)
    try:
        raw_headers = next(iterator)
    except StopIteration as exc:
        raise IngestError("workbook sheet is empty") from exc

    headers = _dedupe_headers(["" if h is None else str(h) for h in raw_headers])
    rows = []
    for row in iterator:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        rows.append(
            {
                headers[i]: ("" if cell is None else str(cell).strip())
                for i, cell in enumerate(row[: len(headers)])
            }
        )
    workbook.close()
    return FlatFile(tuple(headers), tuple(rows), sheet_name=sheet.title)


def _dedupe_headers(raw: list[str]) -> list[str]:
    """Make headers unique and non-empty so rows can be keyed reliably.

    Supplier files genuinely ship duplicate and blank headers. Silently collapsing them
    loses a column; renaming keeps it addressable and visible in the mapping proposal.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for index, header in enumerate(raw):
        name = (header or "").strip() or f"column_{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}__{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out
