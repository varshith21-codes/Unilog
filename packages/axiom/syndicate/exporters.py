"""Channel exporters.

Two destinations with deliberately different shapes, to prove the canonical record can serve
both without either dictating its structure:

* **CX1 PIM import** — a flat, import-ready record with display values and units separated, of
  the kind a distributor's PIM ingests.
* **schema.org JSON-LD** — the vocabulary search engines and, increasingly, AI answer engines
  consume. Specs go in ``additionalProperty`` as typed ``PropertyValue`` entries rather than
  being flattened into the description, because prose is invisible to an agent that needs to
  compare a pressure rating.

Every export carries a content hash so delta publishing can skip unchanged records, and a
readiness report so a refusal is explainable rather than silent.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import Protocol

from axiom.core.product import ClassificationScheme, ProductRecord
from axiom.core.values import Quantity, ValueRange
from axiom.schema import SchemaRegistry
from axiom.syndicate.channels import (
    ChannelReadiness,
    preflight,
    publishable_values,
    render_title,
    truncate_title,
)


@dataclass
class ExportResult:
    """One channel payload plus the evidence it was allowed to be produced."""

    channel: str
    payload: str | None
    content_hash: str | None
    readiness: ChannelReadiness
    value_count: int = 0
    withheld: list[str] = field(default_factory=list)
    """Attributes present on the record but withheld because they are not publishable.

    Reported rather than silently dropped: a merchandiser needs to know the feed is thinner
    than the record, and why.
    """

    @property
    def published(self) -> bool:
        return self.payload is not None

    def summary(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "published": self.published,
            "values": self.value_count,
            "withheld": self.withheld,
            "content_hash": self.content_hash[:12] + "…" if self.content_hash else None,
            "ready": self.readiness.ready,
            "missing": self.readiness.missing,
            "not_publishable": self.readiness.not_publishable,
        }


class Exporter(Protocol):
    name: str

    def export(
        self, record: ProductRecord, registry: SchemaRegistry, *, force: bool = False
    ) -> ExportResult: ...


def _content_hash(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _withheld_codes(record: ProductRecord) -> list[str]:
    return sorted(
        v.attribute_code for v in record.current_values() if not v.is_publishable
    )


def _render_value(value) -> tuple[object, str | None]:
    """Split a canonical value into (magnitude-or-value, unit).

    Channels want the number and the unit in separate fields so they can be filtered on. A
    single ``"600 psi"`` string is human-readable and useless for faceting.
    """
    canonical = value.value_canonical
    if isinstance(canonical, Quantity):
        return canonical.magnitude, canonical.unit
    if isinstance(canonical, ValueRange):
        return f"{canonical.minimum:g}..{canonical.maximum:g}", canonical.unit
    if isinstance(canonical, list):
        return list(canonical), None
    return canonical, None


class Cx1PimExporter:
    """Flat, import-ready record for a distributor PIM."""

    name = "cx1_pim"

    def export(
        self, record: ProductRecord, registry: SchemaRegistry, *, force: bool = False
    ) -> ExportResult:
        readiness = preflight(record, registry, self.name)
        if not readiness.ready and not force:
            return ExportResult(self.name, None, None, readiness, withheld=_withheld_codes(record))

        profile = (
            registry.product_class(record.class_code).channel(self.name)
            if record.class_code
            else None
        )
        values = publishable_values(record)

        attributes = []
        for value in sorted(values, key=lambda v: v.attribute_code):
            definition = _definition_or_none(registry, value.attribute_code)
            magnitude, unit = _render_value(value)
            attributes.append(
                {
                    "code": value.attribute_code,
                    "name": definition.name if definition else value.attribute_code,
                    "value": magnitude,
                    "display": value.value_display,
                    "unit": unit,
                    # Provenance travels with the data. A PIM that receives values without
                    # knowing which were human-approved cannot make its own trust decisions.
                    "source": value.method.value,
                    "confidence": round(value.confidence, 3),
                    "reviewed": value.reviewed_by is not None,
                }
            )

        title = render_title(record, registry, profile) if profile else None
        if title and profile:
            title = truncate_title(title, profile.max_title_chars)

        internal = record.classification(ClassificationScheme.INTERNAL)
        payload = {
            "sku": record.sku,
            "mpn": record.mpn_normalized or record.mpn,
            "brand": record.brand,
            "gtin": record.gtin,
            "supplier_id": record.supplier_id,
            "title": title,
            "class_code": record.class_code,
            "category_path": list(internal.path) if internal else [],
            "schema_version": record.schema_version,
            "classifications": [
                {"scheme": c.scheme.value, "code": c.code, "confidence": round(c.confidence, 3)}
                for c in record.classifications
                if c.is_publishable
            ],
            "attributes": attributes,
            "gaps": [
                {"code": g.attribute_code, "reason": g.reason.value, "required": g.is_required}
                for g in record.gaps
            ],
        }
        rendered = json.dumps(payload, indent=2, default=str, sort_keys=False)
        return ExportResult(
            channel=self.name,
            payload=rendered,
            content_hash=_content_hash(rendered),
            readiness=readiness,
            value_count=len(attributes),
            withheld=_withheld_codes(record),
        )

    def to_csv(self, record: ProductRecord, registry: SchemaRegistry) -> str:
        """Wide-format CSV, for PIMs that ingest spreadsheets rather than JSON."""
        values = {v.attribute_code: v for v in publishable_values(record)}
        codes = sorted(values)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["sku", "mpn", "brand", "class_code", *codes])
        writer.writerow(
            [
                record.sku,
                record.mpn_normalized or record.mpn or "",
                record.brand or "",
                record.class_code or "",
                *[values[c].value_display or "" for c in codes],
            ]
        )
        return buffer.getvalue()


class SchemaOrgExporter:
    """schema.org ``Product`` as JSON-LD."""

    name = "schema_org"

    def export(
        self, record: ProductRecord, registry: SchemaRegistry, *, force: bool = False
    ) -> ExportResult:
        readiness = preflight(record, registry, self.name)
        if not readiness.ready and not force:
            return ExportResult(self.name, None, None, readiness, withheld=_withheld_codes(record))

        values = publishable_values(record)
        properties = []
        for value in sorted(values, key=lambda v: v.attribute_code):
            definition = _definition_or_none(registry, value.attribute_code)
            magnitude, unit = _render_value(value)
            entry: dict[str, object] = {
                "@type": "PropertyValue",
                "propertyID": value.attribute_code,
                "name": definition.name if definition else value.attribute_code,
                "value": magnitude,
            }
            if unit:
                # unitText rather than unitCode: our canonical units are UCUM-style codes, not
                # the UN/CEFACT codes unitCode expects, and mislabelling them would be worse
                # than using the free-text field honestly.
                entry["unitText"] = unit
            properties.append(entry)

        internal = record.classification(ClassificationScheme.INTERNAL)
        payload: dict[str, object] = {
            "@context": "https://schema.org",
            "@type": "Product",
            "sku": record.sku,
            "name": self._name_for(record, registry),
        }
        if record.mpn_normalized or record.mpn:
            payload["mpn"] = record.mpn_normalized or record.mpn
        if record.brand:
            payload["brand"] = {"@type": "Brand", "name": record.brand}
        if record.gtin:
            payload["gtin"] = record.gtin
        if internal and internal.path:
            payload["category"] = " > ".join(internal.path)
        payload["additionalProperty"] = properties

        rendered = json.dumps(payload, indent=2, default=str)
        return ExportResult(
            channel=self.name,
            payload=rendered,
            content_hash=_content_hash(rendered),
            readiness=readiness,
            value_count=len(properties),
            withheld=_withheld_codes(record),
        )

    @staticmethod
    def _name_for(record: ProductRecord, registry: SchemaRegistry) -> str:
        """Product name, falling back until something identifying is found.

        A ``Product`` with no name is invalid JSON-LD, so something must always be produced.
        But the fallback has to *identify the product*: when a title template loses its brand,
        MPN and spec tokens, all that survives is the class name — and a name that reads
        "Two-Piece Ball Valve" on ten thousand SKUs is a duplicate-content signal and useless
        for disambiguating a search result. In that case the SKU, though ugly, is more honest
        and more useful.
        """
        for channel in ("schema_org", "cx1_pim"):
            profile = (
                registry.product_class(record.class_code).channel(channel)
                if record.class_code
                else None
            )
            if profile is None:
                continue
            title = render_title(record, registry, profile)
            if title and _identifies_product(title, record):
                return truncate_title(title, profile.max_title_chars)
        return record.sku


def _identifies_product(title: str, record: ProductRecord) -> bool:
    """Whether a rendered title distinguishes this product from its classmates."""
    folded = title.casefold()
    candidates = (record.sku, record.mpn, record.mpn_normalized, record.brand)
    return any(c and c.casefold() in folded for c in candidates)


def _definition_or_none(registry: SchemaRegistry, code: str):
    try:
        return registry.attribute(code)
    except KeyError:
        return None


EXPORTERS: dict[str, Exporter] = {
    Cx1PimExporter.name: Cx1PimExporter(),
    SchemaOrgExporter.name: SchemaOrgExporter(),
}


def export_all(
    record: ProductRecord, registry: SchemaRegistry, *, force: bool = False
) -> dict[str, ExportResult]:
    """Run every registered exporter. One canonical record, many destinations."""
    return {
        name: exporter.export(record, registry, force=force)
        for name, exporter in EXPORTERS.items()
    }
