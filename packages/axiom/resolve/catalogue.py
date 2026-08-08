"""Assembling a comparable catalogue for the cross-reference report.

Equivalence compares :class:`~axiom.core.product.ProductRecord` objects, and there are two places
a record can come from. They support very different claims, so the source travels with the report
rather than being left for a reader to assume:

*   :func:`records_from_bundles` — real pipeline output. Every value was extracted from a document,
    carries a verified citation, and passed the acceptance policy. This is what a production
    cross-reference would run on, and there are only ever as many SKUs as have been through the
    pipeline.
*   :func:`records_from_golden` — the ground-truth corpus, read as records. Fifteen SKUs across
    three manufacturers, which is the only set here large enough to make a ranked substitute list
    mean anything. But the values are hand-authored, so a report built on them measures **the
    comparison logic**, not the extraction that would feed it in production.

That second caveat is the same one the committed L4 artifact carries, and it is handled the same
way: :class:`CatalogueSource` travels in the payload so the console and the CLI can say so in
prose instead of letting a reader assume the numbers were measured end to end.

Golden values are marked :attr:`~axiom.core.values.DerivationMethod.HUMAN_ENTRY` and
``HUMAN_APPROVED``, which is both accurate and the only honest way to make them publishable. A
named human really is the source of the golden set, and that family is publishable without an
evidence span precisely because an accountable person stands behind it. Marking them as
extractions to force them through would be a lie the type system would have caught anyway.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.normalize import normalize_value
from axiom.schema.registry import SchemaRegistry


class CatalogueSource(str, Enum):
    """Where the records being compared came from. Determines what the report may claim."""

    PIPELINE = "pipeline"
    """Console bundles written by run_pipeline.py. Extracted, cited, policy-accepted."""

    GOLDEN = "golden"
    """The ground-truth corpus. Hand-authored, so it exercises the comparison and not the
    extraction."""

    @property
    def is_measured(self) -> bool:
        """Whether a verdict from this source describes the system end to end."""
        return self is CatalogueSource.PIPELINE

    @property
    def note(self) -> str:
        if self is CatalogueSource.PIPELINE:
            return (
                "Records are real pipeline output: every value was extracted from a source "
                "document, carries a verified citation, and passed the acceptance policy."
            )
        return (
            "Records are read from the hand-authored golden set, so these verdicts exercise the "
            "comparison logic rather than the extraction that would supply it in production. The "
            "corpus is used because it is the only set here wide enough to rank substitutes "
            "across manufacturers."
        )


@dataclass
class Catalogue:
    """The records a cross-reference runs over, with the provenance of the set."""

    source: CatalogueSource
    records: list[ProductRecord] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)

    def get(self, sku: str) -> ProductRecord | None:
        return next((r for r in self.records if r.sku == sku), None)

    def skus(self) -> list[str]:
        return [r.sku for r in self.records]

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "measured": self.source.is_measured,
            "source_note": self.source.note,
            "records": len(self.records),
            "skus": self.skus(),
            "failures": list(self.failures),
        }


def records_from_bundles(
    directory: Path, registry: SchemaRegistry, *, tenant_id: str = "demo"
) -> Catalogue:
    """Rebuild records from console bundles on disk.

    Only values the bundle marks publishable are kept. That flag is the pipeline's own verdict
    under the risk policy in force at the time, and recomputing it here against a newer policy
    would let a substitution rest on a value the run that produced it declined to publish.
    """
    catalogue = Catalogue(source=CatalogueSource.PIPELINE)
    if not directory.is_dir():
        return catalogue

    for path in sorted(directory.glob("*.bundle.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))["bundle"]
        except (OSError, ValueError, KeyError) as exc:
            catalogue.failures.append(f"{path.name}: unreadable ({exc})")
            continue

        try:
            catalogue.records.append(_record_from_bundle(bundle, registry, tenant_id=tenant_id))
        except (KeyError, TypeError, ValueError) as exc:
            catalogue.failures.append(f"{path.name}: could not rebuild a record ({exc})")

    return catalogue


def _record_from_bundle(
    bundle: Mapping[str, object], registry: SchemaRegistry, *, tenant_id: str
) -> ProductRecord:
    stored = bundle.get("record") or {}
    if not isinstance(stored, Mapping):
        raise TypeError("bundle 'record' is not an object")

    record = ProductRecord(
        tenant_id=str(stored.get("tenant_id") or tenant_id),
        sku=str(bundle["sku"]),
        mpn=_optional_str(stored.get("mpn")),
        brand=_optional_str(stored.get("brand")),
        supplier_id=_optional_str(stored.get("supplier_id")),
        parent_sku=_optional_str(stored.get("parent_sku")),
        class_code=_optional_str(bundle.get("class_code")),
        schema_version=_optional_str(stored.get("schema_version")),
    )

    for raw in bundle.get("values") or ():
        if not isinstance(raw, Mapping) or not raw.get("is_publishable"):
            continue
        try:
            value = AttributeValue.model_validate(dict(raw))
        except ValueError:
            # A value the current model cannot express is skipped rather than fatal: the bundle
            # may predate a schema change, and one stale attribute must not void a whole record.
            continue
        if value.is_publishable:
            record.add_value(value)

    return record


def records_from_golden(registry: SchemaRegistry, *, name: str | None = None) -> Catalogue:
    """Read the ground-truth corpus as publishable records.

    ``axiom.evaluation`` is imported inside the function rather than at module scope. Importing it
    up top would pull the whole backtest harness — and through it the model cascade — into every
    consumer of ``axiom.resolve``, which needs neither. The same function-local import trick
    ``axiom.schema.registry`` uses to reach the unit tables without inverting the layering.
    """
    from axiom.evaluation.golden import GoldenSet

    golden = GoldenSet.load_default(name) if name else GoldenSet.load_default()
    catalogue = Catalogue(source=CatalogueSource.GOLDEN)

    for product in golden.products:
        record = ProductRecord(
            tenant_id="demo",
            sku=product.sku,
            brand=product.brand,
            supplier_id=product.supplier_id,
            class_code=product.class_code,
        )
        for code, raw in product.attributes.items():
            try:
                definition = registry.attribute(code)
            except KeyError:
                catalogue.failures.append(
                    f"{product.sku}: golden set names attribute '{code}', which the schema does "
                    f"not define"
                )
                continue
            value = _human_value(code, raw, definition)
            if value is None:
                catalogue.failures.append(
                    f"{product.sku}: golden value {raw!r} for '{code}' could not be normalised"
                )
                continue
            record.add_value(value)
        catalogue.records.append(record)

    return catalogue


def _human_value(code: str, raw: str, definition) -> AttributeValue | None:
    """Normalise a source-form ground-truth string into a publishable human-entered value."""
    placeholder = AttributeValue(
        attribute_code=code,
        value_raw=raw,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
        status=ValueStatus.HUMAN_APPROVED,
    )
    outcome = normalize_value(placeholder, definition)
    if not outcome.normalized or outcome.value.value_canonical is None:
        return None
    return outcome.value


def load_catalogue(
    registry: SchemaRegistry,
    *,
    bundles: Path | None = None,
    prefer_bundles: bool = False,
) -> Catalogue:
    """Pick a catalogue, preferring real pipeline output when asked for it.

    Falls back to the golden corpus with the reason recorded, rather than returning an empty
    catalogue: "only two SKUs have been through the pipeline" is a fact about this repository, not
    an error, and the report should still be demonstrable on a fresh clone.
    """
    if prefer_bundles and bundles is not None:
        catalogue = records_from_bundles(bundles, registry)
        if len(catalogue) >= 2:
            return catalogue
        fallback = records_from_golden(registry)
        fallback.failures.extend(catalogue.failures)
        fallback.failures.append(
            f"only {len(catalogue)} pipeline bundle(s) available in {bundles}, which is too few "
            f"to cross-reference; fell back to the golden corpus"
        )
        return fallback
    return records_from_golden(registry)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def by_class(records: Iterable[ProductRecord]) -> dict[str | None, list[ProductRecord]]:
    grouped: dict[str | None, list[ProductRecord]] = {}
    for record in records:
        grouped.setdefault(record.class_code, []).append(record)
    return grouped
