"""Projecting one canonical record onto one delivery row.

The projection is where the client's format and AXIOM's evidence rule have to be reconciled, and
the reconciliation is per cell rather than per row. Each column declares a provenance class
(:class:`~axiom.delivery.format.Provenance`) and this module enforces a different rule for each:

* ``passthrough`` — copied from the input verbatim. No gate: the only claim is "you sent us this".
* ``derived`` — computed from passthrough data or from an accepted value. Inherits provenance.
* ``extracted`` — **the full gate applies.** A value that is not publishable is *withheld*, not
  emitted. This is the one rule that must never bend, because these are the cells a distributor
  would act on.
* ``generated`` — supplied by a renderer, and only from cells already established here.
* ``unavailable`` — refused outright, even if a caller passes a value.

The result is a row that is legitimately sparse. Ground truth is sparse too: row 1 of the client's
own example leaves three of its fifteen attribute slots labelled but empty, and 173 of 252 columns
blank across both rows. A row that filled everything would not be better, it would be fabricated.

Nothing here reaches for a model. Every cell is a copy, a lookup, or a deterministic computation,
which is why the whole projection is testable offline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from axiom.core.product import ClassificationScheme, ProductRecord
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.delivery.format import DeliveryColumn, DeliveryFormat, Provenance
from axiom.delivery.render import (
    RecipeBook,
    load_invoice_terms,
    load_recipe_books,
    render_all,
    split_display_unit,
)
from axiom.delivery.source import SupplierRow
from axiom.schema import SchemaRegistry

CLASSPATH_SEPARATOR = ">"
"""No surrounding spaces. The client writes
``Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers``."""

APPROVALS_SEPARATOR = "|"

# Named delivery columns fed by an attribute rather than by a numbered grid slot.
NAMED_ATTRIBUTE_COLUMNS: dict[str, str] = {
    "warranty_terms": "Warranty",
    "included_technology": "With",
    "approvals": "Standard/Approvals",
    "country_of_origin": "Country Of Origin",
    "prop65_warning_required": "Prop 65",
}


@dataclass(frozen=True)
class Cell:
    """One populated delivery cell, with the account of how it got there."""

    column: str
    value: str
    provenance: Provenance
    confidence: float | None = None
    source: str | None = None
    """Where it came from: an input header, an attribute code, or a computation name."""

    evidence: tuple[str, ...] = ()
    """Citation locators, for cells that carry them."""

    reviewed: bool = False

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "column": self.column,
            "provenance": self.provenance.value,
            "source": self.source,
        }
        if self.confidence is not None:
            out["confidence"] = round(self.confidence, 4)
        if self.evidence:
            out["evidence"] = list(self.evidence)
        if self.reviewed:
            out["reviewed"] = True
        return out


@dataclass(frozen=True)
class WithheldCell:
    """A cell that had a value available but was not allowed to publish it.

    Reported rather than silently dropped, for the same reason
    :class:`~axiom.syndicate.exporters.ExportResult` reports withheld attributes: a merchandiser
    needs to know the row is thinner than the record, and why.
    """

    column: str
    attribute_code: str
    reason: str
    confidence: float | None = None

    def summary(self) -> dict[str, object]:
        return {
            "column": self.column,
            "attribute": self.attribute_code,
            "reason": self.reason,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
        }


@dataclass
class DeliveryRow:
    """One row, plus everything needed to audit it."""

    format: DeliveryFormat
    cells: dict[str, Cell] = field(default_factory=dict)
    withheld: list[WithheldCell] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sku: str | None = None
    mpn: str | None = None

    # ------------------------------------------------------------------ serialisation

    def as_dict(self) -> dict[str, str]:
        """Every column, in declared order, with unpopulated cells empty."""
        row = self.format.blank_row()
        for name, cell in self.cells.items():
            row[name] = cell.value
        return row

    def as_list(self) -> list[str]:
        """Values in column order. The shape a CSV writer wants."""
        populated = self.as_dict()
        return [populated[name] for name in self.format.header]

    # ------------------------------------------------------------------ measurement

    @property
    def populated_count(self) -> int:
        return sum(1 for cell in self.cells.values() if cell.value)

    def provenance_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cell in self.cells.values():
            if cell.value:
                counts[cell.provenance.value] = counts.get(cell.provenance.value, 0) + 1
        return counts

    def violations(self) -> dict[str, list[str]]:
        """Declared character/casing constraints this row breaches.

        Checked on the built row rather than trusted from the renderer: a length limit enforced
        only at generation time is not enforced at all once anything else can write the cell.
        """
        out: dict[str, list[str]] = {}
        for cell in self.cells.values():
            problems = self.format.column(cell.column).violations(cell.value)
            if problems:
                out[cell.column] = problems
        return out

    def cited_columns(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, cell in self.cells.items() if cell.evidence))

    # ------------------------------------------------------------------ audit

    def sidecar(self) -> dict[str, object]:
        """The per-cell provenance record.

        The delivery CSV is what the client ingests; this is what makes it auditable. It is the
        Enrichment Certificate projected onto their column names, and it is the artifact that
        distinguishes "we filled 79 columns" from "we can tell you where all 79 came from".
        """
        return {
            "sku": self.sku,
            "mpn": self.mpn,
            "format": f"{self.format.name}@{self.format.version}",
            "populated": self.populated_count,
            "of_columns": len(self.format),
            "provenance": self.provenance_counts(),
            "cited_columns": list(self.cited_columns()),
            "cells": [
                self.cells[name].summary()
                for name in self.format.header
                if name in self.cells and self.cells[name].value
            ],
            "withheld": [w.summary() for w in self.withheld],
            "violations": self.violations(),
            "notes": list(self.notes),
        }


class DeliveryRowBuilder:
    """Builds delivery rows. Stateless between calls; hold one and reuse it."""

    def __init__(
        self,
        fmt: DeliveryFormat,
        registry: SchemaRegistry,
        *,
        recipe_books: Mapping[str, RecipeBook] | None = None,
        invoice_terms: Mapping[str, str] | None = None,
    ) -> None:
        self._format = fmt
        self._registry = registry
        # Loaded once and shared. Passing them in keeps the builder testable with a doctored recipe
        # and keeps YAML parsing off the per-row path for a thousand-row batch.
        self._books = dict(recipe_books) if recipe_books is not None else load_recipe_books()
        self._invoice_terms = (
            dict(invoice_terms) if invoice_terms is not None else load_invoice_terms()
        )

    def build(
        self,
        record: ProductRecord,
        *,
        source: SupplierRow | None = None,
        descriptions: Mapping[str, str] | None = None,
        features: list[str] | None = None,
        reference_urls: list[str] | None = None,
        mfr_url: str | None = None,
        assets: Mapping[str, str] | None = None,
        brand: str | None = None,
        manufacturer: str | None = None,
    ) -> DeliveryRow:
        """Project a record onto a delivery row.

        ``descriptions`` is keyed by delivery column name (``MOBILE_DESC``, ``INVOICE_DESC``, ...)
        rather than by some internal copy-field name. That keeps the mapping decision with the
        renderer that owns it instead of being guessed at here, and it means an unrecognised key
        is a loud error rather than a silently dropped description.

        ``assets`` is keyed by delivery column name too, and only assets actually retrieved
        should appear: writing a filename asserts the file exists.

        Note the deliberate asymmetry in strictness. A column name supplied by a *caller*
        (``descriptions``, ``assets``) is validated and raises on a typo, because a dropped
        description is a silent content bug. A column this builder writes *itself* is skipped
        with a note when the contract does not declare it, because there the contract is the
        authority and a reduced format is legitimate.
        """
        row = DeliveryRow(format=self._format, sku=record.sku, mpn=record.mpn)

        if source is not None:
            self._echo_input(row, source)
            self._resolve_identity(row, record, source)
            row.notes.extend(source.notes)
        else:
            self._resolve_identity(row, record, None)

        # Overrides, applied after resolution so a caller that *knows* the brand beats a guess made
        # from the input. Both are unresolvable from six columns — ground truth expects
        # `FRIGIDAIRE(R)` where the input carries three sentinels and a buying co-op — so retrieval
        # and the golden arm both need this seam. Confidence stays 1.0 because the caller is
        # asserting the value, not inferring it; the sidecar records where it came from.
        if brand:
            self._set(row, "BRAND_NAME", brand, Provenance.DERIVED, confidence=1.0, source="caller")
        if manufacturer:
            self._set(
                row,
                "MANUFACTURER_NAME",
                manufacturer,
                Provenance.DERIVED,
                confidence=1.0,
                source="caller",
            )
            # The withheld note from source resolution no longer applies once a caller has
            # supplied the name, and leaving it would report a refusal that did not happen.
            row.withheld = [w for w in row.withheld if w.column != "MANUFACTURER_NAME"]

        self._taxonomy(row, record)
        self._attribute_grid(row, record)
        self._named_attributes(row, record)
        self._identifiers(row, record)
        self._lifecycle(row, record)
        self._evidence(row, record, reference_urls, mfr_url)

        # Rendered descriptions come after the grid, because the recipes read the same values the
        # grid published and a renderer running first would compose from nothing.
        self._render_descriptions(row, record)
        self._descriptions(row, descriptions)
        self._features(row, features)
        self._assets(row, assets)
        return row

    # ------------------------------------------------------------------ passthrough

    def _echo_input(self, row: DeliveryRow, source: SupplierRow) -> None:
        for column, value in source.echo().items():
            self._set(row, column, value, Provenance.PASSTHROUGH, source=column)

    # ------------------------------------------------------------------ derived

    def _resolve_identity(
        self, row: DeliveryRow, record: ProductRecord, source: SupplierRow | None
    ) -> None:
        mpn = record.mpn_normalized or record.mpn
        if mpn:
            self._set(
                row, "MANUFACTURER_PART_NUMBER", mpn, Provenance.DERIVED, source="record.mpn"
            )

        if source is None:
            if record.brand:
                self._set(row, "BRAND_NAME", record.brand, Provenance.DERIVED, source="record")
            return

        # BRAND_NAME only from a column that actually held a brand. Ground truth row 1 proves the
        # expected value ("FRIGIDAIRE(R)") is absent from the input entirely, so an unresolved
        # brand is left blank for retrieval to fill rather than approximated from Part_Manuf.
        if source.brand.resolved:
            self._set(
                row,
                "BRAND_NAME",
                source.brand.brand or "",
                Provenance.DERIVED,
                confidence=0.55 if source.brand.conflict else 0.80,
                source=source.brand.source_column,
            )

        manufacturer = source.manufacturer
        if manufacturer.publishable_as_manufacturer:
            # Confidence is capped low on purpose: the client requires exact casing and legal
            # suffixes from the approved master, which we do not have. This is a proposal.
            self._set(
                row,
                "MANUFACTURER_NAME",
                manufacturer.name or "",
                Provenance.DERIVED,
                confidence=0.50,
                source="Part_Manuf",
            )
        elif manufacturer.resolved:
            row.withheld.append(
                WithheldCell(
                    column="MANUFACTURER_NAME",
                    attribute_code="Part_Manuf",
                    reason=(
                        f"{manufacturer.name!r} names a distributor or buying co-op, not the "
                        f"manufacturer of the goods"
                    ),
                )
            )

    def _taxonomy(self, row: DeliveryRow, record: ProductRecord) -> None:
        if record.class_code is None:
            row.notes.append("unclassified: Dept/Class/Fine and Classpath cannot be derived")
            return

        try:
            definition = self._registry.product_class(record.class_code)
        except KeyError:
            row.notes.append(f"class {record.class_code!r} is not in the schema")
            return

        internal = record.classification(ClassificationScheme.INTERNAL)
        confidence = internal.confidence if internal else None

        # Classpath from browse_path; Dept/Class/Fine from reporting_path. Two different trees,
        # and the client's ground truth proves neither derives from the other.
        if definition.browse_path:
            self._set(
                row,
                "Classpath",
                CLASSPATH_SEPARATOR.join(definition.browse_path),
                Provenance.DERIVED,
                confidence=confidence,
                source=f"class {definition.code} browse_path",
            )

        reporting = zip(("Dept", "Class", "Fine"), definition.reporting_path, strict=False)
        for column, level in reporting:
            self._set(
                row,
                column,
                level,
                Provenance.DERIVED,
                confidence=confidence,
                source=f"class {definition.code} reporting_path",
            )
        if definition.reporting_path and len(definition.reporting_path) < 3:
            row.notes.append(
                f"class {definition.code} declares only {len(definition.reporting_path)} "
                f"reporting levels; Dept/Class/Fine is partially blank"
            )
        if not definition.reporting_path:
            row.notes.append(
                f"class {definition.code} declares no reporting_path, so Dept/Class/Fine "
                f"cannot be filled"
            )

        # The bare noun, not the class label: ground truth writes "Dishwasher" where the class is
        # called "Built-In Dishwasher". The mounting qualifier lives in Mounting Type.
        self._set(
            row,
            "Product Name",
            definition.product_noun,
            Provenance.DERIVED,
            confidence=confidence,
            source=f"class {definition.code} item_type",
        )

    # ------------------------------------------------------------------ extracted

    def _attribute_grid(self, row: DeliveryRow, record: ProductRecord) -> None:
        """Fill the label/value/UOM triplets from the class's ordered bindings.

        The label is written for every bound attribute whether or not a value exists, because
        that is what the client does: ground truth row 1 carries labels on slots 2, 7 and 14 with
        no value beside them. The label describes the class; the value describes the part.
        """
        if record.class_code is None:
            return
        try:
            definition = self._registry.product_class(record.class_code)
        except KeyError:
            return

        capacity = self._format.slots("attribute_grid", "label")
        bindings = definition.slot_bindings()
        if len(bindings) > capacity:
            row.notes.append(
                f"class {definition.code} binds {len(bindings)} attributes but the grid has "
                f"{capacity} slots; the surplus is not emitted"
            )

        slot = 0
        for binding in bindings:
            if binding.code in NAMED_ATTRIBUTE_COLUMNS or binding.code == "gtin":
                # Fed to its own named column instead of a numbered slot.
                continue
            slot += 1
            if slot > capacity:
                break

            try:
                definition_attr = self._registry.attribute(binding.code)
            except KeyError:
                row.notes.append(f"binding {binding.code!r} has no attribute definition")
                continue

            label = binding.label or definition_attr.name
            self._set(
                row,
                self._format.slot_column("attribute_grid", "label", slot).name,
                label,
                Provenance.DERIVED,
                source=f"schema {binding.code}",
            )

            value = record.get(binding.code)
            if value is None:
                continue

            value_column = self._format.slot_column("attribute_grid", "value", slot).name
            if not value.is_publishable:
                row.withheld.append(
                    WithheldCell(
                        column=value_column,
                        attribute_code=binding.code,
                        reason=_withhold_reason(value),
                        confidence=value.confidence,
                    )
                )
                continue

            rendered, unit = _render(value)
            if rendered:
                self._set(
                    row,
                    value_column,
                    rendered,
                    Provenance.EXTRACTED,
                    confidence=value.confidence,
                    source=binding.code,
                    evidence=tuple(value.citation_summary()),
                    reviewed=value.reviewed_by is not None,
                )
            if unit:
                self._set(
                    row,
                    self._format.slot_column("attribute_grid", "uom", slot).name,
                    unit,
                    Provenance.DERIVED,
                    source=f"{binding.code} unit",
                )

    def _named_attributes(self, row: DeliveryRow, record: ProductRecord) -> None:
        for code, column in NAMED_ATTRIBUTE_COLUMNS.items():
            value = record.get(code)
            if value is None:
                continue
            if not value.is_publishable:
                row.withheld.append(
                    WithheldCell(
                        column=column,
                        attribute_code=code,
                        reason=_withhold_reason(value),
                        confidence=value.confidence,
                    )
                )
                continue
            rendered, _ = _render(value, list_separator=APPROVALS_SEPARATOR)
            if rendered:
                self._set(
                    row,
                    column,
                    rendered,
                    Provenance.EXTRACTED,
                    confidence=value.confidence,
                    source=code,
                    evidence=tuple(value.citation_summary()),
                    reviewed=value.reviewed_by is not None,
                )

    def _identifiers(self, row: DeliveryRow, record: ProductRecord) -> None:
        """GTIN family. UNSPSC is deliberately left alone — see the note below."""
        gtin = record.gtin
        value = record.get("gtin")
        if gtin is None and value is not None and value.is_publishable:
            gtin = value.value_display or str(value.value_canonical or "")
        if gtin:
            self._set(
                row,
                "GTIN",
                gtin,
                Provenance.EXTRACTED,
                confidence=value.confidence if value else None,
                source="gtin",
                evidence=tuple(value.citation_summary()) if value else (),
            )
            # UPC is the 12-digit form of the same identifier. Emitted only when the length says
            # so, rather than copying the GTIN across all three columns and hoping.
            digits = "".join(c for c in gtin if c.isdigit())
            if len(digits) == 12:
                self._set(row, "UPC", digits, Provenance.DERIVED, source="gtin")
            elif len(digits) == 13:
                self._set(row, "EAN", digits, Provenance.DERIVED, source="gtin")

        # UNSPSC: we hold a code per class and deliberately do not write it. The client's ground
        # truth leaves this column blank on both rows, and the guide names that as a known gap in
        # *their* data. Filling it would diverge from the expected output in order to look more
        # complete, which is the wrong trade — "fill discipline" is scored in both directions.
        if record.class_code:
            try:
                mapped = self._registry.product_class(record.class_code).mappings.get("unspsc")
            except KeyError:
                mapped = None
            if mapped:
                row.notes.append(
                    f"UNSPSC {mapped} is available from the class mapping but left blank to "
                    f"match the client's delivery format, which omits it"
                )

    def _lifecycle(self, row: DeliveryRow, record: ProductRecord) -> None:
        from axiom.core.product import LifecycleStatus

        if record.lifecycle_status is LifecycleStatus.DISCONTINUED:
            self._set(
                row, "Discontinued", "Yes", Provenance.DERIVED, source="record.lifecycle_status"
            )

    # ------------------------------------------------------------------ evidence

    def _evidence(
        self,
        row: DeliveryRow,
        record: ProductRecord,
        reference_urls: list[str] | None,
        mfr_url: str | None = None,
    ) -> None:
        """The manufacturer URL and supporting references.

        ``MFR URL`` means *the manufacturer's own page*. The guide's sourcing rule excludes
        marketplaces and distributor sites, so passing ``mfr_url`` explicitly is how a caller states
        that a URL has been judged to be the manufacturer's — ``axiom.retrieve.policy`` decides
        that,
        and only a ``manufacturer``-tier host qualifies.

        Without it, the first reference URL fills the slot. That is the older behaviour and it is
        kept
        for the golden arm, where the URLs were transcribed by hand from the client's own answer
        sheet
        and the first one is the manufacturer's by construction. It is *not* a safe default for
        retrieved URLs, which is why retrieval passes the field rather than relying on ordering: an
        unknown-tier page landing in ``MFR URL`` would publish a claim about who said it.
        """
        others = [u for u in (reference_urls or []) if u]
        primary = mfr_url or (others.pop(0) if mfr_url is None and others else None)
        if not primary and not others:
            return

        overflow = [c for c in self._format.group("reference_urls") if c.name != "MFR URL"]
        if primary:
            self._set(row, "MFR URL", primary, Provenance.EVIDENCE, source="source document")
        else:
            # Deliberately left empty rather than filled with the next best thing. An empty
            # `MFR URL` beside populated `Ref URL` columns says exactly what happened: sources were
            # found, none of them the manufacturer's.
            row.notes.append(
                f"{len(others)} reference URL(s) found but none on a declared manufacturer domain, "
                f"so MFR URL is left empty"
            )

        for column, url in zip(overflow, others, strict=False):
            self._set(row, column.name, url, Provenance.EVIDENCE, source="source document")
        if len(others) > len(overflow):
            row.notes.append(
                f"{len(others) + (1 if primary else 0)} reference URLs supplied but the format has "
                f"{len(overflow) + 1} slots; {len(others) - len(overflow)} not emitted"
            )

    # ------------------------------------------------------------------ generated

    def _render_descriptions(self, row: DeliveryRow, record: ProductRecord) -> None:
        """Assemble the five rewrites from values already established on this row.

        Deterministic, so a template cannot introduce a fact the record does not hold — which is why
        these are `generated` but need no claim check the way prose would.
        """
        book = self._books.get(record.class_code or "")
        if book is None:
            return

        brand = row.cells["BRAND_NAME"].value if "BRAND_NAME" in row.cells else None
        manufacturer = (
            row.cells["MANUFACTURER_NAME"].value if "MANUFACTURER_NAME" in row.cells else None
        )

        for column, rendered in render_all(
            book,
            record,
            self._registry,
            brand=brand,
            manufacturer=manufacturer,
            invoice_terms=self._invoice_terms,
        ).items():
            if rendered.text:
                self._set(
                    row,
                    column,
                    rendered.text,
                    Provenance.GENERATED,
                    source=f"recipe:{book.class_code}",
                )
                continue
            # Withheld. Reported with the reason so a thin row is explainable rather than
            # mysterious: usually "drew on 2 of a required 4 components".
            for source, reason in rendered.dropped:
                if source in {"<completeness>", "<length>"}:
                    row.withheld.append(
                        WithheldCell(
                            column=column,
                            attribute_code=source.strip("<>"),
                            reason=reason,
                        )
                    )

    def _descriptions(self, row: DeliveryRow, descriptions: Mapping[str, str] | None) -> None:
        if not descriptions:
            return
        for column, text in descriptions.items():
            if column not in self._format:
                raise KeyError(
                    f"{column!r} is not a delivery-format column; descriptions must be keyed by "
                    f"delivery column name"
                )
            if text:
                self._set(row, column, text, Provenance.GENERATED, source="renderer")

    def _features(self, row: DeliveryRow, features: list[str] | None) -> None:
        if not features:
            return
        capacity = self._format.slots("item_features", "feature")
        for index, feature in enumerate(features[:capacity], start=1):
            if feature:
                self._set(
                    row,
                    self._format.slot_column("item_features", "feature", index).name,
                    feature,
                    Provenance.GENERATED,
                    source="renderer",
                )
        if len(features) > capacity:
            row.notes.append(
                f"{len(features)} features supplied but the format has {capacity} slots; "
                f"the surplus is not emitted"
            )

    def _assets(self, row: DeliveryRow, assets: Mapping[str, str] | None) -> None:
        if not assets:
            return
        for column, filename in assets.items():
            if column not in self._format:
                raise KeyError(f"{column!r} is not a delivery-format column")
            if filename:
                self._set(row, column, filename, Provenance.DERIVED, source="asset store")

    # ------------------------------------------------------------------ the one setter

    def _set(
        self,
        row: DeliveryRow,
        column: str,
        value: str,
        provenance: Provenance,
        *,
        confidence: float | None = None,
        source: str | None = None,
        evidence: tuple[str, ...] = (),
        reviewed: bool = False,
    ) -> None:
        """Write one cell, enforcing the contract.

        Every write goes through here so the refusals are in one place and cannot be bypassed by
        a new code path forgetting to check.
        """
        if column not in self._format:
            # The contract is the authority, not this builder. A revised format that drops a
            # column should not crash the projection — it should simply not receive that cell.
            # Noted rather than silent, so a v2 that renamed something is visible immediately
            # instead of showing up as a mysteriously empty column.
            row.notes.append(
                f"{column!r} is not declared by {self._format.name}@{self._format.version}; "
                f"skipped"
            )
            return

        declared = self._format.column(column)

        if not declared.provenance.may_be_populated:
            # An `unavailable` column is refused even when a caller has something to put in it.
            # PART_NUMBER and SKU - MY_PART_NUMBER are the client's own key space.
            row.notes.append(
                f"refused to write {column!r}: declared unavailable, and a fabricated value "
                f"would collide with the client's own identifiers"
            )
            return

        if provenance is Provenance.GENERATED and not self._established(row, declared):
            row.notes.append(
                f"refused to write generated {column!r}: no established cell supports it"
            )
            return

        text = str(value).strip()
        if not text:
            return

        row.cells[column] = Cell(
            column=column,
            value=text,
            provenance=provenance,
            confidence=confidence,
            source=source,
            evidence=evidence,
            reviewed=reviewed,
        )

    @staticmethod
    def _established(row: DeliveryRow, column: DeliveryColumn) -> bool:
        """Whether generation has any established fact to draw on.

        A weak check by design — it verifies that the row is not empty of established cells,
        not that this particular sentence is entailed. Sentence-level entailment is
        `generate/claims.py`'s job and it needs the fact sheet, not the row. What this catches is
        the degenerate case: generating copy for a record where nothing at all was established,
        which would be pure invention with no possible support.
        """
        return any(cell.provenance.is_established and cell.value for cell in row.cells.values())


def _withhold_reason(value: AttributeValue) -> str:
    """Why a populated value did not publish, in terms a reviewer can act on."""
    if failures := value.failed_validations():
        rules = ", ".join(sorted({f.rule_id for f in failures if f.rule_id}))
        return f"failed validation ({rules})" if rules else "failed validation"
    if value.method.is_unsourced and not value.has_verified_evidence:
        return (
            "legacy item-master value with no verified evidence: present in the catalogue but "
            "not traceable to a source"
        )
    # Before the generic evidence check, because this value *has* a verified span and the generic
    # message would therefore be wrong about it. The span cites the client's own item master, which
    # establishes what the row says and not what the product is.
    if value.method.is_self_declared:
        return (
            "read from the customer's own item master description, which is the only source for "
            "it. The quote is verified, but the item master is the file being enriched rather than "
            "an independent source, so this stays a candidate: retrieve the manufacturer's "
            "document to publish it"
        )
    if value.method.requires_evidence and not value.has_verified_evidence:
        return "extracted without a verified evidence span"
    if not value.status.is_publishable:
        return f"status is {value.status.value}"
    return "not publishable"


def _render(
    value: AttributeValue, *, list_separator: str = ", "
) -> tuple[str, str | None]:
    """Render a value as (text, unit) for the delivery format.

    Mirrors ``syndicate.exporters._render_value`` in splitting a quantity's unit out, but returns
    display text rather than a raw magnitude: this format's value columns are read by people as
    well as machines, and the client writes "50-1/4" beside a separate "in".
    """
    canonical = value.value_canonical

    if isinstance(canonical, Quantity):
        if value.value_display:
            magnitude, unit = split_display_unit(value.value_display)
            if unit:
                return magnitude, unit
        return f"{canonical.magnitude:g}", canonical.unit

    if isinstance(canonical, ValueRange):
        text = value.value_display or str(canonical)
        return text, canonical.unit

    if isinstance(canonical, list):
        return list_separator.join(str(item) for item in canonical), None

    if isinstance(canonical, bool):
        return ("Yes" if canonical else "No"), None

    text = value.value_display or (str(canonical) if canonical is not None else value.value_raw)
    if not text:
        return "", None

    # A string attribute whose value happens to be nothing but a magnitude and a unit still gets
    # split, because the client does exactly that.
    #
    # `minimum_height` is the case that proves it. It is declared a string because row 1 holds
    # "8-1/2 in Upper Rack, 11-1/4 in Lower Rack" — two qualified measurements, not one length. But
    # row 2 holds a bare "33-7/16 in", and the client's own row puts "33-7/16" in the value column
    # with "in" in the UOM column beside it. So the split is a property of the value, not of the
    # declared datatype, and `split_display_unit` only fires when the remainder is a magnitude and
    # nothing else — which is what keeps the qualified form intact.
    magnitude, unit = split_display_unit(text)
    if unit:
        return magnitude, unit
    return text, None


__all__ = [
    "APPROVALS_SEPARATOR",
    "CLASSPATH_SEPARATOR",
    "NAMED_ATTRIBUTE_COLUMNS",
    "Cell",
    "DeliveryRow",
    "DeliveryRowBuilder",
    "WithheldCell",
]
