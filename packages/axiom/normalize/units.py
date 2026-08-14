"""Industrial unit registry and deterministic conversion.

Design decisions worth stating explicitly:

**Conversions are exact where the definition is exact.** 1 inch is exactly 25.4 mm; 1 lb is
exactly 0.45359237 kg; 1 bar is exactly 100 000 Pa. Those factors are written as their
defining values, not rounded, so round-tripping does not drift.

**Temperature is handled separately.** It is affine, not linear: °F → °C needs an offset.
Treating it as a scale factor is a classic and silent catalog bug.

**Some conversions are deliberately refused.** NPT and BSPT are both tapered pipe threads
with similar nominal designations, and they do not seal against each other. A system that
"helpfully" normalizes one into the other produces a record that will leak in the field.
:class:`ThreadStandard` therefore has no conversion path — mismatches raise.

**Dimensional consistency is checkable.** Every unit declares a :class:`QuantityKind`, so
L1 validation can reject a pressure value that landed in a length field without knowing
anything about the specific attribute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ConversionError(ValueError):
    """Raised when a conversion cannot be performed."""


class IncompatibleQuantityKindError(ConversionError):
    """Raised when units belong to different quantity kinds — the L1 failure case."""


class QuantityKind(str, Enum):
    """What a magnitude physically measures. Two units are convertible only within a kind."""

    LENGTH = "length"
    AREA = "area"
    VOLUME = "volume"
    MASS = "mass"
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    TORQUE = "torque"
    FORCE = "force"
    FLOW_VOLUMETRIC = "flow_volumetric"
    VOLTAGE = "voltage"
    CURRENT = "current"
    POWER = "power"
    FREQUENCY = "frequency"
    ANGLE = "angle"
    TIME = "time"
    DIMENSIONLESS = "dimensionless"
    WIRE_GAUGE = "wire_gauge"
    """Gauge scales are ordinal-ish and nonlinear; they convert to LENGTH/AREA via tables."""

    SOUND_LEVEL = "sound_level"
    """Logarithmic, and weighted. Only ``dBA`` is defined — see the note beside it."""

    THREAD_STANDARD = "thread_standard"
    """Not a measurement. Deliberately non-convertible — see module docstring."""


@dataclass(frozen=True)
class UnitDef:
    """A unit, defined as a linear (or affine) map onto the canonical unit of its kind."""

    code: str
    kind: QuantityKind
    to_canonical_factor: float
    to_canonical_offset: float = 0.0
    aliases: tuple[str, ...] = ()
    display: str | None = None

    def to_canonical(self, magnitude: float) -> float:
        return magnitude * self.to_canonical_factor + self.to_canonical_offset

    def from_canonical(self, canonical: float) -> float:
        return (canonical - self.to_canonical_offset) / self.to_canonical_factor


# Canonical unit per kind. Chosen to match what industrial datasheets actually use, not
# strict SI — storing pressure in pascals would make every stored value unreadable.
CANONICAL_UNITS: dict[QuantityKind, str] = {
    QuantityKind.LENGTH: "mm",
    QuantityKind.AREA: "mm2",
    QuantityKind.VOLUME: "L",
    QuantityKind.MASS: "kg",
    QuantityKind.PRESSURE: "psi",
    QuantityKind.TEMPERATURE: "degC",
    QuantityKind.TORQUE: "N.m",
    QuantityKind.FORCE: "N",
    QuantityKind.FLOW_VOLUMETRIC: "L/min",
    QuantityKind.VOLTAGE: "V",
    QuantityKind.CURRENT: "A",
    QuantityKind.POWER: "W",
    QuantityKind.FREQUENCY: "Hz",
    QuantityKind.ANGLE: "deg",
    QuantityKind.TIME: "s",
    QuantityKind.SOUND_LEVEL: "dBA",
    QuantityKind.DIMENSIONLESS: "1",
}

# --- exact defining constants -------------------------------------------------------
_IN_TO_MM = 25.4  # exact by definition
_FT_TO_MM = 304.8  # exact by definition (1 ft = 0.3048 m); NOT derived as 25.4*12,
# because that product is not exactly representable in binary floating point
_LB_TO_KG = 0.45359237  # exact by definition
_LBF_TO_N = 4.4482216152605  # exact by definition
_PSI_TO_PA = _LBF_TO_N / (_IN_TO_MM / 1000) ** 2  # lbf per square inch
_US_GAL_TO_L = 3.785411784  # exact by definition
_IMP_GAL_TO_L = 4.54609  # exact by definition


_UNITS: tuple[UnitDef, ...] = (
    # ---------------- length (canonical: mm) ----------------
    UnitDef("mm", QuantityKind.LENGTH, 1.0, aliases=("millimeter", "millimetre", "mm.")),
    UnitDef("cm", QuantityKind.LENGTH, 10.0, aliases=("centimeter", "centimetre")),
    UnitDef("m", QuantityKind.LENGTH, 1000.0, aliases=("meter", "metre")),
    UnitDef("um", QuantityKind.LENGTH, 0.001, aliases=("micron", "µm", "micrometer")),
    UnitDef(
        "in",
        QuantityKind.LENGTH,
        _IN_TO_MM,
        aliases=('"', "inch", "inches", "in.", "″"),
        display='"',
    ),
    UnitDef("ft", QuantityKind.LENGTH, _FT_TO_MM, aliases=("'", "foot", "feet", "ft.")),
    UnitDef("mil", QuantityKind.LENGTH, _IN_TO_MM / 1000, aliases=("thou",)),
    # ---------------- area (canonical: mm2) ----------------
    UnitDef("mm2", QuantityKind.AREA, 1.0, aliases=("mm²", "sq mm", "sqmm")),
    UnitDef("cm2", QuantityKind.AREA, 100.0, aliases=("cm²", "sq cm")),
    UnitDef("in2", QuantityKind.AREA, _IN_TO_MM**2, aliases=("in²", "sq in", "sqin")),
    # ---------------- volume (canonical: L) ----------------
    UnitDef("L", QuantityKind.VOLUME, 1.0, aliases=("l", "liter", "litre", "ltr")),
    UnitDef("mL", QuantityKind.VOLUME, 0.001, aliases=("ml", "milliliter", "cc", "cm3")),
    UnitDef(
        "galUS",
        QuantityKind.VOLUME,
        _US_GAL_TO_L,
        aliases=("gal", "gallon", "us gal", "usgal", "gallons"),
    ),
    UnitDef("galUK", QuantityKind.VOLUME, _IMP_GAL_TO_L, aliases=("imp gal", "impgal")),
    # ---------------- mass (canonical: kg) ----------------
    UnitDef("kg", QuantityKind.MASS, 1.0, aliases=("kilogram", "kilo", "kgs")),
    UnitDef("g", QuantityKind.MASS, 0.001, aliases=("gram", "grams", "gm")),
    UnitDef("lb", QuantityKind.MASS, _LB_TO_KG, aliases=("lbs", "pound", "pounds", "#")),
    UnitDef("oz", QuantityKind.MASS, _LB_TO_KG / 16, aliases=("ounce", "ounces")),
    # ---------------- pressure (canonical: psi) ----------------
    UnitDef(
        "psi",
        QuantityKind.PRESSURE,
        1.0,
        aliases=("lbf/in2", "psig", "psia", "pounds per square inch", "wog", "wsp"),
    ),
    UnitDef("bar", QuantityKind.PRESSURE, 100_000.0 / _PSI_TO_PA, aliases=("bars",)),
    UnitDef("kPa", QuantityKind.PRESSURE, 1_000.0 / _PSI_TO_PA, aliases=("kpa",)),
    UnitDef("MPa", QuantityKind.PRESSURE, 1_000_000.0 / _PSI_TO_PA, aliases=("mpa",)),
    UnitDef("Pa", QuantityKind.PRESSURE, 1.0 / _PSI_TO_PA, aliases=("pascal",)),
    # ---------------- torque (canonical: N.m) ----------------
    UnitDef("N.m", QuantityKind.TORQUE, 1.0, aliases=("nm", "n-m", "n·m", "newton meter")),
    UnitDef(
        "ft.lbf",
        QuantityKind.TORQUE,
        _LBF_TO_N * _FT_TO_MM / 1000,
        aliases=("ft-lb", "ft lb", "ftlb", "ft-lbs", "ft.lb", "lb-ft", "lbf-ft"),
    ),
    UnitDef(
        "in.lbf",
        QuantityKind.TORQUE,
        _LBF_TO_N * _IN_TO_MM / 1000,
        aliases=("in-lb", "in lb", "inlb", "in-lbs", "lb-in"),
    ),
    UnitDef(
        "in.ozf",
        QuantityKind.TORQUE,
        _LBF_TO_N * _IN_TO_MM / 1000 / 16,
        aliases=("in-oz", "oz-in", "ozin"),
    ),
    # ---------------- force (canonical: N) ----------------
    UnitDef("N", QuantityKind.FORCE, 1.0, aliases=("newton", "newtons")),
    UnitDef("lbf", QuantityKind.FORCE, _LBF_TO_N, aliases=("pound-force",)),
    UnitDef("kN", QuantityKind.FORCE, 1000.0, aliases=("kilonewton",)),
    # ---------------- volumetric flow (canonical: L/min) ----------------
    UnitDef("L/min", QuantityKind.FLOW_VOLUMETRIC, 1.0, aliases=("lpm", "l/min", "lit/min")),
    UnitDef(
        "galUS/min",
        QuantityKind.FLOW_VOLUMETRIC,
        _US_GAL_TO_L,
        aliases=("gpm", "gal/min", "usgpm"),
    ),
    UnitDef("m3/h", QuantityKind.FLOW_VOLUMETRIC, 1000.0 / 60, aliases=("m³/h", "cmh")),
    UnitDef(
        "ft3/min",
        QuantityKind.FLOW_VOLUMETRIC,
        (_FT_TO_MM / 1000) ** 3 * 1000,
        aliases=("cfm", "ft³/min", "scfm"),
    ),
    # ---------------- electrical ----------------
    UnitDef("V", QuantityKind.VOLTAGE, 1.0, aliases=("volt", "volts", "vac", "vdc")),
    UnitDef("kV", QuantityKind.VOLTAGE, 1000.0, aliases=("kilovolt",)),
    UnitDef("mV", QuantityKind.VOLTAGE, 0.001, aliases=("millivolt",)),
    UnitDef("A", QuantityKind.CURRENT, 1.0, aliases=("amp", "amps", "ampere", "amperes")),
    UnitDef("mA", QuantityKind.CURRENT, 0.001, aliases=("milliamp", "milliampere")),
    UnitDef("kA", QuantityKind.CURRENT, 1000.0, aliases=("kiloamp", "kaic")),
    UnitDef("W", QuantityKind.POWER, 1.0, aliases=("watt", "watts")),
    UnitDef("kW", QuantityKind.POWER, 1000.0, aliases=("kilowatt",)),
    UnitDef("hp", QuantityKind.POWER, 745.6998715822702, aliases=("horsepower",)),
    UnitDef("Hz", QuantityKind.FREQUENCY, 1.0, aliases=("hertz", "cycles", "cps")),
    # ---------------- acoustic ----------------
    #
    # Only the A-weighted decibel, and deliberately so. Plain `dB` is NOT defined here.
    #
    # dBA is dB passed through the A-weighting curve, which approximates human hearing. The two
    # are not interconvertible by any factor: going between them requires the signal's spectrum,
    # which a datasheet never publishes. Defining both in this kind with factor 1.0 would let the
    # registry silently treat "47 dB" and "47 dBA" as the same measurement, and for appliance
    # sound ratings — where a few dB is the whole marketing claim — that is the same class of
    # error as converting NPT into BSPT.
    #
    # A source that states plain dB therefore fails to resolve, which surfaces it for a human
    # instead of quietly mis-typing it. That is the intended behaviour, not a gap.
    #
    # Logarithmic, so arithmetic on these values is meaningless even within the kind: two 40 dBA
    # sources together are 43 dBA, not 80. `Quantity` only ever adds like units, and nothing in
    # the pipeline sums sound levels, but it is worth knowing before someone tries.
    UnitDef(
        "dBA",
        QuantityKind.SOUND_LEVEL,
        1.0,
        aliases=("dba", "db(a)", "db a", "a-weighted decibel"),
    ),
    # ---------------- misc ----------------
    UnitDef("deg", QuantityKind.ANGLE, 1.0, aliases=("°", "degree", "degrees")),
    UnitDef("rad", QuantityKind.ANGLE, 180.0 / math.pi, aliases=("radian",)),
    UnitDef("s", QuantityKind.TIME, 1.0, aliases=("sec", "second", "seconds")),
    UnitDef("min", QuantityKind.TIME, 60.0, aliases=("minute", "minutes")),
    UnitDef("h", QuantityKind.TIME, 3600.0, aliases=("hr", "hour", "hours")),
    UnitDef("1", QuantityKind.DIMENSIONLESS, 1.0, aliases=("", "each", "ea", "ratio", "cv")),
)

# Temperature is affine, so it is defined separately rather than by factor alone.
_TEMPERATURE_UNITS: tuple[UnitDef, ...] = (
    UnitDef(
        "degC",
        QuantityKind.TEMPERATURE,
        1.0,
        0.0,
        aliases=("c", "°c", "celsius", "centigrade"),
    ),
    UnitDef(
        "degF",
        QuantityKind.TEMPERATURE,
        5.0 / 9.0,
        -32.0 * 5.0 / 9.0,
        aliases=("f", "°f", "fahrenheit"),
    ),
    UnitDef("K", QuantityKind.TEMPERATURE, 1.0, -273.15, aliases=("kelvin",)),
)


class ThreadStandard(str, Enum):
    """Thread standards. Non-convertible by design.

    NPT and BSPT look interchangeable on a spec sheet — both are tapered pipe threads with
    similar nominal sizes — and they do not seal against each other. Normalizing between
    them is a field failure, not a data improvement.
    """

    NPT = "NPT"
    NPTF = "NPTF"
    BSPT = "BSPT"
    BSPP = "BSPP"
    UNC = "UNC"
    UNF = "UNF"
    METRIC = "Metric"

    @classmethod
    def parse(cls, raw: str) -> ThreadStandard | None:
        token = raw.strip().upper().replace(" ", "").replace("-", "")
        table = {
            "NPT": cls.NPT,
            "FNPT": cls.NPT,
            "MNPT": cls.NPT,
            "IPS": cls.NPT,
            "NPTF": cls.NPTF,
            "BSPT": cls.BSPT,
            "R": cls.BSPT,
            "BSPP": cls.BSPP,
            "G": cls.BSPP,
            "UNC": cls.UNC,
            "UNF": cls.UNF,
            "M": cls.METRIC,
            "METRIC": cls.METRIC,
        }
        return table.get(token)

    def is_compatible_with(self, other: ThreadStandard) -> bool:
        """Only NPT and NPTF interchange, and only in one direction in practice."""
        if self == other:
            return True
        return {self, other} == {ThreadStandard.NPT, ThreadStandard.NPTF}


def awg_to_mm2(awg: int | str) -> float:
    """Convert American Wire Gauge to cross-sectional area in mm².

    Uses the defining formula rather than a lookup table, so it is exact for any gauge:
    ``d(mm) = 0.127 × 92^((36 − n) / 39)``, area = ``π/4 × d²``.

    Accepts the ``n/0`` forms: ``"4/0"`` (also written ``0000``) is n = −3.

    >>> round(awg_to_mm2(12), 2)
    3.31
    >>> round(awg_to_mm2("4/0"), 1)
    107.2
    """
    n = _parse_awg_gauge(awg)
    diameter_mm = 0.127 * (92 ** ((36 - n) / 39))
    return math.pi / 4 * diameter_mm**2


def _parse_awg_gauge(awg: int | str) -> int:
    if isinstance(awg, int):
        return awg
    token = awg.strip().upper().removeprefix("AWG").strip()
    if "/0" in token:  # 1/0, 2/0, 3/0, 4/0
        count = int(token.split("/")[0])
        return 1 - count
    if token and set(token) == {"0"}:  # 0, 00, 000, 0000
        return 1 - len(token)
    try:
        return int(token)
    except ValueError as exc:
        raise ConversionError(f"cannot parse AWG gauge from {awg!r}") from exc


class UnitRegistry:
    """Lookup and conversion. Deliberately small, exhaustively tested, no model calls."""

    def __init__(self, units: tuple[UnitDef, ...] = _UNITS + _TEMPERATURE_UNITS) -> None:
        self._by_code: dict[str, UnitDef] = {}
        self._lookup: dict[str, UnitDef] = {}
        for unit in units:
            self._by_code[unit.code] = unit
            for key in (unit.code, *unit.aliases):
                self._lookup[self._key(key)] = unit

    @staticmethod
    def _key(raw: str) -> str:
        return raw.strip().lower().replace(" ", "")

    def resolve(self, raw: str) -> UnitDef | None:
        """Resolve a unit string to its definition, or None if unrecognised."""
        return self._lookup.get(self._key(raw))

    def require(self, raw: str) -> UnitDef:
        unit = self.resolve(raw)
        if unit is None:
            raise ConversionError(f"unrecognised unit {raw!r}")
        return unit

    def kind_of(self, raw: str) -> QuantityKind | None:
        unit = self.resolve(raw)
        return unit.kind if unit else None

    def canonical_unit_for(self, raw: str) -> str:
        return CANONICAL_UNITS[self.require(raw).kind]

    def are_compatible(self, a: str, b: str) -> bool:
        """L1 dimensional check: same quantity kind."""
        ka, kb = self.kind_of(a), self.kind_of(b)
        return ka is not None and ka == kb

    def convert(self, magnitude: float, from_unit: str, to_unit: str) -> float:
        """Convert between units of the same quantity kind.

        Raises :class:`IncompatibleQuantityKind` across kinds — that is the L1 validation
        failure, surfaced as an exception rather than a silently wrong number.
        """
        src = self.require(from_unit)
        dst = self.require(to_unit)
        if src.kind != dst.kind:
            raise IncompatibleQuantityKindError(
                f"cannot convert {from_unit!r} ({src.kind.value}) to {to_unit!r} "
                f"({dst.kind.value}) — different quantity kinds"
            )
        if src.kind == QuantityKind.THREAD_STANDARD:
            raise ConversionError("thread standards are not convertible by design")
        return dst.from_canonical(src.to_canonical(magnitude))

    def to_canonical(self, magnitude: float, from_unit: str) -> tuple[float, str]:
        """Convert to the canonical unit of the value's kind. Returns (magnitude, unit)."""
        src = self.require(from_unit)
        canonical = CANONICAL_UNITS[src.kind]
        return self.convert(magnitude, from_unit, canonical), canonical

    def known_units(self) -> list[str]:
        return sorted(self._by_code)


registry = UnitRegistry()
"""Module-level singleton. Stateless, so sharing it is safe."""
