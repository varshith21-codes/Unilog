"""Variant table explosion: one datasheet in, a complete record per orderable part number out.

An industrial datasheet almost never describes one product. It describes a *series* — a shared
specification block plus an ordering table with one row per orderable part number. Running the
whole pipeline once per SKU re-reads and re-pays for the same document N times, and worse, asks
a model N times to pick the right row out of a table, which is exactly the operation it is
weakest at and where a neighbouring-row error is invisible afterwards.

So the split here is deliberate:

*   **Shared specification values** are extracted once, by the model, from the specification
    block. Each child inherits them with the original citation intact.
*   **Per-variant values** come from the ordering table by **deterministic cell lookup**. No
    model call, no chance of reading row 4 for row 3, and the citation is an exact cell
    reference rather than a row the model claims it read.

That second point is the reason this module exists at all. Reading the wrong row of an ordering
table produces a value that is genuinely present in the document and wrong for the part, which
quote verification cannot detect — the same failure class as the wrong-document bug in
``docintel.sku``. Removing the model from the row-selection step removes the failure.

Inheritance is not unconditional. A specification qualified to one size — ``18-22 ft-lb (1/2"
size)`` — must not be copied onto the 1-1/4" variant, and footnotes routinely override a table
column for part of the range. Both are handled below, and where the source is genuinely
contradictory the value is withheld rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.gaps import Gap, GapReason, RecommendedAction
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.docintel import ParsedDocument
from axiom.docintel.models import ParsedTable
from axiom.normalize.parsers import parse_quantity, parse_range
from axiom.schema import SchemaRegistry

SKU_COLUMN_HEADERS = (
    "part number",
    "part no",
    "part #",
    "catalog number",
    "catalog no",
    "cat no",
    "item number",
    "item no",
    "model",
    "model number",
    "order code",
    "ordering code",
    "sku",
    "mpn",
)
"""Headers that identify the part-number column. Kept here rather than in the attribute
dictionary because this is a property of ordering tables in general, not of one attribute."""

MIN_VARIANT_ROWS = 2
"""One row is not a variant table, it is a spec sheet with a table in it. Exploding a
single-row table would produce one child identical to its parent and imply structure that is
not there."""

# Matches a size qualifier in a parenthetical note: '1/2" size', 'sizes 2" and larger',
# 'DN50 only'. Deliberately broad — a false positive withholds an inherited value, which costs
# coverage, while a false negative copies a wrong spec onto a product, which is a defect.
_SIZE_SCOPED = re.compile(
    r"""(
        \bsizes?\b            # the word "size" or "sizes"
        | \d+\s*/\s*\d+\s*"   # an imperial fraction with an inch mark
        | \bdn\s*\d+          # a metric DN designation
        | \bnps\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class VariantColumn:
    """One column of an ordering table, and the attribute it supplies if any."""

    col: int
    header: str
    attribute_code: str | None = None

    @property
    def is_mapped(self) -> bool:
        return self.attribute_code is not None


@dataclass(frozen=True)
class VariantRow:
    """One orderable part number and the cells that describe it."""

    row: int
    sku: str
    sku_cell_ref: str
    values: dict[str, str] = field(default_factory=dict)
    cell_refs: dict[str, str] = field(default_factory=dict)
    bboxes: dict[str, BoundingBox] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantTable:
    """A detected ordering table, with its columns bound to attributes."""

    table_id: str
    page: int
    header_row: int
    sku_column: int
    columns: tuple[VariantColumn, ...]
    rows: tuple[VariantRow, ...]
    unmapped_headers: tuple[str, ...] = ()
    """Headers no attribute claimed. Surfaced rather than dropped: an unmapped column is
    usually a missing ``table_headers`` entry in the schema, which is a one-line fix, and
    silently ignoring it loses real data."""

    @property
    def variant_skus(self) -> tuple[str, ...]:
        return tuple(row.sku for row in self.rows)

    @property
    def mapped_codes(self) -> tuple[str, ...]:
        return tuple(c.attribute_code for c in self.columns if c.attribute_code)

    def row_for(self, sku: str) -> VariantRow | None:
        folded = _fold(sku)
        return next((row for row in self.rows if _fold(row.sku) == folded), None)

    def summary(self) -> dict[str, object]:
        return {
            "table_id": self.table_id,
            "page": self.page,
            "variants": len(self.rows),
            "skus": list(self.variant_skus),
            "per_variant_attributes": list(self.mapped_codes),
            "unmapped_headers": list(self.unmapped_headers),
        }


def _fold(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").casefold())


def detect_variant_table(
    parsed: ParsedDocument, registry: SchemaRegistry, class_code: str
) -> VariantTable | None:
    """Find the ordering table and bind its columns to attributes.

    Returns None when no table qualifies, which is a legitimate outcome: plenty of datasheets
    describe exactly one part number. Guessing a variant structure out of a specification table
    would fabricate products, so the bar is deliberately high.
    """
    header_lookup = _header_lookup(registry, class_code)

    best: VariantTable | None = None
    for page in parsed.pages:
        for table in page.tables:
            candidate = _bind_table(table, header_lookup)
            if candidate is None:
                continue
            # Prefer the table that yields the most variants; on a tie, the most bound columns.
            if best is None or (len(candidate.rows), len(candidate.mapped_codes)) > (
                len(best.rows),
                len(best.mapped_codes),
            ):
                best = candidate
    return best


def _header_lookup(registry: SchemaRegistry, class_code: str) -> dict[str, str]:
    """``folded header -> attribute code``, for attributes this class actually declares."""
    lookup: dict[str, str] = {}
    product_class = registry.product_class(class_code)
    for binding in product_class.attributes:
        definition = registry.attribute(binding.code)
        for header in definition.table_headers:
            lookup.setdefault(_fold(header), binding.code)
    return lookup


def _bind_table(table: ParsedTable, header_lookup: dict[str, str]) -> VariantTable | None:
    grid = table.rows()
    if len(grid) < MIN_VARIANT_ROWS + 1:  # header plus at least two variants
        return None

    header_row = 0
    headers = grid[header_row]

    sku_column = _find_sku_column(headers)
    if sku_column is None:
        return None

    columns: list[VariantColumn] = []
    unmapped: list[str] = []
    for col, header in enumerate(headers):
        if col == sku_column:
            columns.append(VariantColumn(col=col, header=header, attribute_code=None))
            continue
        code = header_lookup.get(_fold(header))
        columns.append(VariantColumn(col=col, header=header, attribute_code=code))
        if code is None and header.strip():
            unmapped.append(header.strip())

    by_code = {c.col: c.attribute_code for c in columns if c.attribute_code}

    rows: list[VariantRow] = []
    for row_index in range(header_row + 1, len(grid)):
        sku_cell = table.cell(row_index, sku_column)
        sku = (sku_cell.text if sku_cell else "").strip()
        # A blank or non-part-number cell ends the data — footnote rows sometimes land inside
        # the detected table bounds, and treating one as a variant would invent a product.
        if not _looks_like_part_number(sku):
            continue

        values: dict[str, str] = {}
        cell_refs: dict[str, str] = {}
        bboxes: dict[str, BoundingBox] = {}
        for col, code in by_code.items():
            cell = table.cell(row_index, col)
            text = (cell.text if cell else "").strip()
            if not text:
                continue
            values[code] = text
            cell_refs[code] = table.cell_ref(row_index, col)
            if cell is not None:
                bboxes[code] = cell.bbox

        rows.append(
            VariantRow(
                row=row_index,
                sku=sku,
                sku_cell_ref=table.cell_ref(row_index, sku_column),
                values=values,
                cell_refs=cell_refs,
                bboxes=bboxes,
            )
        )

    if len(rows) < MIN_VARIANT_ROWS:
        return None

    return VariantTable(
        table_id=table.table_id,
        page=table.page,
        header_row=header_row,
        sku_column=sku_column,
        columns=tuple(columns),
        rows=tuple(rows),
        unmapped_headers=tuple(dict.fromkeys(unmapped)),
    )


def _find_sku_column(headers: list[str]) -> int | None:
    for col, header in enumerate(headers):
        if _fold(header) in {_fold(h) for h in SKU_COLUMN_HEADERS}:
            return col
    return None


def _looks_like_part_number(text: str) -> bool:
    """Contains a digit and a letter or separator, and no spaces. Excludes prose and bare sizes.

    Bare numbers are excluded on purpose: a carton quantity column would otherwise qualify, and
    a table keyed on '12' would explode into products that do not exist.
    """
    candidate = text.strip()
    if not candidate or " " in candidate or len(candidate) < 3:
        return False
    if not any(ch.isdigit() for ch in candidate):
        return False
    return any(ch.isalpha() or ch in "-./_" for ch in candidate)


def is_size_scoped(value: AttributeValue) -> str | None:
    """The size qualifier attached to a value, if it carries one.

    ``18-22 ft-lb (1/2" size)`` describes one variant, not the series. Inheriting it across the
    range would attach a plausible, precisely-cited, wrong torque figure to four other products.
    """
    raw = value.value_raw or ""
    if "(" not in raw:
        return None

    parsed = parse_range(raw) if _looks_like_range(raw) else parse_quantity(raw)
    note = parsed.conditional_note
    if note and _SIZE_SCOPED.search(note):
        return note
    return None


def _looks_like_range(raw: str) -> bool:
    return bool(re.search(r"\d\s*(?:-|–|to)\s*\d", raw))


# A size designation inside a qualifier note: 1/2", 1-1/4", 2", DN50.
_SIZE_TOKEN = re.compile(r"(\d+(?:-\d+/\d+|/\d+)?\s*\"|\bdn\s*\d+)", re.IGNORECASE)


def _note_covers_size(note: str, row_size: str | None) -> bool:
    """Does a size-scoped note apply to this variant's size?

    Compared as whole extracted tokens, not by prefix. Prefix matching looks equivalent and is
    not: folding ``1/2" size`` gives ``12size``, which *starts with* the ``1`` from a 1" variant,
    so the 1" row would silently be treated as the size the note applies to and inherit a torque
    figure stated only for the half-inch valve. Found by exactly that off-by-one in testing.
    """
    if not row_size:
        return False
    row_token = _fold(row_size)
    if not row_token:
        return False
    return any(_fold(match) == row_token for match in _SIZE_TOKEN.findall(note))


def explode(
    reference: ProductRecord,
    table: VariantTable,
    parsed: ParsedDocument,
    registry: SchemaRegistry,
    *,
    tenant_id: str | None = None,
) -> list[ProductRecord]:
    """Build one record per variant from a reference record and the ordering table.

    ``reference`` is the fully-extracted record for any one SKU in the series; its
    specification values are the shared ones. Per-variant values are overwritten from the
    table, so whichever SKU was used as the reference does not bias the others.
    """
    shared, withheld = _partition_shared(reference, table)

    children: list[ProductRecord] = []
    for row in table.rows:
        child = ProductRecord(
            tenant_id=tenant_id or reference.tenant_id,
            sku=row.sku,
            mpn=row.sku,
            mpn_normalized=row.sku,
            brand=reference.brand,
            brand_id=reference.brand_id,
            supplier_id=reference.supplier_id,
            class_code=reference.class_code,
            schema_version=reference.schema_version,
            parent_sku=reference.sku if reference.sku != row.sku else None,
            source_document_ids=list(reference.source_document_ids),
        )
        child.classifications.extend(reference.classifications)

        for value in shared:
            child.add_value(_inherited(value, row.sku))

        for code, raw in row.values.items():
            child.add_value(_from_cell(code, raw, row, table, parsed))

        # A size-scoped specification belongs to exactly one variant. That variant inherits it
        # normally; for every other variant it is not missing data but *inapplicable*, and
        # saying so is what stops a reviewer chasing a supplier for a figure that was never
        # meant to exist for their part.
        row_size = row.values.get("nominal_size")
        for value, note in withheld:
            if _note_covers_size(note, row_size):
                child.add_value(_inherited(value, row.sku))
                continue
            child.add_gap(
                Gap(
                    attribute_code=value.attribute_code,
                    reason=GapReason.NOT_PRESENT_IN_ANY_SOURCE,
                    sources_searched=list(reference.source_document_ids),
                    detail=(
                        f"the source states this only for {note!r}, so it does not apply to "
                        f"{row.sku}"
                    ),
                    recommended_action=RecommendedAction.ACCEPT_AS_NOT_APPLICABLE,
                )
            )

        for gap in reference.gaps:
            if gap.attribute_code in row.values:
                continue  # the table supplied what the spec block did not
            if any(g.attribute_code == gap.attribute_code for g in child.gaps):
                continue
            child.add_gap(gap.model_copy())

        children.append(child)

    return children


def _partition_shared(
    reference: ProductRecord, table: VariantTable
) -> tuple[list[AttributeValue], list[tuple[AttributeValue, str]]]:
    """Split the reference's values into inheritable and size-scoped."""
    per_variant = set(table.mapped_codes)
    shared: list[AttributeValue] = []
    withheld: list[tuple[AttributeValue, str]] = []

    for value in reference.current_values():
        if value.attribute_code in per_variant:
            continue  # the table is authoritative for these
        note = is_size_scoped(value)
        if note:
            withheld.append((value, note))
        else:
            shared.append(value)
    return shared, withheld


def _inherited(value: AttributeValue, sku: str) -> AttributeValue:
    """Copy a series-level value onto a variant, keeping its original citation.

    The method stays as extracted rather than becoming a derivation: the value really was read
    from the document, and the citation still resolves to the line that states it. Relabelling
    it would understate the evidence.
    """
    return value.model_copy(
        update={
            "status": ValueStatus.CANDIDATE,
            "derived_from": f"series specification inherited by {sku}",
        },
        deep=True,
    )


def _from_cell(
    code: str,
    raw: str,
    row: VariantRow,
    table: VariantTable,
    parsed: ParsedDocument,
) -> AttributeValue:
    """A per-variant value, cited to the exact cell it came from.

    ``quote_verified=True`` is asserted here without a search, and that is correct rather than
    lax: the quote *is* the cell text, read directly out of the parsed document. There is no
    model claim to check. The citation is a cell reference, which is strictly more precise than
    anything a located quote can produce.
    """
    bbox = row.bboxes.get(code)
    span = EvidenceSpan(
        span_id=f"sp_{parsed.document.sha256[:8]}_{table.table_id}_{row.row}_{code}",
        document_id=parsed.document.document_id,
        document_sha256=parsed.document.sha256,
        quote=raw,
        page=table.page,
        bbox=bbox,
        table_ref=row.cell_refs.get(code),
        quote_verified=True,
        match_score=1.0,
    )
    return AttributeValue(
        attribute_code=code,
        value_raw=raw,
        method=DerivationMethod.TABLE_EXTRACTION,
        confidence=0.95,
        evidence=[span],
        schema_version=None,
    )
