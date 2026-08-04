"""Tests for variant table explosion.

The property this module exists to guarantee: **each variant's values come from its own row.**

Reading row 4 for row 3 produces a value that is genuinely present in the document, cites it
correctly, and is wrong for the part. Quote verification cannot detect that — the same failure
class as the wrong-document bug in ``docintel.sku``. The fix is structural rather than
statistical: the row-selection step does not involve a model at all, so the error is unreachable
instead of merely unlikely.

The second property is subtler and was a real bug caught here. A specification qualified to one
size — ``18-22 ft-lb (1/2" size)`` — must not be inherited across the range. The first
implementation compared the qualifier to each row's size with ``startswith``, and folding
``1/2" size`` gives ``12size``, which starts with the ``1`` of a 1" variant. The 1" valve was
silently treated as the size the note applied to and kept a torque figure that was never stated
for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core.evidence import BoundingBox, DocumentType, EvidenceSpan, SourceDocument
from axiom.core.gaps import Gap, GapReason, RecommendedAction
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod
from axiom.docintel import parse_text
from axiom.extract import detect_variant_table, explode, is_size_scoped
from axiom.extract.variants import MIN_VARIANT_ROWS, _note_covers_size
from axiom.schema import load_default

BALL_CLASS = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60

DATASHEET = """\
MILWAUKEE VALVE - BA-100 SERIES
Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08

SPECIFICATIONS
  Body Material .................. Bronze C84400
  Pressure Rating ................ 600 PSI WOG @ 73 degF
  Operating Torque ............... 18-22 ft-lb (1/2" size)

ORDERING INFORMATION
  Part Number      Size        Handle       Carton Qty
  BA-100-025       1/4"        Lever        24
  BA-100-050       1/2"        Lever        24
  BA-100-075       3/4"        Lever        12
  BA-100-100       1"          Lever        12
  BA-100-125       1-1/4"      Tee          6
"""

NO_TABLE = """\
ACME SINGLE VALVE DATASHEET
  Body Material .................. Bronze C84400
  Pressure Rating ................ 600 PSI WOG
"""

NO_SKU_COLUMN = """\
SPECIFICATION SUMMARY
  Size        Handle       Carton Qty
  1/4"        Lever        24
  1/2"        Lever        24
  3/4"        Lever        12
"""


@pytest.fixture(scope="module")
def registry():
    return load_default()


def document(name: str = "ba100") -> SourceDocument:
    return SourceDocument(
        document_id=name,
        uri=f"local://{name}",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


@pytest.fixture
def parsed():
    return parse_text(DATASHEET, document())


@pytest.fixture
def table(parsed, registry):
    detected = detect_variant_table(parsed, registry, BALL_CLASS)
    assert detected is not None, "the fixture contains an ordering table"
    return detected


def spec_value(code: str, raw: str, quote: str) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=raw,
        value_canonical=raw,
        value_display=raw,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.93,
        model_tier="volume",
        evidence=[
            EvidenceSpan(
                span_id=f"sp-{code}",
                document_id="ba100",
                document_sha256=SHA,
                quote=quote,
                page=1,
                bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
                quote_verified=True,
                match_score=1.0,
            )
        ],
    )


def reference(sku: str = "BA-100-050") -> ProductRecord:
    record = ProductRecord(
        tenant_id="demo",
        sku=sku,
        mpn=sku,
        brand="Milwaukee Valve",
        supplier_id="milwaukee",
        class_code=BALL_CLASS,
        schema_version=f"{BALL_CLASS}@v1",
        source_document_ids=["ba100"],
    )
    record.add_value(
        spec_value("body_material", "Bronze C84400", "Body Material .... Bronze C84400")
    )
    record.add_value(
        spec_value("pressure_rating_wog", "600 PSI WOG", "Pressure Rating .... 600 PSI WOG")
    )
    record.add_value(
        spec_value(
            "operating_torque",
            '18-22 ft-lb (1/2" size)',
            'Operating Torque ... 18-22 ft-lb (1/2" size)',
        )
    )
    record.add_gap(
        Gap(attribute_code="cv_flow_coefficient", reason=GapReason.REFERRED_ELSEWHERE)
    )
    return record


# ===================================================================== detection


def test_the_ordering_table_is_found(table):
    assert table.table_id
    assert len(table.rows) == 5
    assert table.variant_skus == (
        "BA-100-025",
        "BA-100-050",
        "BA-100-075",
        "BA-100-100",
        "BA-100-125",
    )


def test_columns_bind_to_attributes_from_the_schema(table):
    """Declarative: 'Size' binds to nominal_size because the YAML says so, not by guessing."""
    assert set(table.mapped_codes) == {"nominal_size", "handle_type", "case_quantity"}


def test_the_part_number_column_is_excluded_from_attribute_mapping(table):
    sku_column = next(c for c in table.columns if c.col == table.sku_column)
    assert sku_column.attribute_code is None


def test_every_row_records_the_cell_each_value_came_from(table):
    for row in table.rows:
        for code in row.values:
            assert row.cell_refs[code].startswith(f"{table.table_id}:r{row.row}:")


def test_unmapped_headers_are_surfaced_not_dropped(parsed, registry):
    """An unmapped column is a missing `table_headers` entry — a one-line schema fix.

    Silently ignoring it loses real data, and the omission is invisible afterwards.
    """
    text = DATASHEET.replace("Carton Qty", "Widget Dia ")
    detected = detect_variant_table(parse_text(text, document()), registry, BALL_CLASS)
    assert detected is not None
    assert any("Widget" in header for header in detected.unmapped_headers)


def test_a_document_with_no_table_yields_nothing(registry):
    """Guessing variant structure out of a spec sheet would fabricate products."""
    assert detect_variant_table(parse_text(NO_TABLE, document()), registry, BALL_CLASS) is None


def test_a_table_without_a_part_number_column_is_not_a_variant_table(registry):
    parsed_doc = parse_text(NO_SKU_COLUMN, document())
    assert detect_variant_table(parsed_doc, registry, BALL_CLASS) is None


def test_a_single_row_table_is_not_exploded(registry):
    """One row is a spec sheet with a table in it, not a series."""
    text = DATASHEET.rsplit("BA-100-050", 1)[0]
    detected = detect_variant_table(parse_text(text, document()), registry, BALL_CLASS)
    if detected is not None:
        assert len(detected.rows) >= MIN_VARIANT_ROWS


def test_footnote_rows_are_not_mistaken_for_part_numbers(parsed, registry):
    text = DATASHEET + '\n  NOTE 1: Sizes 1" and larger ship with a locking lever.\n'
    detected = detect_variant_table(parse_text(text, document()), registry, BALL_CLASS)
    assert detected is not None
    assert all(sku.startswith("BA-100-") for sku in detected.variant_skus)


def test_summary_is_reportable(table):
    summary = table.summary()
    for key in ("table_id", "variants", "skus", "per_variant_attributes", "unmapped_headers"):
        assert key in summary


# ===================================================================== size scoping


def test_a_size_qualified_value_is_detected():
    value = spec_value("operating_torque", '18-22 ft-lb (1/2" size)', "q")
    assert is_size_scoped(value) == '1/2" size'


def test_an_unqualified_value_is_not_size_scoped():
    assert is_size_scoped(spec_value("body_material", "Bronze C84400", "q")) is None


def test_a_reference_condition_is_not_a_size_qualifier():
    """'600 PSI WOG @ 73 degF' is scoped by temperature, not size, and applies to the series."""
    assert is_size_scoped(spec_value("pressure_rating_wog", "600 PSI WOG @ 73 degF", "q")) is None


@pytest.mark.parametrize(
    ("note", "size", "covers"),
    [
        ('1/2" size', '1/2"', True),
        # The bug: folding '1/2" size' gives '12size', which starts with the '1' of 1".
        ('1/2" size', '1"', False),
        ('1/2" size', '1-1/4"', False),
        ('1/2" size', '1/4"', False),
        ('1/2" size', '3/4"', False),
        ('sizes 2" and larger', '2"', True),
        ("DN50 only", "DN50", True),
        ('1/2" size', None, False),
        ('1/2" size', "", False),
    ],
)
def test_size_notes_are_matched_as_whole_tokens(note, size, covers):
    assert _note_covers_size(note, size) is covers


# ===================================================================== explosion


@pytest.fixture
def children(parsed, table, registry):
    return explode(reference(), table, parsed, registry)


def test_one_record_per_orderable_part_number(children):
    assert [child.sku for child in children] == [
        "BA-100-025",
        "BA-100-050",
        "BA-100-075",
        "BA-100-100",
        "BA-100-125",
    ]


def test_children_link_to_their_parent(children):
    for child in children:
        if child.sku == "BA-100-050":
            assert child.parent_sku is None, "the reference is not its own child"
        else:
            assert child.parent_sku == "BA-100-050"


def test_each_variant_takes_its_size_from_its_own_row(children):
    """The property the whole module exists for."""
    sizes = {
        child.sku: next(
            v.value_raw for v in child.current_values() if v.attribute_code == "nominal_size"
        )
        for child in children
    }
    assert sizes == {
        "BA-100-025": '1/4"',
        "BA-100-050": '1/2"',
        "BA-100-075": '3/4"',
        "BA-100-100": '1"',
        "BA-100-125": '1-1/4"',
    }


def test_the_footnote_variant_gets_its_own_handle(children):
    """BA-100-125 ships with a Tee handle while the rest are Lever. Row-bound, so it differs."""
    handles = {
        child.sku: next(
            v.value_raw for v in child.current_values() if v.attribute_code == "handle_type"
        )
        for child in children
    }
    assert handles["BA-100-125"] == "Tee"
    assert handles["BA-100-075"] == "Lever"


def test_per_variant_values_cite_an_exact_cell(children):
    for child in children:
        for code in ("nominal_size", "handle_type", "case_quantity"):
            value = next(v for v in child.current_values() if v.attribute_code == code)
            span = value.evidence[0]
            assert span.table_ref is not None
            assert span.table_ref.count(":") == 2, "a cell reference, not just a row"
            assert span.quote_verified is True
            assert value.method is DerivationMethod.TABLE_EXTRACTION


def test_a_cell_citation_quotes_the_cell_text_itself(children):
    """No search is needed: the quote *is* the parsed cell, so there is nothing to verify."""
    child = next(c for c in children if c.sku == "BA-100-125")
    size = next(v for v in child.current_values() if v.attribute_code == "nominal_size")
    assert size.evidence[0].quote == '1-1/4"'


def test_series_level_values_are_inherited_with_their_original_citation(children):
    for child in children:
        material = next(
            v for v in child.current_values() if v.attribute_code == "body_material"
        )
        assert material.value_raw == "Bronze C84400"
        assert material.evidence[0].quote.startswith("Body Material")
        assert material.derived_from is not None, "inheritance must be traceable"


def test_a_size_scoped_value_is_not_inherited(children):
    """Inheriting the half-inch torque would attach a precisely-cited wrong figure to four
    products."""
    for child in children:
        codes = {v.attribute_code for v in child.current_values()}
        if child.sku == "BA-100-050":
            assert "operating_torque" in codes, "the size it was stated for keeps it"
        else:
            assert "operating_torque" not in codes, f"{child.sku} inherited a 1/2\" spec"


def test_the_variants_it_does_not_apply_to_get_an_inapplicable_gap(children):
    """Not missing data. Saying so stops a reviewer chasing a supplier for it."""
    for child in children:
        gap = next(
            (g for g in child.gaps if g.attribute_code == "operating_torque"), None
        )
        if child.sku == "BA-100-050":
            assert gap is None
        else:
            assert gap is not None, f"{child.sku} should record torque as inapplicable"
            assert gap.recommended_action is RecommendedAction.ACCEPT_AS_NOT_APPLICABLE
            assert '1/2"' in gap.detail


def test_reference_gaps_carry_over_to_every_variant(children):
    """A value the datasheet defers is deferred for the whole series."""
    for child in children:
        assert any(g.attribute_code == "cv_flow_coefficient" for g in child.gaps)


def test_a_table_supplied_value_does_not_also_appear_as_a_gap(parsed, table, registry):
    """If the spec block was silent but the table answered, that is not a gap."""
    record = reference()
    record.add_gap(
        Gap(attribute_code="handle_type", reason=GapReason.NOT_PRESENT_IN_ANY_SOURCE)
    )

    for child in explode(record, table, parsed, registry):
        codes_with_values = {v.attribute_code for v in child.current_values()}
        assert "handle_type" in codes_with_values
        assert not any(g.attribute_code == "handle_type" for g in child.gaps)


def test_classification_and_provenance_carry_to_children(children):
    for child in children:
        assert child.class_code == BALL_CLASS
        assert child.brand == "Milwaukee Valve"
        assert child.supplier_id == "milwaukee"
        assert child.source_document_ids == ["ba100"]


def test_exploding_does_not_mutate_the_reference(parsed, table, registry):
    record = reference()
    before = {v.attribute_code: v.value_raw for v in record.current_values()}
    gaps_before = len(record.gaps)

    explode(record, table, parsed, registry)

    assert {v.attribute_code: v.value_raw for v in record.current_values()} == before
    assert len(record.gaps) == gaps_before
