"""One enriched SKU as a delivery row, and as files on disk.

The delivery projection expects a supplier row. A typed submission is not one, so the four typed
fields are assembled into the six-column echo the item master would have carried and pushed
through the same :meth:`SupplierRow.parse` a real row goes through. That is not ceremony: the echo
columns exist so the client can join our output back to their input, and ``Part_Manuf`` screening
— the check that catches ``Appliance Dealers Cooperative`` before it lands in
``MANUFACTURER_NAME`` — lives in that parse. A typed manufacturer name goes through the same door
as a supplied one.

**The typed manufacturer is a caller assertion, not a verified fact.**
``publishable_as_manufacturer`` is documented as "safe to propose, not verified"; the approved
master the client requires exact casing and legal suffixes from is not in this repository. So the
name is passed through ``builder.build(..., manufacturer=)`` — the seam whose comment reads
"Retrieval will supply them the same way" — and the sidecar records that its source was the caller
rather than a document.

Files are written at run time rather than on download. A download that re-ran the pipeline would
spend another two model calls to produce bytes we already had, and would return something subtly
different from what the screen just reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axiom.core.naming import sku_slug
from axiom.delivery import (
    WORKBOOK_SUFFIX,
    DeliveryFormat,
    DeliveryFormatExporter,
    DeliveryRow,
    DeliveryRowBuilder,
    DeliveryWorkbookExporter,
    SupplierRow,
)
from axiom.delivery.source import INPUT_COLUMNS
from axiom.schema import SchemaRegistry


@dataclass(frozen=True)
class EnrichmentDelivery:
    """The delivery row for one enriched SKU, and the artifacts written from it."""

    row: DeliveryRow
    source: SupplierRow
    csv_path: Path
    xlsx_path: Path
    provenance_path: Path
    content_hash: str
    populated: int
    columns: int
    withheld: int
    compliant: bool

    def summary(self) -> dict[str, object]:
        return {
            "populated": self.populated,
            "columns": self.columns,
            "blank": self.columns - self.populated,
            "withheld": self.withheld,
            "compliant": self.compliant,
            "content_hash": self.content_hash,
            "provenance": self.row.provenance_counts(),
            "cited_columns": list(self.row.cited_columns()),
            "notes": list(self.row.notes),
            "input_notes": list(self.source.notes),
            "files": {
                "csv": self.csv_path.name,
                "xlsx": self.xlsx_path.name,
                "provenance": self.provenance_path.name,
            },
        }


def echo_row(
    *,
    mpn: str,
    manufacturer: str | None = None,
    description: str | None = None,
    brand: str | None = None,
) -> dict[str, str]:
    """The six input columns, as a typed submission would have filled them.

    Blank rather than sentinel for the fields nobody typed. A real item master writes
    ``-- Unbranded --`` and the echo preserves it verbatim, because the client diffs our output
    against their file; inventing a sentinel here would put a value in a cell nobody supplied.

    ``brand`` goes to ``DIB_Brand`` because that is the column :data:`BRAND_COLUMNS` prefers, and
    preferring a different one would mean a typed brand resolved differently from a supplied one.
    """
    return {
        "Mfg_Part_Num": mpn,
        "Part_Desc": description or "",
        "E1_Brand": "",
        "Unilog_Brand": "",
        "DIB_Brand": brand or "",
        "Part_Manuf": manufacturer or "",
    }


def build_delivery(
    record,
    *,
    registry: SchemaRegistry,
    fmt: DeliveryFormat,
    out_dir: Path | str,
    mpn: str,
    manufacturer: str | None = None,
    description: str | None = None,
    brand: str | None = None,
    source_url: str | None = None,
    builder: DeliveryRowBuilder | None = None,
) -> EnrichmentDelivery:
    """Project one enriched record onto a delivery row and write the three artifacts.

    ``source_url`` becomes ``MFR URL``, which the builder treats as an evidence claim rather than
    a link — it is an assertion about *who said it*. It is only passed when the caller actually
    fetched that URL, so the column is never populated with a link nobody opened.
    """
    source = SupplierRow.parse(
        echo_row(mpn=mpn, manufacturer=manufacturer, description=description, brand=brand)
    )
    builder = builder or DeliveryRowBuilder(fmt, registry)

    row = builder.build(
        record,
        source=source,
        # Both are unresolvable from six columns — ground truth expects `FRIGIDAIRE(R)` where the
        # input carries three sentinels and a buying co-op — which is exactly why this seam exists.
        # A typed value is a caller assertion, and the sidecar records it as one.
        brand=brand,
        manufacturer=manufacturer if manufacturer else None,
        mfr_url=source_url,
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = sku_slug(record.sku or mpn)

    exporter = DeliveryFormatExporter(fmt)
    export = exporter.to_csv([row])
    csv_path = out / f"{slug}.delivery.csv"
    # newline="" so the exporter's own line endings survive; letting Python translate them would
    # rewrite the bytes whose hash the export just reported.
    csv_path.write_text(export.csv_text, encoding="utf-8", newline="")

    provenance_path = out / f"{slug}.provenance.json"
    provenance_path.write_text(exporter.sidecar([row]), encoding="utf-8")

    workbook = DeliveryWorkbookExporter(fmt).to_workbook(
        [row], source_name=f"submission:{mpn}", include_audit=True
    )
    xlsx_path = out / f"{slug}.delivery{WORKBOOK_SUFFIX}"
    xlsx_path.write_bytes(workbook.data)

    return EnrichmentDelivery(
        row=row,
        source=source,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        provenance_path=provenance_path,
        content_hash=export.content_hash,
        populated=row.populated_count,
        columns=len(fmt),
        withheld=len(row.withheld),
        compliant=export.compliant,
    )


__all__ = ["INPUT_COLUMNS", "EnrichmentDelivery", "build_delivery", "echo_row"]
