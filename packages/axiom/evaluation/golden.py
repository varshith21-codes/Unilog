"""The golden set: ground truth, and the masking that makes it a benchmark.

Ground truth is recorded in **source form** — the string a human reads off the datasheet — and
then normalised through the same pipeline the extractor's output goes through. That choice has
a consequence worth being explicit about: the backtest measures *extraction*, not
normalisation, because a normaliser bug would corrupt both sides identically and cancel out.
Normalisation is covered separately by its own unit tests, where a wrong conversion factor
fails loudly instead of hiding.

The other half is ``absent``: attributes the source genuinely does not state. Without those,
abstention cannot be scored at all, and a system that fabricates freely would benchmark
identically to one that abstains honestly. They are the most valuable rows in the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parents[3] / "data" / "golden"


@dataclass(frozen=True)
class GoldenProduct:
    """One SKU with known-correct values and known-absent attributes."""

    sku: str
    class_code: str
    source: str
    attributes: dict[str, str] = field(default_factory=dict)
    absent: tuple[str, ...] = ()
    supplier_id: str | None = None
    brand: str | None = None
    notes: str | None = None

    @property
    def covered_codes(self) -> tuple[str, ...]:
        """Every attribute this record can score — present and absent alike."""
        return (*self.attributes.keys(), *self.absent)

    def expected_raw(self, code: str) -> str | None:
        """Source-form ground truth, or None when the attribute is known absent."""
        return self.attributes.get(code)


@dataclass
class GoldenSet:
    """A named collection of ground-truth products."""

    name: str
    products: list[GoldenProduct] = field(default_factory=list)
    documents: dict[str, Path] = field(default_factory=dict)
    description: str | None = None

    def __len__(self) -> int:
        return len(self.products)

    @property
    def comparison_count(self) -> int:
        """Total (sku, attribute) judgements this set can produce."""
        return sum(len(p.covered_codes) for p in self.products)

    @property
    def absent_count(self) -> int:
        return sum(len(p.absent) for p in self.products)

    def document_for(self, product: GoldenProduct) -> Path:
        try:
            return self.documents[product.source]
        except KeyError as exc:
            raise KeyError(
                f"product {product.sku} references source '{product.source}', which the "
                f"golden set does not declare"
            ) from exc

    def by_class(self, class_code: str) -> list[GoldenProduct]:
        return [p for p in self.products if p.class_code == class_code]

    @classmethod
    def load(cls, path: Path | str) -> GoldenSet:
        source = Path(path)
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        root = source.parent

        documents: dict[str, Path] = {}
        defaults: dict[str, dict[str, str | None]] = {}
        for entry in payload.get("source_documents", []):
            key = entry["id"]
            documents[key] = (root / entry["path"]).resolve()
            defaults[key] = {
                "supplier_id": entry.get("supplier_id"),
                "brand": entry.get("brand"),
            }

        products: list[GoldenProduct] = []
        for entry in payload.get("products", []):
            source_id = entry["source"]
            document_defaults = defaults.get(source_id, {})
            products.append(
                GoldenProduct(
                    sku=entry["sku"],
                    class_code=entry["class_code"],
                    source=source_id,
                    attributes={k: str(v) for k, v in (entry.get("attributes") or {}).items()},
                    absent=tuple(entry.get("absent") or ()),
                    supplier_id=entry.get("supplier_id") or document_defaults.get("supplier_id"),
                    brand=entry.get("brand") or document_defaults.get("brand"),
                    notes=entry.get("notes"),
                )
            )

        missing = {p.source for p in products} - set(documents)
        if missing:
            raise ValueError(
                f"golden set '{payload.get('name')}' references undeclared source documents: "
                f"{sorted(missing)}"
            )
        absent_paths = [str(p) for p in documents.values() if not p.is_file()]
        if absent_paths:
            raise FileNotFoundError(f"golden set source files not found: {absent_paths}")

        return cls(
            name=payload.get("name", source.stem),
            products=products,
            documents=documents,
            description=payload.get("description"),
        )

    @classmethod
    def load_default(cls, name: str = "pvf_valves.yaml") -> GoldenSet:
        return cls.load(DEFAULT_GOLDEN_PATH / name)


def mask(product: GoldenProduct) -> dict[str, str]:
    """The 'before' state: what an ERP row looks like with the specs stripped out.

    Masked-attribute backtesting works by hiding values that are known to be correct and
    measuring how many the pipeline recovers from the source documents alone. This returns only
    the identifying fields that a real item master would carry, so extraction cannot cheat by
    reading the answer out of its own input.
    """
    return {
        "sku": product.sku,
        "class_code": product.class_code,
        "brand": product.brand or "",
    }
