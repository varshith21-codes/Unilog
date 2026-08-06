"""Plain-text parser with aligned-column table detection.

Two reasons this exists rather than being PDF-only:

1. **Tests must not depend on binary fixtures.** Parser behaviour, table detection and quote
   location are all testable against text, deterministically and in milliseconds.
2. **Plenty of real sources are text.** Crawled pages, ERP exports and OCR output all arrive
   as text, and they still need citable coordinates.

Coordinates are synthesised on a monospace grid. They are not the true typographic positions
of an original PDF, but they are internally consistent, which is all the evidence viewer
needs to draw a highlight over the right characters.
"""

from __future__ import annotations

import re

from axiom.core.evidence import BoundingBox, SourceDocument
from axiom.docintel.models import (
    ParsedDocument,
    ParsedLine,
    ParsedPage,
    ParsedTable,
    ParsedWord,
    TableCell,
)

# US Letter at 72 dpi, 0.75in margins, 10pt monospace-ish metrics.
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN_X = 54.0
MARGIN_Y = 54.0
CHAR_WIDTH = 6.0
LINE_HEIGHT = 12.0
LINES_PER_PAGE = 58

# A column gap is two or more spaces. One space is a word gap; requiring two is what
# separates "Carton Qty" (one cell) from two adjacent cells.
_GAP = re.compile(r"\s{2,}")

MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2


def parse_text(
    content: str,
    document: SourceDocument,
    *,
    detect_tables: bool = True,
    lines_per_page: int = LINES_PER_PAGE,
    parser: str = "text",
) -> ParsedDocument:
    """Parse text into pages with lines, synthetic coordinates and detected tables.

    ``parser`` names the route that produced ``content``. The HTML path renders markup to aligned
    text and then comes through here, and a citation should say which of the two it came from —
    the difference explains why a quote's whitespace may not match the original bytes.
    """
    raw_lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pages: list[ParsedPage] = []
    table_counter = 0

    for page_index in range(0, max(1, len(raw_lines)), lines_per_page):
        chunk = raw_lines[page_index : page_index + lines_per_page]
        page_number = page_index // lines_per_page + 1

        lines: list[ParsedLine] = []
        for offset, text in enumerate(chunk):
            lines.append(_build_line(text, page_number, offset))

        tables: tuple[ParsedTable, ...] = ()
        if detect_tables:
            found, table_counter = _detect_tables(lines, page_number, table_counter)
            tables = found

        pages.append(
            ParsedPage(
                number=page_number,
                width=PAGE_WIDTH,
                height=PAGE_HEIGHT,
                lines=tuple(lines),
                tables=tables,
            )
        )

    return ParsedDocument(document=document, pages=tuple(pages), parser=parser)


def _build_line(text: str, page: int, offset: int) -> ParsedLine:
    y0 = MARGIN_Y + offset * LINE_HEIGHT
    y1 = y0 + LINE_HEIGHT
    # An empty line still needs a valid, non-degenerate box: BoundingBox rejects x1 <= x0.
    x1 = MARGIN_X + max(len(text), 1) * CHAR_WIDTH
    bbox = BoundingBox(x0=MARGIN_X, y0=y0, x1=x1, y1=y1)

    words: list[ParsedWord] = []
    for match in re.finditer(r"\S+", text):
        words.append(
            ParsedWord(
                text=match.group(0),
                bbox=BoundingBox(
                    x0=MARGIN_X + match.start() * CHAR_WIDTH,
                    y0=y0,
                    x1=MARGIN_X + max(match.end(), match.start() + 1) * CHAR_WIDTH,
                    y1=y1,
                ),
                page=page,
            )
        )
    return ParsedLine(
        text=text, bbox=bbox, page=page, line_index=offset, words=tuple(words)
    )


def _looks_tabular(text: str) -> bool:
    """A line is a table-row candidate when it has at least two column gaps.

    Dot-leader spec lines ("Body Material ....... Bronze C84400") deliberately fail this:
    they are a two-column list, not an ordering matrix, and treating them as a table would
    bury the specification block inside a fake grid.
    """
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    return len(_GAP.findall(stripped)) >= MIN_TABLE_COLS - 1


def _detect_tables(
    lines: list[ParsedLine], page: int, counter: int
) -> tuple[tuple[ParsedTable, ...], int]:
    """Group consecutive tabular lines and split them on shared whitespace columns."""
    tables: list[ParsedTable] = []
    block: list[ParsedLine] = []

    def flush() -> None:
        nonlocal counter, block
        if len(block) >= MIN_TABLE_ROWS:
            boundaries = _column_boundaries([line.text for line in block])
            if len(boundaries) >= MIN_TABLE_COLS:
                counter += 1
                table = _build_table(block, boundaries, page, f"t{counter}")
                if table is not None:
                    tables.append(table)
        block = []

    for line in lines:
        if _looks_tabular(line.text):
            block.append(line)
        else:
            # A single blank line inside an aligned block is a visual separator, not the
            # end of the table, so only non-blank non-tabular content breaks the run.
            if line.text.strip():
                flush()
    flush()
    return tuple(tables), counter


def _column_boundaries(texts: list[str]) -> list[tuple[int, int]]:
    """Find (start, end) character ranges shared by every line in the block.

    A column boundary is a character position that is whitespace on *all* lines. Requiring
    unanimity is what keeps a value containing a space from splitting its own cell.
    """
    width = max(len(t) for t in texts)
    padded = [t.ljust(width) for t in texts]
    is_gap = [all(row[i] == " " for row in padded) for i in range(width)]

    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index in range(width):
        if not is_gap[index]:
            if start is None:
                start = index
        elif start is not None:
            # Only a gap of two or more columns terminates a cell.
            run_end = index
            while run_end < width and is_gap[run_end]:
                run_end += 1
            if run_end - index >= 2 or run_end >= width:
                ranges.append((start, index))
                start = None
    if start is not None:
        ranges.append((start, width))
    return ranges


def _build_table(
    lines: list[ParsedLine],
    boundaries: list[tuple[int, int]],
    page: int,
    table_id: str,
) -> ParsedTable | None:
    cells: list[TableCell] = []
    for row_index, line in enumerate(lines):
        for col_index, (start, end) in enumerate(boundaries):
            text = line.text[start:end].strip()
            if not text:
                continue
            cells.append(
                TableCell(
                    text=text,
                    row=row_index,
                    col=col_index,
                    bbox=line.span_bbox(start, end),
                )
            )
    if not cells:
        return None

    return ParsedTable(
        table_id=table_id,
        page=page,
        cells=tuple(cells),
        row_count=len(lines),
        col_count=len(boundaries),
        bbox=BoundingBox(
            x0=min(c.bbox.x0 for c in cells),
            y0=min(c.bbox.y0 for c in cells),
            x1=max(c.bbox.x1 for c in cells),
            y1=max(c.bbox.y1 for c in cells),
        ),
    )
