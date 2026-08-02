"""Column-mapping inference for supplier flat files.

Every supplier invents their own headers. ``PN``, ``PART#``, ``MFR PART NO``, ``Item``,
``Cat No`` all mean manufacturer part number, and an operator currently re-maps them by hand
every time a file arrives.

This is deliberately **deterministic, not model-based**. Header matching is a small, closed
problem where a synonym table plus fuzzy matching is more accurate than an LLM, costs
nothing, runs offline, and — most importantly — is testable. An LLM pass is only worth
adding for headers this cannot resolve, and even then it should propose rather than decide.

The mapping is *proposed with confidence*, confirmed once by a human, and then remembered
per supplier. That is the whole value: the operator maps a supplier once instead of
every time.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Canonical targets that are product-record fields rather than schema attributes.
RECORD_FIELDS = ("sku", "mpn", "brand", "description", "supplier_id")

# Synonyms observed in real distributor and manufacturer files. Keys are canonical targets.
# Matching is done on a folded form, so case, spacing and punctuation are irrelevant here.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "mpn": (
        "mpn", "part", "partno", "partnumber", "partnum", "pn", "part#", "mfrpn",
        "mfgpn", "mfrpartno", "mfgpartno", "manufacturerpartnumber", "vendorpartnumber",
        "catno", "catalogno", "catalognumber", "itemno", "itemnumber", "model", "modelno",
        "modelnumber", "stocknumber",
    ),
    "sku": ("sku", "skuid", "itemid", "internalid", "productid", "productcode", "ourpart"),
    "brand": ("brand", "make", "manufacturer", "mfr", "mfg", "mfgname", "vendor", "supplier"),
    "description": (
        "description", "desc", "desc1", "descr", "descr1", "productname", "name", "title",
        "itemdescription", "shortdescription", "longdescription",
    ),
    "gtin": ("gtin", "upc", "upca", "ean", "barcode", "gtin14", "upccode"),
    "nominal_size": ("size", "nominalsize", "nps", "dn", "pipesize", "valvesize", "sizein"),
    "body_material": ("material", "bodymaterial", "body", "bodymat", "construction"),
    "pressure_rating_wog": (
        "pressure", "pressurerating", "wog", "wogpressure", "maxpressure", "psi", "cwp",
    ),
    "steam_pressure_rating": ("wsp", "steam", "steamrating", "steampressure", "swp"),
    "end_connection": ("connection", "endconnection", "ends", "endtype", "connectiontype"),
    "port_type": ("port", "porttype", "bore", "portsize"),
    "seat_material": ("seat", "seatmaterial", "seatmat"),
    "stem_material": ("stem", "stemmaterial", "stemmat"),
    "handle_type": ("handle", "handletype", "operator", "actuator"),
    "cv_flow_coefficient": ("cv", "cvvalue", "flowcoefficient", "flowcoeff"),
    "temperature_range": ("temp", "temperature", "temprange", "temperaturerange"),
    "operating_torque": ("torque", "operatingtorque", "torquerange"),
    "each_weight": (
        "weight", "wt", "wteach", "weighteach", "unitweight", "netweight", "shipweight",
    ),
    "case_weight": ("casewt", "caseweight", "cartonweight", "grossweight", "boxweight"),
    "case_quantity": (
        "qtycs", "casequantity", "caseqty", "cartonqty", "cartonquantity", "stdpack",
        "standardpack", "masterpack", "packqty", "unitsper", "unitspercase",
    ),
    "selling_uom": ("uom", "um", "unit", "unitofmeasure", "sellinguom", "sellunit", "priceum"),
    "country_of_origin": ("coo", "countryoforigin", "origin", "country", "madein"),
    "approvals": ("approvals", "certifications", "listings", "certs", "approval"),
    "lead_free_compliant": ("leadfree", "lf", "leadfreecompliant", "nolead"),
    "product_series": ("series", "productseries", "family", "productline", "line"),
    "number_of_pieces": ("pieces", "bodypieces", "construction", "piececount"),
}

# Built once: folded synonym -> canonical target.
_LOOKUP: dict[str, str] = {}
for _target, _aliases in SYNONYMS.items():
    for _alias in _aliases:
        _LOOKUP.setdefault(_alias, _target)


def fold_header(header: str) -> str:
    """Reduce a header to its comparable core.

    Strips units in parentheses or brackets, punctuation, and whitespace, so
    ``"Weight (lb)"``, ``"WEIGHT_LB"`` and ``"wt"`` converge as far as they usefully can.
    """
    text = re.sub(r"[\(\[].*?[\)\]]", " ", header)
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class ColumnMatch:
    """One proposed header-to-target mapping."""

    header: str
    target: str | None
    confidence: float
    method: str
    unit_hint: str | None = None
    """Unit parsed out of the header itself, e.g. 'lb' from 'Weight (lb)'. Supplier files
    frequently put the unit in the header and a bare number in the cell."""

    @property
    def is_confident(self) -> bool:
        return self.target is not None and self.confidence >= 0.80


@dataclass
class ColumnMapping:
    """The full proposal for one file, plus what it could not resolve."""

    matches: tuple[ColumnMatch, ...]
    supplier_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> dict[str, str]:
        """target -> header, for confident matches only."""
        out: dict[str, str] = {}
        for match in self.matches:
            if match.is_confident and match.target and match.target not in out:
                out[match.target] = match.header
        return out

    @property
    def unmapped(self) -> tuple[str, ...]:
        return tuple(m.header for m in self.matches if not m.is_confident)

    @property
    def needs_confirmation(self) -> tuple[ColumnMatch, ...]:
        """Matched but not confidently — exactly what a human should look at."""
        return tuple(
            m for m in self.matches if m.target is not None and not m.is_confident
        )

    def coverage(self) -> float:
        if not self.matches:
            return 0.0
        return sum(1 for m in self.matches if m.is_confident) / len(self.matches)


_UNIT_IN_HEADER = re.compile(r"[\(\[]\s*([A-Za-z°/.\-]+)\s*[\)\]]")


def _unit_hint(header: str) -> str | None:
    """Pull a unit out of a header like 'Weight (lb)'. Validated against the unit registry."""
    from axiom.normalize import registry

    match = _UNIT_IN_HEADER.search(header)
    if not match:
        return None
    resolved = registry.resolve(match.group(1))
    return resolved.code if resolved else None


def infer_mapping(
    headers: list[str],
    *,
    supplier_id: str | None = None,
    known: dict[str, str] | None = None,
    valid_targets: set[str] | None = None,
) -> ColumnMapping:
    """Propose a header-to-target mapping.

    ``known`` is a previously confirmed mapping for this supplier, keyed target -> header.
    Remembered mappings win outright: a human already decided, and re-guessing would be
    both wasteful and disrespectful of that decision.
    """
    remembered = {header: target for target, header in (known or {}).items()}
    matches: list[ColumnMatch] = []
    notes: list[str] = []
    taken: set[str] = set()

    for header in headers:
        hint = _unit_hint(header)

        if header in remembered:
            target = remembered[header]
            matches.append(ColumnMatch(header, target, 1.0, "supplier_memory", hint))
            taken.add(target)
            continue

        folded = fold_header(header)
        if not folded:
            matches.append(ColumnMatch(header, None, 0.0, "empty_header", hint))
            continue

        target = _LOOKUP.get(folded)
        if target is not None:
            matches.append(ColumnMatch(header, target, 0.98, "synonym_exact", hint))
            taken.add(target)
            continue

        # Fuzzy fallback over the synonym vocabulary. Threshold is deliberately high:
        # a wrong mapping silently corrupts a whole column, so abstaining is cheaper.
        candidates = difflib.get_close_matches(folded, _LOOKUP.keys(), n=1, cutoff=0.86)
        if candidates:
            ratio = difflib.SequenceMatcher(None, folded, candidates[0]).ratio()
            guess = _LOOKUP[candidates[0]]
            # Below the confident bar on purpose, so it surfaces for confirmation.
            matches.append(ColumnMatch(header, guess, round(ratio * 0.8, 3), "fuzzy", hint))
            continue

        matches.append(ColumnMatch(header, None, 0.0, "unresolved", hint))

    if valid_targets is not None:
        unknown = sorted(
            {
                m.target
                for m in matches
                if m.target and m.target not in valid_targets and m.target not in RECORD_FIELDS
            }
        )
        if unknown:
            notes.append(
                f"mapped to targets absent from the active schema: {', '.join(unknown)}"
            )

    duplicates = sorted(
        {
            m.target
            for m in matches
            if m.target and sum(1 for o in matches if o.target == m.target) > 1
        }
    )
    if duplicates:
        notes.append(
            f"multiple columns map to the same target ({', '.join(duplicates)}); "
            f"the first confident match wins and the rest need review"
        )

    return ColumnMapping(tuple(matches), supplier_id=supplier_id, notes=notes)


class MappingMemory:
    """Per-supplier confirmed mappings, persisted as JSON.

    Small but it is the feature an operator actually feels: map a supplier once, and the
    next four hundred files from them map themselves.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, str]] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, supplier_id: str) -> dict[str, str]:
        return dict(self._data.get(supplier_id, {}))

    def remember(self, supplier_id: str, mapping: dict[str, str]) -> None:
        self._data.setdefault(supplier_id, {}).update(mapping)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def suppliers(self) -> list[str]:
        return sorted(self._data)
