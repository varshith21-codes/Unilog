"""Parsed document structure with spatial coordinates preserved.

Coordinates are the whole point. Text extraction that discards position produces a record
you can read but cannot *cite*: the evidence viewer has nothing to highlight, and a reviewer
is back to scrolling a PDF hunting for the number. So every line and every table cell keeps
its bounding box, and every table cell keeps a stable reference like ``t1:r14:c3``.

Table reconstruction matters even more than line coordinates. Industrial datasheets put the
sellable SKUs in an ordering table, so a parser that flattens tables into prose destroys both
variant explosion and any hope of reading the right row for the target part number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.core.evidence import BoundingBox, SourceDocument


@dataclass(frozen=True)
class ParsedWord:
    """One token with its position on the page."""

    text: str
    bbox: BoundingBox
    page: int


@dataclass(frozen=True)
class ParsedLine:
    """A line of text with its position. The unit most citations resolve to."""

    text: str
    bbox: BoundingBox
    page: int
    line_index: int
    words: tuple[ParsedWord, ...] = ()

    def span_bbox(self, start: int, end: int) -> BoundingBox:
        """Approximate the bbox of a character range within this line.

        Used to tighten a highlight from the whole line down to the quoted fragment.
        Interpolating across the line width is an approximation, but for proportional
        fonts it lands close enough that a reviewer's eye goes to the right place, which
        is the only thing the highlight has to achieve.
        """
        if not self.text:
            return self.bbox
        width = self.bbox.x1 - self.bbox.x0
        length = len(self.text)
        x0 = self.bbox.x0 + width * (max(0, start) / length)
        x1 = self.bbox.x0 + width * (min(length, end) / length)
        if x1 <= x0:
            x1 = min(self.bbox.x1, x0 + 1.0)
        return BoundingBox(x0=x0, y0=self.bbox.y0, x1=x1, y1=self.bbox.y1)


@dataclass(frozen=True)
class TableCell:
    text: str
    row: int
    col: int
    bbox: BoundingBox


@dataclass(frozen=True)
class ParsedTable:
    """A reconstructed table. Cells keep coordinates so a row can be highlighted."""

    table_id: str
    page: int
    cells: tuple[TableCell, ...]
    row_count: int
    col_count: int
    bbox: BoundingBox

    def rows(self) -> list[list[str]]:
        grid = [["" for _ in range(self.col_count)] for _ in range(self.row_count)]
        for cell in self.cells:
            if 0 <= cell.row < self.row_count and 0 <= cell.col < self.col_count:
                grid[cell.row][cell.col] = cell.text
        return grid

    @property
    def header(self) -> list[str]:
        rows = self.rows()
        return rows[0] if rows else []

    def cell(self, row: int, col: int) -> TableCell | None:
        for candidate in self.cells:
            if candidate.row == row and candidate.col == col:
                return candidate
        return None

    def cell_ref(self, row: int, col: int) -> str:
        """Stable citation reference for one cell."""
        return f"{self.table_id}:r{row}:c{col}"

    def find_row(self, value: str, *, col: int | None = None) -> int | None:
        """Locate the row containing a value — how the target SKU's row is found.

        Reading the wrong row of an ordering table produces a value that is genuinely
        present in the document and wrong for the part, which quote verification cannot
        detect. Finding the row deterministically avoids the problem entirely.
        """
        needle = value.strip().casefold()
        if not needle:
            return None
        for index, row in enumerate(self.rows()):
            cols = enumerate(row) if col is None else [(col, row[col])] if col < len(row) else []
            for _, text in cols:
                if text.strip().casefold() == needle:
                    return index
        return None

    def row_bbox(self, row: int) -> BoundingBox | None:
        cells = [c for c in self.cells if c.row == row]
        if not cells:
            return None
        return BoundingBox(
            x0=min(c.bbox.x0 for c in cells),
            y0=min(c.bbox.y0 for c in cells),
            x1=max(c.bbox.x1 for c in cells),
            y1=max(c.bbox.y1 for c in cells),
        )

    def to_prompt_text(self) -> str:
        """Render for the extraction prompt, preserving row and column structure."""
        rows = self.rows()
        if not rows:
            return ""
        widths = [
            max((len(rows[r][c]) for r in range(len(rows))), default=0)
            for c in range(self.col_count)
        ]
        lines = [f"<table id=\"{self.table_id}\">"]
        for index, row in enumerate(rows):
            rendered = " | ".join(text.ljust(widths[i]) for i, text in enumerate(row))
            lines.append(f"  r{index}: {rendered.rstrip()}")
        lines.append("</table>")
        return "\n".join(lines)


@dataclass(frozen=True)
class ParsedPage:
    number: int
    width: float
    height: float
    lines: tuple[ParsedLine, ...] = ()
    tables: tuple[ParsedTable, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_prompt_text(self) -> str:
        """Page content for the prompt, with tables rendered structurally.

        Lines that a table already covers are omitted, otherwise the model sees the same
        numbers twice in two layouts and has to guess which is authoritative.
        """
        covered: set[int] = set()
        for table in self.tables:
            top = min(c.bbox.y0 for c in table.cells)
            bottom = max(c.bbox.y1 for c in table.cells)
            for line in self.lines:
                if line.bbox.y0 >= top - 0.5 and line.bbox.y1 <= bottom + 0.5:
                    covered.add(line.line_index)

        parts: list[str] = []
        emitted_tables: set[str] = set()
        for line in self.lines:
            if line.line_index in covered:
                for table in self.tables:
                    if table.table_id in emitted_tables:
                        continue
                    top = min(c.bbox.y0 for c in table.cells)
                    if line.bbox.y0 >= top - 0.5:
                        parts.append(table.to_prompt_text())
                        emitted_tables.add(table.table_id)
                        break
                continue
            parts.append(line.text)

        for table in self.tables:
            if table.table_id not in emitted_tables:
                parts.append(table.to_prompt_text())
        return "\n".join(parts)


@dataclass
class ParsedDocument:
    """A source document turned into citable structure."""

    document: SourceDocument
    pages: tuple[ParsedPage, ...] = ()
    parser: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page(self, number: int) -> ParsedPage | None:
        for page in self.pages:
            if page.number == number:
                return page
        return None

    @property
    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def all_lines(self) -> list[ParsedLine]:
        return [line for page in self.pages for line in page.lines]

    def all_tables(self) -> list[ParsedTable]:
        return [table for page in self.pages for table in page.tables]

    def table(self, table_id: str) -> ParsedTable | None:
        for table in self.all_tables():
            if table.table_id == table_id:
                return table
        return None

    def to_prompt_content(self, *, max_pages: int | None = None) -> str:
        """Render the whole document for an extraction prompt.

        Page markers are emitted so the model can report a page number, which is what makes
        the returned citation resolvable back to a coordinate.
        """
        pages = self.pages if max_pages is None else self.pages[:max_pages]
        blocks = []
        for page in pages:
            blocks.append(f'<page number="{page.number}">')
            blocks.append(page.to_prompt_text())
            blocks.append("</page>")
        return "\n".join(blocks)
