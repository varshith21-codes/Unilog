"""Tests for the raw-to-canonical pipeline and brand/MPN unification.

The recurring theme: when the pipeline cannot interpret a value it must say so, not guess and
not silently null it. A wrong enum or a guessed boolean is invisible in review and corrupts a
facet for every product in the class.
"""

from __future__ import annotations

import pytest
from axiom.core.values import AttributeValue, DerivationMethod, Quantity, ValueRange
from axiom.normalize import (
    BrandMaster,
    clean_mpn,
    fold_brand,
    mpn_match_key,
    mpn_variants,
    normalize_all,
    normalize_value,
    to_imperial_fraction,
)
from axiom.schema import load_default


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def brands():
    return BrandMaster.load()


def raw_value(code: str, raw: str) -> AttributeValue:
    """A human-entered value, so no evidence span is required for these unit tests."""
    return AttributeValue(
        attribute_code=code,
        value_raw=raw,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=0.9,
    )


def normalize(registry, code: str, raw: str):
    return normalize_value(raw_value(code, raw), registry.attribute(code))


# --------------------------------------------------------------------- quantities


def test_pressure_is_canonicalised(registry):
    outcome = normalize(registry, "pressure_rating_wog", "600 PSI WOG @ 73 degF")
    assert outcome.normalized
    assert outcome.value.value_canonical == Quantity(magnitude=600.0, unit="psi")
    assert outcome.value.value_raw == "600 PSI WOG @ 73 degF", "raw text must survive"


def test_metric_pressure_converts_to_the_canonical_unit(registry):
    outcome = normalize(registry, "pressure_rating_wog", "40 bar")
    assert outcome.value.value_canonical.unit == "psi"
    assert outcome.value.value_canonical.magnitude == pytest.approx(580.15, rel=1e-3)


def test_qualifiers_are_recorded_not_discarded(registry):
    """A derating reference temperature changes what the number means."""
    outcome = normalize(registry, "pressure_rating_wog", "600 PSI WOG @ 73 degF")
    detail = " ".join(i.detail or "" for i in outcome.issues)
    assert "rating=WOG" in detail
    assert "at=73 degF" in detail


def test_wrong_quantity_kind_is_an_l1_failure(registry):
    """This is why a pressure value cannot end up in a length field."""
    outcome = normalize(registry, "nominal_size", "600 PSI")
    assert not outcome.normalized
    issue = outcome.issues[0]
    assert issue.layer.value == "L1"
    assert issue.rule_id == "quantity_kind_mismatch"


def test_unit_hint_covers_a_bare_number(registry):
    outcome = normalize(registry, "pressure_rating_wog", "600")
    assert outcome.value.value_canonical == Quantity(magnitude=600.0, unit="psi")


def test_unparseable_quantity_reports_a_reason(registry):
    outcome = normalize(registry, "pressure_rating_wog", "consult factory")
    assert not outcome.normalized
    assert "no numeric magnitude" in outcome.issues[0].reason


# --------------------------------------------------------------------- dimensions


def test_fractional_inch_becomes_millimetres(registry):
    outcome = normalize(registry, "nominal_size", '3/4"')
    assert outcome.value.value_canonical == Quantity(magnitude=19.05, unit="mm")


def test_nominal_size_displays_as_an_imperial_fraction(registry):
    """A plumbing buyer searches for 3/4", not 19.05 mm, even though mm is right for storage."""
    outcome = normalize(registry, "nominal_size", '3/4"')
    assert outcome.value.value_display == '3/4"'


def test_metric_input_still_displays_imperially(registry):
    outcome = normalize(registry, "nominal_size", "19.05 mm")
    assert outcome.value.value_canonical.magnitude == pytest.approx(19.05)
    assert outcome.value.value_display == '3/4"'


def test_mixed_number_size(registry):
    outcome = normalize(registry, "nominal_size", '1-1/4"')
    assert outcome.value.value_canonical.magnitude == pytest.approx(31.75)
    assert outcome.value.value_display == '1-1/4"'


@pytest.mark.parametrize(
    ("mm", "expected"),
    [
        (12.7, '1/2"'),
        (19.05, '3/4"'),
        (25.4, '1"'),
        (31.75, '1-1/4"'),
        (50.8, '2"'),
        (63.5, '2-1/2"'),
    ],
)
def test_imperial_fraction_rendering(mm, expected):
    assert to_imperial_fraction(mm) == expected


# --------------------------------------------------------------------- ranges


def test_temperature_range_converts_both_bounds(registry):
    outcome = normalize(registry, "temperature_range", "-20 degF to 366 degF")
    value = outcome.value.value_canonical
    assert isinstance(value, ValueRange)
    assert value.unit == "degC"
    assert value.minimum == pytest.approx(-28.889, abs=1e-2)
    assert value.maximum == pytest.approx(185.556, abs=1e-2)


def test_torque_range_converts(registry):
    outcome = normalize(registry, "operating_torque", "18-22 ft-lb")
    value = outcome.value.value_canonical
    assert value.unit == "N.m"
    assert value.minimum == pytest.approx(24.405, rel=1e-3)


def test_single_value_where_a_range_is_expected_is_flagged_not_rejected(registry):
    """'600 PSI max' bounds one end only; a reviewer should decide, not the pipeline."""
    outcome = normalize(registry, "temperature_range", "366 degF max")
    assert outcome.normalized
    assert outcome.value.value_canonical.minimum == outcome.value.value_canonical.maximum
    assert any(i.rule_id == "range_collapsed" for i in outcome.issues)
    assert not outcome.failed, "a collapsed range is a warning, not a blocking failure"


# --------------------------------------------------------------------- enums


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bronze C84400", "Bronze C84400"),
        ("C84400", "Bronze C84400"),
        ("leaded red brass", "Bronze C84400"),
        ("SS316", "Stainless Steel 316"),
        ("A351 CF8M", "Stainless Steel 316"),
    ],
)
def test_enum_snapping_via_aliases(registry, raw, expected):
    outcome = normalize(registry, "body_material", raw)
    assert outcome.value.value_canonical == expected


def test_unmatched_enum_abstains_rather_than_guessing(registry):
    """Picking the closest value would corrupt a facet invisibly."""
    outcome = normalize(registry, "body_material", "unobtainium alloy")
    assert not outcome.normalized
    assert outcome.issues[0].rule_id == "enum_unmatched"
    assert "does not match any permitted value" in outcome.issues[0].reason


# --- containment matching -----------------------------------------------------------------
#
# Found by running the pipeline: the model extracted "NPT threaded, female both ends"
# verbatim, exactly as the contract instructs, and strict alias matching rejected a correct
# value over phrasing.


def test_qualified_source_text_snaps_to_the_permitted_value(registry):
    outcome = normalize(registry, "end_connection", "NPT threaded, female both ends")
    assert outcome.normalized
    assert outcome.value.value_canonical == "NPT Threaded"


def test_dropped_qualifying_text_is_reported(registry):
    """'female both ends' is real information; discarding it silently would be data loss."""
    outcome = normalize(registry, "end_connection", "NPT threaded, female both ends")
    warning = next(i for i in outcome.issues if i.rule_id == "enum_partial_match")
    assert "additional text" in warning.reason
    assert "female both ends" in (warning.detail or "")


def test_containment_prefers_the_longest_alias(registry):
    """A more specific value must win over one that is merely a prefix of it."""
    outcome = normalize(registry, "handle_type", "supplied with a locking lever handle")
    assert outcome.value.value_canonical == "Locking Lever"


def test_containment_does_not_confuse_incompatible_threads(registry):
    """The distinction this whole schema is careful about must survive containment matching."""
    npt = normalize(registry, "end_connection", "MNPT threaded male ends")
    bspt = normalize(registry, "end_connection", "BSPT threaded taper both ends")
    assert npt.value.value_canonical == "NPT Threaded"
    assert bspt.value.value_canonical == "BSPT Threaded"


def test_containment_still_abstains_on_genuinely_unknown_values(registry):
    """Containment must not become a way to always find something."""
    outcome = normalize(registry, "end_connection", "proprietary quick-lock coupling")
    assert not outcome.normalized


def test_exact_match_reports_no_dropped_text(registry):
    outcome = normalize(registry, "end_connection", "FNPT")
    assert outcome.value.value_canonical == "NPT Threaded"
    assert not any(i.rule_id == "enum_partial_match" for i in outcome.issues)


def test_npt_and_bspt_stay_distinct_through_normalization(registry):
    npt = normalize(registry, "end_connection", "FNPT").value.value_canonical
    bspt = normalize(registry, "end_connection", "R thread").value.value_canonical
    assert npt == "NPT Threaded"
    assert bspt == "BSPT Threaded"
    assert npt != bspt


# --------------------------------------------------------------------- multi enums


def test_multi_enum_resolves_every_token(registry):
    outcome = normalize(registry, "approvals", "UL listed, CSA certified, NSF/ANSI 61")
    assert outcome.value.value_canonical == ["UL", "CSA", "NSF-61"]
    assert outcome.value.value_display == "UL, CSA, NSF-61"


def test_multi_enum_keeps_what_it_can_and_reports_the_rest(registry):
    """An unrecognised certification might be the one that matters, so it must be visible."""
    outcome = normalize(registry, "approvals", "UL, CSA, FooCert 9000")
    assert outcome.value.value_canonical == ["UL", "CSA"]
    warning = next(i for i in outcome.issues if i.rule_id == "multi_enum_partial")
    assert "FooCert 9000" in warning.reason


def test_multi_enum_deduplicates(registry):
    outcome = normalize(registry, "approvals", "UL, u.l., UL listed")
    assert outcome.value.value_canonical == ["UL"]


def test_multi_enum_with_nothing_recognisable_fails(registry):
    outcome = normalize(registry, "approvals", "BarCert, BazCert")
    assert not outcome.normalized


# --------------------------------------------------------------------- booleans


@pytest.mark.parametrize("raw", ["true", "Yes", "certified", "1", "compliant"])
def test_truthy_booleans(registry, raw):
    outcome = normalize(registry, "lead_free_compliant", raw)
    assert outcome.value.value_canonical is True
    assert outcome.value.value_display == "Yes"


@pytest.mark.parametrize("raw", ["false", "No", "0", "non-compliant"])
def test_falsy_booleans(registry, raw):
    assert normalize(registry, "lead_free_compliant", raw).value.value_canonical is False


def test_ambiguous_boolean_on_a_compliance_claim_is_a_blocking_error(registry):
    """Guessing a compliance flag is the single thing that must never happen."""
    outcome = normalize(registry, "lead_free_compliant", "NSF/ANSI 61")
    assert not outcome.normalized
    assert outcome.failed is True
    assert outcome.issues[0].rule_id == "boolean_ambiguous"


# --------------------------------------------------------------------- numbers, strings


def test_integer_normalization(registry):
    assert normalize(registry, "case_quantity", "12").value.value_canonical == 12


def test_non_whole_integer_is_rejected(registry):
    outcome = normalize(registry, "case_quantity", "12.5")
    assert not outcome.normalized
    assert outcome.issues[0].rule_id == "integer_not_whole"


def test_number_normalization(registry):
    assert normalize(registry, "cv_flow_coefficient", "18.5").value.value_canonical == 18.5


def test_string_pattern_is_enforced(registry):
    """GTIN has a declared pattern; a malformed one must not be stored."""
    good = normalize(registry, "gtin", "012345678905")
    assert good.value.value_canonical == "012345678905"

    bad = normalize(registry, "gtin", "12-34")
    assert not bad.normalized
    assert bad.issues[0].rule_id == "pattern_mismatch"


def test_string_whitespace_is_collapsed(registry):
    outcome = normalize(registry, "country_of_origin", "  United   States  ")
    assert outcome.value.value_canonical == "United States"


def test_empty_raw_value_is_reported(registry):
    outcome = normalize_value(
        raw_value("body_material", "   "), registry.attribute("body_material")
    )
    assert not outcome.normalized
    assert outcome.issues[0].rule_id == "empty_raw_value"


# --------------------------------------------------------------------- batch


def test_normalize_all_processes_a_batch(registry):
    values = [
        raw_value("body_material", "C84400"),
        raw_value("pressure_rating_wog", "600 PSI"),
        raw_value("nominal_size", '3/4"'),
    ]
    normalized, issues = normalize_all(values, registry)
    assert [v.value_canonical for v in normalized] == [
        "Bronze C84400",
        Quantity(magnitude=600.0, unit="psi"),
        Quantity(magnitude=19.05, unit="mm"),
    ]
    assert not any(i.is_blocking for i in issues)


def test_unknown_attribute_is_reported_rather_than_dropped(registry):
    """Usually means a prompt or schema version drifted, which is worth knowing."""
    normalized, issues = normalize_all([raw_value("mystery_field", "x")], registry)
    assert len(normalized) == 1
    assert normalized[0].value_canonical is None
    assert any(i.rule_id == "unknown_attribute" for i in issues)


# --------------------------------------------------------------------- brands


def test_brand_master_loads(brands: BrandMaster):
    assert len(brands) >= 10
    assert "milwaukee_valve" in brands.brand_ids


@pytest.mark.parametrize(
    ("raw", "expected_id"),
    [
        ("Milwaukee Valve", "milwaukee_valve"),
        ("milwaukee", "milwaukee_valve"),
        ("MILWAUKEE VALVE COMPANY", "milwaukee_valve"),
        ("NIBCO Inc.", "nibco"),
        ("nibco", "nibco"),
        ("Conbraco Industries", "apollo"),
        ("Watts Water Technologies", "watts"),
        ("Wilkins", "zurn"),
        ("Grinnell", "anvil"),
    ],
)
def test_brand_aliases_resolve(brands: BrandMaster, raw, expected_id):
    resolution = brands.resolve(raw)
    assert resolution.resolved
    assert resolution.brand.brand_id == expected_id


def test_corporate_suffixes_are_stripped():
    assert fold_brand("NIBCO Inc.") == fold_brand("nibco")
    assert fold_brand("Mueller Industries") == "mueller"


def test_suffix_stripping_only_applies_at_the_end():
    """'Industries' inside a name can be load-bearing."""
    assert fold_brand("Anvil International") == "anvil"
    assert fold_brand("Industries Anvil") == "industriesanvil"


def test_combined_brand_string_resolves_from_a_component(brands: BrandMaster):
    resolution = brands.resolve("Apollo/Conbraco")
    assert resolution.resolved
    assert resolution.brand.brand_id == "apollo"


def test_unknown_brand_abstains(brands: BrandMaster):
    """A wrongly resolved brand silently merges two manufacturers' catalogues."""
    resolution = brands.resolve("Totally Unknown Valve Co")
    assert not resolution.resolved
    assert resolution.method == "unresolved"


def test_empty_brand_is_handled(brands: BrandMaster):
    assert not brands.resolve(None).resolved
    assert not brands.resolve("   ").resolved


# --------------------------------------------------------------------- MPN cleansing


def test_vendor_prefix_is_stripped(brands: BrandMaster):
    """Distributor-added prefixes block matching against the manufacturer's own data."""
    milwaukee = brands.get("milwaukee_valve")
    assert clean_mpn("MIL-BA-100-075", brand=milwaukee) == "BA-100-075"
    assert clean_mpn("MV-BA-100-075", brand=milwaukee) == "BA-100-075"


def test_prefix_is_not_stripped_when_it_is_the_whole_value(brands: BrandMaster):
    milwaukee = brands.get("milwaukee_valve")
    assert clean_mpn("MIL-", brand=milwaukee) == "MIL-"


def test_separator_noise_is_removed():
    assert clean_mpn(" ba 100.075 ") == "BA100075"
    assert clean_mpn("BA/100/075") == "BA100075"


def test_hyphens_are_preserved_as_meaningful():
    assert clean_mpn("BA-100-075") == "BA-100-075"


def test_empty_mpn_returns_none_not_empty_string():
    """An absent part number must not masquerade as a present one."""
    assert clean_mpn(None) is None
    assert clean_mpn("  ") is None
    assert clean_mpn("///") is None


def test_match_key_is_separator_insensitive():
    key = mpn_match_key("BA-100-075")
    assert key == "BA100075"
    assert mpn_match_key("BA100075") == key
    assert mpn_match_key("ba 100.075") == key
    assert mpn_match_key("BA/100/075") == key


def test_match_key_does_not_merge_genuinely_different_parts():
    assert mpn_match_key("BA-100-075") != mpn_match_key("BA-100-100")


def test_match_key_does_not_strip_leading_zeros():
    """Deliberate: zero-stripping needs segment boundaries, separator-stripping destroys them.

    The two cannot coexist in one key, so leading-zero variation is handled by the variant
    set instead.
    """
    assert mpn_match_key("BA-0100-075") != mpn_match_key("BA-100-075")


def test_variants_bridge_the_leading_zero_case():
    """Blocking indexes all variants, which is how this is normally solved."""
    padded = mpn_variants("BA-0100-075")
    plain = mpn_variants("BA-100-075")
    assert padded & plain, "the two spellings must share at least one blocking key"


def test_variants_cover_plausible_spellings():
    variants = mpn_variants("BA-100-075")
    assert "BA-100-075" in variants
    assert "BA100075" in variants


def test_variants_of_an_absent_mpn_are_empty():
    assert mpn_variants(None) == set()
    assert mpn_variants("///") == set()


# ------------------------------------------------------- metric size designations


@pytest.mark.parametrize(
    ("dn", "nps"),
    [
        ("DN15", '1/2"'),
        ("DN20", '3/4"'),
        ("DN25", '1"'),
        ("DN32", '1-1/4"'),
        ("DN 50", '2"'),
        ("DN65", '2-1/2"'),
        ("DN100", '4"'),
    ],
)
def test_dn_normalises_identically_to_its_nps_twin(registry, dn, nps):
    """DN and NPS name the same pipe, so they must reach one canonical value.

    They are not arithmetically related: DN15 is 1/2" by designation, while 15 mm converts to
    0.59". If DN were read as a millimetre measurement, the metric and imperial datasheets for
    a single valve would normalise to two different sizes, and comparison and de-duplication
    would both break without ever raising an error.
    """
    metric = normalize(registry, "nominal_size", dn)
    imperial = normalize(registry, "nominal_size", nps)

    assert metric.value.value_canonical.magnitude == pytest.approx(
        imperial.value.value_canonical.magnitude
    )
    assert metric.value.value_display == imperial.value.value_display == nps


def test_dn_is_not_read_as_inches(registry):
    """The regression this guards: DN15 parsed as fifteen inches.

    With ``unit_hint: in`` on the attribute, a bare ``15`` reads as inches — 381 mm, a 25x
    error that sits comfortably inside the plausible range and so passes every downstream
    check, then renders as ``15"`` on a half-inch valve.
    """
    outcome = normalize(registry, "nominal_size", "DN15")
    assert outcome.value.value_canonical.magnitude == pytest.approx(12.7)
    assert outcome.value.value_canonical.magnitude != pytest.approx(381.0)


def test_a_real_millimetre_measurement_is_not_remapped(registry):
    """Only the DN *designation* is table-resolved; an explicit mm value is taken at face value."""
    outcome = normalize(registry, "nominal_size", "15 mm")
    assert outcome.value.value_canonical.magnitude == pytest.approx(15.0)


def test_unlisted_dn_size_falls_back_to_millimetres(registry):
    """An off-standard DN is still metric. Reading it as inches would be the 25x error again."""
    outcome = normalize(registry, "nominal_size", "DN37")
    assert outcome.value.value_canonical.magnitude == pytest.approx(37.0)


def test_dn_table_is_declarative():
    """The designation table is a domain fact, so it lives in YAML a merchandiser can correct."""
    from axiom.validate.constants import RuleConstants

    table = RuleConstants.load().tables["DN_TO_NPS_INCHES"]
    assert table["15"] == pytest.approx(0.5)
    assert table["50"] == pytest.approx(2.0)
