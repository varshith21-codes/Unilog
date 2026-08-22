"""Writing the delivery file as a workbook.

The client's contract is 252 columns of text and :mod:`axiom.delivery.exporter` already serialises
it as CSV. This module exists because a human on the other end opens the file in Excel, and CSV
loses on arrival: Excel reinterprets it. ``0123`` becomes ``123``, ``50-1/4`` becomes a date, and
``3/4`` becomes March 4th — silently, in the importer, after our exporter did everything right. A
part number that survives our pipeline and dies in the client's spreadsheet is still a delivery
failure.

XLSX fixes that at the type level. Every cell here is written with the explicit string type
(``t="s"``), so there is nothing for Excel to guess at, and the column style is text as well so a
later hand-edit does not reintroduce the coercion.

**Four sheets, and the split is deliberate.**

``Delivery``
    Row 1 is the 252-column header, byte-identical to the contract. Rows 2..n are the data. Nothing
    else — no annotation row, no merged title, no blank spacer. The client ingests this sheet, so a
    "helpful" second header row would shift every record by one and break the import.

``Summary``, ``Provenance``, ``Withheld``
    Everything that makes the delivery sheet auditable, kept off it for exactly the reason above.
    This is the JSON sidecar rendered where a reviewer will actually look at it: the sidecar is the
    artifact that separates "we populated 79 columns" from "we can tell you where all 79 came
    from", and a workbook that shipped without it would drop the only claim the numbers rest on.

Numbers are not recomputed here. The batch is run through
:class:`~axiom.delivery.exporter.DeliveryFormatExporter` and the summary reports *its* totals, so
the workbook and the CSV can never disagree about how many cells were populated or withheld.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from axiom.delivery.builder import DeliveryRow
from axiom.delivery.exporter import DeliveryExport, DeliveryFormatExporter
from axiom.delivery.format import DeliveryFormat

WORKBOOK_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
"""The OOXML media type, as the artifact endpoint already declares it for ``.xlsx``."""

WORKBOOK_SUFFIX = ".xlsx"

DELIVERY_SHEET = "Delivery"
SUMMARY_SHEET = "Summary"
PROVENANCE_SHEET = "Provenance"
WITHHELD_SHEET = "Withheld"

TEXT_FORMAT = "@"
"""Excel's text number-format. Applied per column so a hand-edit stays text."""

# Characters XLSX forbids outright. openpyxl raises IllegalCharacterError rather than escaping
# them, which would turn one stray control byte in one supplier description into a failed export
# for the whole batch. Tab, newline and carriage return are legal and are left alone.
_ILLEGAL = re.compile(r"[\000-\010\013\014\016-\037]")

_EVIDENCE_SEPARATOR = " | "

# Column widths for the identity and description columns, which are the ones a human scrolls to.
# Keyed by column name rather than by index so a contract change cannot silently misapply them.
_WIDTHS: dict[str, int] = {
    "MFR URL": 44,
    "PART_NUMBER": 20,
    "SKU - MY_PART_NUMBER": 22,
    "Mfg_Part_Num": 20,
    "Part_Desc": 40,
    "E1_Brand": 18,
    "Unilog_Brand": 20,
    "DIB_Brand": 18,
    "Part_Manuf": 30,
    "MANUFACTURER_NAME": 26,
    "BRAND_NAME": 20,
    "MANUFACTURER_PART_NUMBER": 24,
    "Classpath": 48,
    "MOBILE_DESC": 30,
    "INVOICE_DESC": 40,
    "SHORT_DESC": 40,
    "LONG_DESC1": 56,
    "RETAIL_DESC": 40,
    "MARKETING_DESCRIPTION": 56,
    "Product Name": 30,
}
_DEFAULT_WIDTH = 16


@dataclass
class DeliveryWorkbook:
    """A rendered workbook, plus the export whose numbers it reports."""

    data: bytes
    export: DeliveryExport
    sheets: tuple[str, ...]
    sanitised_cells: int = 0
    """Cells that carried a character XLSX forbids and were written with it stripped. Non-zero
    means the delivery sheet differs from the CSV in those cells, so it is reported rather than
    absorbed."""

    formula_guarded_cells: int = 0
    """Cells whose text begins with ``=``. Written as literal text rather than as a formula."""

    @property
    def byte_size(self) -> int:
        return len(self.data)

    def summary(self) -> dict[str, object]:
        return {
            **self.export.summary(),
            "sheets": list(self.sheets),
            "bytes": self.byte_size,
            "sanitised_cells": self.sanitised_cells,
            "formula_guarded_cells": self.formula_guarded_cells,
        }


@dataclass
class _Counters:
    """Corrections applied while writing the delivery sheet, so they can be reported."""

    sanitised: int = 0
    guarded: int = 0


class DeliveryWorkbookExporter:
    """Serialises delivery rows to an XLSX workbook.

    Holds the same contract object the CSV exporter does, and delegates to it for every number it
    reports. Construct one and reuse it; there is no per-call state.
    """

    name = "unilog_delivery_xlsx"

    def __init__(self, fmt: DeliveryFormat) -> None:
        self._format = fmt
        self._csv = DeliveryFormatExporter(fmt)

    def to_workbook(
        self,
        rows: Iterable[DeliveryRow],
        *,
        source_name: str | None = None,
        generated_at: datetime | None = None,
        include_audit: bool = True,
    ) -> DeliveryWorkbook:
        """Render a batch.

        ``rows`` is materialised because the audit sheets need a second pass over it, and a
        generator that could only be walked once would silently produce an empty Provenance sheet.

        ``include_audit=False`` emits the delivery sheet alone. Offered for a caller that has
        already taken the sidecar separately, and deliberately not the default: the provenance
        record is what makes the file checkable, and defaulting it off would make the honest
        artifact the one you have to remember to ask for.
        """
        from openpyxl import Workbook

        materialised: list[DeliveryRow] = list(rows)

        # Run the CSV exporter first, for two reasons: it applies the format guard and the row
        # width check before anything is written, and every count in the Summary sheet then comes
        # from the same place the CSV's counts do.
        export = self._csv.to_csv(materialised)

        counters = _Counters()
        workbook = Workbook(write_only=True)
        sheets: list[str] = []

        self._write_delivery(workbook, materialised, counters)
        sheets.append(DELIVERY_SHEET)

        if include_audit:
            self._write_summary(
                workbook,
                export,
                counters,
                source_name=source_name,
                generated_at=generated_at or datetime.now(UTC),
                row_count=len(materialised),
            )
            sheets.append(SUMMARY_SHEET)
            self._write_provenance(workbook, materialised)
            sheets.append(PROVENANCE_SHEET)
            self._write_withheld(workbook, materialised)
            sheets.append(WITHHELD_SHEET)

        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()

        return DeliveryWorkbook(
            data=buffer.getvalue(),
            export=export,
            sheets=tuple(sheets),
            sanitised_cells=counters.sanitised,
            formula_guarded_cells=counters.guarded,
        )

    # ------------------------------------------------------------------ delivery sheet

    def _write_delivery(
        self, workbook, rows: Sequence[DeliveryRow], counters: _Counters
    ) -> None:
        from openpyxl.utils import get_column_letter

        sheet = workbook.create_sheet(DELIVERY_SHEET)

        # Set before the first append. In write-only mode the ``<cols>`` block is flushed with the
        # first row, so dimensions assigned afterwards are silently dropped.
        for index, name in enumerate(self._format.header, start=1):
            dimension = sheet.column_dimensions[get_column_letter(index)]
            dimension.width = _WIDTHS.get(name, _DEFAULT_WIDTH)
            # Text, so a value a reviewer retypes into the cell is not coerced either.
            dimension.number_format = TEXT_FORMAT

        # Row 1 stays scrolled into view. The grid is 252 columns wide and a header you cannot see
        # makes ATTRIBUTE_VALUE 37 unidentifiable.
        sheet.freeze_panes = "A2"

        sheet.append(self._heading(sheet, self._format.header))
        for row in rows:
            sheet.append([self._text(sheet, value, counters) for value in row.as_list()])

    # ------------------------------------------------------------------ audit sheets

    def _write_summary(
        self,
        workbook,
        export: DeliveryExport,
        counters: _Counters,
        *,
        source_name: str | None,
        generated_at: datetime,
        row_count: int,
    ) -> None:
        sheet = workbook.create_sheet(SUMMARY_SHEET)
        sheet.column_dimensions["A"].width = 34
        sheet.column_dimensions["B"].width = 74
        sheet.append(self._heading(sheet, ["Field", "Value"]))

        def pair(label: str, value: object) -> None:
            sheet.append([self._cell(sheet, label), self._cell(sheet, value)])

        pair("format", export.format_name)
        pair("columns", len(self._format))
        pair("rows", row_count)
        pair("source file", source_name or "(not recorded)")
        pair("generated at (UTC)", generated_at.isoformat())
        pair("content hash (SHA-256 of the CSV projection)", export.content_hash)
        pair("columns populated at least once", len(export.populated_by_column))
        pair("withheld cells", export.withheld_count)
        pair("rows carrying notes", export.rows_with_notes)
        pair("character/casing limits", "PASS" if export.compliant else "FAIL")
        pair("constraint violations", len(export.violations))
        if counters.sanitised:
            pair("cells sanitised (illegal XLSX characters removed)", counters.sanitised)
        if counters.guarded:
            pair("cells beginning with '=' written as literal text", counters.guarded)

        sheet.append([])
        sheet.append(self._heading(sheet, ["Cells by provenance", "Count"]))
        for provenance, count in sorted(export.provenance_totals.items(), key=lambda kv: -kv[1]):
            pair(provenance, count)

        sheet.append([])
        sheet.append(self._heading(sheet, ["Note", ""]))
        # Said in the file rather than only in a README, because the file is what gets forwarded.
        # A reader who sees 28 of 252 columns filled and no explanation will assume it is broken.
        for line in (
            "Blank cells are deliberate. A column is left empty when nothing in the supplied "
            "input or the attached documents evidenced a value for it.",
            "Every populated cell appears on the Provenance sheet with the class of claim it "
            "makes and, where it carries one, the citation it was read from.",
            "The Withheld sheet lists cells where a value existed but was not allowed to "
            "publish. Those are refusals, not gaps.",
            "Column order and spelling on the Delivery sheet are the client's contract and are "
            "not adjusted.",
        ):
            sheet.append([self._cell(sheet, line)])

    def _write_provenance(self, workbook, rows: Sequence[DeliveryRow]) -> None:
        sheet = workbook.create_sheet(PROVENANCE_SHEET)
        for letter, width in (
            ("A", 22), ("B", 22), ("C", 26), ("D", 14), ("E", 26), ("F", 12), ("G", 60), ("H", 10)
        ):
            sheet.column_dimensions[letter].width = width
        sheet.freeze_panes = "A2"
        sheet.append(
            self._heading(
                sheet,
                [
                    "SKU",
                    "Mfg_Part_Num",
                    "Column",
                    "Provenance",
                    "Source",
                    "Confidence",
                    "Evidence",
                    "Reviewed",
                ],
            )
        )

        for row in rows:
            # Iterated in contract order rather than dict order so the sheet reads down the
            # delivery file, which is how a reviewer checking a specific cell will scan it.
            for name in self._format.header:
                cell = row.cells.get(name)
                if cell is None or not cell.value:
                    continue
                sheet.append(
                    [
                        self._cell(sheet, row.sku or ""),
                        self._cell(sheet, row.mpn or ""),
                        self._cell(sheet, cell.column),
                        self._cell(sheet, cell.provenance.value),
                        self._cell(sheet, cell.source or ""),
                        self._cell(
                            sheet, "" if cell.confidence is None else f"{cell.confidence:.4f}"
                        ),
                        self._cell(sheet, _EVIDENCE_SEPARATOR.join(cell.evidence)),
                        self._cell(sheet, "yes" if cell.reviewed else ""),
                    ]
                )

    def _write_withheld(self, workbook, rows: Sequence[DeliveryRow]) -> None:
        sheet = workbook.create_sheet(WITHHELD_SHEET)
        for letter, width in (("A", 22), ("B", 22), ("C", 26), ("D", 26), ("E", 72), ("F", 12)):
            sheet.column_dimensions[letter].width = width
        sheet.freeze_panes = "A2"
        sheet.append(
            self._heading(
                sheet,
                ["SKU", "Mfg_Part_Num", "Column", "Attribute", "Reason", "Confidence"],
            )
        )

        for row in rows:
            for withheld in row.withheld:
                sheet.append(
                    [
                        self._cell(sheet, row.sku or ""),
                        self._cell(sheet, row.mpn or ""),
                        self._cell(sheet, withheld.column),
                        self._cell(sheet, withheld.attribute_code),
                        self._cell(sheet, withheld.reason),
                        self._cell(
                            sheet,
                            "" if withheld.confidence is None else f"{withheld.confidence:.4f}",
                        ),
                    ]
                )

    # ------------------------------------------------------------------ cells

    def _heading(self, sheet, labels: Sequence[str]) -> list:
        from openpyxl.styles import Font, PatternFill

        font = Font(bold=True, color="FFFFFFFF")
        fill = PatternFill("solid", fgColor="FF1F2933")
        cells = []
        for label in labels:
            cell = self._cell(sheet, label)
            cell.font = font
            cell.fill = fill
            cells.append(cell)
        return cells

    def _cell(self, sheet, value: object):
        """One cell, forced to text.

        Two corrections happen here.

        ``data_type = "s"`` is reasserted *after* the value is bound because openpyxl classifies
        any string starting with ``=`` as a formula. A delivery value can legitimately start with
        ``=`` and writing it as a formula would mean Excel evaluating supplier text — a broken cell
        at best and an injection vector at worst.

        Control characters XLSX forbids are stripped, because openpyxl raises on them rather than
        escaping them, and losing a thousand-row export to one stray byte in one description is the
        wrong trade.
        """
        from openpyxl.cell import WriteOnlyCell

        text = "" if value is None else str(value)
        cell = WriteOnlyCell(sheet, value=_ILLEGAL.sub("", text))
        cell.data_type = "s"
        cell.number_format = TEXT_FORMAT
        return cell

    def _text(self, sheet, value: str, counters: _Counters):
        """A cell on the *delivery* sheet, with both corrections counted.

        Counted here and not in :meth:`_cell` so the reported numbers describe the deliverable.
        Tallying the audit sheets as well would multiply every correction by the number of times
        the same part number is echoed on the Provenance sheet, and a caller reading
        ``sanitised_cells`` wants to know how many delivery cells differ from the CSV, not how
        many times we wrote the string.
        """
        text = "" if value is None else str(value)
        if _ILLEGAL.search(text):
            counters.sanitised += 1
        if text.startswith("="):
            counters.guarded += 1
        return self._cell(sheet, text)


def to_workbook_bytes(
    fmt: DeliveryFormat,
    rows: Iterable[DeliveryRow],
    *,
    source_name: str | None = None,
    generated_at: datetime | None = None,
    include_audit: bool = True,
) -> bytes:
    """Render a batch to XLSX bytes. The one-liner for callers that want only the file."""
    return (
        DeliveryWorkbookExporter(fmt)
        .to_workbook(
            rows,
            source_name=source_name,
            generated_at=generated_at,
            include_audit=include_audit,
        )
        .data
    )


__all__ = [
    "DELIVERY_SHEET",
    "PROVENANCE_SHEET",
    "SUMMARY_SHEET",
    "WITHHELD_SHEET",
    "WORKBOOK_MEDIA_TYPE",
    "WORKBOOK_SUFFIX",
    "DeliveryWorkbook",
    "DeliveryWorkbookExporter",
    "to_workbook_bytes",
]
