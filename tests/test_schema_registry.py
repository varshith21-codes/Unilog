"""Tests for the schema registry.

The negative tests matter more than the positive ones. The registry's whole purpose is to
turn latent runtime bugs — a mistyped unit, a rule that can never fire, a channel that can
never publish — into a loud failure at load time. If those tests do not exist, the guarantee
does not exist either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from axiom.schema import (
    Datatype,
    EvidenceRequirement,
    Requirement,
    SchemaIntegrityError,
    SchemaRegistry,
    build_extraction_prompt,
    build_output_schema,
    load_default,
)

BALL_VALVE = "PLB.VLV.BALL.2PC"
GATE_VALVE = "PLB.VLV.GATE.BRZ"


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return load_default()


# --------------------------------------------------------------- the shipped schema loads


def test_default_schema_loads_and_is_internally_consistent(registry: SchemaRegistry):
    """If this fails, every downstream layer is built on sand."""
    # Sorted, so the appliance class leads. Asserted as a set membership plus a count rather
    # than an exact list: the shipped schema grows as categories are added, and a test that
    # has to be edited for every new class stops being a signal.
    assert {BALL_VALVE, GATE_VALVE} <= set(registry.class_codes)
    assert "APP.KIT.DISHWASHER.BUILTIN" in registry.class_codes
    assert len(registry.attribute_codes) >= 20


def test_both_classes_reuse_the_shared_dictionary(registry: SchemaRegistry):
    """The point of separating attributes from classes: definitions are shared, not copied."""
    ball = set(registry.product_class(BALL_VALVE).codes())
    gate = set(registry.product_class(GATE_VALVE).codes())
    shared = ball & gate
    assert len(shared) >= 15, "the two classes should share most of their attributes"
    # and the shared ones are literally the same object, not a copy
    for code in shared:
        assert registry.attribute(code) is registry.attribute(code)


def test_classes_differ_in_requirement_levels_not_definitions(registry: SchemaRegistry):
    """A gate valve must state its steam rating; a ball valve need not."""
    ball = registry.product_class(BALL_VALVE)
    gate = registry.product_class(GATE_VALVE)
    assert ball.binding("steam_pressure_rating").requirement is Requirement.OPTIONAL
    assert gate.binding("steam_pressure_rating").requirement is Requirement.REQUIRED
    # port_type is meaningless for a gate valve, so it is simply not bound
    assert gate.binding("port_type") is None
    assert ball.binding("port_type") is not None


def test_every_declared_unit_resolves_in_the_unit_registry(registry: SchemaRegistry):
    """Guards against the failure this check exists for: a silently mistyped unit."""
    from axiom.normalize import registry as units

    for code in registry.attribute_codes:
        attr = registry.attribute(code)
        if attr.canonical_unit:
            resolved = units.resolve(attr.canonical_unit)
            assert resolved is not None, f"{code}: bad canonical_unit"
            if attr.quantity_kind:
                assert resolved.kind.value == attr.quantity_kind, f"{code}: kind mismatch"


def test_compliance_attributes_all_require_strict_evidence(registry: SchemaRegistry):
    """A legal claim may never rest on inference. Enforced by the model, asserted here."""
    compliance = registry.compliance_attributes(BALL_VALVE)
    assert {a.code for a in compliance} == {
        "lead_free_compliant",
        "potable_water_approved",
        "approvals",
        "country_of_origin",
    }
    for attr in compliance:
        assert attr.evidence_requirement is EvidenceRequirement.STRICT


# --------------------------------------------------------------- enum alias snapping


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SS316", "Stainless Steel 316"),
        ("316 ss", "Stainless Steel 316"),
        ("A351 CF8M", "Stainless Steel 316"),
        ("C84400", "Bronze C84400"),
        ("leaded red brass", "Bronze C84400"),
        ("free-cutting brass", "Brass C36000"),
    ],
)
def test_material_aliases_snap_deterministically(registry: SchemaRegistry, raw, expected):
    assert registry.attribute("body_material").resolve_allowed(raw) == expected


def test_unknown_enum_value_returns_none_rather_than_guessing(registry: SchemaRegistry):
    assert registry.attribute("body_material").resolve_allowed("unobtainium") is None


def test_npt_and_bspt_are_separate_enum_values(registry: SchemaRegistry):
    """The schema must not collapse two incompatible thread standards into one value."""
    end = registry.attribute("end_connection")
    assert end.resolve_allowed("FNPT") == "NPT Threaded"
    assert end.resolve_allowed("R thread") == "BSPT Threaded"
    assert end.resolve_allowed("FNPT") != end.resolve_allowed("R thread")


# --------------------------------------------------------------- weighted completeness


def test_completeness_is_weighted_not_a_flat_count(registry: SchemaRegistry):
    """Missing a pressure rating must hurt more than missing a handle style."""
    required = registry.required_codes(BALL_VALVE)
    without_pressure = [c for c in required if c != "pressure_rating_wog"]
    without_uom = [c for c in required if c != "selling_uom"]

    score_no_pressure = registry.completeness(BALL_VALVE, without_pressure)
    score_no_uom = registry.completeness(BALL_VALVE, without_uom)

    assert score_no_pressure < score_no_uom, (
        "a weight-3 attribute must cost more than a weight-1 attribute"
    )
    assert registry.completeness(BALL_VALVE, required) == pytest.approx(1.0)
    assert registry.completeness(BALL_VALVE, []) == 0.0


def test_completeness_ignores_non_required_attributes(registry: SchemaRegistry):
    """Recommended attributes must not inflate the completeness denominator."""
    required = registry.required_codes(BALL_VALVE)
    assert registry.completeness(BALL_VALVE, [*required, "operating_torque"]) == pytest.approx(1.0)


# --------------------------------------------------------------- prompt generation


def test_prompt_is_generated_from_the_schema(registry: SchemaRegistry):
    """Adding an attribute to YAML must change the prompt with no code edit."""
    prompt = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="BA-100-075"
    )
    prefix = prompt.cacheable_prefix

    # the description and example values must reach the model
    assert "Maximum non-shock working pressure" in prefix
    assert "600 PSI" in prefix
    # permitted enum values must be enumerated so snapping has a target
    assert "Bronze C84400" in prefix
    # extraction hints must survive
    assert "first column of an ordering table" in prefix
    # strict compliance attributes must carry their warning
    assert "STRICT" in prefix
    # the negative demonstration is what suppresses fabrication
    assert "absence is a correct answer" in prefix
    assert "consult factory" in prefix.lower()
    # open-ended source data is a separate channel, not an invented schema attribute
    assert "manufacturer_specifications" in prompt.system
    assert "EVERY explicit product label/value pair" in prompt.system


def test_prompt_prefix_is_stable_across_skus_so_it_can_be_cached(registry: SchemaRegistry):
    """The caching claim in blueprint Part 8.1, asserted rather than assumed."""
    a = build_extraction_prompt(
        registry, BALL_VALVE, source_content="doc A", target_sku="BA-100-050"
    )
    b = build_extraction_prompt(
        registry, BALL_VALVE, source_content="doc B", target_sku="BA-100-075"
    )
    assert a.cacheable_prefix == b.cacheable_prefix
    assert a.cache_key() == b.cache_key()
    assert a.volatile_suffix != b.volatile_suffix, "SKU and source belong in the suffix"


def test_target_sku_and_source_land_in_the_volatile_suffix(registry: SchemaRegistry):
    prompt = build_extraction_prompt(
        registry,
        BALL_VALVE,
        source_content="SPEC BLOCK",
        target_sku="BA-100-075",
        source_name="milwaukee-ba100.pdf",
    )
    assert "BA-100-075" in prompt.volatile_suffix
    assert "milwaukee-ba100.pdf" in prompt.volatile_suffix
    assert "SPEC BLOCK" in prompt.volatile_suffix


def test_only_codes_narrows_the_request_for_targeted_gap_fill(registry: SchemaRegistry):
    """Gap fill asks a narrow question, which is both cheaper and more accurate."""
    prompt = build_extraction_prompt(
        registry,
        BALL_VALVE,
        source_content="...",
        target_sku="X",
        only_codes=("cv_flow_coefficient",),
    )
    assert prompt.attribute_codes == ("cv_flow_coefficient",)
    assert "cv_flow_coefficient" in prompt.cacheable_prefix
    assert "pressure_rating_wog" not in prompt.cacheable_prefix


def test_only_codes_overrides_requirement_filtering(registry: SchemaRegistry):
    """An explicitly named attribute is included whatever its requirement level.

    ``operating_torque`` is optional on this class. Gap fill that asked for it and silently
    got nothing back would be a trap, since the fields most often missing are exactly the
    ones not marked required.
    """
    prompt = build_extraction_prompt(
        registry,
        BALL_VALVE,
        source_content="...",
        target_sku="X",
        only_codes=("operating_torque",),
    )
    assert prompt.attribute_codes == ("operating_torque",)


def test_required_only_prompt_excludes_recommended(registry: SchemaRegistry):
    prompt = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="X", include_recommended=False
    )
    assert "pressure_rating_wog" in prompt.attribute_codes
    assert "stem_material" not in prompt.attribute_codes


def test_optional_attributes_are_excluded_by_default(registry: SchemaRegistry):
    """Optional fields cost tokens on every SKU for something most SKUs lack."""
    default = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="X"
    )
    deep = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="X", include_optional=True
    )
    assert "operating_torque" not in default.attribute_codes
    assert "operating_torque" in deep.attribute_codes
    assert set(default.attribute_codes) < set(deep.attribute_codes)


# --------------------------------------------------------------- datatype reporting guidance
#
# These assertions exist because of an observed model failure: asked for a boolean
# compliance attribute, the model returned the supporting citation text ("NSF/ANSI 61") as
# the value. Reasonable behaviour given no instruction, and it would have corrupted every
# boolean attribute we have — including the compliance flags.


def test_boolean_attributes_tell_the_model_where_the_citation_goes(registry: SchemaRegistry):
    block = registry.attribute("lead_free_compliant").prompt_block()
    assert '"true" or "false"' in block
    assert "evidence_quote, not in value_raw" in block


def test_multi_enum_asks_for_every_applicable_value(registry: SchemaRegistry):
    block = registry.attribute("approvals").prompt_block()
    assert "comma-separated" in block
    assert "not just the first" in block


def test_range_asks_for_both_bounds(registry: SchemaRegistry):
    block = registry.attribute("temperature_range").prompt_block()
    assert "both bounds" in block
    assert "Do not report only one bound" in block


def test_integer_guidance_excludes_units(registry: SchemaRegistry):
    assert "whole number" in registry.attribute("case_quantity").prompt_block()


def test_quantity_attributes_get_no_reporting_line(registry: SchemaRegistry):
    """Quantities are covered by the global 'do not convert units' rule already."""
    assert "Report as:" not in registry.attribute("pressure_rating_wog").prompt_block()


def test_system_prompt_carries_the_applicability_rule(registry: SchemaRegistry):
    """Regression guard for a real observed failure.

    The model reported an operating torque stated for the 1/2" size when the target SKU was
    3/4". That value is genuinely in the document, so quote verification passes — meaning
    groundedness checking structurally cannot catch it. The only cheap defence is an explicit
    instruction, so it must not be silently dropped from the prompt.
    """
    prompt = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="X"
    )
    assert "APPLICABILITY" in prompt.system
    assert "describes a different variant is NOT a value for this SKU" in prompt.system


def test_prompt_carries_versions_for_reproducibility(registry: SchemaRegistry):
    prompt = build_extraction_prompt(
        registry, BALL_VALVE, source_content="...", target_sku="X"
    )
    assert prompt.prompt_version
    assert prompt.schema_version == f"{BALL_VALVE}@v1"


def test_empty_attribute_selection_is_an_error(registry: SchemaRegistry):
    with pytest.raises(ValueError, match="no attributes selected"):
        build_extraction_prompt(
            registry, BALL_VALVE, source_content="...", target_sku="X", only_codes=("nope",)
        )


def test_output_schema_constrains_attribute_codes(registry: SchemaRegistry):
    attrs = registry.attributes_for(BALL_VALVE, Requirement.REQUIRED)
    schema = build_output_schema(attrs)
    attribute_items = schema["properties"]["attributes"]["items"]
    props = attribute_items["properties"]
    assert set(props["attribute_code"]["enum"]) == {a.code for a in attrs}
    assert attribute_items["additionalProperties"] is False
    assert props["found"]["type"] == "boolean"

    specifications = schema["properties"]["manufacturer_specifications"]
    assert specifications["items"]["required"] == [
        "label_raw",
        "value_raw",
        "evidence_quote",
    ]
    assert schema["additionalProperties"] is False


# --------------------------------------------------------------- integrity checking
#
# These fixtures are built as Python dicts and dumped to YAML rather than assembled by
# string concatenation. Concatenating indented YAML fragments is how you end up debugging
# your test harness instead of your code.


def _attr(code: str, **overrides) -> dict:
    base = {
        "code": code,
        "name": code.replace("_", " ").title(),
        "datatype": "quantity",
        "quantity_kind": "length",
        "canonical_unit": "mm",
        "description": f"The overall {code} of the product as stated by the manufacturer.",
    }
    base.update(overrides)
    return base


def _cls(*, attributes=None, **overrides) -> dict:
    base = {
        "code": "TEST.CLS",
        "name": "Test Class",
        "version": "v1",
        "attributes": attributes or [{"code": "width", "requirement": "required"}],
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, attributes: list[dict], class_def: dict) -> Path:
    root = tmp_path / "schema"
    (root / "attributes").mkdir(parents=True)
    (root / "classes").mkdir(parents=True)
    (root / "attributes" / "a.yaml").write_text(
        yaml.safe_dump({"attributes": attributes}, sort_keys=False), encoding="utf-8"
    )
    (root / "classes" / "c.yaml").write_text(
        yaml.safe_dump({"class": class_def}, sort_keys=False), encoding="utf-8"
    )
    return root


def _load_expecting_problems(tmp_path: Path, attributes: list[dict], class_def: dict) -> list[str]:
    with pytest.raises(SchemaIntegrityError) as exc:
        SchemaRegistry.load(_write(tmp_path, attributes, class_def))
    return exc.value.problems


def test_minimal_valid_schema_loads(tmp_path: Path):
    reg = SchemaRegistry.load(_write(tmp_path, [_attr("width")], _cls()))
    assert reg.required_codes("TEST.CLS") == ["width"]


def test_mistyped_canonical_unit_is_caught_at_load(tmp_path: Path):
    """The check that earns its keep — invisible in YAML review, corrupts every value."""
    problems = _load_expecting_problems(
        tmp_path, [_attr("width", canonical_unit="millimetres")], _cls()
    )
    assert any("does not recognise" in p for p in problems)


def test_quantity_kind_inconsistent_with_unit_is_caught(tmp_path: Path):
    problems = _load_expecting_problems(
        tmp_path, [_attr("width", quantity_kind="pressure")], _cls()
    )
    assert any("but its canonical_unit" in p for p in problems)


def test_bad_unit_hint_is_caught(tmp_path: Path):
    problems = _load_expecting_problems(tmp_path, [_attr("width", unit_hint="cubits")], _cls())
    assert any("unit_hint" in p for p in problems)


def test_inverted_plausible_range_is_caught(tmp_path: Path):
    problems = _load_expecting_problems(
        tmp_path, [_attr("width", plausible_range=[100.0, 1.0])], _cls()
    )
    assert any("min >= max" in p for p in problems)


def test_class_binding_an_unknown_attribute_is_caught(tmp_path: Path):
    class_def = _cls(
        attributes=[
            {"code": "width", "requirement": "required"},
            {"code": "nonexistent", "requirement": "required"},
        ]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert any("unknown attribute 'nonexistent'" in p for p in problems)


def test_rule_referencing_an_unbound_attribute_is_caught(tmp_path: Path):
    """A rule that can never evaluate is worse than no rule; it looks like coverage."""
    class_def = _cls(
        cross_field_rules=[
            {
                "id": "R_X",
                "expr": "height > width",
                "references": ["height", "width"],
                "message": "height must exceed width",
            }
        ]
    )
    problems = _load_expecting_problems(
        tmp_path, [_attr("width"), _attr("height")], class_def
    )
    assert any("does not bind" in p and "R_X" in p for p in problems)


def test_rule_referencing_an_undefined_attribute_is_caught(tmp_path: Path):
    class_def = _cls(
        cross_field_rules=[
            {
                "id": "R_Z",
                "expr": "ghost > width",
                "references": ["ghost"],
                "message": "nonsense",
            }
        ]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert any("unknown attribute 'ghost'" in p for p in problems)


def test_rule_without_declared_references_is_caught(tmp_path: Path):
    class_def = _cls(
        cross_field_rules=[
            {"id": "R_Y", "expr": "width > 0", "message": "width must be positive"}
        ]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert any("declares no references" in p for p in problems)


def test_channel_template_with_unknown_token_is_caught(tmp_path: Path):
    class_def = _cls(
        channel_profiles=[{"name": "somewhere", "title_template": "{brand} {mystery_field}"}]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert any("mystery_field" in p for p in problems)


def test_channel_template_may_use_computed_tokens(tmp_path: Path):
    """brand/mpn/sku come from the product record, not the attribute set."""
    class_def = _cls(
        channel_profiles=[
            {"name": "somewhere", "title_template": "{brand} {mpn} {sku} {width}"}
        ]
    )
    reg = SchemaRegistry.load(_write(tmp_path, [_attr("width")], class_def))
    assert reg.product_class("TEST.CLS").channel("somewhere") is not None


def test_channel_requiring_an_unbound_attribute_is_caught(tmp_path: Path):
    """Publication to that channel could never succeed, so fail now instead."""
    class_def = _cls(channel_profiles=[{"name": "somewhere", "required": ["depth"]}])
    problems = _load_expecting_problems(tmp_path, [_attr("width"), _attr("depth")], class_def)
    assert any("does not bind" in p and "somewhere" in p for p in problems)


def test_duplicate_attribute_definition_is_caught(tmp_path: Path):
    problems = _load_expecting_problems(tmp_path, [_attr("width"), _attr("width")], _cls())
    assert any("already defined" in p for p in problems)


def test_enum_without_allowed_values_is_rejected(tmp_path: Path):
    """And the reported problem must name the actual defect, not just 'validation error'."""
    attrs = [
        {
            "code": "colour",
            "name": "Colour",
            "datatype": "enum",
            "description": "The finish colour of the product as stated by the manufacturer.",
        }
    ]
    class_def = _cls(attributes=[{"code": "colour", "requirement": "required"}])
    problems = _load_expecting_problems(tmp_path, attrs, class_def)
    assert any("allowed_values" in p for p in problems), problems


def test_compliance_claim_without_strict_evidence_is_rejected(tmp_path: Path):
    attrs = [
        {
            "code": "certified",
            "name": "Certified",
            "datatype": "boolean",
            "compliance_claim": True,
            "description": "Whether the product carries the certification in the source.",
        }
    ]
    class_def = _cls(attributes=[{"code": "certified", "requirement": "required"}])
    problems = _load_expecting_problems(tmp_path, attrs, class_def)
    assert any("evidence_requirement: strict" in p for p in problems), problems


def test_tolerance_on_a_non_numeric_attribute_is_rejected(tmp_path: Path):
    attrs = [
        {
            "code": "label",
            "name": "Label",
            "datatype": "string",
            "tolerance": 0.1,
            "description": "The printed label text as stated by the manufacturer.",
        }
    ]
    class_def = _cls(attributes=[{"code": "label", "requirement": "required"}])
    problems = _load_expecting_problems(tmp_path, attrs, class_def)
    assert any("tolerance" in p for p in problems), problems


def test_duplicate_binding_within_a_class_is_rejected(tmp_path: Path):
    class_def = _cls(
        attributes=[
            {"code": "width", "requirement": "required"},
            {"code": "width", "requirement": "optional"},
        ]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert any("duplicate" in p for p in problems), problems


def test_all_problems_are_collected_not_just_the_first(tmp_path: Path):
    """Fixing schema errors one exception at a time is miserable."""
    class_def = _cls(
        attributes=[
            {"code": "width", "requirement": "required"},
            {"code": "ghost_one", "requirement": "required"},
            {"code": "ghost_two", "requirement": "required"},
        ]
    )
    problems = _load_expecting_problems(tmp_path, [_attr("width")], class_def)
    assert len(problems) >= 2
    assert any("ghost_one" in p for p in problems)
    assert any("ghost_two" in p for p in problems)


def test_non_strict_load_returns_a_registry_despite_problems(tmp_path: Path):
    """Useful for tooling that wants to report problems rather than crash."""
    class_def = _cls(
        attributes=[
            {"code": "width", "requirement": "required"},
            {"code": "ghost", "requirement": "required"},
        ]
    )
    reg = SchemaRegistry.load(_write(tmp_path, [_attr("width")], class_def), strict=False)
    assert "width" in reg.attribute_codes


def test_missing_schema_directory_raises_clearly(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="attribute directory"):
        SchemaRegistry.load(tmp_path / "nope")


def test_unknown_class_and_attribute_lookups_raise_clearly(registry: SchemaRegistry):
    with pytest.raises(KeyError, match="unknown product class"):
        registry.product_class("NOPE")
    with pytest.raises(KeyError, match="unknown attribute"):
        registry.attribute("nope")


def test_datatype_helpers():
    assert Datatype.QUANTITY.needs_unit and Datatype.QUANTITY.is_numeric
    assert Datatype.ENUM.is_enumerated and not Datatype.ENUM.needs_unit
    assert Datatype.RANGE.needs_unit
    assert not Datatype.STRING.is_numeric
