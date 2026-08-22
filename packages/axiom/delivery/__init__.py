"""The Unilog delivery format: the graded output contract.

This package exists because the client's output schema is fixed. Parts 0-16 of the blueprint
assumed we would design our own export shape and prove the canonical record could serve it; the
dataset pack removed that freedom. There are 252 columns, in an order we do not control, and a
file that does not match them is unusable no matter how good the enrichment behind it is.

Three responsibilities, deliberately separated:

* :mod:`axiom.delivery.format` — the contract. Which columns, in what order, what each one
  claims, and what character limits apply. Loaded from ``schema/delivery/*.yaml``.
* :mod:`axiom.delivery.builder` — projection. One :class:`~axiom.core.product.ProductRecord`
  onto one delivery row, with per-cell provenance recorded rather than discarded.
* :mod:`axiom.delivery.exporter` — serialisation, and the provenance sidecar that makes the CSV
  auditable cell by cell.

The separation matters because the contract is the client's, the projection is ours, and the two
change for entirely unrelated reasons.
"""

from axiom.delivery.builder import (
    Cell,
    DeliveryRow,
    DeliveryRowBuilder,
    WithheldCell,
)
from axiom.delivery.exporter import DeliveryExport, DeliveryFormatExporter
from axiom.delivery.format import (
    Casing,
    DeliveryColumn,
    DeliveryFormat,
    DeliveryFormatError,
    DeliverySection,
    Provenance,
    default_format_path,
    load_default,
)
from axiom.delivery.scoring import (
    CellScore,
    RowScore,
    ScoreReport,
    Verdict,
    render_report,
    score_rows,
)
from axiom.delivery.source import (
    BrandResolution,
    ManufacturerResolution,
    SupplierRow,
    resolve_brand,
    resolve_manufacturer,
    strip_supplier_code,
)
from axiom.delivery.xlsx import (
    WORKBOOK_MEDIA_TYPE,
    WORKBOOK_SUFFIX,
    DeliveryWorkbook,
    DeliveryWorkbookExporter,
    to_workbook_bytes,
)

__all__ = [
    "WORKBOOK_MEDIA_TYPE",
    "WORKBOOK_SUFFIX",
    "BrandResolution",
    "Casing",
    "Cell",
    "CellScore",
    "DeliveryColumn",
    "DeliveryExport",
    "DeliveryFormat",
    "DeliveryFormatError",
    "DeliveryFormatExporter",
    "DeliveryRow",
    "DeliveryRowBuilder",
    "DeliverySection",
    "DeliveryWorkbook",
    "DeliveryWorkbookExporter",
    "ManufacturerResolution",
    "Provenance",
    "RowScore",
    "ScoreReport",
    "SupplierRow",
    "Verdict",
    "WithheldCell",
    "default_format_path",
    "load_default",
    "render_report",
    "resolve_brand",
    "resolve_manufacturer",
    "score_rows",
    "strip_supplier_code",
    "to_workbook_bytes",
]
