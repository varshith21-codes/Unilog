"""The workbook export, checked against the client's own file.

``test_delivery_format.py`` asserts that the *contract* matches the client's header. This file
asserts that the **artifact we actually hand over** does, which is not the same claim and was the
gap: the contract could be perfect while the workbook writer prepended a title row, dropped a
column with no value in the batch, or reordered the attribute grid, and nothing would have caught
it. A reader of the contract test would reasonably assume it covered the file. It did not.

So the assertion here is made against the emitted bytes — parsed back out of the workbook with
openpyxl, exactly as the client's importer will read them — and compared to
``Unihack_ Expected Output - Delivery Format.csv`` rather than to anything we wrote.

The second concern in this file is the one XLSX exists to solve. A CSV of the same cells is
reinterpreted on import: ``0123`` loses its leading zero, ``50-1/4`` becomes a date, ``3/4``
becomes March 4th. Those are silent, they happen in the client's spreadsheet rather than in our
exporter, and a part number that survives the whole pipeline and dies there is still a failed
delivery. The type of every cell is therefore part of the contract too, and is pinned below.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from axiom.delivery import (
    DeliveryFormat,
    DeliveryFormatExporter,
    DeliveryRow,
    DeliveryWorkbookExporter,
    load_default,
)
from axiom.delivery.batch import BatchOptions, run_batch
from axiom.schema import load_default as load_schema

openpyxl = pytest.importorskip("openpyxl")

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
SAMPLE_INPUT = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"

DELIVERY_SHEET = "Delivery"

requires_data = pytest.mark.skipif(
    not (GROUND_TRUTH.exists() and SAMPLE_INPUT.exists()),
    reason="the client's CSVs are not present in this checkout",
)


def _client_header() -> list[str]:
    with open(GROUND_TRUTH, newline="", encoding="utf-8-sig") as handle:
        return next(csv.reader(handle))


def _input_rows() -> list[dict[str, str]]:
    with open(SAMPLE_INPUT, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def fmt() -> DeliveryFormat:
    return load_default()


@pytest.fixture(scope="module")
def rows(fmt: DeliveryFormat) -> list[DeliveryRow]:
    """Delivery rows for the two SKUs the client gave known-good output for.

    Built through ``run_batch`` — the same function the CLI and the upload endpoint call — so this
    exercises the real path rather than a hand-assembled row that could satisfy the test while the
    shipped path did something else.
    """
    if not (GROUND_TRUTH.exists() and SAMPLE_INPUT.exists()):
        pytest.skip("the client's CSVs are not present in this checkout")

    wanted = {"PDSH4816AF", "WDTS7024RZ"}
    selected = [r for r in _input_rows() if (r.get("Mfg_Part_Num") or "").strip() in wanted]
    result = run_batch(
        selected,
        fmt=fmt,
        registry=load_schema(),
        document_id="test-item-master",
        document_sha256="0" * 64,
        options=BatchOptions(),
    )
    assert result.rows, "the batch produced no rows to export"
    return result.rows


def _sheet(rows: list[DeliveryRow], fmt: DeliveryFormat, **kwargs):
    """Render the workbook and read the Delivery sheet back out of the bytes."""
    workbook = DeliveryWorkbookExporter(fmt).to_workbook(rows, **kwargs)
    reopened = openpyxl.load_workbook(io.BytesIO(workbook.data), data_only=True)
    return reopened[DELIVERY_SHEET], workbook


def _grid(sheet) -> list[list[str]]:
    return [["" if c is None else c for c in row] for row in sheet.iter_rows(values_only=True)]


# --------------------------------------------------------------------------- the columns


@requires_data
def test_workbook_header_is_byte_identical_to_the_clients(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """Row 1 of the Delivery sheet must equal the client's header, exactly and in order.

    The single binary requirement in the deliverable. Reported as a positional diff because a
    252-column mismatch shown as "lists differ" is unactionable, while the first differing index
    names the section that drifted.
    """
    sheet, _ = _sheet(rows, fmt)
    expected = _client_header()
    actual = _grid(sheet)[0]

    assert len(actual) == len(expected), (
        f"the workbook has {len(actual)} columns, the client's file has {len(expected)}"
    )

    mismatches = [
        (i, e, a) for i, (e, a) in enumerate(zip(expected, actual, strict=True)) if e != a
    ]
    assert not mismatches, "column mismatch at " + "; ".join(
        f"index {i}: client {e!r} != workbook {a!r}" for i, e, a in mismatches[:5]
    )


@requires_data
def test_header_is_the_first_row_with_nothing_above_it(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """No title row, no spacer, no annotation band.

    The client ingests this sheet directly, so anything helpful inserted above the header shifts
    every record by one and breaks the import. Worth its own test because it is the most tempting
    thing to add to a spreadsheet a human will also open.
    """
    sheet, _ = _sheet(rows, fmt)
    grid = _grid(sheet)
    assert grid[0] == list(fmt.header)
    # Row 2 is data, not a second header or a units row.
    assert grid[1] != list(fmt.header)
    assert grid[1][fmt.header.index("Mfg_Part_Num")] in {"PDSH4816AF", "WDTS7024RZ"}


@requires_data
def test_every_data_row_is_exactly_as_wide_as_the_contract(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """A short row shifts every value after the gap into the wrong column.

    The failure mode this guards is a row that *looks* plausible: 240 populated cells and every
    one of them under the wrong header.
    """
    sheet, _ = _sheet(rows, fmt)
    grid = _grid(sheet)
    widths = {len(row) for row in grid}
    assert widths == {len(_client_header())}, f"ragged sheet: row widths {sorted(widths)}"


@requires_data
def test_workbook_cells_equal_the_csv_projection(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """The two output formats must be the same file in different containers.

    If they can disagree, then "the CSV scores 79.9% against ground truth" says nothing about the
    workbook, and the workbook is what a human opens.
    """
    sheet, _ = _sheet(rows, fmt)
    from_csv = list(csv.reader(io.StringIO(DeliveryFormatExporter(fmt).to_csv(rows).csv_text)))
    assert _grid(sheet) == from_csv


@requires_data
def test_columns_the_client_left_blank_stay_blank(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """Matching the header is necessary and not sufficient: the columns also have to be *used* the
    way the client uses them.

    173 of the client's 252 columns are empty in their own known-good rows. Filling one means we
    invented data, which is the failure the whole evidence contract exists to prevent — and it
    would be invisible to a header check.
    """
    client_rows = _client_rows_by_mpn()
    sheet, _ = _sheet(rows, fmt)
    grid = _grid(sheet)
    header = grid[0]
    mpn_at = header.index("Mfg_Part_Num")

    over_filled: list[str] = []
    for row in grid[1:]:
        expected = client_rows.get(row[mpn_at])
        if expected is None:
            continue
        for column, ours in zip(header, row, strict=True):
            if ours and not expected.get(column, ""):
                over_filled.append(f"{row[mpn_at]} {column}={ours!r}")

    assert not over_filled, "populated a cell the client left blank: " + "; ".join(
        over_filled[:5]
    )


def _client_rows_by_mpn() -> dict[str, dict[str, str]]:
    with open(GROUND_TRUTH, newline="", encoding="utf-8-sig") as handle:
        return {row["Mfg_Part_Num"]: row for row in csv.DictReader(handle)}


# --------------------------------------------------------------------------- cell types


@requires_data
def test_every_cell_is_written_as_text(rows: list[DeliveryRow], fmt: DeliveryFormat) -> None:
    """The reason this export is XLSX and not CSV.

    Read without ``data_only`` so the stored type is visible. ``s`` is a shared/inline string;
    anything else means Excel is free to reinterpret the value, and ``f`` would mean it evaluates
    it as a formula.
    """
    workbook = DeliveryWorkbookExporter(fmt).to_workbook(rows)
    reopened = openpyxl.load_workbook(io.BytesIO(workbook.data))
    types = {
        cell.data_type
        for row in reopened[DELIVERY_SHEET].iter_rows()
        for cell in row
        if cell.value not in (None, "")
    }
    assert types == {"s"}, f"non-text cell types in the delivery sheet: {sorted(types)}"


def test_values_excel_would_mangle_survive_a_round_trip(fmt: DeliveryFormat) -> None:
    """The specific coercions a CSV loses, pinned as cases.

    Every one of these is a real shape in this dataset: fractional inch dimensions, part numbers
    with leading zeros, bare digits, and values a spreadsheet would read as a date. Asserted
    through the workbook rather than in principle.
    """
    hazards = {
        "Mfg_Part_Num": "0123",
        "PART_NUMBER": "7024",
        "SHORT_DESC": "50-1/4 in",
        "LONG_DESC1": "3/4 turn, 1.5E3 cycles",
        "INVOICE_DESC": "-40 to 250 F",
        "MOBILE_DESC": "=SUM(A1:A2)",
        "RETAIL_DESC": "TRUE",
    }
    row = DeliveryRow(format=fmt, sku="HAZARD", mpn="0123")
    for column, value in hazards.items():
        row.cells[column] = _cell(fmt, column, value)

    workbook = DeliveryWorkbookExporter(fmt).to_workbook([row])
    reopened = openpyxl.load_workbook(io.BytesIO(workbook.data), data_only=True)
    grid = _grid(reopened[DELIVERY_SHEET])
    header, data = grid[0], grid[1]

    for column, expected in hazards.items():
        assert data[header.index(column)] == expected, (
            f"{column}: {expected!r} came back as {data[header.index(column)]!r}"
        )

    # The formula-shaped value is counted, not silently corrected, so a caller can see it happened.
    assert workbook.formula_guarded_cells == 1


def _cell(fmt: DeliveryFormat, column: str, value: str):
    from axiom.delivery.builder import Cell

    return Cell(
        column=column,
        value=value,
        provenance=fmt.column(column).provenance,
        source="test",
    )


# --------------------------------------------------------------------------- audit sheets


@requires_data
def test_audit_sheets_accompany_the_delivery_sheet(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    """The provenance record ships with the file by default.

    Not decoration: without it, "we populated 28 of 252 columns from a 35-character description" is
    indistinguishable from fabrication. Defaulting it off would make the honest artifact the one you
    have to remember to ask for.
    """
    workbook = DeliveryWorkbookExporter(fmt).to_workbook(rows, source_name="test.csv")
    assert workbook.sheets == (DELIVERY_SHEET, "Summary", "Provenance", "Withheld")

    reopened = openpyxl.load_workbook(io.BytesIO(workbook.data), data_only=True)
    provenance = _grid(reopened["Provenance"])
    assert provenance[0] == [
        "SKU",
        "Mfg_Part_Num",
        "Column",
        "Provenance",
        "Source",
        "Confidence",
        "Evidence",
        "Reviewed",
    ]
    # One row per populated cell, so the sheet accounts for the whole delivery sheet.
    populated = sum(row.populated_count for row in rows)
    assert len(provenance) - 1 == populated

    # Every column named on the Provenance sheet must be a real contract column.
    for entry in provenance[1:]:
        assert entry[2] in fmt, f"provenance names an unknown column: {entry[2]!r}"


@requires_data
def test_audit_can_be_omitted_without_touching_the_delivery_sheet(
    rows: list[DeliveryRow], fmt: DeliveryFormat
) -> None:
    with_audit, _ = _sheet(rows, fmt, include_audit=True)
    without_audit, workbook = _sheet(rows, fmt, include_audit=False)

    assert workbook.sheets == (DELIVERY_SHEET,)
    assert _grid(without_audit) == _grid(with_audit)
