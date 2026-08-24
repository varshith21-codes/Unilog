"""Extraction prompts, generated from the schema.

Prompts are **assembled from the attribute dictionary**, never hand-written per attribute.
That is what makes the scalability claim real: adding an attribute to the YAML changes the
prompt with no code edit and no prompt engineering.

The prompt is deliberately split into a cacheable prefix and a volatile suffix. The system
instructions, the schema block and the demonstrations are identical for every SKU in a
class, so putting them first and the source content last maximises the prompt-cache hit
rate. With a per-class schema block reused across thousands of SKUs that is close to free
money — see blueprint Part 8.1.

The negative demonstration is not decoration. Showing the model a case where *absence* is
the correct answer measurably reduces fabrication, and abstention is the behaviour this
domain most needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiom.schema.models import AttributeDefinition, ClassDefinition, Requirement
from axiom.schema.registry import SchemaRegistry

PROMPT_VERSION = "extract.v3"
"""Bumped from v2: one response now preserves open-ended manufacturer specifications alongside
schema-bound attributes. The typed channel remains closed; the source-native channel retains every
explicit label/value pair without inventing attribute codes.

Versioning the prompt is not bureaucracy. Every extracted value records the prompt version
that produced it, so when accuracy moves you can attribute it to a specific change instead
of guessing.
"""

SYSTEM_INSTRUCTIONS = """\
You extract product specifications from manufacturer documentation for an industrial \
distributor. You are an extraction system, not an assistant.

ABSOLUTE RULES
1. Only report a value if it is explicitly present in the SOURCE CONTENT. Never infer, \
never estimate, never rely on prior knowledge of this product or brand.
2. Every reported value must include a verbatim quote copied EXACTLY, character for \
character, from the source content, plus the page number where it appears.
3. If a value is not present, set "found": false and give a short reason. This is a \
CORRECT and EXPECTED outcome. Do not guess in order to fill a field.
4. Copy values exactly as written, including units, symbols and qualifiers. Do NOT convert \
units. Do NOT reformat. Normalization happens downstream in deterministic code.
5. When the source defers a value elsewhere ("consult factory", "see chart"), that is not a \
value. Set "found": false.
6. Attributes describing one specific part number must be read from the ordering-table row \
whose part number matches the target SKU, not from a neighbouring row.
7. If the source states conflicting values for the same attribute, report each of them as a \
separate element with its own quote.
8. APPLICABILITY. If a value is qualified as applying to a specific size, model, variant or \
condition — for example "(1/2\" size)", "for 3 inch and larger", "cold water only" — report \
it ONLY when that qualifier matches the target SKU. Otherwise set "found": false and state \
the mismatch in "reason". A value that is present in the document but describes a different \
variant is NOT a value for this SKU, even though you can quote it.
9. MANUFACTURER SPECIFICATIONS. Independently of the requested schema attributes, enumerate EVERY \
explicit product label/value pair that applies to the target SKU. Include identifiers, dimensions, \
materials, construction, compatibility, operating limits, performance, packaging, approvals and \
manufacturer-specific fields even when no requested attribute represents them. Copy both \
label_raw and value_raw from the source; both must appear in evidence_quote. Do not include \
navigation, contact details, legal boilerplate, or a marketing sentence that states no concrete \
product value.

OUTPUT
Return ONLY one JSON object. No markdown fences, no commentary:
{
  "attributes": [
    {"attribute_code": str, "found": bool, "value_raw": str|null,
     "evidence_quote": str|null, "evidence_page": int|null,
     "certainty": "high"|"medium"|"low", "reason": str|null}
  ],
  "manufacturer_specifications": [
    {"label_raw": str, "value_raw": str, "evidence_quote": str,
     "evidence_page": int|null, "certainty": "high"|"medium"|"low"}
  ]
}
Return one attributes element for every requested attribute. Return only specifications that are \
present; do not create found=false specification rows. A known attribute should ALSO appear in \
manufacturer_specifications when the source prints it, because that channel is the complete \
source-native account rather than only an overflow bucket.
"""

NEGATIVE_DEMONSTRATION = """\
EXAMPLE — absence is a correct answer
  Source excerpt: "Flow Coefficient (Cv) .......... Consult factory"
  Correct output element:
    {"attribute_code": "cv_flow_coefficient", "found": false, "value_raw": null,
     "evidence_quote": null, "evidence_page": null, "certainty": "high",
     "reason": "deferred to factory; no value stated in source"}
"""


@dataclass(frozen=True)
class ExtractionPrompt:
    """A prompt split at the caching boundary."""

    system: str
    cacheable_prefix: str
    volatile_suffix: str
    prompt_version: str
    schema_version: str
    attribute_codes: tuple[str, ...]

    @property
    def user_message(self) -> str:
        return f"{self.cacheable_prefix}\n\n{self.volatile_suffix}"

    def cache_key(self) -> str:
        """Identifies the reusable portion — same for every SKU in the class."""
        return f"{self.prompt_version}:{self.schema_version}"


def build_extraction_prompt(
    registry: SchemaRegistry,
    class_code: str,
    *,
    source_content: str,
    target_sku: str,
    source_name: str | None = None,
    include_recommended: bool = True,
    include_optional: bool = False,
    only_codes: tuple[str, ...] | None = None,
) -> ExtractionPrompt:
    """Assemble an extraction prompt for one class.

    ``only_codes`` narrows the request to specific attributes, which is how targeted gap
    fill works: when you already know which field is missing, asking a narrow question is
    both cheaper and more accurate than re-extracting everything.

    ``include_optional`` is off by default because optional attributes cost tokens on every
    SKU for a field most SKUs will not have. Turn it on for a deep pass over high-value
    products, or when backtesting coverage.
    """
    definition = registry.product_class(class_code)

    if only_codes:
        # An explicitly named attribute is always included, whatever its requirement level.
        # Targeted gap fill asks for the field it knows is missing, and that field is very
        # often an optional one — silently returning nothing would make the feature useless.
        wanted = set(only_codes)
        attributes = [
            a for a in registry.attributes_for(class_code) if a.code in wanted
        ]
    else:
        requirements = [Requirement.REQUIRED]
        if include_recommended:
            requirements.append(Requirement.RECOMMENDED)
        if include_optional:
            requirements.append(Requirement.OPTIONAL)
        attributes = registry.attributes_for(class_code, *requirements)
    if not attributes:
        raise ValueError(f"no attributes selected for class '{class_code}'")

    prefix = "\n".join(
        [
            _class_header(definition),
            "",
            "ATTRIBUTES TO EXTRACT:",
            *[a.prompt_block() for a in attributes],
            "",
            NEGATIVE_DEMONSTRATION,
        ]
    )
    suffix = "\n".join(
        [
            f"TARGET SKU: {target_sku}",
            f"SOURCE DOCUMENT: {source_name or 'unnamed'}",
            "",
            "SOURCE CONTENT:",
            "<<<",
            source_content,
            ">>>",
        ]
    )
    return ExtractionPrompt(
        system=SYSTEM_INSTRUCTIONS,
        cacheable_prefix=prefix,
        volatile_suffix=suffix,
        prompt_version=PROMPT_VERSION,
        schema_version=definition.schema_version,
        attribute_codes=tuple(a.code for a in attributes),
    )


def build_specification_extraction_prompt(
    *,
    source_content: str,
    target_sku: str,
    source_name: str | None = None,
) -> ExtractionPrompt:
    """Build a source-native pass for a document whose product class is still unknown.

    No typed value can be created without a class definition. The independent specification
    channel does not need one, so it remains useful instead of turning classification abstention
    into an identity-only record.
    """
    prefix = "\n".join(
        [
            "PRODUCT CLASS: Unclassified (source-native specification pass)",
            "",
            "ATTRIBUTES TO EXTRACT:",
            "None. Return an empty attributes array.",
            "",
            "Retain every explicit target-SKU label/value pair in manufacturer_specifications.",
            "",
            NEGATIVE_DEMONSTRATION,
        ]
    )
    suffix = "\n".join(
        [
            f"TARGET SKU: {target_sku}",
            f"SOURCE DOCUMENT: {source_name or 'unnamed'}",
            "",
            "SOURCE CONTENT:",
            "<<<",
            source_content,
            ">>>",
        ]
    )
    return ExtractionPrompt(
        system=SYSTEM_INSTRUCTIONS,
        cacheable_prefix=prefix,
        volatile_suffix=suffix,
        prompt_version=PROMPT_VERSION,
        schema_version="source-native@v1",
        attribute_codes=(),
    )


def _class_header(definition: ClassDefinition) -> str:
    lines = [f"PRODUCT CLASS: {definition.name} ({definition.code})"]
    if definition.browse_path:
        lines.append(f"CATEGORY PATH: {' > '.join(definition.browse_path)}")
    return "\n".join(lines)


def build_output_schema(attributes: list[AttributeDefinition]) -> dict:
    """JSON Schema for providers supporting constrained structured output."""
    attribute_item = {
        "type": "object",
        "required": ["attribute_code", "found"],
        "properties": {
            "attribute_code": {
                "type": "string",
                "enum": [a.code for a in attributes],
            },
            "found": {"type": "boolean"},
            "value_raw": {"type": ["string", "null"]},
            "evidence_quote": {"type": ["string", "null"]},
            "evidence_page": {"type": ["integer", "null"]},
            "certainty": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }
    specification_item = {
        "type": "object",
        "required": ["label_raw", "value_raw", "evidence_quote"],
        "properties": {
            "label_raw": {"type": "string", "minLength": 1},
            "value_raw": {"type": "string", "minLength": 1},
            "evidence_quote": {"type": "string", "minLength": 1},
            "evidence_page": {"type": ["integer", "null"]},
            "certainty": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["attributes", "manufacturer_specifications"],
        "properties": {
            "attributes": {"type": "array", "items": attribute_item},
            "manufacturer_specifications": {
                "type": "array",
                "items": specification_item,
            },
        },
        "additionalProperties": False,
    }
