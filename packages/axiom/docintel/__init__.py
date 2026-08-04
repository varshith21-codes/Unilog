"""Document intelligence: turn source artifacts into citable structure.

The output of this package is what makes provenance concrete. Every line and table cell keeps
its coordinates, so a value's citation resolves to a page and a highlight rather than to a
filename and a promise.
"""

from axiom.core.evidence import DocumentType
from axiom.docintel.models import (
    ParsedDocument,
    ParsedLine,
    ParsedPage,
    ParsedTable,
    ParsedWord,
    TableCell,
)
from axiom.docintel.pdf_parser import PdfParseError, parse_pdf
from axiom.docintel.sku import MIN_SKU_CHARS, SkuPresence, find_sku
from axiom.docintel.spans import (
    DEFAULT_THRESHOLD,
    QuoteLocation,
    build_evidence_span,
    locate_quote,
    squash,
    verify_quote,
)
from axiom.docintel.text_parser import parse_text


def parse_artifact(data: bytes, document, **kwargs) -> ParsedDocument:
    """Parse an artifact, dispatching on its detected type.

    Callers should not need to know whether a source was a PDF or text — the parsed shape is
    identical either way, which is what lets the extraction layer stay source-agnostic.
    """
    if data.startswith(b"%PDF-"):
        return parse_pdf(data, document)

    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            content = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        content = data.decode("utf-8", errors="replace")
    return parse_text(content, document, **kwargs)


__all__ = [
    "DEFAULT_THRESHOLD",
    "MIN_SKU_CHARS",
    "DocumentType",
    "ParsedDocument",
    "ParsedLine",
    "ParsedPage",
    "ParsedTable",
    "ParsedWord",
    "PdfParseError",
    "QuoteLocation",
    "SkuPresence",
    "TableCell",
    "build_evidence_span",
    "find_sku",
    "locate_quote",
    "parse_artifact",
    "parse_pdf",
    "parse_text",
    "squash",
    "verify_quote",
]
