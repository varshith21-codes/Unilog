"""Schema-driven extraction with a mandatory evidence contract."""

from axiom.extract.client import (
    CASCADE_ORDER,
    BedrockModelClient,
    ModelCascade,
    ModelClient,
    ModelError,
    ModelResponse,
    StubModelClient,
    UsageLedger,
    invoke_with_cascade,
)
from axiom.extract.contract import (
    Certainty,
    ContractError,
    ContractItem,
    classify_abstention,
    parse_contract,
)
from axiom.extract.extractor import ExtractionResult, Extractor
from axiom.extract.pricing import ModelPrice, PriceTable, load_prices
from axiom.extract.variants import (
    SKU_COLUMN_HEADERS,
    VariantColumn,
    VariantRow,
    VariantTable,
    detect_variant_table,
    explode,
    is_size_scoped,
)

__all__ = [
    "CASCADE_ORDER",
    "BedrockModelClient",
    "Certainty",
    "ContractError",
    "ContractItem",
    "ExtractionResult",
    "Extractor",
    "ModelCascade",
    "ModelClient",
    "ModelError",
    "SKU_COLUMN_HEADERS",
    "ModelPrice",
    "ModelResponse",
    "PriceTable",
    "StubModelClient",
    "UsageLedger",
    "VariantColumn",
    "VariantRow",
    "VariantTable",
    "classify_abstention",
    "detect_variant_table",
    "explode",
    "invoke_with_cascade",
    "is_size_scoped",
    "load_prices",
    "parse_contract",
]
