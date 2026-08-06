"""HTML to citable text.

Manufacturer product pages are a first-class source — often the *only* source for a
newly-released part, and frequently carrying an ordering table the PDF catalogue has not caught
up with. But feeding raw markup to an extractor is worse than useless: the model spends tokens on
``<div class="spec-row">`` and every quote it returns fails verification because the quote
contains tags the human-readable text does not.

**The design decision here is to render, not to re-implement.** This converts HTML into the same
space-aligned text the plain-text parser already handles, then hands off to it. So HTML tables are
reconstructed by the *existing* column-boundary detector, evidence spans get coordinates from the
*existing* monospace grid, and quote location works unchanged. A parallel table builder for HTML
would have been a second implementation of the hardest part of the parser, drifting from the first.

Two consequences worth stating:

*   Coordinates are synthetic, exactly as they are for text. They are internally consistent, which
    is all the evidence viewer needs to box the right characters. They are not the positions a
    browser would have laid out.
*   The stored artifact remains the original bytes. Rendering happens at parse time, never at
    ingest, so the hash in a citation still resolves to what the server actually sent.

Nothing in here executes, resolves or fetches anything from the document. ``html.parser`` is a
non-validating pure-Python parser with no DTD handling, so there is no external-entity or
entity-expansion exposure; scripts, styles and embedded frames are discarded rather than followed.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from axiom.core.evidence import SourceDocument
from axiom.docintel.models import ParsedDocument

# Content that is markup machinery rather than product information. Dropped wholesale, including
# any text inside, because a stylesheet or an inline script contains nothing citable and its
# contents would otherwise land in the extractable text.
_DISCARD = frozenset(
    {"script", "style", "head", "noscript", "svg", "template", "iframe", "object", "select"}
)

# Tags after which a line must break, so a specification list does not collapse into one
# unreadable run. `parse_text` works line by line, and a single 4,000-character line would give
# every value on the page the same coordinates.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
        "ul",
    }
)

_TABLE_CELLS = frozenset({"td", "th"})

# Two spaces is what the text parser reads as a column gap, so cells are padded to a shared width
# and joined with exactly that. Aligning the whole table rather than each row is what makes the
# gap columns unanimous, which is the condition the boundary detector actually tests.
_COLUMN_GAP = "  "

_WHITESPACE = re.compile(r"[^\S\n]+")
_BLANK_RUN = re.compile(r"\n{3,}")


class _Renderer(HTMLParser):
    """Accumulates visible text, rendering tables as aligned columns."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._line: list[str] = []
        self._discard_depth = 0

        # Tables nest in real pages, usually as a layout hangover. A stack keeps an inner table
        # from terminating its parent and losing the outer rows.
        self._tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    # ---------------------------------------------------------------- line assembly

    def _break_line(self) -> None:
        """Flush the current line, if it has content.

        Emits nothing for an empty buffer. Every block element breaks on both its open and its
        close tag, so inserting a separator here would put a blank line between every pair of
        paragraphs and double it between nested blocks.
        """
        text = _WHITESPACE.sub(" ", "".join(self._line)).strip()
        self._line = []
        if text:
            self.lines.append(text)

    def _emit(self, text: str) -> None:
        self.lines.append(text)

    # ---------------------------------------------------------------- tag handling

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._discard_depth:
            return
        if tag in _DISCARD:
            self._discard_depth = 1
            return

        if tag == "table":
            self._break_line()
            self._tables.append([])
            return

        if tag == "tr" and self._tables:
            self._tables[-1].append([])
            return

        if tag in _TABLE_CELLS and self._tables:
            if not self._tables[-1]:
                # A cell outside any row: malformed, but real. Synthesise the row rather than
                # dropping the content.
                self._tables[-1].append([])
            self._cell = []
            return

        if tag in _BLOCK:
            self._break_line()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self._discard_depth and tag == "br":
            self._break_line()

    def handle_endtag(self, tag: str) -> None:
        if self._discard_depth:
            if tag in _DISCARD:
                self._discard_depth = 0
            return

        if tag in _TABLE_CELLS and self._cell is not None:
            text = _WHITESPACE.sub(" ", "".join(self._cell)).strip()
            self._cell = None
            if self._tables and self._tables[-1]:
                self._tables[-1][-1].append(text)
            return

        if tag == "table" and self._tables:
            rows = self._tables.pop()
            rendered = _align(rows)
            # An inner table's rendering belongs to the cell that contains it, flattened onto one
            # line. Emitting it at document level would interleave its rows with the outer
            # table's and destroy both grids.
            if self._tables and self._cell is not None:
                self._cell.append(" ".join(r.strip() for r in rendered))
                return
            for line in rendered:
                self._emit(line)
            if rendered:
                self._emit("")
            return

        if tag in _BLOCK:
            self._break_line()

    def handle_data(self, data: str) -> None:
        if self._discard_depth:
            # Not just the tags — the contents. An inline script's "600 PSI" string is not a
            # specification, and a model handed it would cite text no human can read.
            return

        target = self._cell if self._cell is not None else self._line
        if not data.strip():
            # Whitespace between two inline elements is a genuine word separator: without it
            # `<span>Body Material</span> <span>Bronze</span>` collapses into one token. At the
            # start of a block it is layout, so it is dropped.
            if target:
                target.append(" ")
            return
        target.append(data)

    def finish(self) -> str:
        self._break_line()
        # Any unclosed table still holds rows. Dropping them because the document was malformed
        # would lose exactly the ordering table this parser exists to recover.
        while self._tables:
            for line in _align(self._tables.pop()):
                self._emit(line)
        return _BLANK_RUN.sub("\n\n", "\n".join(self.lines)).strip("\n")


def _align(rows: list[list[str]]) -> list[str]:
    """Render table rows as space-aligned columns.

    Padding is computed across the whole table, not per row, because the text parser looks for
    character positions that are whitespace on *every* line of a block. Row-local padding would
    leave the gap columns misaligned and the table would not be detected at all.
    """
    populated = [row for row in rows if any(cell for cell in row)]
    if not populated:
        return []

    columns = max(len(row) for row in populated)
    if columns == 1:
        # A single-column table is a layout wrapper, not data. Rendering it as a "table" would
        # invite the column detector to find structure that is not there.
        return [row[0] for row in populated if row and row[0]]

    widths = [
        max(len(row[index]) if index < len(row) else 0 for row in populated)
        for index in range(columns)
    ]

    lines: list[str] = []
    for row in populated:
        cells = [
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(columns)
        ]
        lines.append(_COLUMN_GAP.join(cells).rstrip())
    return lines


def html_to_text(html: str) -> str:
    """Render HTML as the aligned plain text the text parser consumes."""
    renderer = _Renderer()
    renderer.feed(html)
    renderer.close()
    return renderer.finish()


def looks_like_html(data: bytes) -> bool:
    """Whether these bytes should be rendered before parsing.

    Sniffed from content rather than trusted from a Content-Type header or a file extension,
    because both are wrong often enough to matter: supplier portals serve HTML as
    ``application/octet-stream``, and a saved page keeps a ``.txt`` name.
    """
    head = data[:2048].lstrip()[:512].lower()
    if not head:
        return False
    if head.startswith((b"<!doctype html", b"<html", b"<?xml-stylesheet")):
        return True
    return b"<html" in head or (b"<head" in head and b"<meta" in head) or b"<body" in head


def parse_html(
    html: str, document: SourceDocument, **kwargs
) -> ParsedDocument:
    """Render HTML to text and parse it, reporting ``html`` as the parser."""
    from axiom.docintel.text_parser import parse_text

    return parse_text(html_to_text(html), document, parser="html", **kwargs)
