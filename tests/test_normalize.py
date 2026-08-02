"""Tests for the deterministic normalization layer.

These exist because a wrong unit conversion is a *systematic* error: it corrupts an entire
category at once, silently, and nobody notices until a customer receives the wrong part.
Every conversion factor here is checked against its defining value or a published table.
"""

from __future__ import annotations

import math

import pytest
from axiom.normalize import (
    ConversionError,
    IncompatibleQuantityKindError,
    QuantityKind,
    awg_to_mm2,
    parse_dimension,
    parse_fraction,
    parse_quantity,
    parse_range,
    registry,
)
from axiom.normalize.parsers import Qualifier
from axiom.normalize.units import ThreadStandard

# ------------------------------------------------------------------ exact definitions


def test_inch_to_mm_is_exact():
    assert registry.convert(1, "in", "mm") == 25.4
    assert registry.convert(1, "ft", "mm") == 304.8


def test_pound_to_kg_is_exact():
    assert registry.convert(1, "lb", "kg") == 0.45359237


def test_bar_to_psi_matches_published_value():
    # 1 bar = 100 000 Pa = 14.5037738... psi
    assert registry.convert(1, "bar", "psi") == pytest.approx(14.503773773, rel=1e-9)


def test_psi_round_trip_does_not_drift():
    for unit in ("bar", "kPa", "MPa", "Pa"):
        assert registry.convert(registry.convert(600, "psi", unit), unit, "psi") == pytest.approx(
            600, rel=1e-12
        )


def test_torque_conversions():
    assert registry.convert(1, "ft.lbf", "N.m") == pytest.approx(1.3558179483314004, rel=1e-12)
    # 1 ft-lb is exactly 12 in-lb
    assert registry.convert(1, "ft.lbf", "in.lbf") == pytest.approx(12.0, rel=1e-12)
    # 1 in-lb is exactly 16 in-oz
    assert registry.convert(1, "in.lbf", "in.ozf") == pytest.approx(16.0, rel=1e-12)


def test_us_and_imperial_gallons_are_different():
    us = registry.convert(1, "galUS", "L")
    uk = registry.convert(1, "galUK", "L")
    assert us == 3.785411784
    assert uk == 4.54609
    assert us != uk, "conflating US and imperial gallons is a classic catalog bug"


def test_gpm_is_us_gallons_per_minute():
    assert registry.convert(1, "gpm", "L/min") == pytest.approx(3.785411784, rel=1e-12)


# ------------------------------------------------------------------ temperature is affine


def test_fahrenheit_to_celsius_uses_an_offset_not_a_factor():
    assert registry.convert(32, "degF", "degC") == pytest.approx(0.0, abs=1e-12)
    assert registry.convert(212, "degF", "degC") == pytest.approx(100.0, abs=1e-12)
    assert registry.convert(73, "degF", "degC") == pytest.approx(22.7777778, abs=1e-6)


def test_kelvin_conversion():
    assert registry.convert(0, "degC", "K") == pytest.approx(273.15, abs=1e-12)
    assert registry.convert(273.15, "K", "degC") == pytest.approx(0.0, abs=1e-9)


def test_negative_temperature_survives_round_trip():
    assert registry.convert(registry.convert(-20, "degC", "degF"), "degF", "degC") == pytest.approx(
        -20, abs=1e-9
    )


# ------------------------------------------------------------------ dimensional safety


def test_cross_kind_conversion_is_refused():
    with pytest.raises(IncompatibleQuantityKindError) as exc:
        registry.convert(600, "psi", "mm")
    assert "different quantity kinds" in str(exc.value)


def test_compatibility_check_backs_l1_validation():
    assert registry.are_compatible("psi", "bar") is True
    assert registry.are_compatible("psi", "mm") is False
    assert registry.are_compatible("nonsense", "mm") is False


def test_unknown_unit_raises():
    with pytest.raises(ConversionError):
        registry.require("furlongs per fortnight")


def test_canonical_units_are_defined_for_every_resolvable_kind():
    for code in registry.known_units():
        kind = registry.kind_of(code)
        assert kind is not None
        if kind in {QuantityKind.THREAD_STANDARD, QuantityKind.WIRE_GAUGE}:
            continue
        assert registry.canonical_unit_for(code), f"{code} has no canonical unit"


def test_aliases_resolve_to_the_same_unit():
    for alias in ('"', "inch", "inches", "in."):
        assert registry.resolve(alias).code == "in"
    for alias in ("ft-lb", "ft lb", "lb-ft", "FTLB"):
        assert registry.resolve(alias).code == "ft.lbf"


# ------------------------------------------------------------------ thread standards


def test_npt_and_bspt_are_not_interchangeable():
    npt = ThreadStandard.parse("FNPT")
    bspt = ThreadStandard.parse("BSPT")
    assert npt is ThreadStandard.NPT
    assert bspt is ThreadStandard.BSPT
    assert npt.is_compatible_with(bspt) is False, (
        "NPT and BSPT do not seal against each other; normalizing between them is a "
        "field failure"
    )


def test_npt_and_nptf_do_interchange():
    assert ThreadStandard.NPT.is_compatible_with(ThreadStandard.NPTF) is True


def test_thread_aliases():
    assert ThreadStandard.parse("IPS") is ThreadStandard.NPT
    assert ThreadStandard.parse("G") is ThreadStandard.BSPP
    assert ThreadStandard.parse("unknown-thread") is None


# ------------------------------------------------------------------ AWG


@pytest.mark.parametrize(
    ("gauge", "expected_mm2"),
    [
        (14, 2.08),
        (12, 3.31),
        (10, 5.26),
        (8, 8.37),
        (4, 21.15),
        (2, 33.63),
        ("1/0", 53.49),
        ("4/0", 107.22),
    ],
)
def test_awg_matches_published_table(gauge, expected_mm2):
    assert awg_to_mm2(gauge) == pytest.approx(expected_mm2, rel=2e-3)


def test_awg_zero_forms_are_equivalent():
    assert awg_to_mm2("0000") == pytest.approx(awg_to_mm2("4/0"), rel=1e-12)
    assert awg_to_mm2("AWG 12") == pytest.approx(awg_to_mm2(12), rel=1e-12)


def test_awg_is_nonlinear():
    """Halving the gauge number does not double the area — this is why a lookup, not math."""
    assert awg_to_mm2(6) / awg_to_mm2(12) == pytest.approx(4.0, rel=0.02)


def test_awg_rejects_garbage():
    with pytest.raises(ConversionError):
        awg_to_mm2("thick")


# ------------------------------------------------------------------ fraction parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3/4", 0.75),
        ("1/2", 0.5),
        ("1-1/4", 1.25),
        ("1 1/4", 1.25),
        ("2-1/2", 2.5),
        ("2.5", 2.5),
        ("12", 12.0),
        ("-20", -20.0),
        ("1\u20131/4", 1.25),  # en dash as the mixed-number separator
    ],
)
def test_parse_fraction(text, expected):
    assert parse_fraction(text) == pytest.approx(expected)


def test_parse_fraction_rejects_nonsense():
    assert parse_fraction("consult factory") is None
    assert parse_fraction("") is None
    assert parse_fraction("1/0") is None


# ------------------------------------------------------------------ quantity parsing


def test_parses_fractional_inch_with_symbol():
    p = parse_quantity('3/4"')
    assert p.magnitude == 0.75
    assert p.unit == "in"
    assert p.kind is QuantityKind.LENGTH
    assert p.canonical_magnitude == pytest.approx(19.05)
    assert p.canonical_unit == "mm"


def test_parses_pressure_with_rating_class_and_reference_condition():
    p = parse_quantity("600 PSI WOG @ 73°F")
    assert p.magnitude == 600.0
    assert p.unit == "psi"
    assert p.rating_class == "WOG"
    assert p.reference_condition == "73°F"
    assert p.qualifier is Qualifier.EXACT


def test_parses_maximum_qualifier():
    p = parse_quantity("600 PSI max")
    assert p.magnitude == 600.0
    assert p.qualifier is Qualifier.MAXIMUM
    assert p.display == "max 600 psi"


def test_parses_conditional_note():
    p = parse_quantity("66 (cover closed)")
    assert p.magnitude == 66.0
    assert p.conditional_note == "cover closed"


def test_unit_hint_is_used_when_source_omits_the_unit():
    p = parse_quantity("600", unit_hint="psi")
    assert p.unit == "psi"
    assert p.canonical_magnitude == 600.0


def test_value_with_no_number_is_reported_not_invented():
    p = parse_quantity("consult factory")
    assert p.magnitude is None
    assert p.canonical_magnitude is None
    assert "no numeric magnitude found" in p.warnings


def test_missing_unit_produces_a_warning_rather_than_a_guess():
    p = parse_quantity("600")
    assert p.magnitude == 600.0
    assert p.unit is None
    assert any("no unit resolved" in w for w in p.warnings)


# ------------------------------------------------------------------ range parsing


def test_parses_hyphen_range():
    r = parse_range("18-22 ft-lb")
    assert r.is_range
    assert (r.minimum, r.maximum) == (18.0, 22.0)
    assert r.unit == "ft.lbf"


def test_parses_en_dash_range():
    r = parse_range("18\u201322 ft-lb")
    assert (r.minimum, r.maximum) == (18.0, 22.0)


def test_parses_signed_word_range():
    r = parse_range("-20 °C to +60 °C")
    assert r.is_range
    assert r.minimum == -20.0
    assert r.maximum == 60.0
    assert r.unit == "degC"


def test_mixed_number_is_not_mistaken_for_a_range():
    """The disambiguation that matters: 1-1/4 in is 1.25 inches, not 1 to 4 inches."""
    r = parse_range("1-1/4 in")
    assert r.is_range is False
    assert r.magnitude == pytest.approx(1.25)
    assert r.unit == "in"


def test_reversed_range_is_ordered_and_flagged():
    r = parse_range("60 to -20 degC")
    assert (r.minimum, r.maximum) == (-20.0, 60.0)
    assert any("reversed" in w for w in r.warnings)


def test_range_canonicalizes_the_lower_bound():
    r = parse_range("1/2 to 2 in")
    assert r.canonical_magnitude == pytest.approx(12.7)
    assert r.canonical_unit == "mm"


def test_single_value_falls_back_to_quantity_parsing():
    r = parse_range("600 PSI")
    assert r.is_range is False
    assert r.magnitude == 600.0


# ------------------------------------------------------------------ dimension sets


def test_parses_dimension_set_with_shared_trailing_unit():
    dims = parse_dimension("2 x 4 x 6 in")
    assert [d.canonical_magnitude for d in dims] == pytest.approx([50.8, 101.6, 152.4])
    assert all(d.unit == "in" for d in dims)


def test_dimension_set_accepts_unicode_multiplication_sign():
    dims = parse_dimension("10 × 20 mm")
    assert [d.magnitude for d in dims] == [10.0, 20.0]


def test_single_dimension_returns_one_component():
    assert len(parse_dimension("6 in")) == 1


# ------------------------------------------------------------------ realistic end-to-end


def test_realistic_datasheet_row():
    """A row lifted from the shape of a real two-piece ball valve spec table."""
    size = parse_quantity('3/4"')
    pressure = parse_quantity("600 PSI WOG @ 73\u00b0F")
    torque = parse_range("18\u201322 ft-lb")
    temp = parse_range("-20 \u00b0C to +60 \u00b0C")

    assert size.canonical_magnitude == pytest.approx(19.05)
    assert pressure.canonical_magnitude == 600.0
    assert pressure.reference_condition == "73\u00b0F"
    assert torque.canonical_unit == "N.m"
    assert torque.canonical_magnitude == pytest.approx(24.4047, rel=1e-4)
    assert (temp.minimum, temp.maximum) == (-20.0, 60.0)

    # and the values remain dimensionally distinguishable, which is what L1 relies on
    assert not registry.are_compatible(size.unit, pressure.unit)
    assert math.isclose(registry.convert(1, "in", "mm"), 25.4)
