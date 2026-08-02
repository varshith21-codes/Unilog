"""PDF parser built on pdfplumber.

Produces the same :class:`ParsedDocument` shape as the text parser, so everything downstream
is source-agnostic. Coordinates here are the real typographic positions, which is what makes
the evidence viewer highlight land exactly on the cited characters.

**Scanned documents are detected, not silently mishandled.** A page with an image and no
extractable text yields a warning rather than an empty page pretending to be parsed. That
distinction matters: "this datasheet has no Cv value" and "we could not read this datasheet"
are completely different statements, and only one of them is an honest gap.
"""

from __future__ import annotations

import io

from axiom.core.evidence import BoundingBox, SourceDocument
from axiom.docintel.models import (
    ParsedDocument,
    ParsedLine,
    ParsedPage,
    ParsedTable,
    ParsedWord,
    TableCell,
)

# Below this many extractable characters, a page is almost certainly a scan.
SCANNED_PAGE_CHAR_THRESHOLD = 20


class PdfParseError(Exception):
    pass


def parse_pdf(data: bytes, document: SourceDocument) -> ParsedDocument:
    """Parse a PDF into pages with lines, words and reconstructed tables."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise PdfParseError("pdfplumber is required to parse PDFs") from exc

    pages: list[ParsedPage] = []
    warnings: list[str] = []
    table_counter = 0

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                parsed, table_counter, page_warnings = _parse_page(page, index, table_counter)
                pages.append(parsed)
                warnings.extend(page_warnings)
    except PdfParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clear error, not a library trace
        raise PdfParseError(f"could not parse PDF: {exc}") from exc

    if not pages:
        raise PdfParseError("PDF contained no pages")

    return ParsedDocument(
        document=document, pages=tuple(pages), parser="pdfplumber", warnings=warnings
    )


def _parse_page(page, number: int, table_counter: int) -> tuple[ParsedPage, int, list[str]]:
    warnings: list[str] = []
    width = float(page.width)
    height = float(page.height)

    words = _extract_words(page, number)
    lines = _extract_lines(page, number, words)

    char_count = sum(len(line.text.strip()) for line in lines)
    if char_count < SCANNED_PAGE_CHAR_THRESHOLD:
        warnings.append(
            f"page {number} yielded {char_count} characters and is probably a scan; "
            f"route it to OCR or Bedrock Data Automation rather than treating the absence "
            f"of values as a gap"
        )

    tables, table_counter = _extract_tables(page, number, table_counter)

    return (
        ParsedPage(
            number=number, width=width, height=height, lines=tuple(lines), tables=tables
        ),
        table_counter,
        warnings,
    )


def _extract_words(page, number: int) -> list[ParsedWord]:
    out: list[ParsedWord] = []
    for word in page.extract_words(use_text_flow=False, keep_blank_chars=False):
        out.append(
            ParsedWord(
                text=word["text"],
                bbox=_bbox(word["x0"], word["top"], word["x1"], word["bottom"]),
                page=number,
            )
        )
    return out


def _extract_lines(page, number: int, words: list[ParsedWord]) -> list[ParsedLine]:
    """Prefer pdfplumber's own line grouping; fall back to clustering words by baseline."""
    try:
        raw_lines = page.extract_text_lines(strip=False, return_chars=False)
    except Exception:  # noqa: BLE001 - older pdfplumber, or an awkward page
        raw_lines = None

    if raw_lines:
        result = []
        for index, line in enumerate(raw_lines):
            bbox = _bbox(line["x0"], line["top"], line["x1"], line["bottom"])
            in_line = tuple(w for w in words if _vertically_within(w.bbox, bbox))
            result.append(
                ParsedLine(
                    text=line["text"],
                    bbox=bbox,
                    page=number,
                    line_index=index,
                    words=in_line,
                )
            )
        return result

    return _cluster_words_into_lines(words, number)


def _cluster_words_into_lines(words: list[ParsedWord], number: int) -> list[ParsedLine]:
    """Group words sharing a baseline, with a tolerance for sub-pixel drift."""
    if not words:
        return []
    tolerance = 2.0
    buckets: list[list[ParsedWord]] = []
    for word in sorted(words, key=lambda w: (w.bbox.y0, w.bbox.x0)):
        for bucket in buckets:
            if abs(bucket[0].bbox.y0 - word.bbox.y0) <= tolerance:
                bucket.append(word)
                break
        else:
            buckets.append([word])

    lines: list[ParsedLine] = []
    for index, bucket in enumerate(buckets):
        ordered = sorted(bucket, key=lambda w: w.bbox.x0)
        lines.append(
            ParsedLine(
                text=" ".join(w.text for w in ordered),
                bbox=BoundingBox(
                    x0=min(w.bbox.x0 for w in ordered),
                    y0=min(w.bbox.y0 for w in ordered),
                    x1=max(w.bbox.x1 for w in ordered),
                    y1=max(w.bbox.y1 for w in ordered),
                ),
                page=number,
                line_index=index,
                words=tuple(ordered),
            )
        )
    return lines


def _extract_tables(page, number: int, counter: int) -> tuple[tuple[ParsedTable, ...], int]:
    tables: list[ParsedTable] = []
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 - table finding is best-effort
        return (), counter

    for table in found:
        try:
            grid = table.extract()
        except Exception:  # noqa: BLE001
            continue
        if not grid:
            continue

        counter += 1
        cells: list[TableCell] = []
        for row_index, row in enumerate(table.rows):
            for col_index, cell_bbox in enumerate(row.cells):
                if cell_bbox is None:
                    continue
                text = ""
                if row_index < len(grid) and col_index < len(grid[row_index]):
                    text = (grid[row_index][col_index] or "").strip()
                if not text:
                    continue
                x0, top, x1, bottom = cell_bbox
                cells.append(
                    TableCell(
                        text=text,
                        row=row_index,
                        col=col_index,
                        bbox=_bbox(x0, top, x1, bottom),
                    )
                )
        if not cells:
            continue

        tables.append(
            ParsedTable(
                table_id=f"t{counter}",
                page=number,
                cells=tuple(cells),
                row_count=len(table.rows),
                col_count=max((c.col for c in cells), default=0) + 1,
                bbox=_bbox(*table.bbox),
            )
        )
    return tuple(tables), counter


def _bbox(x0, top, x1, bottom) -> BoundingBox:
    """Build a bbox, forcing a non-degenerate box.

    Zero-width and zero-height boxes occur in real PDFs (empty cells, hairline rules) and
    BoundingBox rejects them, so they are nudged rather than allowed to abort a parse.
    """
    x0f, y0f, x1f, y1f = float(x0), float(top), float(x1), float(bottom)
    if x1f <= x0f:
        x1f = x0f + 0.5
    if y1f <= y0f:
        y1f = y0f + 0.5
    return BoundingBox(x0=x0f, y0=y0f, x1=x1f, y1=y1f)


def _vertically_within(inner: BoundingBox, outer: BoundingBox) -> bool:
    centre = (inner.y0 + inner.y1) / 2
    return outer.y0 - 1.0 <= centre <= outer.y1 + 1.0
