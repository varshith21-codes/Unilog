"""Quote location: turning a model's claimed quote into a verified coordinate.

This is the cheapest and most effective anti-fabrication mechanism in the system. The
extraction contract requires the model to return a verbatim quote for every value. If that
quote cannot be located in the source, the value is discarded — no model call, no judgement,
no second opinion, just a string search that either succeeds or does not.

It is also what makes the evidence viewer possible: a located quote carries a page, a
bounding box and, when it came from a table, a cell reference.

**What this cannot catch.** A value that is genuinely printed in the document but describes a
different variant will verify perfectly, because the quote is real. That is a scope error, not
a fabrication, and it needs the applicability rule in the prompt plus validation layers L2/L3.
Quote verification is a floor, not a ceiling.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.docintel.models import ParsedDocument, ParsedLine, ParsedTable

DEFAULT_THRESHOLD = 0.90
"""Minimum similarity for a fuzzy match to count as verified.

Set high deliberately. A loose threshold turns quote verification into theatre: it would
accept a paraphrase, which is exactly the failure mode the check exists to prevent.
"""

MIN_QUOTE_CHARS = 2
"""Minimum quote length for free-text evidence.

A one-character quote matches almost any document, so in prose it is not evidence. But a short
quote that resolves to a specific *table cell* is strong evidence, because the citation carries
coordinates: ``t1:r5:c3`` identifies exactly which row and column the value came from.

This distinction matters in practice. Carton quantities, port counts and pack sizes are
routinely single digits, and rejecting them outright turned correct values into gaps — the
guard was suppressing real data rather than fabrication.
"""


def squash(text: str) -> str:
    """Normalise whitespace and case for comparison.

    PDF extraction routinely collapses or expands runs of spaces and swaps hyphen variants,
    so comparing raw strings would reject correct quotes for cosmetic reasons.
    """
    normalised = text.replace("\u00a0", " ")
    for dash in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        normalised = normalised.replace(dash, "-")
    normalised = normalised.replace("\u201c", '"').replace("\u201d", '"')
    normalised = normalised.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", normalised).strip().casefold()


@dataclass(frozen=True)
class QuoteLocation:
    """Where a quote was found, and how confident the match is."""

    page: int
    bbox: BoundingBox
    match_score: float
    method: str
    matched_text: str
    table_ref: str | None = None
    line_index: int | None = None
    page_hint_matched: bool | None = None
    """None when no hint was supplied. False is a mild signal: the model cited the wrong
    page for text that does exist, which suggests it was reconstructing rather than reading."""

    @property
    def is_exact(self) -> bool:
        return self.match_score >= 0.999


def locate_quote(
    quote: str | None,
    parsed: ParsedDocument,
    *,
    page_hint: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> QuoteLocation | None:
    """Find a quote in a parsed document. Returns None when it cannot be located.

    Search order is *most precise citation first*, then cheapest. Table cells are checked
    before lines because a cell reference (``t1:r3:c0``) is strictly more informative than a
    line reference: it identifies the row, which is what a reviewer needs in order to confirm
    the value belongs to this part number rather than a neighbouring one. Searching lines
    first would find the same text and silently discard that precision.

    Within tables, exact cell equality beats containment, so a short quote like ``12`` binds
    to the cell that *is* that value rather than the first cell that happens to contain it.
    """
    if not quote or not quote.strip():
        return None
    needle = squash(quote)
    if not needle:
        return None

    ordered = _pages_in_search_order(parsed, page_hint)

    if len(quote.strip()) < MIN_QUOTE_CHARS:
        # Short quotes are only accepted as an exact table-cell match, where the cell reference
        # supplies the precision the quote itself lacks. Anywhere else they are not evidence.
        for page in ordered:
            hint_matched = None if page_hint is None else (page.number == page_hint)
            if found := _match_exact_cell(needle, page.tables, page.lines, hint_matched):
                return found
        return None

    best: QuoteLocation | None = None

    for page in ordered:
        hint_matched = None if page_hint is None else (page.number == page_hint)

        if found := _match_in_tables(needle, page.tables, page.lines, hint_matched):
            return found
        if found := _match_table_row(needle, page.tables, page.lines, hint_matched):
            return found
        if found := _match_in_lines(needle, page.lines, hint_matched):
            return found
        if found := _match_across_lines(needle, page.lines, page.number, hint_matched):
            return found

        candidate = _fuzzy_match(needle, page.lines, hint_matched, threshold)
        if candidate and (best is None or candidate.match_score > best.match_score):
            best = candidate

    return best


def _pages_in_search_order(parsed: ParsedDocument, page_hint: int | None):
    """Hinted page first. Respects the model's claim without trusting it."""
    if page_hint is None:
        return list(parsed.pages)
    hinted = [p for p in parsed.pages if p.number == page_hint]
    return hinted + [p for p in parsed.pages if p.number != page_hint]


def _match_in_lines(
    needle: str, lines: tuple[ParsedLine, ...], hint_matched: bool | None
) -> QuoteLocation | None:
    """Exact substring within a single line, with the bbox tightened to the fragment."""
    for line in lines:
        haystack = squash(line.text)
        if not haystack or needle not in haystack:
            continue
        start, end = _char_range(line.text, needle)
        bbox = line.span_bbox(start, end) if start is not None else line.bbox
        return QuoteLocation(
            page=line.page,
            bbox=bbox,
            match_score=1.0,
            method="exact_line",
            matched_text=line.text.strip(),
            line_index=line.line_index,
            page_hint_matched=hint_matched,
        )
    return None


def _char_range(original: str, needle: str) -> tuple[int | None, int | None]:
    """Map a squashed match back to character offsets in the original line.

    Needed because the highlight must sit over the original characters, not over the
    normalised form used for comparison.
    """
    squashed_to_original: list[int] = []
    squashed_chars: list[str] = []
    previous_was_space = True
    for index, char in enumerate(original):
        replacement = squash(char)
        if not replacement:
            if not previous_was_space:
                squashed_chars.append(" ")
                squashed_to_original.append(index)
                previous_was_space = True
            continue
        squashed_chars.append(replacement)
        squashed_to_original.append(index)
        previous_was_space = False

    haystack = "".join(squashed_chars).strip()
    offset = len("".join(squashed_chars)) - len("".join(squashed_chars).lstrip())
    position = haystack.find(needle)
    if position < 0:
        return None, None
    start_squashed = position + offset
    end_squashed = start_squashed + len(needle)
    if start_squashed >= len(squashed_to_original):
        return None, None
    start = squashed_to_original[start_squashed]
    end = (
        squashed_to_original[end_squashed - 1] + 1
        if end_squashed - 1 < len(squashed_to_original)
        else len(original)
    )
    return start, end


def _match_across_lines(
    needle: str, lines: tuple[ParsedLine, ...], page: int, hint_matched: bool | None
) -> QuoteLocation | None:
    """Handle a quote spanning a line break — common when a spec wraps."""
    for start_index in range(len(lines)):
        combined = ""
        for end_index in range(start_index, min(start_index + 4, len(lines))):
            combined = squash(" ".join(line.text for line in lines[start_index : end_index + 1]))
            if needle in combined:
                involved = lines[start_index : end_index + 1]
                return QuoteLocation(
                    page=page,
                    bbox=_union(involved),
                    match_score=1.0,
                    method="exact_multiline",
                    matched_text=" ".join(line.text.strip() for line in involved),
                    line_index=involved[0].line_index,
                    page_hint_matched=hint_matched,
                )
            if len(combined) > len(needle) * 4:
                break
    return None


def _line_index_for(bbox: BoundingBox, lines: tuple[ParsedLine, ...]) -> int | None:
    """Line whose vertical extent best overlaps a region.

    Table matches carry geometry but no text position, and the review UI needs a line to
    highlight. Deriving it from vertical overlap keeps this parser-agnostic — computing it from
    the text parser's fixed line height would silently break on PDFs.
    """
    best_index: int | None = None
    best_overlap = 0.0
    for line in lines:
        overlap = min(bbox.y1, line.bbox.y1) - max(bbox.y0, line.bbox.y0)
        if overlap > best_overlap:
            best_overlap, best_index = overlap, line.line_index
    return best_index


def _match_in_tables(
    needle: str,
    tables: tuple[ParsedTable, ...],
    lines: tuple[ParsedLine, ...],
    hint_matched: bool | None,
) -> QuoteLocation | None:
    """Match a table cell, yielding a citable cell reference.

    Two passes: exact cell equality, then containment. Without the equality pass, a short
    quote such as ``12`` would bind to whichever cell merely contains those characters,
    which produces a citation pointing at the wrong row.
    """
    if found := _match_exact_cell(needle, tables, lines, hint_matched):
        return found
    return _match_cell_containing(needle, tables, lines, hint_matched)


def _match_exact_cell(
    needle: str,
    tables: tuple[ParsedTable, ...],
    lines: tuple[ParsedLine, ...],
    hint_matched: bool | None,
) -> QuoteLocation | None:
    """Cell whose entire content equals the quote."""
    for table in tables:
        for cell in table.cells:
            if squash(cell.text) == needle:
                return _cell_location(table, cell, lines, hint_matched)
    return None


def _match_cell_containing(
    needle: str,
    tables: tuple[ParsedTable, ...],
    lines: tuple[ParsedLine, ...],
    hint_matched: bool | None,
) -> QuoteLocation | None:
    for table in tables:
        for cell in table.cells:
            cell_text = squash(cell.text)
            if cell_text and needle in cell_text:
                return _cell_location(table, cell, lines, hint_matched)
    return None


def _cell_location(
    table: ParsedTable, cell, lines: tuple[ParsedLine, ...], hint_matched: bool | None
) -> QuoteLocation:
    return QuoteLocation(
        page=table.page,
        bbox=cell.bbox,
        match_score=1.0,
        method="table_cell",
        matched_text=cell.text,
        table_ref=table.cell_ref(cell.row, cell.col),
        line_index=_line_index_for(cell.bbox, lines),
        page_hint_matched=hint_matched,
    )


ROW_SEPARATOR = "|"
"""Column separator used when rendering tables into the prompt."""

MIN_ROW_CELLS = 2


def _match_table_row(
    needle: str,
    tables: tuple[ParsedTable, ...],
    lines: tuple[ParsedLine, ...],
    hint_matched: bool | None,
) -> QuoteLocation | None:
    """Match a quote that is a whole rendered table row.

    Tables are rendered into the prompt with ``|`` separators so the model can see column
    structure. When a value comes from a table the model naturally quotes the row *as it was
    shown*, which contains separators that do not exist in the underlying document. Searching
    the raw text for that string fails, and the value gets discarded even though the model
    behaved correctly and the data is real.

    So a rendered row is matched structurally instead: split on the separator and confirm the
    cells against an actual table row. The resulting citation is row-level (``t1:r3``), which
    is the honest granularity — the claim really is "this came from this row".
    """
    if ROW_SEPARATOR not in needle:
        return None
    parts = [p.strip() for p in needle.split(ROW_SEPARATOR)]
    parts = [p for p in parts if p]
    if len(parts) < MIN_ROW_CELLS:
        return None

    for table in tables:
        for row_index, row in enumerate(table.rows()):
            cells = [squash(text) for text in row if text.strip()]
            if not cells:
                continue
            # Every quoted fragment must appear in the row, in order.
            if not _is_ordered_subset(parts, cells):
                continue
            bbox = table.row_bbox(row_index)
            if bbox is None:
                continue
            return QuoteLocation(
                page=table.page,
                bbox=bbox,
                match_score=1.0,
                method="table_row",
                matched_text=" | ".join(row).strip(),
                table_ref=f"{table.table_id}:r{row_index}",
                line_index=_line_index_for(bbox, lines),
                page_hint_matched=hint_matched,
            )
    return None


def _is_ordered_subset(needles: list[str], cells: list[str]) -> bool:
    position = 0
    for fragment in needles:
        while position < len(cells) and fragment not in cells[position]:
            position += 1
        if position >= len(cells):
            return False
        position += 1
    return True


def _fuzzy_match(
    needle: str,
    lines: tuple[ParsedLine, ...],
    hint_matched: bool | None,
    threshold: float,
) -> QuoteLocation | None:
    best: QuoteLocation | None = None
    for line in lines:
        haystack = squash(line.text)
        if not haystack:
            continue
        score = difflib.SequenceMatcher(None, needle, haystack).ratio()
        if score >= threshold and (best is None or score > best.match_score):
            best = QuoteLocation(
                page=line.page,
                bbox=line.bbox,
                match_score=round(score, 4),
                method="fuzzy_line",
                matched_text=line.text.strip(),
                line_index=line.line_index,
                page_hint_matched=hint_matched,
            )
    return best


def _union(lines) -> BoundingBox:
    return BoundingBox(
        x0=min(line.bbox.x0 for line in lines),
        y0=min(line.bbox.y0 for line in lines),
        x1=max(line.bbox.x1 for line in lines),
        y1=max(line.bbox.y1 for line in lines),
    )


def verify_quote(
    quote: str | None, parsed: ParsedDocument, *, threshold: float = DEFAULT_THRESHOLD
) -> tuple[bool, float]:
    """Convenience predicate: was the quote locatable, and how well did it match?"""
    location = locate_quote(quote, parsed, threshold=threshold)
    return (location is not None, location.match_score if location else 0.0)


def build_evidence_span(
    quote: str,
    parsed: ParsedDocument,
    *,
    span_id: str,
    page_hint: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> EvidenceSpan:
    """Build an evidence span, verified where possible.

    An unlocatable quote still produces a span, with ``quote_verified=False``. Recording the
    failed claim is more useful than discarding it: the reviewer sees what the model asserted
    and that it could not be substantiated, which is a far better diagnostic than a silent
    absence. Publication is blocked separately by :attr:`AttributeValue.is_publishable`.
    """
    location = locate_quote(quote, parsed, page_hint=page_hint, threshold=threshold)
    document = parsed.document
    if location is None:
        return EvidenceSpan(
            span_id=span_id,
            document_id=document.document_id,
            document_sha256=document.sha256,
            quote=quote,
            page=page_hint,
            quote_verified=False,
            match_score=0.0,
        )
    return EvidenceSpan(
        span_id=span_id,
        document_id=document.document_id,
        document_sha256=document.sha256,
        quote=quote,
        page=location.page,
        bbox=location.bbox,
        table_ref=location.table_ref,
        quote_verified=True,
        match_score=location.match_score,
    )
