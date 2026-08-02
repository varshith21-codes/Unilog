"""Tests for document parsing and quote location.

The quote-location tests are the ones that matter most. They guard the mechanism that makes
fabrication detectable, and they also pin down its *limits* — a quote that is genuinely
printed in the document verifies even when the value does not apply to the target SKU, and
there is a test asserting exactly that so nobody mistakes verification for correctness.
"""

from __future__ import annotations

import io

import pytest
from axiom.core.evidence import SourceDocument
from axiom.docintel import (
    ParsedDocument,
    build_evidence_span,
    locate_quote,
    parse_artifact,
    parse_pdf,
    parse_text,
    squash,
    verify_quote,
)
from axiom.docintel.pdf_parser import PdfParseError

# --------------------------------------------------------------------- text parsing


def test_lines_are_parsed_with_coordinates(parsed_datasheet: ParsedDocument):
    lines = parsed_datasheet.all_lines()
    assert len(lines) > 20
    first = lines[0]
    assert first.text.startswith("MILWAUKEE VALVE")
    assert first.bbox.x1 > first.bbox.x0
    assert first.bbox.y1 > first.bbox.y0
    assert first.page == 1


def test_blank_lines_still_get_a_valid_bbox(source_document: SourceDocument):
    """BoundingBox rejects degenerate boxes, so an empty line must not abort the parse."""
    parsed = parse_text("first\n\n\nlast", source_document)
    for line in parsed.all_lines():
        assert line.bbox.x1 > line.bbox.x0


def test_words_carry_their_own_boxes(source_document: SourceDocument):
    parsed = parse_text("Pressure Rating 600 PSI", source_document)
    words = parsed.all_lines()[0].words
    assert [w.text for w in words] == ["Pressure", "Rating", "600", "PSI"]
    # boxes advance left to right and do not overlap
    for earlier, later in zip(words, words[1:], strict=False):
        assert later.bbox.x0 >= earlier.bbox.x1


def test_long_documents_are_split_into_pages(source_document: SourceDocument):
    content = "\n".join(f"line {i}" for i in range(130))
    parsed = parse_text(content, source_document, lines_per_page=58)
    assert parsed.page_count == 3
    assert parsed.page(1) is not None
    assert parsed.page(2).lines[0].page == 2
    assert parsed.page(99) is None


def test_full_text_round_trips(parsed_datasheet: ParsedDocument, datasheet_text: str):
    assert "Consult factory" in parsed_datasheet.full_text
    assert parsed_datasheet.full_text.count("BA-100-075") == datasheet_text.count("BA-100-075")


# --------------------------------------------------------------------- table detection


def test_ordering_table_is_detected(parsed_datasheet: ParsedDocument):
    tables = parsed_datasheet.all_tables()
    assert len(tables) == 1, "the ordering matrix is the only real table on this page"
    table = tables[0]
    assert table.header[0] == "Part Number"
    assert "Carton Qty" in table.header


def test_dot_leader_spec_block_is_not_treated_as_a_table(parsed_datasheet: ParsedDocument):
    """A two-column spec list is not an ordering matrix.

    Treating it as a table would bury the specification block inside a fake grid and make
    every spec value harder to cite, not easier.
    """
    for table in parsed_datasheet.all_tables():
        flattened = " ".join(cell.text for cell in table.cells)
        assert "Body Material" not in flattened


def test_multiword_header_stays_one_cell(parsed_datasheet: ParsedDocument):
    """Column boundaries require whitespace on *every* row, which is what keeps
    'Part Number' from splitting into two cells."""
    table = parsed_datasheet.all_tables()[0]
    assert "Part Number" in table.header
    assert "Part" not in table.header


def test_table_rows_are_reconstructed(parsed_datasheet: ParsedDocument):
    rows = parsed_datasheet.all_tables()[0].rows()
    values = [row[0] for row in rows]
    assert values[1:] == [
        "BA-100-025",
        "BA-100-050",
        "BA-100-075",
        "BA-100-100",
        "BA-100-125",
    ]


def test_find_row_locates_the_target_sku(parsed_datasheet: ParsedDocument):
    """Deterministically finding the right row avoids an error class that quote
    verification cannot detect: a real value read from the wrong row."""
    table = parsed_datasheet.all_tables()[0]
    row = table.find_row("BA-100-075")
    assert row == 3
    assert table.rows()[row] == ["BA-100-075", '3/4"', "Lever", "12"]


def test_find_row_returns_none_for_an_absent_value(parsed_datasheet: ParsedDocument):
    assert parsed_datasheet.all_tables()[0].find_row("BA-999-999") is None
    assert parsed_datasheet.all_tables()[0].find_row("  ") is None


def test_cell_reference_and_row_bbox(parsed_datasheet: ParsedDocument):
    table = parsed_datasheet.all_tables()[0]
    assert table.cell_ref(3, 1) == "t1:r3:c1"
    bbox = table.row_bbox(3)
    assert bbox is not None and bbox.x1 > bbox.x0
    assert table.row_bbox(99) is None


def test_table_lookup_by_id(parsed_datasheet: ParsedDocument):
    assert parsed_datasheet.table("t1") is not None
    assert parsed_datasheet.table("nope") is None


def test_table_detection_can_be_disabled(datasheet_text, source_document):
    parsed = parse_text(datasheet_text, source_document, detect_tables=False)
    assert parsed.all_tables() == []


# --------------------------------------------------------------------- prompt rendering


def test_prompt_content_carries_page_markers(parsed_datasheet: ParsedDocument):
    content = parsed_datasheet.to_prompt_content()
    assert '<page number="1">' in content
    assert "</page>" in content


def test_prompt_content_renders_tables_structurally(parsed_datasheet: ParsedDocument):
    content = parsed_datasheet.to_prompt_content()
    assert '<table id="t1">' in content
    assert "r3:" in content
    assert "BA-100-075" in content


def test_table_rows_are_not_duplicated_as_loose_lines(parsed_datasheet: ParsedDocument):
    """Emitting the same numbers twice in two layouts forces the model to guess which is
    authoritative."""
    content = parsed_datasheet.to_prompt_content()
    assert content.count("BA-100-075") == 1


def test_prompt_content_respects_max_pages(source_document: SourceDocument):
    content = "\n".join(f"line {i}" for i in range(130))
    parsed = parse_text(content, source_document, lines_per_page=58)
    assert '<page number="3">' not in parsed.to_prompt_content(max_pages=2)


# --------------------------------------------------------------------- squashing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  600   PSI  ", "600 psi"),
        ("600\u00a0PSI", "600 psi"),
        ("18\u201322 ft-lb", "18-22 ft-lb"),
        ('3/4\u201d', '3/4"'),
    ],
)
def test_squash_normalises_cosmetic_variation(raw, expected):
    """PDF extraction mangles whitespace and dashes; rejecting correct quotes over that
    would make verification useless."""
    assert squash(raw) == expected


# --------------------------------------------------------------------- quote location


def test_exact_quote_in_a_line_is_located(parsed_datasheet: ParsedDocument):
    location = locate_quote("600 PSI WOG @ 73 degF", parsed_datasheet)
    assert location is not None
    assert location.method == "exact_line"
    assert location.match_score == 1.0
    assert location.page == 1


def test_located_bbox_is_tightened_to_the_quoted_fragment(parsed_datasheet: ParsedDocument):
    """The highlight should sit on the value, not on the whole line."""
    line = next(
        line for line in parsed_datasheet.all_lines() if "Pressure Rating" in line.text
    )
    location = locate_quote("600 PSI WOG @ 73 degF", parsed_datasheet)
    assert location is not None
    assert location.bbox.x0 > line.bbox.x0, "highlight should start after the label"
    assert location.bbox.x1 <= line.bbox.x1 + 0.001


def test_quote_from_a_table_cell_yields_a_cell_reference(parsed_datasheet: ParsedDocument):
    location = locate_quote("BA-100-075", parsed_datasheet)
    assert location is not None
    assert location.method == "table_cell"
    assert location.table_ref == "t1:r3:c0"


def test_whitespace_variation_still_matches(parsed_datasheet: ParsedDocument):
    assert locate_quote("600   PSI   WOG  @  73  degF", parsed_datasheet) is not None


def test_en_dash_variation_still_matches(parsed_datasheet: ParsedDocument):
    """The source uses a hyphen; a model may return an en dash."""
    assert locate_quote("18\u201322 ft-lb", parsed_datasheet) is not None


def test_fabricated_quote_is_not_located(parsed_datasheet: ParsedDocument):
    """The mechanism that makes fabrication detectable, asserted directly."""
    assert locate_quote("Flow Coefficient (Cv) .......... 18.5", parsed_datasheet) is None
    assert locate_quote("Country of Origin .... Taiwan", parsed_datasheet) is None


def test_paraphrase_is_rejected(parsed_datasheet: ParsedDocument):
    """A loose threshold would turn verification into theatre."""
    assert locate_quote("the pressure rating is six hundred psi", parsed_datasheet) is None


def test_empty_quotes_are_not_evidence(parsed_datasheet: ParsedDocument):
    assert locate_quote("", parsed_datasheet) is None
    assert locate_quote(None, parsed_datasheet) is None
    assert locate_quote("   ", parsed_datasheet) is None


# --------------------------------------------------------------------- short quotes
#
# Regression tests for a guard that was suppressing real data. Requiring two characters
# rejected single-digit carton quantities, turning correct values into gaps. A one-character
# quote is not evidence in prose, but it is strong evidence when it resolves to a table cell,
# because the cell reference supplies the precision the quote lacks.


def test_single_character_quote_resolves_when_it_is_a_table_cell(
    parsed_datasheet: ParsedDocument,
):
    location = locate_quote("6", parsed_datasheet)
    assert location is not None
    assert location.method == "table_cell"
    assert location.table_ref == "t1:r5:c3", "must pin the exact row and column"


def test_single_character_quote_is_rejected_when_only_found_in_prose(
    source_document: SourceDocument,
):
    """Without coordinates a one-character quote would match almost anything."""
    parsed = parse_text("Rated for 6 bar service in ordinary locations", source_document)
    assert locate_quote("6", parsed) is None


def test_short_quote_must_match_a_cell_exactly_not_partially(
    parsed_datasheet: ParsedDocument,
):
    """A digit appearing inside a longer cell is not a citation for that digit."""
    # '2' appears inside '24' and '12' but is not itself any cell's full content.
    assert locate_quote("2", parsed_datasheet) is None


def test_short_quote_for_an_absent_value_is_still_rejected(parsed_datasheet: ParsedDocument):
    assert locate_quote("9", parsed_datasheet) is None


def test_multiline_quote_is_located(source_document: SourceDocument):
    parsed = parse_text(
        "Maximum working pressure is\n600 PSI at ambient temperature", source_document
    )
    location = locate_quote(
        "Maximum working pressure is 600 PSI at ambient temperature", parsed
    )
    assert location is not None
    assert location.method == "exact_multiline"


def test_fuzzy_match_within_threshold(source_document: SourceDocument):
    parsed = parse_text("Seat Material RPTFE reinforced", source_document)
    location = locate_quote("Seat Material RPTFE reinforcd", parsed)  # one typo
    assert location is not None
    assert location.method == "fuzzy_line"
    assert 0.90 <= location.match_score < 1.0


def test_page_hint_is_honoured_but_not_trusted(source_document: SourceDocument):
    content = "\n".join(["filler"] * 58 + ["Pressure Rating 600 PSI WOG"])
    parsed = parse_text(content, source_document, lines_per_page=58)

    correct = locate_quote("Pressure Rating 600 PSI WOG", parsed, page_hint=2)
    assert correct is not None and correct.page == 2
    assert correct.page_hint_matched is True

    # a wrong hint must still find the text, and record that the hint was wrong
    wrong = locate_quote("Pressure Rating 600 PSI WOG", parsed, page_hint=1)
    assert wrong is not None and wrong.page == 2
    assert wrong.page_hint_matched is False


def test_verify_quote_predicate(parsed_datasheet: ParsedDocument):
    ok, score = verify_quote("600 PSI WOG @ 73 degF", parsed_datasheet)
    assert ok is True and score == 1.0
    bad, bad_score = verify_quote("Cv 18.5", parsed_datasheet)
    assert bad is False and bad_score == 0.0


def test_verification_cannot_catch_a_scope_error(parsed_datasheet: ParsedDocument):
    """The documented limit of this mechanism.

    The torque figure is qualified to the 1/2" size. For a 3/4" SKU the value is wrong, yet
    the quote verifies perfectly because the text really is in the document. Catching this
    needs the applicability rule in the prompt plus validation layers, not a string search.
    """
    ok, score = verify_quote('18-22 ft-lb (1/2" size)', parsed_datasheet)
    assert ok is True
    assert score == 1.0


# --------------------------------------------------------------------- evidence spans


def test_build_span_marks_a_located_quote_verified(parsed_datasheet: ParsedDocument):
    span = build_evidence_span("600 PSI WOG @ 73 degF", parsed_datasheet, span_id="sp_1")
    assert span.quote_verified is True
    assert span.match_score == 1.0
    assert span.page == 1
    assert span.bbox is not None
    assert span.document_sha256 == parsed_datasheet.document.sha256


def test_build_span_records_an_unverifiable_claim_rather_than_hiding_it(
    parsed_datasheet: ParsedDocument,
):
    """Recording the failed claim is a better diagnostic than a silent absence."""
    span = build_evidence_span("Cv is 18.5", parsed_datasheet, span_id="sp_2", page_hint=4)
    assert span.quote_verified is False
    assert span.match_score == 0.0
    assert span.quote == "Cv is 18.5"
    assert span.page == 4, "the claimed page is preserved for the reviewer to see"


def test_table_span_carries_the_cell_reference(parsed_datasheet: ParsedDocument):
    span = build_evidence_span("BA-100-075", parsed_datasheet, span_id="sp_3")
    assert span.table_ref == "t1:r3:c0"
    assert span.locator() == "milwaukee-ba100@9f2c0000 p.1 t1:r3:c0"


# --------------------------------------------------------------------- rendered table rows
#
# Regression tests for a real failure found by running the pipeline. Tables are rendered into
# the prompt with '|' separators; the model then quotes the row as it was shown, and those
# separators do not exist in the source document. Searching raw text for that string fails and
# a correct value gets discarded.


def test_rendered_table_row_is_matched_structurally(parsed_datasheet: ParsedDocument):
    quoted_as_shown = 'BA-100-075  | 3/4"   | Lever  | 12'
    location = locate_quote(quoted_as_shown, parsed_datasheet)
    assert location is not None, "a model quoting the row it was shown must not be penalised"
    assert location.method == "table_row"
    assert location.table_ref == "t1:r3"


def test_table_matches_resolve_to_a_source_line(parsed_datasheet: ParsedDocument):
    """The review UI highlights a line, so a table citation must carry one.

    Regression guard: table matches originally returned geometry and a cell reference but no
    line index, so the values most in need of row-level highlighting were the ones the UI could
    not highlight at all.
    """
    row = locate_quote('BA-100-075  | 3/4"   | Lever  | 12', parsed_datasheet)
    cell = locate_quote("BA-100-075", parsed_datasheet)

    assert row is not None and row.line_index is not None
    assert cell is not None and cell.line_index is not None
    assert row.line_index == cell.line_index, "both point at the same source row"

    line = parsed_datasheet.all_lines()[row.line_index]
    assert "BA-100-075" in line.text


def test_rendered_row_citation_is_row_level(parsed_datasheet: ParsedDocument):
    """Row granularity is the honest claim: the value came from this row."""
    span = build_evidence_span(
        'BA-100-075  | 3/4"   | Lever  | 12', parsed_datasheet, span_id="sp_row"
    )
    assert span.quote_verified is True
    assert span.table_ref == "t1:r3"
    assert span.locator() == "milwaukee-ba100@9f2c0000 p.1 t1:r3"


def test_rendered_row_binds_to_the_correct_row(parsed_datasheet: ParsedDocument):
    """Neighbouring rows must not be confused; that is the error this whole area guards."""
    location = locate_quote('BA-100-125  | 1-1/4"  | Tee  | 6', parsed_datasheet)
    assert location is not None
    assert location.table_ref == "t1:r5"


def test_invented_row_is_still_rejected(parsed_datasheet: ParsedDocument):
    """Structural row matching must not become a loophole for fabricated rows."""
    assert locate_quote('BA-100-999  | 8"   | Gear  | 1', parsed_datasheet) is None


def test_row_match_requires_cells_in_order(parsed_datasheet: ParsedDocument):
    """A shuffled row is not the row that was shown."""
    assert locate_quote('12 | Lever | 3/4" | BA-100-075', parsed_datasheet) is None


def test_stray_separator_without_a_real_row_is_rejected(parsed_datasheet: ParsedDocument):
    """Row matching relaxes the check for genuine rendered rows only.

    A single fragment carrying a stray separator is neither verbatim source text nor a
    complete row, so it stays unlocatable. Loosening this would turn the row path into a
    general-purpose bypass of quote verification.
    """
    assert locate_quote("| BA-100-075", parsed_datasheet) is None


def test_clean_cell_quote_still_prefers_a_cell_citation(parsed_datasheet: ParsedDocument):
    location = locate_quote("BA-100-075", parsed_datasheet)
    assert location is not None
    assert location.method == "table_cell"
    assert location.table_ref == "t1:r3:c0"


# --------------------------------------------------------------------- PDF path


def _make_pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Courier", 10)
    y = 750
    for line in lines:
        pdf.drawString(54, y, line)
        y -= 14
    pdf.save()
    return buffer.getvalue()


def test_pdf_parsing_produces_citable_lines(source_document: SourceDocument):
    data = _make_pdf(["MILWAUKEE BA-100 SERIES", "Pressure Rating 600 PSI WOG @ 73 degF"])
    parsed = parse_pdf(data, source_document)

    assert parsed.parser == "pdfplumber"
    assert parsed.page_count == 1
    assert "600 PSI WOG" in parsed.full_text
    location = locate_quote("600 PSI WOG @ 73 degF", parsed)
    assert location is not None
    assert location.page == 1
    assert location.bbox.x1 > location.bbox.x0


def test_pdf_coordinates_are_real_not_synthetic(source_document: SourceDocument):
    """Two lines at different heights must have different vertical positions."""
    data = _make_pdf(["first line", "second line"])
    parsed = parse_pdf(data, source_document)
    tops = sorted({round(line.bbox.y0, 1) for line in parsed.all_lines()})
    assert len(tops) == 2


def test_scanned_page_is_flagged_rather_than_reported_as_empty(source_document):
    """'No Cv value in this datasheet' and 'we could not read this datasheet' are
    different statements, and only one of them is an honest gap."""
    data = _make_pdf([])
    parsed = parse_pdf(data, source_document)
    assert any("probably a scan" in w for w in parsed.warnings)


def test_corrupt_pdf_raises_a_clear_error(source_document: SourceDocument):
    with pytest.raises(PdfParseError):
        parse_pdf(b"%PDF-1.7 truncated garbage", source_document)


def test_parse_artifact_dispatches_on_content(source_document: SourceDocument):
    pdf = _make_pdf(["Pressure Rating 600 PSI"])
    assert parse_artifact(pdf, source_document).parser == "pdfplumber"
    assert parse_artifact(b"Pressure Rating 600 PSI", source_document).parser == "text"


def test_parse_artifact_survives_cp1252_bytes(source_document: SourceDocument):
    data = "Size 3/4\u201d valve".encode("cp1252")
    parsed = parse_artifact(data, source_document)
    assert "valve" in parsed.full_text
