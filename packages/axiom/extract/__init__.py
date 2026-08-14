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

# Two model-free paths and the model path all mint AttributeValues, so each has its own
# ``PROMPT_VERSION`` and its own ``to_attribute_values``. Re-exporting them here requires aliases:
# the names collide, and the collision is meaningful — a value's provenance has to say which
# path produced it. The prefixed spellings are the package-level API; the unprefixed originals
# stay reachable on the submodules.
from axiom.extract.description import (
    ABBREVIATION_CONFIDENCE,
    MIN_TOKEN_LENGTH,
    QUANTITY_CONFIDENCE,
    SAFE_BARE_UNITS,
    AbbreviationTable,
    DescriptionExtraction,
    DescriptionMatch,
    default_abbreviation_path,
    extract_from_description,
)
from axiom.extract.description import (
    PROMPT_VERSION as DESCRIPTION_PROMPT_VERSION,
)
from axiom.extract.description import (
    to_attribute_values as description_values,
)
from axiom.extract.extractor import ExtractionResult, Extractor
from axiom.extract.grammar import (
    Conflict,
    GrammarReading,
    Observation,
    PartNumberGrammar,
    Prediction,
    RejectedRule,
    RejectionReason,
    RuleKind,
    Segment,
    SegmentKind,
    SegmentRule,
    induce,
    segment,
    shape,
)
from axiom.extract.pricing import ModelPrice, PriceTable, load_prices
from axiom.extract.structured import (
    MAX_LABEL_CHARS,
    MAX_VALUE_WORDS,
    MIN_SPACE_RUN,
    ORDERING_TABLE,
    SPEC_LINE,
    SPEC_LINE_CONFIDENCE,
    TABLE_CELL_CONFIDENCE,
    LabelBinding,
    StructuredExtraction,
    StructuredMatch,
    contested_labels,
    extract_structured,
    label_lookup,
    reject_unresolved,
    size_qualifier,
    split_spec_line,
    verify_quotes,
)
from axiom.extract.structured import (
    PROMPT_VERSION as STRUCTURED_PROMPT_VERSION,
)
from axiom.extract.structured import (
    to_attribute_values as structured_values,
)
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
    "ABBREVIATION_CONFIDENCE",
    "CASCADE_ORDER",
    "DESCRIPTION_PROMPT_VERSION",
    "MAX_LABEL_CHARS",
    "MAX_VALUE_WORDS",
    "MIN_SPACE_RUN",
    "MIN_TOKEN_LENGTH",
    "ORDERING_TABLE",
    "QUANTITY_CONFIDENCE",
    "SAFE_BARE_UNITS",
    "SKU_COLUMN_HEADERS",
    "SPEC_LINE",
    "SPEC_LINE_CONFIDENCE",
    "STRUCTURED_PROMPT_VERSION",
    "TABLE_CELL_CONFIDENCE",
    "AbbreviationTable",
    "BedrockModelClient",
    "Certainty",
    "Conflict",
    "ContractError",
    "ContractItem",
    "DescriptionExtraction",
    "DescriptionMatch",
    "ExtractionResult",
    "Extractor",
    "GrammarReading",
    "LabelBinding",
    "ModelCascade",
    "ModelClient",
    "ModelError",
    "ModelPrice",
    "ModelResponse",
    "Observation",
    "PartNumberGrammar",
    "Prediction",
    "PriceTable",
    "RejectedRule",
    "RejectionReason",
    "RuleKind",
    "Segment",
    "SegmentKind",
    "SegmentRule",
    "StructuredExtraction",
    "StructuredMatch",
    "StubModelClient",
    "UsageLedger",
    "VariantColumn",
    "VariantRow",
    "VariantTable",
    "classify_abstention",
    "contested_labels",
    "default_abbreviation_path",
    "description_values",
    "detect_variant_table",
    "explode",
    "extract_from_description",
    "extract_structured",
    "induce",
    "invoke_with_cascade",
    "is_size_scoped",
    "label_lookup",
    "load_prices",
    "parse_contract",
    "reject_unresolved",
    "segment",
    "shape",
    "size_qualifier",
    "split_spec_line",
    "structured_values",
    "verify_quotes",
]
