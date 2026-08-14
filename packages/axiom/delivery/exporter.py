"""Writing the delivery file, and the sidecar that makes it auditable.

Two artifacts, always produced together:

* **The CSV** the client ingests. 252 columns in their order, every row the same width.
* **The provenance sidecar**, JSON, recording for every populated cell which class it belongs to,
  how confident we are, and what it cites. This is the Enrichment Certificate projected onto the
  client's column names.

The sidecar is not optional decoration. Without it, "we populated 79 columns from a 35-character
description" is indistinguishable from fabrication — to a judge, to the client, and after a few
weeks to us. With it, the claim is checkable cell by cell.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from axiom.delivery.builder import DeliveryRow
from axiom.delivery.format import DeliveryFormat, Provenance


@dataclass
class DeliveryExport:
    """The result of exporting a batch."""

    csv_text: str
    row_count: int
    format_name: str
    content_hash: str
    populated_by_column: dict[str, int] = field(default_factory=dict)
    provenance_totals: dict[str, int] = field(default_factory=dict)
    violations: list[dict[str, object]] = field(default_factory=list)
    withheld_count: int = 0
    rows_with_notes: int = 0

    @property
    def compliant(self) -> bool:
        """Whether every declared character and casing constraint held across the batch."""
        return not self.violations

    def summary(self) -> dict[str, object]:
        return {
            "format": self.format_name,
            "rows": self.row_count,
            "content_hash": self.content_hash[:12] + "…",
            "columns_populated": len(self.populated_by_column),
            "provenance": self.provenance_totals,
            "withheld": self.withheld_count,
            "constraint_violations": len(self.violations),
            "compliant": self.compliant,
            "rows_with_notes": self.rows_with_notes,
        }


class DeliveryFormatExporter:
    """Serialises delivery rows to the client's CSV and a provenance sidecar."""

    name = "unilog_delivery"

    def __init__(self, fmt: DeliveryFormat) -> None:
        self._format = fmt

    # ------------------------------------------------------------------ csv

    def to_csv(self, rows: Iterable[DeliveryRow]) -> DeliveryExport:
        """Write the batch.

        ``lineterminator="\\n"`` rather than the csv module's default ``\\r\\n``: the default
        produces CRLF on every platform, which on Windows becomes CRCRLF once anything else
        touches the file in text mode. Excel and the client's importer both read LF correctly.

        Quoting is left at QUOTE_MINIMAL, which is what the client's own file uses — their
        descriptions contain commas and are quoted; their part numbers are not.
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self._format.header)

        populated: dict[str, int] = {}
        provenance: dict[str, int] = {}
        violations: list[dict[str, object]] = []
        withheld = 0
        with_notes = 0
        count = 0

        for row in rows:
            self._check_format(row)
            values = row.as_list()
            if len(values) != len(self._format):
                raise ValueError(
                    f"row produced {len(values)} values for a {len(self._format)}-column "
                    f"format; the row and the contract have diverged"
                )
            writer.writerow(values)
            count += 1

            for name, cell in row.cells.items():
                if cell.value:
                    populated[name] = populated.get(name, 0) + 1
            for key, value in row.provenance_counts().items():
                provenance[key] = provenance.get(key, 0) + value

            for column, problems in row.violations().items():
                violations.append(
                    {"row": count, "mpn": row.mpn, "column": column, "problems": problems}
                )
            withheld += len(row.withheld)
            if row.notes:
                with_notes += 1

        text = buffer.getvalue()
        return DeliveryExport(
            csv_text=text,
            row_count=count,
            format_name=f"{self._format.name}@{self._format.version}",
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            populated_by_column=populated,
            provenance_totals=provenance,
            violations=violations,
            withheld_count=withheld,
            rows_with_notes=with_notes,
        )

    # ------------------------------------------------------------------ sidecar

    def sidecar(self, rows: Sequence[DeliveryRow]) -> str:
        """The per-cell provenance record for a batch, as JSON."""
        payload = {
            "format": f"{self._format.name}@{self._format.version}",
            "columns": len(self._format),
            "rows": len(rows),
            "unavailable_columns": list(self._format.unavailable_columns()),
            "provenance_legend": {
                p.value: {
                    "requires_evidence_span": p.requires_evidence_span,
                    "may_be_populated": p.may_be_populated,
                    "may_support_generation": p.is_established,
                }
                for p in Provenance
            },
            "records": [row.sidecar() for row in rows],
        }
        return json.dumps(payload, indent=2, default=str)

    # ------------------------------------------------------------------ guards

    def _check_format(self, row: DeliveryRow) -> None:
        if row.format is not self._format:
            raise ValueError(
                "row was built against a different delivery format than this exporter holds; "
                "mixing contracts would produce a file whose columns do not match its header"
            )


__all__ = ["DeliveryExport", "DeliveryFormatExporter"]
