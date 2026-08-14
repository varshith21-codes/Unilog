"""Deterministic extraction from document layout.

Two kinds of test here, and the second matters more.

The unit tests pin the mechanisms: label splitting, row selection, vocabulary separation, the
refusal paths. They use small hand-built documents so a failure names one cause.

The **golden-set test** at the bottom is the one that would catch a real regression. It runs the
extractor over the three committed sample documents — including a real PDF — and compares every
value against `data/golden/pvf_valves.yaml`, which is hand-read ground truth. Plausible-looking
output proves nothing; agreement with a human reading is the only evidence that counts. It also
asserts the golden set's `absent` list is respected, which is what catches fabrication.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from axiom.core.compare import agree
from axiom.core.evidence import BoundingBox, DocumentType, SourceDocument
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.docintel import parse_artifact
from axiom.docintel.models import (
    ParsedDocument,
    ParsedLine,
    ParsedPage,
    ParsedTable,
    TableCell,
)
from axiom.extract.structured import (
    ORDERING_TABLE,
    SPEC_LINE,
    contested_labels,
    extract_structured,
    label_lookup,
    reject_unresolved,
    size_qualifier,
    split_spec_line,
    to_attribute_values,
    verify_quotes,
)
from axiom.normalize import normalize_all
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "data" / "samples"
GOLDEN = REPO_ROOT / "data" / "golden" / "pvf_valves.yaml"

BALL = "PLB.VLV.BALL.2PC"
GATE = "PLB.VLV.GATE.BRZ"
SHA = "a" * 64

# Which sample document each golden `source` id refers to, and the class to extract against.
GOLDEN_DOCUMENTS = {
    "ba100": ("ba100.txt", BALL),
    "gv200": ("gv200.txt", GATE),
    "ap77c": ("ap77c.pdf", BALL),
}


@pytest.fixture(scope="module")
def registry():
    return load_default()


def _document(name: str) -> SourceDocument:
    return SourceDocument(
        document_id=name,
        uri=f"file:///{name}",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime.now(UTC),
    )


def _parse_sample(name: str) -> ParsedDocument:
    return parse_artifact((SAMPLES / name).read_bytes(), _document(name))


def _line(text: str, index: int, *, y: float = 0.0) -> ParsedLine:
    return ParsedLine(
        text=text,
        bbox=BoundingBox(x0=0.0, y0=y, x1=float(max(len(text), 1)), y1=y + 1.0),
        page=1,
        line_index=index,
    )


def _synthetic(
    lines: list[str], *, tables: tuple[ParsedTable, ...] = ()
) -> ParsedDocument:
    """A one-page document from bare text, for pinning one behaviour at a time.

    Lines are stacked well below any table's rows so the overlap suppression in the extractor does
    not silently hide them — that suppression has its own test.
    """
    parsed_lines = tuple(_line(text, i, y=1000.0 + i * 10) for i, text in enumerate(lines))
    page = ParsedPage(number=1, width=612.0, height=792.0, lines=parsed_lines, tables=tables)
    return ParsedDocument(document=_document("synthetic"), pages=(page,), parser="test")


def _table(header: list[str], rows: list[list[str]], *, table_id: str = "t1") -> ParsedTable:
    cells: list[TableCell] = []
    grid = [header, *rows]
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            cells.append(
                TableCell(
                    text=text,
                    row=r,
                    col=c,
                    bbox=BoundingBox(
                        x0=float(c * 10),
                        y0=float(r * 10),
                        x1=float(c * 10 + 9),
                        y1=float(r * 10 + 9),
                    ),
                )
            )
    return ParsedTable(
        table_id=table_id,
        page=1,
        cells=tuple(cells),
        row_count=len(grid),
        col_count=len(header),
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=float(len(grid) * 10)),
    )


# --------------------------------------------------------------------------- line splitting


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        ("  Body Material .................. Bronze C84400", "Body Material", "Bronze C84400"),
        ("Pressure Rating ...... 600 PSI WOG", "Pressure Rating", "600 PSI WOG"),
        ("Country of origin: Taiwan", "Country of origin", "Taiwan"),
        ("Seat Material      RPTFE", "Seat Material", "RPTFE"),
        ("Steam Rating ___ 150 PSI WSP", "Steam Rating", "150 PSI WSP"),
    ],
)
def test_separators_are_all_recognised(text, label, value):
    split = split_spec_line(text)
    assert split is not None
    assert split[0] == label
    assert split[1] == value


def test_the_offset_locates_the_value_in_the_line():
    """The offset tightens a citation's highlight from the whole line to the value."""
    text = "  Body Material .................. Bronze C84400"
    label, value, offset = split_spec_line(text)
    assert text[offset : offset + len(value)] == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "NOTE 1: Sizes 1 inch and larger ship with a locking lever handle.",
        "WARNING: read the manual before installation",
        "See page 7 for the derating chart",
        "* Consult factory for special orders",
    ],
)
def test_commentary_is_not_a_specification(text):
    """A footnote read as a spec attaches a qualified value unqualified."""
    assert split_spec_line(text) is None


def test_prose_with_a_colon_is_not_a_label():
    """Otherwise every sentence containing a colon becomes an attribute."""
    assert split_spec_line(
        "Installation: the valve must be supported independently of the pipework it serves"
    ) is None


def test_a_label_cannot_be_a_sentence():
    assert split_spec_line("This is a very long descriptive phrase indeed: value") is None


# --------------------------------------------------------------------------- vocabularies


def test_spec_labels_and_table_headers_are_separate_vocabularies(registry):
    """The split that stops a column header being read as a specification label."""
    spec = label_lookup(registry, BALL, source=SPEC_LINE)
    table = label_lookup(registry, BALL, source=ORDERING_TABLE)

    assert "handwheel" in table
    assert "handwheel" not in spec, (
        "'Handwheel' is a valid ordering-table column for handle_type, but in a specification "
        "block 'Handwheel ..... Malleable Iron' is the handwheel's material"
    )


def test_the_handwheel_material_trap_is_closed(registry):
    """The concrete regression. Found on gv200.txt before spec_labels existed."""
    parsed = _synthetic(["  Handwheel ...................... Malleable Iron"])
    result = extract_structured(parsed, registry, class_code=GATE)
    assert "handle_type" not in result.codes
    assert "Handwheel" in result.unmapped_labels


def test_a_class_only_sees_attributes_it_binds(registry):
    """Scoping is what stops a 'Size' column populating any length attribute in the dictionary."""
    lookup = label_lookup(registry, BALL, source=ORDERING_TABLE)
    bound = {b.code for b in registry.product_class(BALL).attributes}
    assert {b.attribute_code for b in lookup.values()} <= bound


def test_attribute_name_is_always_matched(registry):
    lookup = label_lookup(registry, BALL, source=SPEC_LINE)
    assert lookup["bodymaterial"].attribute_code == "body_material"
    assert lookup["endconnection"].attribute_code == "end_connection"


def test_no_label_is_contested_in_the_shipped_schema(registry):
    """Two attributes claiming one label makes the second unreachable and the first suspect."""
    for class_code in registry.class_codes:
        for source in (SPEC_LINE, ORDERING_TABLE):
            contested = contested_labels(registry, class_code, source=source)
            assert contested == {}, f"{class_code} {source}: {contested}"


# --------------------------------------------------------------------------- ordering tables


def test_the_target_row_is_selected_by_exact_part_number(registry):
    """A substring match would read 77C-105R's row for 77C-105 — a wrong value, perfectly cited."""
    table = _table(
        ["Catalog No", "Size", "Port"],
        [["77C-105", '1"', "Full"], ["77C-105R", '1"', "Reduced"]],
    )
    parsed = _synthetic([], tables=(table,))

    full = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105")
    reduced = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105R")

    assert next(m.value_raw for m in full.matches if m.attribute_code == "port_type") == "Full"
    assert (
        next(m.value_raw for m in reduced.matches if m.attribute_code == "port_type") == "Reduced"
    )


def test_without_a_target_sku_the_table_is_not_read(registry):
    """A table with six part numbers has six answers; picking one would be a guess."""
    table = _table(["Catalog No", "Port"], [["77C-105", "Full"], ["77C-106", "Reduced"]])
    parsed = _synthetic([], tables=(table,))
    result = extract_structured(parsed, registry, class_code=BALL)
    assert result.by_source(ORDERING_TABLE) == []
    assert any("no target SKU" in note for note in result.notes)


def test_a_missing_part_number_is_reported_not_approximated(registry):
    """A part absent from the document stops the whole read, not just the table.

    The relevance guard fires before the table path, so the note is about the document rather than
    about the table. That is the stronger outcome: if the part is not in the document at all, its
    specification block does not describe it either.
    """
    table = _table(["Catalog No", "Port"], [["77C-105", "Full"]])
    parsed = _synthetic(
        ["  Body Material .................. Bronze C84400"], tables=(table,)
    )
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="NOT-IN-TABLE")
    assert result.matches == []
    assert any("does not appear in this document" in note for note in result.notes)


def test_a_part_in_the_document_but_not_in_the_table_reads_only_spec_lines(registry):
    """Relevance established by a mention, but no ordering row to read per-variant cells from."""
    table = _table(["Catalog No", "Port"], [["77C-105", "Full"]])
    parsed = _synthetic(
        [
            "Ordering information for 77C-108 follows in the next revision",
            "  Body Material .................. Bronze C84400",
        ],
        tables=(table,),
    )
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-108")
    assert result.by_source(ORDERING_TABLE) == []
    assert any(m.attribute_code == "body_material" for m in result.matches)
    assert any("no ordering table contained" in note for note in result.notes)


def test_a_contested_column_is_resolved_by_declared_priority(registry):
    """`nominal_size` declares 'size' before 'dn', so the imperial column wins.

    Both columns describe the same attribute and produce different numbers — DN25 is 25 mm, 1" is
    25.4 mm — so emitting both would give one attribute two values.
    """
    table = _table(["Catalog No", "DN", "Size"], [["77C-105", "DN25", '1"']])
    parsed = _synthetic([], tables=(table,))
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105")

    sizes = [m for m in result.matches if m.attribute_code == "nominal_size"]
    assert len(sizes) == 1
    assert sizes[0].value_raw == '1"'
    assert sizes[0].label == "Size"
    assert any("both claim it" in note for note in result.notes)


def test_unmapped_headers_are_reported(registry):
    """Each one is a missing table_headers entry — a one-line schema fix, if anyone is told."""
    table = _table(["Catalog No", "Zorblatt Index"], [["77C-105", "17"]])
    parsed = _synthetic([], tables=(table,))
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105")
    assert result.unmapped_headers == ["Zorblatt Index"]


def test_a_table_cell_outranks_a_specification_line(registry):
    """A row keyed on the part number is more specific than a block describing the series."""
    table = _table(["Catalog No", "Port"], [["77C-105", "Reduced"]])
    parsed = _synthetic(["  Port Type ...................... Full Port"], tables=(table,))
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105")

    port = [m for m in result.matches if m.attribute_code == "port_type"]
    assert len(port) == 1
    assert port[0].source == ORDERING_TABLE
    assert port[0].value_raw == "Reduced"


# --------------------------------------------------------------------------- size scoping


def test_a_size_qualified_value_is_detected():
    assert size_qualifier('18-22 ft-lb (1/2" size)') == '1/2" size'
    assert size_qualifier("125 PSI WSP") is None
    assert size_qualifier("600 PSI WOG @ 73 degF") is None


def test_a_value_qualified_to_another_size_is_refused(registry):
    """The defect the golden set found.

    ba100.txt states `Operating Torque ..... 18-22 ft-lb (1/2" size)`. Emitting that for the 3/4"
    valve produces a genuinely-present, correctly-cited, wrong number — and the golden set records
    torque as absent for every SKU in the series.
    """
    table = _table(["Part Number", "Size"], [["BA-100-075", '3/4"']])
    parsed = _synthetic(
        ['  Operating Torque ............... 18-22 ft-lb (1/2" size)'], tables=(table,)
    )
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")

    assert "operating_torque" not in result.codes
    reason = dict(result.refused)["operating_torque"]
    assert "does not cover the target size" in reason


def test_a_value_qualified_to_the_target_size_is_kept(registry):
    table = _table(["Part Number", "Size"], [["BA-100-050", '1/2"']])
    parsed = _synthetic(
        ['  Operating Torque ............... 18-22 ft-lb (1/2" size)'], tables=(table,)
    )
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-050")
    assert "operating_torque" in result.codes


def test_a_size_qualified_value_with_no_known_size_is_refused(registry):
    """Applicability cannot be established, so the value is not asserted."""
    parsed = _synthetic(['  Operating Torque ............... 18-22 ft-lb (1/2" size)'])
    result = extract_structured(parsed, registry, class_code=BALL)
    assert "operating_torque" not in result.codes
    assert "target size is unknown" in dict(result.refused)["operating_torque"]


# --------------------------------------------------------------------------- refusals


def test_a_declined_value_is_not_recorded_as_a_value(registry):
    """"Consult factory" is an explicit absence. Recording it stops anyone finding the real
    number, which is worse than a gap."""
    parsed = _synthetic(["  Flow Coefficient (Cv) .......... Consult factory"])
    result = extract_structured(parsed, registry, class_code=BALL)
    assert "cv_flow_coefficient" not in result.codes
    assert "declines to give a value" in dict(result.refused)["cv_flow_coefficient"]


def test_an_implausible_magnitude_is_refused(registry):
    parsed = _synthetic(["  Pressure Rating ................ 900000 PSI WOG"])
    result = extract_structured(parsed, registry, class_code=BALL)
    assert "pressure_rating_wog" not in result.codes
    assert "outside the plausible range" in dict(result.refused)["pressure_rating_wog"]


def test_trailing_sentence_punctuation_is_stripped(registry):
    parsed = _synthetic(["Country of origin: Taiwan."])
    result = extract_structured(parsed, registry, class_code=BALL)
    assert next(
        m.value_raw for m in result.matches if m.attribute_code == "country_of_origin"
    ) == "Taiwan"


def test_a_dimension_keeps_its_inch_mark(registry):
    """Stripping punctuation must not damage a measurement."""
    table = _table(["Part Number", "Size"], [["BA-100-125", '1-1/4"']])
    parsed = _synthetic([], tables=(table,))
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-125")
    assert next(
        m.value_raw for m in result.matches if m.attribute_code == "nominal_size"
    ) == '1-1/4"'


def test_no_class_means_no_extraction(registry):
    parsed = _synthetic(["  Body Material .................. Bronze C84400"])
    result = extract_structured(parsed, registry, class_code=None)
    assert result.matches == []
    assert any("no product class" in note for note in result.notes)


def test_an_unknown_class_is_reported(registry):
    parsed = _synthetic(["  Body Material .................. Bronze C84400"])
    result = extract_structured(parsed, registry, class_code="NO.SUCH.CLASS")
    assert result.matches == []
    assert any("not in the schema" in note for note in result.notes)


def test_lines_a_table_covers_are_read_only_once(registry):
    """Otherwise one fact gets two citations, one of which cannot address a cell."""
    table = _table(["Part Number", "Size"], [["BA-100-075", '3/4"']])
    overlapping = ParsedLine(
        text="Part Number | Size",
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=50.0, y1=9.0),
        page=1,
        line_index=0,
    )
    page = ParsedPage(
        number=1, width=612.0, height=792.0, lines=(overlapping,), tables=(table,)
    )
    parsed = ParsedDocument(document=_document("overlap"), pages=(page,), parser="test")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    assert result.by_source(SPEC_LINE) == []


# --------------------------------------------------------------------------- evidence


def test_every_value_carries_a_resolvable_citation(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    values = to_attribute_values(result, SHA)

    assert values
    for value in values:
        assert len(value.evidence) == 1
        span = value.evidence[0]
        assert span.quote_verified
        assert span.document_sha256 == SHA
        assert span.page == 1


def test_quotes_are_present_in_the_document(registry):
    """The check that separates a computed citation from a claimed one."""
    for name, class_code, sku in (
        ("ba100.txt", BALL, "BA-100-075"),
        ("gv200.txt", GATE, "T-113-100"),
        ("ap77c.pdf", BALL, "77C-105R"),
    ):
        parsed = _parse_sample(name)
        result = extract_structured(parsed, registry, class_code=class_code, target_sku=sku)
        assert verify_quotes(result, parsed) == [], name


def test_table_values_cite_a_cell_and_lines_cite_a_line(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")

    for match in result.by_source(ORDERING_TABLE):
        assert match.locator.startswith("t1:r")
        assert ":c" in match.locator
    for match in result.by_source(SPEC_LINE):
        assert match.locator.startswith("p1:l")


def test_methods_distinguish_the_two_paths(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    values = {v.attribute_code: v for v in to_attribute_values(result, SHA)}

    assert values["nominal_size"].method is DerivationMethod.TABLE_EXTRACTION
    assert values["body_material"].method is DerivationMethod.DOCUMENT_EXTRACTION
    # Both are extraction-family, so the type system requires the evidence span.
    assert all(v.method.requires_evidence for v in values.values())


def test_values_are_publishable_after_normalisation(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    values = to_attribute_values(result, SHA)
    normalized, _ = normalize_all(values, registry, class_code=BALL)
    kept, _dropped = reject_unresolved(normalized, registry)
    assert kept
    assert all(v.is_publishable for v in kept)


def test_accept_false_leaves_the_policy_to_decide(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    values = to_attribute_values(result, SHA, accept=False)
    assert all(v.status is ValueStatus.CANDIDATE for v in values)
    assert not any(v.is_publishable for v in values)


def test_reject_unresolved_drops_a_null_canonical(registry):
    """A value that normalised to nothing keeps its accepted status and verified citation, so
    `is_publishable` says yes and a null reaches the feed with a perfect quote attached."""
    unresolved = AttributeValue(
        attribute_code="handle_type",
        value_raw="Malleable Iron",
        value_canonical=None,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
        status=ValueStatus.HUMAN_APPROVED,
    )
    kept, dropped = reject_unresolved([unresolved], registry)
    assert kept == []
    assert dropped and "did not resolve" in dropped[0][1]
    assert "permitted values are" in dropped[0][1]


# --------------------------------------------------------------------------- real documents


def test_the_committed_pdf_yields_cited_values(registry):
    """A real PDF, parsed and read with no model and no credentials."""
    parsed = _parse_sample("ap77c.pdf")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-105R")

    codes = set(result.codes)
    assert {"nominal_size", "port_type", "cv_flow_coefficient"} <= codes

    by_code = {m.attribute_code: m.value_raw for m in result.matches}
    # 77C-105R is the reduced-port twin of 77C-105. Reading the wrong row is the failure this
    # module is built to prevent, and the two rows differ only here.
    assert by_code["port_type"] == "Reduced"
    assert by_code["cv_flow_coefficient"] == "21.0"


def test_the_text_datasheet_yields_both_paths(registry):
    parsed = _parse_sample("ba100.txt")
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="BA-100-075")
    assert len(result.by_source(SPEC_LINE)) >= 6
    assert len(result.by_source(ORDERING_TABLE)) >= 3


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden set not present")
def test_agrees_with_hand_read_ground_truth(registry):
    """The test that would catch a real regression.

    Every value the extractor produces for the three committed documents is compared against
    `data/golden/pvf_valves.yaml`, and the golden set's `absent` list is checked in the other
    direction. Precision is asserted absolutely — one disagreement or one fabrication fails the
    build — while coverage is asserted as a floor, because reading more of a document is an
    improvement and reading less is a regression.
    """
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    parsed_cache: dict[str, ParsedDocument] = {}

    agreed = disagreed = 0
    fabricated: list[str] = []
    mismatches: list[str] = []

    for product in golden["products"]:
        entry = GOLDEN_DOCUMENTS.get(product.get("source") or "")
        if entry is None:
            continue
        name, class_code = entry
        if name not in parsed_cache:
            parsed_cache[name] = _parse_sample(name)

        result = extract_structured(
            parsed_cache[name], registry, class_code=class_code, target_sku=product["sku"]
        )
        normalized, _ = normalize_all(
            to_attribute_values(result, SHA), registry, class_code=class_code
        )
        kept, _dropped = reject_unresolved(normalized, registry)
        produced = {v.attribute_code: v for v in kept}

        for code, source_form in (product.get("attributes") or {}).items():
            got = produced.get(code)
            if got is None:
                continue  # coverage, not correctness; asserted as a floor below
            attribute = registry.attribute(code)
            reference, _ = normalize_all(
                [
                    AttributeValue(
                        attribute_code=code,
                        value_raw=str(source_form),
                        method=DerivationMethod.HUMAN_ENTRY,
                        confidence=1.0,
                        status=ValueStatus.HUMAN_APPROVED,
                    )
                ],
                registry,
                class_code=class_code,
            )
            if agree(
                reference[0].value_canonical,
                got.value_canonical,
                tolerance=attribute.tolerance or 0.0,
            ):
                agreed += 1
            else:
                disagreed += 1
                mismatches.append(
                    f"{product['sku']} {code}: golden {reference[0].value_canonical!r} != "
                    f"extracted {got.value_canonical!r}"
                )

        for code in product.get("absent") or []:
            if code in produced:
                fabricated.append(
                    f"{product['sku']} {code} = {produced[code].value_canonical!r} but the "
                    f"golden set records it as absent"
                )

    assert not mismatches, "extracted values disagree with ground truth:\n" + "\n".join(mismatches)
    assert disagreed == 0
    assert not fabricated, "values produced for attributes the document does not state:\n" + (
        "\n".join(fabricated)
    )
    # Coverage floor. Measured at 115 agreements across the three documents; a drop means the
    # extractor stopped reading something it used to read.
    assert agreed >= 115, f"coverage regressed: {agreed} agreements, expected at least 115"


# --------------------------------------------------------------------------- wrong document


def test_a_document_that_does_not_mention_the_part_yields_nothing(registry):
    """The most dangerous failure in this system, and the one it is easiest to ship.

    Handed three datasheets and asked about one valve, an unguarded reader attributes all three
    specification blocks to it and the later ones supersede the earlier. Every value then carries a
    quote that verifies, because the words really are in a document — just not in a document about
    this product. Quote verification cannot catch it; only a relevance check can.

    Caught in exactly this shape while wiring the export driver: BA-100-075 came out with
    `Bronze C89833` and `Solder`, cited to the NIBCO gate-valve sheet.
    """
    gate_sheet = _parse_sample("gv200.txt")
    result = extract_structured(
        gate_sheet, registry, class_code=BALL, target_sku="BA-100-075"
    )
    assert result.matches == []
    assert any("does not appear in this document" in note for note in result.notes)


def test_each_document_is_only_read_for_the_part_it_describes(registry):
    """The positive half: the right document still yields its values."""
    ball = extract_structured(
        _parse_sample("ba100.txt"), registry, class_code=BALL, target_sku="BA-100-075"
    )
    by_code = {m.attribute_code: m.value_raw for m in ball.matches}
    assert by_code["body_material"] == "Bronze C84400"
    assert by_code["end_connection"] == "NPT threaded, female both ends"
    assert by_code["steam_pressure_rating"] == "150 PSI WSP"


def test_a_withdrawn_part_is_refused(registry):
    """ap77c.pdf discontinues 77C-102 and supersedes it with 77C-103.

    An absent part number and a withdrawn one need different remedies — find the right document
    versus delist the product — so they are reported separately.
    """
    result = extract_structured(
        _parse_sample("ap77c.pdf"), registry, class_code=BALL, target_sku="77C-102"
    )
    assert result.matches == []
    assert any("only to be withdrawn" in note for note in result.notes)


def test_the_relevance_guard_can_be_waived_explicitly(registry):
    """`require_sku=False` exists for a caller that established relevance another way.

    Not the default, because the safe behaviour should be the one you get without thinking.
    """
    gate_sheet = _parse_sample("gv200.txt")
    guarded = extract_structured(gate_sheet, registry, class_code=BALL, target_sku="BA-100-075")
    waived = extract_structured(
        gate_sheet, registry, class_code=BALL, target_sku="BA-100-075", require_sku=False
    )
    assert guarded.matches == []
    assert waived.matches != []


def test_the_wrong_document_supplies_contradicting_values_not_merely_extra_ones(registry):
    """Why the relevance guard is necessary rather than merely tidy.

    The waiver test above shows the guard removes values. This one shows *what* it removes, and it
    is the reason the check cannot be optional: a datasheet for another product does not stay
    silent on the attributes it shares. It answers them, differently, with a verbatim quote.

    `T-113-100` is a NIBCO bronze gate valve. Read against the Milwaukee ball-valve sheet it comes
    back with that valve's body alloy, end connection and temperature ceiling — every one correctly
    quoted from a genuine manufacturer document, and every one wrong for this part. Nothing
    downstream can catch it: the span resolves, the quote verifies, the hash pins the content.
    """
    own = extract_structured(
        _parse_sample("gv200.txt"), registry, class_code=GATE, target_sku="T-113-100"
    )
    # require_sku=False reproduces the behaviour before the guard existed: read every document
    # offered, whether or not it mentions the part.
    foreign = extract_structured(
        _parse_sample("ba100.txt"),
        registry,
        class_code=GATE,
        target_sku="T-113-100",
        require_sku=False,
    )

    correct = {m.attribute_code: m.value_raw for m in own.matches}
    wrong = {m.attribute_code: m.value_raw for m in foreign.matches}

    contradicted = {
        code: (wrong[code], correct[code])
        for code in correct.keys() & wrong.keys()
        if wrong[code] != correct[code]
    }
    assert contradicted, (
        "the foreign sheet answered none of this part's attributes differently, so this test no "
        "longer demonstrates why the guard exists — pick a corpus where the sheets genuinely "
        "disagree"
    )
    # The specific alloys are worth pinning: they are both plausible bronzes for a valve body, so
    # no plausibility check would ever separate them.
    assert contradicted["body_material"] == ("Bronze C84400", "Bronze C89833")

    # And with the guard on, the foreign sheet contributes nothing at all.
    guarded = extract_structured(
        _parse_sample("ba100.txt"), registry, class_code=GATE, target_sku="T-113-100"
    )
    assert guarded.matches == []


def test_absence_of_a_target_sku_is_flagged_as_unverified(registry):
    """Without a part number there is no way to establish what the document is about."""
    result = extract_structured(_parse_sample("ba100.txt"), registry, class_code=BALL)
    assert any("relevance to a specific part is unverified" in note for note in result.notes)


def test_a_part_mentioned_without_an_ordering_row_is_flagged(registry):
    """Mentioned is not the same as sold. A cross-reference is not a description."""
    table = _table(["Catalog No", "Port"], [["77C-103", "Full"]])
    parsed = _synthetic(
        [
            "NOTE 3: valve 77C-999 is available on request",
            "  Body Material .................. Bronze C84400",
        ],
        tables=(table,),
    )
    result = extract_structured(parsed, registry, class_code=BALL, target_sku="77C-999")
    assert any("may reference it rather than describe it" in note for note in result.notes)
