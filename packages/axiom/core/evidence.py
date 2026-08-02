"""Source documents and evidence spans.

Provenance is the foundation of the whole system. An `EvidenceSpan` is a pointer into a
specific region of a specific *version* of a source document, together with the verbatim
text that was found there. The `SourceDocument.sha256` is what makes the pointer stable:
if the supplier reissues the datasheet, the hash changes and the old citation still
resolves to the bytes it was actually derived from.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentType(str, Enum):
    """Classification of a source artifact, used to route extraction blueprints."""

    SPEC_SHEET = "spec_sheet"
    CATALOG_PAGE = "catalog_page"
    INSTALLATION_MANUAL = "installation_manual"
    CERTIFICATE = "certificate"
    SAFETY_DATA_SHEET = "safety_data_sheet"
    DECLARATION = "declaration"
    DRAWING = "drawing"
    PRODUCT_IMAGE = "product_image"
    WEB_PAGE = "web_page"
    SUPPLIER_FEED = "supplier_feed"
    ERP_EXPORT = "erp_export"
    UNKNOWN = "unknown"


class SourceDocument(BaseModel):
    """An immutable, content-addressed source artifact."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    uri: str = Field(description="S3 URI or original URL the bytes were retrieved from")
    sha256: str = Field(description="Content hash — makes every citation stable")
    doc_type: DocumentType = DocumentType.UNKNOWN
    fetched_at: datetime
    page_count: int | None = None
    revision_label: str | None = Field(
        default=None,
        description="Manufacturer revision marker, e.g. 'Rev C 2024-08'. Used to break "
        "ties when two sources disagree.",
    )
    supplier_id: str | None = None
    license_note: str | None = Field(
        default=None,
        description="Licensing/usage terms observed at retrieval time. Required before any "
        "asset from this document is republished.",
    )

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        cleaned = v.lower().removeprefix("sha256:")
        if len(cleaned) != 64 or not all(c in "0123456789abcdef" for c in cleaned):
            raise ValueError(f"sha256 must be 64 hex characters, got {v!r}")
        return cleaned


class BoundingBox(BaseModel):
    """Region on a rendered page, in PDF points with origin at top-left."""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float

    @field_validator("x1")
    @classmethod
    def _x_ordered(cls, v: float, info) -> float:
        if "x0" in info.data and v <= info.data["x0"]:
            raise ValueError("x1 must be greater than x0")
        return v

    @field_validator("y1")
    @classmethod
    def _y_ordered(cls, v: float, info) -> float:
        if "y0" in info.data and v <= info.data["y0"]:
            raise ValueError("y1 must be greater than y0")
        return v

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class EvidenceSpan(BaseModel):
    """A verifiable pointer to the exact place a value was found.

    `quote` must be reproducible from the source document. The extraction pipeline
    verifies this mechanically (see `axiom.validate` layer L5) and discards any value whose
    quote cannot be located — which is the single cheapest and most effective
    anti-fabrication mechanism in the system.
    """

    model_config = ConfigDict(frozen=True)

    span_id: str
    document_id: str
    document_sha256: str
    quote: str = Field(min_length=1, description="Verbatim text as it appears in the source")
    page: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    table_ref: str | None = Field(
        default=None, description="e.g. 't1:r14:c3' — table, row and column identifiers"
    )
    quote_verified: bool = Field(
        default=False,
        description="True once `quote` has been matched back against the parsed source text. "
        "Values whose spans are all unverified must not be published.",
    )
    match_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Similarity of quote to located source text"
    )

    def locator(self) -> str:
        """Human-readable citation, for UI and certificate rendering."""
        parts = [self.document_id]
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.table_ref:
            parts.append(self.table_ref)
        return " ".join(parts)
