"""Reading one row of the Unilog item master.

The input is six columns, three of which are mostly sentinels. This module turns that into the
few things that can be honestly established from it, and — more importantly — is explicit about
what cannot.

The interesting content here is all in what it *refuses* to do. Two examples, both measured from
the client's own ground truth:

**`Part_Manuf` is not a manufacturer name.** It is a vendor/supplier field, and it is mixed. Most
values are manufacturers (`Freud Inc (2435)`, `Kichler Lighting (KICLI)`) but a substantial
minority are distributors and buying co-ops (`Appliance Dealers Cooperative (APPDE)`,
`Boise Cascade Building Materials (BOICA)`, `Parksite (6151)`, `U S Lumber (3073)`). Writing
"Appliance Dealers Cooperative" into `MANUFACTURER_NAME` would be confidently wrong, so the
resolution is reported with a flag rather than asserted.

**`BRAND_NAME` frequently cannot be derived at all.** Ground truth row 1 has all three brand
columns set to sentinels and `Part_Manuf` set to a co-op, yet the expected `BRAND_NAME` is
`FRIGIDAIRE(R)`. That string is not present anywhere in the input. It comes from the manufacturer
URL, or from the approved brand master — both of which are outside this row. So the honest result
is "unresolved, needs retrieval", not a guess assembled from the nearest available token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from axiom.ingest.placeholders import clean

# A trailing supplier code: "Kichler Lighting (KICLI)" -> "Kichler Lighting".
#
# Restricted to codes that are entirely upper-case letters and digits, because a manufacturer name
# can legitimately end in a parenthesised word. "Black & Decker/dewlt (2585)" must lose "(2585)";
# a hypothetical "Acme (Europe)" must keep "(Europe)".
_SUPPLIER_CODE = re.compile(r"\s*\(([A-Z0-9][A-Z0-9\-]*)\)\s*$")

# The three brand columns, in the order they are preferred when several carry real values.
#
# DIB before E1 is a deliberate, and admittedly weakly-evidenced, choice: on the sample file DIB
# carries recognisable consumer brands (Philips, Diablo, DEWALT, Leviton, Satco) while E1 carries
# a mix that includes building-material product lines (LP SMARTSIDE, JAMESHARDIE). Neither is
# wrong, and where both are populated and disagree the disagreement is REPORTED rather than
# silently resolved by this ordering — see `BrandResolution.conflict`.
BRAND_COLUMNS: tuple[str, ...] = ("DIB_Brand", "E1_Brand", "Unilog_Brand")

INPUT_COLUMNS: tuple[str, ...] = (
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
)

# Tokens that mark a `Part_Manuf` value as a distributor, co-op or buying group rather than the
# manufacturer of the goods. Not exhaustive and not meant to be: it exists so the common cases
# are flagged instead of being written into MANUFACTURER_NAME as fact.
_NOT_A_MANUFACTURER = (
    "cooperative",
    "co-op",
    "dealers",
    "distribut",
    "supply",
    "building materials",
    "lumber",
    "wholesale",
    "buying group",
)


@dataclass(frozen=True)
class BrandResolution:
    """What the brand columns support, and how confident that is."""

    brand: str | None = None
    source_column: str | None = None
    candidates: tuple[tuple[str, str], ...] = ()
    """Every (column, value) pair carrying a real brand, in preference order."""

    conflict: bool = False
    """Two or more brand columns carry different real values. Reported, never auto-resolved:
    picking by column order would resolve a genuine disagreement by spreadsheet layout."""

    @property
    def resolved(self) -> bool:
        return self.brand is not None

    @property
    def needs_review(self) -> bool:
        return self.conflict

    def summary(self) -> dict[str, object]:
        return {
            "brand": self.brand,
            "source_column": self.source_column,
            "candidates": [{"column": c, "value": v} for c, v in self.candidates],
            "conflict": self.conflict,
        }


@dataclass(frozen=True)
class ManufacturerResolution:
    """What `Part_Manuf` supports about the manufacturer."""

    name: str | None = None
    supplier_code: str | None = None
    raw: str | None = None
    looks_like_a_distributor: bool = False
    """The value names a distributor or buying co-op rather than a manufacturer, so it must not
    be published as MANUFACTURER_NAME without confirmation against the approved master."""

    @property
    def resolved(self) -> bool:
        return self.name is not None

    @property
    def publishable_as_manufacturer(self) -> bool:
        """Whether this may be written into MANUFACTURER_NAME as-is.

        False for distributors, and the honest answer for everything else is still "probably not
        without the master list" — the client requires exact casing and legal suffixes from
        `UniCat_Manufacturer_and_Brand_List.xlsx`, which is not in this repository. Callers should
        treat True as "safe to propose", not "verified".
        """
        return self.resolved and not self.looks_like_a_distributor

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "supplier_code": self.supplier_code,
            "looks_like_a_distributor": self.looks_like_a_distributor,
            "publishable_as_manufacturer": self.publishable_as_manufacturer,
        }


@dataclass(frozen=True)
class SupplierRow:
    """One row of the Unilog item master, with sentinels resolved away."""

    raw: dict[str, str]
    mpn: str | None
    description: str | None
    brand: BrandResolution
    manufacturer: ManufacturerResolution
    notes: tuple[str, ...] = field(default=())

    @classmethod
    def parse(cls, row: dict[str, str]) -> SupplierRow:
        notes: list[str] = []

        mpn = clean(row.get("Mfg_Part_Num"))
        description = clean(row.get("Part_Desc"))
        if mpn is None:
            notes.append("no manufacturer part number: the row cannot be identified")
        if description is None:
            notes.append("no part description: nothing to classify or extract from")

        brand = resolve_brand(row)
        if brand.conflict:
            listed = ", ".join(f"{c}={v!r}" for c, v in brand.candidates)
            notes.append(f"brand columns disagree ({listed}); needs review")

        manufacturer = resolve_manufacturer(row.get("Part_Manuf"))
        if manufacturer.looks_like_a_distributor:
            notes.append(
                f"Part_Manuf {manufacturer.name!r} looks like a distributor or buying co-op "
                f"rather than a manufacturer; MANUFACTURER_NAME needs the approved master or "
                f"the manufacturer URL"
            )
        if not brand.resolved and not manufacturer.publishable_as_manufacturer:
            notes.append(
                "neither a brand nor a usable manufacturer can be established from this row; "
                "BRAND_NAME requires retrieval"
            )

        return cls(
            raw=dict(row),
            mpn=mpn,
            description=description,
            brand=brand,
            manufacturer=manufacturer,
            notes=tuple(notes),
        )

    @property
    def identified(self) -> bool:
        return self.mpn is not None

    def echo(self) -> dict[str, str]:
        """The six input columns exactly as supplied, sentinels included.

        Faithfulness matters more than cleanliness here: these columns exist so the client can
        join our output back to their input, and "improving" `-- Unbranded --` to an empty cell
        would break a diff they run against their own file.
        """
        return {column: self.raw.get(column, "") for column in INPUT_COLUMNS}

    def summary(self) -> dict[str, object]:
        return {
            "mpn": self.mpn,
            "description": self.description,
            "brand": self.brand.summary(),
            "manufacturer": self.manufacturer.summary(),
            "notes": list(self.notes),
        }


def strip_supplier_code(value: str) -> tuple[str, str | None]:
    """Split a trailing supplier code off a vendor name.

    ``"Kichler Lighting (KICLI)"`` -> ``("Kichler Lighting", "KICLI")``. Returns the code as well
    as the name because the code is the stable key: names get re-spelled between files and the
    code does not, so it is the better thing to remember a mapping against.
    """
    match = _SUPPLIER_CODE.search(value)
    if not match:
        return value.strip(), None
    return value[: match.start()].strip(), match.group(1)


def resolve_manufacturer(raw: str | None) -> ManufacturerResolution:
    """Read `Part_Manuf` into a manufacturer name, a code, and a warning where warranted."""
    cleaned = clean(raw)
    if cleaned is None:
        return ManufacturerResolution(raw=raw)

    name, code = strip_supplier_code(cleaned)
    if not name:
        return ManufacturerResolution(raw=raw, supplier_code=code)

    folded = name.casefold()
    distributor = any(token in folded for token in _NOT_A_MANUFACTURER)
    return ManufacturerResolution(
        name=name,
        supplier_code=code,
        raw=raw,
        looks_like_a_distributor=distributor,
    )


def resolve_brand(row: dict[str, str]) -> BrandResolution:
    """Choose among the competing brand columns, reporting disagreement rather than hiding it."""
    candidates: list[tuple[str, str]] = []
    for column in BRAND_COLUMNS:
        value = clean(row.get(column))
        if value is not None:
            candidates.append((column, value))

    if not candidates:
        return BrandResolution()

    distinct = {value.casefold() for _, value in candidates}
    column, value = candidates[0]
    return BrandResolution(
        brand=value,
        source_column=column,
        candidates=tuple(candidates),
        conflict=len(distinct) > 1,
    )


__all__ = [
    "BRAND_COLUMNS",
    "INPUT_COLUMNS",
    "BrandResolution",
    "ManufacturerResolution",
    "SupplierRow",
    "resolve_brand",
    "resolve_manufacturer",
    "strip_supplier_code",
]
