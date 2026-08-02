"""Brand unification and manufacturer part number cleansing.

Unglamorous and disproportionately valuable. A catalogue holding "Sq. D", "Square-D" and
"SCHNEIDER/SQUARE D" as three separate brands has three broken facets, three sets of search
results, and no way to report on a supplier line. Same for part numbers: distributors prepend
their own vendor codes on import, and those prefixes then block every attempt to match the
item back to the manufacturer's own data.

Deliberately deterministic. Brand resolution is a closed vocabulary problem, and a lookup
table that a merchandiser can read and correct beats a model that is right most of the time
and unexplainable when it is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_BRANDS_PATH = Path(__file__).resolve().parents[3] / "schema" / "brands.yaml"

# Corporate suffixes that carry no identifying information.
_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co", "llc", "ltd",
    "limited", "gmbh", "sa", "nv", "bv", "plc", "lp", "llp", "holdings", "group",
    "international", "intl", "industries", "mfg", "manufacturing",
)


def fold_brand(raw: str) -> str:
    """Reduce a brand string to a comparable key.

    Strips punctuation, collapses whitespace, and removes trailing corporate suffixes so
    "NIBCO Inc." and "nibco" converge. Suffixes are only stripped from the end — "Industries"
    inside a name can be load-bearing.
    """
    text = re.sub(r"[^a-z0-9\s]", " ", raw.lower())
    tokens = [t for t in text.split() if t]
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return "".join(tokens)


@dataclass(frozen=True)
class Brand:
    brand_id: str
    name: str
    aliases: tuple[str, ...] = ()
    mpn_prefixes: tuple[str, ...] = ()

    def lookup_keys(self) -> set[str]:
        """Folded forms this brand should be findable by.

        Not named ``keys`` because that collides with the mapping protocol and makes callers
        (and linters) read the object as a dict.
        """
        return {fold_brand(self.name), *(fold_brand(a) for a in self.aliases)} - {""}


@dataclass(frozen=True)
class BrandResolution:
    brand: Brand | None
    confidence: float
    method: str
    raw: str

    @property
    def resolved(self) -> bool:
        return self.brand is not None


class BrandMaster:
    """Alias-resolving brand registry."""

    def __init__(self, brands: list[Brand]) -> None:
        self._brands = {b.brand_id: b for b in brands}
        self._lookup: dict[str, Brand] = {}
        for brand in brands:
            for key in brand.lookup_keys():
                self._lookup.setdefault(key, brand)

    @classmethod
    def load(cls, path: Path | str | None = None) -> BrandMaster:
        source = Path(path or DEFAULT_BRANDS_PATH)
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        brands = [
            Brand(
                brand_id=entry["brand_id"],
                name=entry["name"],
                aliases=tuple(entry.get("aliases", ())),
                mpn_prefixes=tuple(entry.get("mpn_prefixes", ())),
            )
            for entry in payload.get("brands", [])
        ]
        return cls(brands)

    def __len__(self) -> int:
        return len(self._brands)

    def get(self, brand_id: str) -> Brand | None:
        return self._brands.get(brand_id)

    @property
    def brand_ids(self) -> list[str]:
        return sorted(self._brands)

    def resolve(self, raw: str | None) -> BrandResolution:
        """Resolve a raw brand string to a canonical brand.

        Abstains rather than guessing. An unresolved brand becomes a review item; a wrongly
        resolved one silently merges two manufacturers' catalogues, which is far worse and
        much harder to notice.
        """
        if not raw or not raw.strip():
            return BrandResolution(None, 0.0, "empty", raw or "")

        key = fold_brand(raw)
        if not key:
            return BrandResolution(None, 0.0, "empty", raw)

        if brand := self._lookup.get(key):
            return BrandResolution(brand, 1.0, "exact", raw)

        # A combined string such as "Apollo/Conbraco" or "Zurn - Wilkins" may name a brand in
        # one of its parts. Splitting is safe because each part is checked against the same
        # closed vocabulary.
        for part in re.split(r"[/,;&+]| - ", raw):
            part_key = fold_brand(part)
            if part_key and (brand := self._lookup.get(part_key)):
                return BrandResolution(brand, 0.90, "component", raw)

        return BrandResolution(None, 0.0, "unresolved", raw)


# Separators that appear inside part numbers with no semantic weight.
_MPN_NOISE = re.compile(r"[\s\u2010-\u2015\u2212._/\\]+")


def clean_mpn(raw: str | None, *, brand: Brand | None = None) -> str | None:
    """Cleanse a manufacturer part number for storage and matching.

    Uppercases, strips whitespace and separator noise, and removes any known vendor prefix
    for the brand. Returns None for empty input rather than an empty string, so an absent
    part number cannot masquerade as a present one.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip().upper()
    if brand:
        for prefix in brand.mpn_prefixes:
            candidate = prefix.upper()
            if text.startswith(candidate) and len(text) > len(candidate):
                text = text[len(candidate) :]
                break

    cleaned = _MPN_NOISE.sub("", text)
    cleaned = re.sub(r"[^A-Z0-9\-]", "", cleaned)
    return cleaned or None


def mpn_match_key(raw: str | None, *, brand: Brand | None = None) -> str | None:
    """Separator-insensitive key for blocking and duplicate detection.

    ``BA-100-075``, ``BA100075`` and ``ba 100.075`` all collapse to ``BA100075``.

    Note what this deliberately does *not* do: it does not strip leading zeros inside
    segments. Two goals were originally wanted here — ignore separators, and treat
    ``BA-0100-075`` as ``BA-100-075`` — and they are mutually exclusive in a single key,
    because zero-stripping needs segment boundaries and separator-stripping destroys them.
    Separator variation is far more common in real supplier data, so it wins; zero variation
    is handled by :func:`mpn_variants` instead.

    Used only for *candidate generation*. Deciding two items are genuinely the same still
    requires comparing attributes, because distinct products do share normalised keys.
    """
    cleaned = clean_mpn(raw, brand=brand)
    if not cleaned:
        return None
    return cleaned.replace("-", "")


def mpn_variants(raw: str | None, *, brand: Brand | None = None) -> set[str]:
    """Every plausible spelling of a part number, for blocking against messy data.

    Blocking indexes all variants rather than relying on one canonical form, which is the
    standard way to handle the fact that no single normalisation covers every real spelling.
    """
    cleaned = clean_mpn(raw, brand=brand)
    if not cleaned:
        return set()

    variants = {cleaned, cleaned.replace("-", "")}
    if raw:
        variants.add(raw.strip().upper())

    # Zero-stripped per segment: catches BA-0100-075 against BA-100-075. Only meaningful
    # while the separators are still present to define the segments.
    if "-" in cleaned:
        stripped = [segment.lstrip("0") or "0" for segment in cleaned.split("-")]
        variants.add("-".join(stripped))
        variants.add("".join(stripped))

    return {v for v in variants if v}
