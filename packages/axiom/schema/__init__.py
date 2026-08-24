"""Schema registry: the governed definition of what a complete product looks like.

The YAML under ``schema/`` is the source of truth. This package loads it, checks it for
internal consistency, and renders extraction prompts from it.
"""

from axiom.schema.models import (
    AllowedValue,
    AttributeBinding,
    AttributeDefinition,
    ChannelProfile,
    ClassDefinition,
    CrossFieldRule,
    Datatype,
    EvidenceRequirement,
    Requirement,
    Severity,
)
from axiom.schema.prompts import (
    PROMPT_VERSION,
    ExtractionPrompt,
    build_extraction_prompt,
    build_output_schema,
    build_specification_extraction_prompt,
)
from axiom.schema.registry import (
    SchemaIntegrityError,
    SchemaRegistry,
    default_schema_root,
    load_default,
)

__all__ = [
    "PROMPT_VERSION",
    "AllowedValue",
    "AttributeBinding",
    "AttributeDefinition",
    "ChannelProfile",
    "ClassDefinition",
    "CrossFieldRule",
    "Datatype",
    "EvidenceRequirement",
    "ExtractionPrompt",
    "Requirement",
    "SchemaIntegrityError",
    "SchemaRegistry",
    "Severity",
    "build_extraction_prompt",
    "build_output_schema",
    "build_specification_extraction_prompt",
    "default_schema_root",
    "load_default",
]
