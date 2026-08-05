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

    # Ruled lines are the reliable signal, so they win outright. Only when a page has none does
    # geometry get a turn — a page with real rules and a stray aligned block should not acquire a
    # second, overlapping table whose cell references contradict the first.
    if not tables:
        tables, table_counter = _extract_aligned_tables(lines, words, number, table_counter)

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


MIN_ALIGNED_ROWS = 3
MIN_ALIGNED_COLS = 3

COLUMN_GAP_MULTIPLE = 1.8
"""A column gap must exceed this multiple of the page's typical single-space width.

The text parser's rule is "two or more spaces separate columns, one space separates words", and
this is its geometric equivalent. Getting it wrong in the lenient direction is not a near miss:
if the threshold falls below one space width, every inter-word gap in a *sentence* looks like a
column boundary, prose joins the candidate block, and its words then occupy so many x positions
that no vertical band is unanimously blank — which collapses the real table into a single column.
That was the observed failure, not a hypothetical one.
"""

MIN_COLUMN_GAP_FLOOR_PT = 6.0
"""Absolute floor, for pages too sparse to estimate a space width from."""


def _extract_tables(page, number: int, counter: int) -> tuple[tuple[ParsedTable, ...], int]:
    """Ruled tables only. Whitespace-aligned tables are handled separately.

    Order matters: ruled-line detection is unambiguous where rules exist, so it is never
    second-guessed.
    """
    tables: list[ParsedTable] = []
    for table in _find_ruled(page):
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
            counter -= 1
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


def _find_ruled(page):
    try:
        return page.find_tables()
    except Exception:  # noqa: BLE001 - table finding is best-effort
        return []


# ---------------------------------------------------------------- aligned tables


def _extract_aligned_tables(
    lines: list[ParsedLine], words: list[ParsedWord], number: int, counter: int
) -> tuple[tuple[ParsedTable, ...], int]:
    """Detect whitespace-aligned tables from word geometry.

    Most supplier ordering tables are laid out with spaces and carry no ruled lines at all, so
    ``find_tables`` returns nothing for them. Losing the table costs every value in it its
    cell-level citation — the difference between citing ``t1:r4:c2`` and citing a line that
    happens to contain the right number.

    pdfplumber's own ``vertical_strategy="text"`` is not usable here: applied to a page it treats
    every inter-word gap as a column boundary, returning one enormous table with words split
    mid-token. Bogus cells are worse than no cells in this architecture, because
    ``confidence.features`` scores a cell reference at maximum citation precision — garbage
    references would inflate confidence on precisely the least trustworthy values.

    So this ports the algorithm the text parser already uses successfully: group *consecutive*
    candidate rows, then keep only the column boundaries that are blank on **every** row of the
    block. Unanimity is what stops a value containing a space from splitting its own cell.
    """
    words_by_line = _group_words_by_line(lines, words)
    gap_threshold = _column_gap_threshold(words_by_line)

    tables: list[ParsedTable] = []
    block: list[tuple[ParsedLine, list[ParsedWord]]] = []

    def flush() -> None:
        nonlocal counter, block
        if len(block) >= MIN_ALIGNED_ROWS:
            table = _build_aligned_table(block, number, counter + 1, gap_threshold)
            if table is not None:
                counter += 1
                tables.append(table)
        block = []

    for line in lines:
        line_words = words_by_line.get(line.line_index, [])
        if _has_column_gaps(line_words, gap_threshold):
            block.append((line, line_words))
        else:
            flush()
    flush()

    return tuple(tables), counter


def _column_gap_threshold(words_by_line: dict[int, list[ParsedWord]]) -> float:
    """Derive the column-gap threshold from this page's own typical word spacing.

    Measured rather than assumed, because it has to hold across font sizes: a hardcoded value
    tuned for 9pt Courier is below one space width at 12pt and the detection inverts.
    """
    gaps: list[float] = []
    for line_words in words_by_line.values():
        for previous, following in zip(line_words, line_words[1:], strict=False):
            gap = following.bbox.x0 - previous.bbox.x1
            if 0 < gap < 40:  # ignore column-sized gaps when estimating a word-sized one
                gaps.append(gap)

    if not gaps:
        return MIN_COLUMN_GAP_FLOOR_PT

    gaps.sort()
    median = gaps[len(gaps) // 2]
    return max(median * COLUMN_GAP_MULTIPLE, MIN_COLUMN_GAP_FLOOR_PT)


def _group_words_by_line(
    lines: list[ParsedLine], words: list[ParsedWord]
) -> dict[int, list[ParsedWord]]:
    """Assign each word to the line whose vertical band contains it."""
    grouped: dict[int, list[ParsedWord]] = {line.line_index: [] for line in lines}
    for word in words:
        centre = (word.bbox.y0 + word.bbox.y1) / 2
        for line in lines:
            if line.bbox.y0 - 1 <= centre <= line.bbox.y1 + 1:
                grouped[line.line_index].append(word)
                break
    for line_words in grouped.values():
        line_words.sort(key=lambda w: w.bbox.x0)
    return grouped


def _has_column_gaps(words: list[ParsedWord], threshold: float) -> bool:
    """Whether a row has enough wide gaps to be a table row rather than a sentence."""
    if len(words) < MIN_ALIGNED_COLS:
        return False
    gaps = sum(
        1
        for previous, following in zip(words, words[1:], strict=False)
        if following.bbox.x0 - previous.bbox.x1 >= threshold
    )
    return gaps >= MIN_ALIGNED_COLS - 1


def _build_aligned_table(
    block: list[tuple[ParsedLine, list[ParsedWord]]],
    number: int,
    table_number: int,
    gap_threshold: float,
) -> ParsedTable | None:
    """Split a block of aligned rows into cells on unanimously blank vertical bands."""
    boundaries = _unanimous_column_bands(block, gap_threshold)
    if len(boundaries) < MIN_ALIGNED_COLS:
        return None

    cells: list[TableCell] = []
    for row_index, (_, line_words) in enumerate(block):
        for col_index, (left, right) in enumerate(boundaries):
            in_column = [
                word
                for word in line_words
                if left <= (word.bbox.x0 + word.bbox.x1) / 2 <= right
            ]
            if not in_column:
                continue
            text = " ".join(word.text for word in in_column).strip()
            if not text:
                continue
            cells.append(
                TableCell(
                    text=text,
                    row=row_index,
                    col=col_index,
                    bbox=BoundingBox(
                        x0=min(w.bbox.x0 for w in in_column),
                        y0=min(w.bbox.y0 for w in in_column),
                        x1=max(w.bbox.x1 for w in in_column),
                        y1=max(w.bbox.y1 for w in in_column),
                    ),
                )
            )

    if not cells:
        return None

    populated_rows = {cell.row for cell in cells}
    if len(populated_rows) < MIN_ALIGNED_ROWS:
        return None

    return ParsedTable(
        table_id=f"t{table_number}",
        page=number,
        cells=tuple(cells),
        row_count=len(block),
        col_count=len(boundaries),
        bbox=BoundingBox(
            x0=min(c.bbox.x0 for c in cells),
            y0=min(c.bbox.y0 for c in cells),
            x1=max(c.bbox.x1 for c in cells),
            y1=max(c.bbox.y1 for c in cells),
        ),
    )


def _unanimous_column_bands(
    block: list[tuple[ParsedLine, list[ParsedWord]]], gap_threshold: float
) -> list[tuple[float, float]]:
    """Column x-ranges, derived from vertical bands blank on every row of the block.

    The x-axis equivalent of the text parser's character-position check. A band only separates
    columns if no row has a word crossing it, which is what keeps ``Locking Lever`` in one cell
    while still splitting the columns either side of it.
    """
    occupied: list[tuple[float, float]] = [
        (word.bbox.x0, word.bbox.x1) for _, line_words in block for word in line_words
    ]
    if not occupied:
        return []

    left = min(x0 for x0, _ in occupied)
    right = max(x1 for _, x1 in occupied)

    # Sample on a fine grid; a coarse one merges narrow columns.
    step = 1.0
    blank: list[bool] = []
    position = left
    while position <= right:
        blank.append(not any(x0 - 0.5 <= position <= x1 + 0.5 for x0, x1 in occupied))
        position += step

    bands: list[tuple[float, float]] = []
    start: float | None = None
    for index, is_blank in enumerate(blank):
        x = left + index * step
        if not is_blank and start is None:
            start = x
        elif is_blank and start is not None:
            run = 0
            while index + run < len(blank) and blank[index + run]:
                run += 1
            if run * step >= gap_threshold:
                bands.append((start, x))
                start = None
    if start is not None:
        bands.append((start, right))

    return bands


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
