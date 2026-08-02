"""Named sets, lookup tables and helper functions available to cross-field rules.

Loaded from ``schema/constants.yaml`` so domain facts — which alloys contain lead, what Cv a
full-port valve should reach — live where a merchandiser can read and correct them. Burying
those in a Python set puts a domain fact where no domain expert will ever look for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONSTANTS_PATH = Path(__file__).resolve().parents[3] / "schema" / "constants.yaml"


@dataclass
class RuleConstants:
    """Everything a rule expression may reference by name."""

    sets: dict[str, list[str]] = field(default_factory=dict)
    tables: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None) -> RuleConstants:
        source = Path(path or DEFAULT_CONSTANTS_PATH)
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        return cls(
            sets={k: list(v) for k, v in (payload.get("sets") or {}).items()},
            tables={
                name: {str(k): float(v) for k, v in table.items()}
                for name, table in (payload.get("tables") or {}).items()
            },
        )

    def namespace(self) -> dict[str, Any]:
        """Names bound as values in a rule expression."""
        return dict(self.sets)

    def functions(self) -> dict[str, Any]:
        """Names callable from a rule expression.

        Deliberately small. Every function here is total — it returns a value or ``None`` and
        never raises — because an exception inside a rule would be reported as a data problem
        when it is really a rule problem.
        """
        return {
            "abs": abs,
            "min": _safe_min,
            "max": _safe_max,
            "len": _safe_len,
            "cv_floor": self._make_lookup("CV_FLOOR_BY_SIZE_MM", numeric_key=True),
            "material_max_pressure": self._make_lookup("MATERIAL_MAX_PRESSURE_PSI"),
            "seat_temperature_limit": self._make_lookup("SEAT_TEMPERATURE_LIMITS_C"),
        }

    def _make_lookup(self, table_name: str, *, numeric_key: bool = False):
        table = self.tables.get(table_name, {})

        def lookup(key: Any) -> float | None:
            if key is None:
                return None
            # A Quantity may be passed straight through from an attribute value.
            magnitude = getattr(key, "magnitude", key)
            if numeric_key:
                return _nearest_numeric(table, magnitude)
            return table.get(str(magnitude))

        return lookup


def _nearest_numeric(table: dict[str, float], value: Any) -> float | None:
    """Look up the closest numeric key.

    Nominal sizes are stored as exact millimetre conversions of imperial fractions, so an
    exact string match would fail on floating-point representation alone. Nearest-key with a
    tolerance is the honest way to index them.
    """
    try:
        target = float(value)
    except (TypeError, ValueError):
        return None
    best_key, best_delta = None, None
    for key in table:
        try:
            candidate = float(key)
        except ValueError:
            continue
        delta = abs(candidate - target)
        if best_delta is None or delta < best_delta:
            best_key, best_delta = key, delta
    # 0.5 mm is tighter than the gap between any two nominal sizes, so this cannot silently
    # snap a 1" valve onto the 3/4" floor.
    if best_key is None or best_delta is None or best_delta > 0.5:
        return None
    return table[best_key]


def _safe_min(*args):
    values = _flatten(args)
    return min(values) if values else None


def _safe_max(*args):
    values = _flatten(args)
    return max(values) if values else None


def _safe_len(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return 1  # a scalar string is one value, not its character count
    try:
        return len(value)
    except TypeError:
        return 1


def _flatten(args) -> list:
    out = []
    for arg in args:
        if isinstance(arg, list | tuple | set):
            out.extend(a for a in arg if a is not None)
        elif arg is not None:
            out.append(arg)
    return out
