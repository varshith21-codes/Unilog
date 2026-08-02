"""Deterministic normalization: units, parsers, enum snapping.

Nothing in this package calls a model. That is the entire point.

Unit conversion, fraction parsing, dimensional consistency and arithmetic are exactly the
operations an LLM gets subtly and silently wrong, and a wrong unit conversion is a
systematic error that corrupts a whole category at once. So they live here, as tested code.

The industrial specifics matter more than the generic SI plumbing. A generic unit library
knows metres and pascals; it does not know that NPT and BSPT are both "tapered pipe
thread" and are *not* interchangeable, or that AWG maps nonlinearly to mm², or that sheet
gauge depends on the material. Those are the conversions that actually break catalogs.
"""

from axiom.normalize.brands import (
    Brand,
    BrandMaster,
    BrandResolution,
    clean_mpn,
    fold_brand,
    mpn_match_key,
    mpn_variants,
)
from axiom.normalize.parsers import (
    ParsedValue,
    parse_dimension,
    parse_fraction,
    parse_quantity,
    parse_range,
)
from axiom.normalize.pipeline import (
    NormalizationOutcome,
    normalize_all,
    normalize_value,
    to_imperial_fraction,
)
from axiom.normalize.units import (
    ConversionError,
    IncompatibleQuantityKindError,
    QuantityKind,
    UnitRegistry,
    awg_to_mm2,
    registry,
)

__all__ = [
    "Brand",
    "BrandMaster",
    "BrandResolution",
    "ConversionError",
    "IncompatibleQuantityKindError",
    "NormalizationOutcome",
    "ParsedValue",
    "QuantityKind",
    "UnitRegistry",
    "awg_to_mm2",
    "clean_mpn",
    "fold_brand",
    "mpn_match_key",
    "mpn_variants",
    "normalize_all",
    "normalize_value",
    "parse_dimension",
    "parse_fraction",
    "parse_quantity",
    "parse_range",
    "registry",
    "to_imperial_fraction",
]
