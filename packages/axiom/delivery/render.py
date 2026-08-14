"""Rendering the five description rewrites.

The guide calls this most of the task: *"the same product information is rewritten five times at
five different lengths and casings - for the till receipt, the mobile app, the search results page,
the product page and the marketing copy."*

The important claim this module makes is that **four of the five are not generation at all.** They
are deterministic assembly from values that are already established, driven by recipes in
``schema/descriptions/``. That matters for three reasons:

* A template cannot introduce a fact. The claim-check problem disappears rather than being solved.
* Character limits are satisfiable by construction, not by asking a model nicely and hoping.
* The same product produces the same string on every run, which is what makes a catalogue's titles
  consistent enough for on-site search to work.

Only ``MARKETING_DESCRIPTION`` is genuinely generative, and it is manufacturer marketing copy — so
it belongs to retrieval plus a claim check, not here.

The one subtle mechanism is overflow handling. When a component does not fit the budget, the
renderer **skips it and continues** rather than stopping. That is forced by the client's own data,
not chosen: row 1 of the ground truth keeps a depth measurement and drops a sound level, while row 2
drops the depth and keeps the sound. See the note at the top of the recipe file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from axiom.core.product import ProductRecord
from axiom.delivery.format import Casing
from axiom.schema import SchemaRegistry

RECORD_TOKENS = frozenset(
    {
        "product_name",
        "brand",
        "brand_plain",
        "manufacturer_brand",
        "mpn",
        "sku",
        "with_clause",
    }
)

_SYMBOLS = re.compile(r"[\u00ae\u2122\u00a9]")

# Units that close up against their magnitude on the invoice line: "120V", not "120 V". The client
# writes them that way because a 40-character budget cannot afford eight spaces, and the house style
# that requires the space is a long-form rule.
_COMPACT_SPACE = re.compile(r"(?<=\d)\s+(?=[A-Za-z])")

# What may sit in front of a unit and still be a magnitude: a number, optionally with a decimal or
# an imperial fraction. "50-1/4" qualifies; "Professional" does not.
_MAGNITUDE = re.compile(r"\d+(?:\.\d+)?(?:-\d+/\d+)?|\d+/\d+")


class RecipeError(Exception):
    """Raised when a description recipe is inconsistent with the schema it renders from."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        bullets = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{len(problems)} description-recipe problem(s):\n{bullets}")


@dataclass(frozen=True)
class Component:
    """One element of a description, and how it joins to what precedes it."""

    source: str
    phrase: str = "{value}"
    separator: str = ", "
    abbreviate: bool = False
    """Shorten the value through the invoice-terms table."""

    compact_unit: bool = False
    """Close the gap between magnitude and unit: "120 V" -> "120V"."""

    omit_if_contains: str | None = None
    """Drop this component when its rendered value contains the given text.

    Exists for the ``With`` clause. Row 1's "With CleanBoost(TM)" is one feature and appears in the
    title; row 2's "With Washing 3rd Rack, Water Repellent Silverware Basket" is two and does not.
    A comma-bearing clause inside a comma-delimited description is ambiguous to read and to parse,
    so the rule is defensible on its own terms as well as reproducing both rows.
    """

    def render(self, value: str, *, invoice_terms: dict[str, str] | None = None) -> str | None:
        """Apply this component's transformations, or ``None`` if it should be omitted."""
        text = value.strip()
        if not text:
            return None
        if self.abbreviate and invoice_terms:
            text = _abbreviate(text, invoice_terms)
        if self.compact_unit:
            text = _COMPACT_SPACE.sub("", text)
        if self.omit_if_contains and self.omit_if_contains in text:
            return None
        rendered = self.phrase.replace("{value}", text).strip()
        return rendered or None


@dataclass(frozen=True)
class Recipe:
    """How one description column is constructed."""

    name: str
    column: str
    components: tuple[Component, ...]
    min_chars: int | None = None
    max_chars: int | None = None
    casing: Casing | None = None
    on_overflow: str = "skip"
    min_components: int | None = None
    """How many components the result must draw on to be worth publishing.

    The gate that stops this module producing fragments. A recipe with nine available inputs that
    found two has not produced the thing the formula describes: "DISHWASHER SST" is not a till line,
    it is a category and a colour, and it tells a buyer strictly less than the part number does.

    It also matters for how the output is judged. An absent description is a *missing* value, which
    is an honest gap the retrieval stage will close. A fragment is a *wrong* value, which ships. The
    two are not equally bad and the gate is what keeps them apart.
    """

    note: str | None = None

    @property
    def skips_on_overflow(self) -> bool:
        return self.on_overflow == "skip"


@dataclass
class RenderedDescription:
    """One description, plus what it drew on and what it had to leave out."""

    column: str
    text: str
    used: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    """(source, reason) for components that were available but not included."""

    @property
    def populated(self) -> bool:
        return bool(self.text)

    def summary(self) -> dict[str, object]:
        return {
            "column": self.column,
            "chars": len(self.text),
            "used": list(self.used),
            "dropped": [{"source": s, "reason": r} for s, r in self.dropped],
        }


class RecipeBook:
    """The description recipes for one product class."""

    def __init__(self, class_code: str, recipes: tuple[Recipe, ...]) -> None:
        self.class_code = class_code
        self._recipes = recipes
        self._by_name = {r.name: r for r in recipes}

    @classmethod
    def load(cls, path: Path | str) -> RecipeBook:
        path = Path(path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = payload.get("descriptions", payload)
        class_code = raw.get("class") or ""

        problems: list[str] = []
        recipes: list[Recipe] = []
        for entry in raw.get("recipes", []):
            name = entry.get("name") or "<unnamed>"
            column = entry.get("column")
            if not column:
                problems.append(f"recipe '{name}' declares no column")
                continue

            casing = None
            if entry.get("casing"):
                try:
                    casing = Casing(entry["casing"])
                except ValueError:
                    problems.append(f"recipe '{name}' has unknown casing {entry['casing']!r}")
                    continue

            overflow = entry.get("on_overflow", "skip")
            if overflow not in {"skip", "stop"}:
                problems.append(
                    f"recipe '{name}' declares on_overflow={overflow!r}; expected 'skip' or 'stop'"
                )
                continue

            components = tuple(
                Component(
                    source=c["source"],
                    phrase=c.get("phrase", "{value}"),
                    separator=c.get("separator", ", "),
                    abbreviate=bool(c.get("abbreviate", False)),
                    compact_unit=bool(c.get("compact_unit", False)),
                    omit_if_contains=c.get("omit_if_contains"),
                )
                for c in entry.get("components", [])
                if c.get("source")
            )
            if not components:
                problems.append(f"recipe '{name}' declares no components")
                continue

            recipes.append(
                Recipe(
                    name=name,
                    column=column,
                    components=components,
                    min_chars=entry.get("min_chars"),
                    max_chars=entry.get("max_chars"),
                    casing=casing,
                    on_overflow=overflow,
                    min_components=entry.get("min_components"),
                    note=" ".join(str(entry["note"]).split()) if entry.get("note") else None,
                )
            )

        if problems:
            raise RecipeError(problems)
        return cls(class_code, tuple(recipes))

    def __len__(self) -> int:
        return len(self._recipes)

    @property
    def recipes(self) -> tuple[Recipe, ...]:
        return self._recipes

    def recipe(self, name: str) -> Recipe:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"no recipe named {name!r}") from exc

    def validate_against(self, registry: SchemaRegistry) -> list[str]:
        """Every component source must be a record token or an attribute the class binds.

        A recipe referencing an attribute the class does not bind can never render, which is
        exactly the kind of silent dead configuration the schema registry refuses elsewhere.
        """
        problems: list[str] = []
        try:
            definition = registry.product_class(self.class_code)
        except KeyError:
            return [f"recipe book targets unknown class {self.class_code!r}"]
        bound = {b.code for b in definition.attributes}
        for recipe in self._recipes:
            for component in recipe.components:
                if component.source in RECORD_TOKENS or component.source in bound:
                    continue
                problems.append(
                    f"recipe '{recipe.name}' references {component.source!r}, which is neither a "
                    f"record token nor an attribute bound by {self.class_code}"
                )
        return problems


def default_recipe_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "schema" / "descriptions"


def load_recipe_books() -> dict[str, RecipeBook]:
    """Every recipe book on disk, keyed by class code."""
    directory = default_recipe_dir()
    if not directory.is_dir():
        return {}
    books: dict[str, RecipeBook] = {}
    for path in sorted(directory.glob("*.yaml")):
        book = RecipeBook.load(path)
        if book.class_code:
            books[book.class_code] = book
    return books


def load_invoice_terms(path: Path | str | None = None) -> dict[str, str]:
    """Canonical value -> short form, for the till line."""
    if path is None:
        path = Path(__file__).resolve().parents[3] / "schema" / "abbreviations.yaml"
    path = Path(path)
    if not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (payload.get("invoice_terms") or {}).items()}


def _abbreviate(text: str, terms: dict[str, str]) -> str:
    """Replace a canonical value with its short form. Longest key first."""
    folded = text.casefold()
    for key in sorted(terms, key=len, reverse=True):
        if folded == key.casefold():
            return terms[key]
    return text


def split_display_unit(display: str) -> tuple[str, str | None]:
    """Split a rendered value into (magnitude, unit code) using the unit registry.

    Shared by the attribute grid and the description recipes, because both want the unit a *human*
    reads rather than the one we store in. ``normalize`` canonicalises a depth to millimetres and
    renders it as ``50-1/4"``; the client writes ``50-1/4`` beside a separate ``in`` in the grid,
    and ``50-1/4 in Depth With Door Open`` in the long description. Emitting ``mm`` would put the
    wrong unit in a column their importer reads, and passing the ``"`` glyph through would produce
    ``50-1/4"IN`` on the till line.

    The trailing token is matched against every spelling the registry knows, longest suffix first,
    and reported as that unit's canonical *code*. So ``"`` resolves to ``in``: the glyph is a
    display convention and the code is the identifier.
    """
    from axiom.normalize.units import registry as unit_registry

    text = display.strip()
    if not text:
        return "", None

    match = re.search(r"[^\d\s.,/\-]+$", text)
    if match:
        candidate = match.group(0)
        for length in range(len(candidate), 0, -1):
            resolved = unit_registry.resolve(candidate[-length:])
            if resolved is None:
                continue
            magnitude = text[: len(text) - length].strip()
            # The magnitude must actually be a magnitude. Without this guard "Professional Series"
            # splits into ("Professional", "s") — `s` is the canonical unit for seconds — and every
            # value ending in a letter that happens to name a unit gets quietly truncated.
            if magnitude and _MAGNITUDE.fullmatch(magnitude):
                return magnitude, resolved.code
    return text, None


def display_with_unit_code(display: str) -> str:
    """A display value with its unit spelled as the registry code: ``50-1/4"`` -> ``50-1/4 in``."""
    magnitude, unit = split_display_unit(display)
    return f"{magnitude} {unit}" if unit else magnitude


# ------------------------------------------------------------------ value resolution


def _record_token(
    token: str, record: ProductRecord, registry: SchemaRegistry, *, brand: str | None
) -> str | None:
    if token == "mpn":
        return record.mpn_normalized or record.mpn
    if token == "sku":
        return record.sku
    if token == "brand":
        return brand
    if token == "brand_plain":
        return _SYMBOLS.sub("", brand).strip() if brand else None
    if token == "product_name":
        if not record.class_code:
            return None
        try:
            return registry.product_class(record.class_code).product_noun
        except KeyError:
            return None
    if token == "with_clause":
        value = record.get("included_technology")
        return value.value_display if value and value.is_publishable else None
    return None


def _manufacturer_brand(manufacturer: str | None, brand: str | None) -> str | None:
    """Join manufacturer and brand, dropping the redundant one.

    "Rheem Manufacturing" + "FRIGIDAIRE" share nothing, so both are informative. "Whirlpool
    Corporation" + "Whirlpool" do not: printing both would spend a third of a 60-80 character
    budget restating the same company. When one contains the other, the shorter survives — it is
    the brand a buyer recognises, and the corporate suffix adds nothing on a phone screen.
    """
    plain_brand = _SYMBOLS.sub("", brand).strip() if brand else None
    if not manufacturer:
        return plain_brand
    if not plain_brand:
        return manufacturer
    if plain_brand.casefold() in manufacturer.casefold():
        return plain_brand
    if manufacturer.casefold() in plain_brand.casefold():
        return plain_brand
    return f"{manufacturer} {plain_brand}"


def _attribute_value(record: ProductRecord, code: str) -> str | None:
    value = record.get(code)
    if value is None or not value.is_publishable:
        return None
    if value.value_display:
        # Respell the unit as its registry code. The client writes "50-1/4 in Depth With Door Open",
        # never '50-1/4" Depth With Door Open'.
        return display_with_unit_code(value.value_display)
    return str(value.value_canonical) if value.value_canonical is not None else None


# ------------------------------------------------------------------ assembly


def render(
    recipe: Recipe,
    record: ProductRecord,
    registry: SchemaRegistry,
    *,
    brand: str | None = None,
    manufacturer: str | None = None,
    invoice_terms: dict[str, str] | None = None,
) -> RenderedDescription:
    """Assemble one description.

    Components are appended in declared order while the running length stays inside the budget. A
    component that would overflow is skipped and the next tried, unless the recipe declares
    ``on_overflow: stop``.
    """
    out = RenderedDescription(column=recipe.column, text="")
    terms = invoice_terms if invoice_terms is not None else {}
    pieces: list[str] = []
    length = 0

    for component in recipe.components:
        if component.source == "manufacturer_brand":
            raw = _manufacturer_brand(manufacturer, brand)
        elif component.source in RECORD_TOKENS:
            raw = _record_token(component.source, record, registry, brand=brand)
        else:
            raw = _attribute_value(record, component.source)

        if not raw:
            continue

        rendered = component.render(raw, invoice_terms=terms)
        if rendered is None:
            out.dropped.append((component.source, "omitted by rule"))
            continue

        separator = "" if not pieces else component.separator
        addition = len(separator) + len(rendered)

        if recipe.max_chars is not None and length + addition > recipe.max_chars:
            out.dropped.append(
                (
                    component.source,
                    f"would take the length to {length + addition}, over the "
                    f"{recipe.max_chars}-character limit",
                )
            )
            if recipe.skips_on_overflow:
                continue
            break

        pieces.append(f"{separator}{rendered}")
        length += addition
        out.used.append(component.source)

    # The completeness gate, applied before casing so the withheld text is reported as assembled.
    if recipe.min_components is not None and len(out.used) < recipe.min_components:
        out.dropped.append(
            (
                "<completeness>",
                f"drew on {len(out.used)} of a required {recipe.min_components} components "
                f"({', '.join(out.used) or 'none'}); withheld rather than published as a "
                f"fragment, because a fragment is a wrong value where an absence is an honest gap",
            )
        )
        out.text = ""
        return out

    text = "".join(pieces).strip()
    if recipe.casing is Casing.UPPER:
        text = text.upper()
    elif recipe.casing is Casing.LOWER:
        text = text.lower()

    # A minimum is reported, never padded. Padding to reach 60 characters would mean adding words
    # that say nothing, which is the definition of the filler this system exists to avoid.
    if recipe.min_chars is not None and text and len(text) < recipe.min_chars:
        out.dropped.append(
            (
                "<length>",
                f"result is {len(text)} characters, under the {recipe.min_chars}-character "
                f"minimum; not padded because padding would add words carrying no information",
            )
        )

    out.text = text
    return out


def render_all(
    book: RecipeBook,
    record: ProductRecord,
    registry: SchemaRegistry,
    *,
    brand: str | None = None,
    manufacturer: str | None = None,
    invoice_terms: dict[str, str] | None = None,
) -> dict[str, RenderedDescription]:
    """Render every recipe in a book, keyed by delivery column name."""
    terms = invoice_terms if invoice_terms is not None else load_invoice_terms()
    return {
        recipe.column: render(
            recipe,
            record,
            registry,
            brand=brand,
            manufacturer=manufacturer,
            invoice_terms=terms,
        )
        for recipe in book.recipes
    }


__all__ = [
    "RECORD_TOKENS",
    "Component",
    "Recipe",
    "RecipeBook",
    "RecipeError",
    "RenderedDescription",
    "default_recipe_dir",
    "load_invoice_terms",
    "load_recipe_books",
    "render",
    "render_all",
]
