"""The five description rewrites.

The guide calls getting these formats right "most of the task", so the headline test here feeds the
client's own attribute values into the recipes and demands their exact strings back — all five
formats, both rows, character for character. That measures the *renderer* while holding extraction
constant, which is the only way to test it honestly while retrieval is unavailable.

The rest of the file pins the two mechanisms that make it work, both of which were forced by the
ground truth rather than chosen: skip-don't-stop overflow, and the completeness gate that stops a
two-component fragment being published as a description.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.core.evidence import EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.delivery.format import Casing
from axiom.delivery.render import (
    RECORD_TOKENS,
    Component,
    Recipe,
    RecipeBook,
    RecipeError,
    default_recipe_dir,
    load_invoice_terms,
    load_recipe_books,
    render,
    render_all,
)
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
DISHWASHER = "APP.KIT.DISHWASHER.BUILTIN"
SHA = "f" * 64

# The class's grid slots, in declared order. Slot n of the client's grid is binding n.
SLOTS = [
    "product_series",
    "model_number",
    "wash_cycle_count",
    "voltage_rating",
    "amperage_rating",
    "mounting_type",
    "plug_type",
    "overall_size",
    "depth_with_door_open",
    "minimum_height",
    "maximum_height",
    "sound_level",
    "primary_material",
    "finish_color",
    "additional_information",
]

DESCRIPTION_COLUMNS = ("INVOICE_DESC", "MOBILE_DESC", "SHORT_DESC", "RETAIL_DESC", "LONG_DESC1")


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def books():
    return load_recipe_books()


@pytest.fixture(scope="module")
def book(books):
    return books[DISHWASHER]


@pytest.fixture(scope="module")
def terms():
    return load_invoice_terms()


@pytest.fixture(scope="module")
def truth_rows():
    if not GROUND_TRUTH.exists():
        pytest.skip("client delivery-format CSV not present")
    with open(GROUND_TRUTH, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _accepted(code: str, display: str) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=display,
        value_canonical=display,
        value_display=display,
        method=DerivationMethod.SUPPLIER_FEED,
        confidence=1.0,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[
            EvidenceSpan(
                span_id=f"gt-{code}",
                document_id="ground-truth",
                document_sha256=SHA,
                quote=display,
                quote_verified=True,
                match_score=1.0,
            )
        ],
    )


def _record_from(row: dict[str, str]) -> ProductRecord:
    """A record holding exactly the attribute values the client's row states."""
    record = ProductRecord(
        tenant_id="unilog",
        sku=row["Mfg_Part_Num"],
        mpn=row["Mfg_Part_Num"],
        class_code=DISHWASHER,
    )
    for slot, code in enumerate(SLOTS, 1):
        value = row[f"ATTRIBUTE_VALUE {slot}"].strip()
        if not value:
            continue
        uom = row[f"ATTRIBUTE_UOM {slot}"].strip()
        record.add_value(_accepted(code, f"{value} {uom}" if uom else value))
    if row["With"].strip():
        record.add_value(_accepted("included_technology", row["With"].strip()))
    return record


# --------------------------------------------------------------------------- the headline


def test_every_format_reproduces_the_clients_string_exactly(registry, book, terms, truth_rows):
    """All five formats, both rows, character for character.

    If this passes, the construction formulas are right and the only thing standing between us and
    the client's descriptions is the attribute values themselves.
    """
    failures: list[str] = []
    for row in truth_rows:
        record = _record_from(row)
        rendered = render_all(
            book,
            record,
            registry,
            brand=row["BRAND_NAME"],
            manufacturer=row["MANUFACTURER_NAME"],
            invoice_terms=terms,
        )
        for column in DESCRIPTION_COLUMNS:
            expected, actual = row[column].strip(), rendered[column].text
            if expected != actual:
                failures.append(
                    f"{row['Mfg_Part_Num']} {column}:\n"
                    f"      expected {expected!r}\n"
                    f"      actual   {actual!r}"
                )
    assert not failures, "\n".join(failures)


def test_all_five_formats_are_declared(book):
    assert {r.column for r in book.recipes} == set(DESCRIPTION_COLUMNS)


def test_rendered_lengths_match_the_client(registry, book, terms, truth_rows):
    """Length equality is a weaker check than string equality, but it localises a failure fast."""
    for row in truth_rows:
        rendered = render_all(
            book,
            _record_from(row),
            registry,
            brand=row["BRAND_NAME"],
            manufacturer=row["MANUFACTURER_NAME"],
            invoice_terms=terms,
        )
        for column in DESCRIPTION_COLUMNS:
            assert len(rendered[column].text) == len(row[column].strip()), column


# --------------------------------------------------------------------------- overflow


def test_overflow_skips_rather_than_stops(registry, book, terms, truth_rows):
    """The rule the client's own rows force.

    Row 1 keeps a depth measurement and drops the sound level; row 2 drops the depth and keeps the
    sound. A renderer that stopped at the first component that did not fit could not produce row 2.
    """
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    invoice = book.recipe("invoice")

    first = render(
        invoice,
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    )
    assert "depth_with_door_open" in first.used
    assert "sound_level" not in first.used

    second = render(
        invoice,
        _record_from(by_mpn["WDTS7024RZ"]),
        registry,
        brand="Whirlpool\u00ae",
        manufacturer="Whirlpool Corporation",
        invoice_terms=terms,
    )
    assert "depth_with_door_open" not in second.used
    assert "sound_level" in second.used
    # And the skip is explained, not silent.
    assert any(source == "depth_with_door_open" for source, _ in second.dropped)


def test_a_stop_recipe_halts_at_the_first_overflow(registry, terms):
    recipe = Recipe(
        name="t",
        column="INVOICE_DESC",
        max_chars=14,
        on_overflow="stop",
        components=(
            Component(source="product_name", separator=" "),
            Component(source="overall_size", separator=" "),
            Component(source="primary_material", separator=" "),
        ),
    )
    record = ProductRecord(tenant_id="t", sku="S", mpn="S", class_code=DISHWASHER)
    record.add_value(_accepted("overall_size", "a very long size indeed"))
    record.add_value(_accepted("primary_material", "SST"))
    out = render(recipe, record, load_default(), invoice_terms=terms)
    assert out.used == ["product_name"]
    assert "primary_material" not in out.used


def test_declared_limits_are_never_exceeded(registry, book, terms, truth_rows):
    for row in truth_rows:
        rendered = render_all(
            book,
            _record_from(row),
            registry,
            brand=row["BRAND_NAME"],
            manufacturer=row["MANUFACTURER_NAME"],
            invoice_terms=terms,
        )
        for recipe in book.recipes:
            text = rendered[recipe.column].text
            if recipe.max_chars is not None and text:
                assert len(text) <= recipe.max_chars, recipe.column


# --------------------------------------------------------------------------- the invoice line


def test_invoice_is_uppercase_and_within_forty(registry, book, terms, truth_rows):
    for row in truth_rows:
        text = render(
            book.recipe("invoice"),
            _record_from(row),
            registry,
            brand=row["BRAND_NAME"],
            manufacturer=row["MANUFACTURER_NAME"],
            invoice_terms=terms,
        ).text
        assert text == text.upper()
        assert len(text) <= 40


def test_invoice_abbreviates_values(registry, book, terms, truth_rows):
    """Stainless Steel -> SST, Built-in -> BLTLN. A shortening, not a truncation."""
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    text = render(
        book.recipe("invoice"),
        _record_from(by_mpn["WDTS7024RZ"]),
        registry,
        brand="Whirlpool\u00ae",
        manufacturer="Whirlpool Corporation",
        invoice_terms=terms,
    ).text
    assert "BLTLN" in text
    assert "SST" in text
    assert "STAINLESS STEEL" not in text


def test_invoice_closes_up_units(registry, book, terms, truth_rows):
    """"120 V" becomes "120V" here, though the house style requires the space in long form."""
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    invoice = render(
        book.recipe("invoice"),
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    ).text
    long_form = render(
        book.recipe("long"),
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    ).text
    assert "120V" in invoice and "120 V" not in invoice
    assert "120 V" in long_form


# --------------------------------------------------------------------------- brand handling


def test_manufacturer_and_brand_are_both_kept_when_distinct(registry, book, terms, truth_rows):
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    text = render(
        book.recipe("mobile"),
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    ).text
    assert text.startswith("Rheem Manufacturing FRIGIDAIRE")
    # Symbols are stripped in the mobile line but kept in the title.
    assert "\u00ae" not in text


def test_a_redundant_corporate_suffix_is_dropped(registry, book, terms, truth_rows):
    """"Whirlpool Corporation Whirlpool" would spend a third of a 60-80 budget on a repetition."""
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    text = render(
        book.recipe("mobile"),
        _record_from(by_mpn["WDTS7024RZ"]),
        registry,
        brand="Whirlpool\u00ae",
        manufacturer="Whirlpool Corporation",
        invoice_terms=terms,
    ).text
    assert text.startswith("Whirlpool,")
    assert "Corporation" not in text


def test_title_keeps_the_registered_symbol(registry, book, terms, truth_rows):
    """The client requires brands to match exactly, symbols and all."""
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    text = render(
        book.recipe("short"),
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    ).text
    assert text.startswith("FRIGIDAIRE\u00ae ")


# --------------------------------------------------------------------------- the With clause


def test_a_single_feature_clause_is_included(registry, book, terms, truth_rows):
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    text = render(
        book.recipe("short"),
        _record_from(by_mpn["PDSH4816AF"]),
        registry,
        brand="FRIGIDAIRE\u00ae",
        manufacturer="Rheem Manufacturing",
        invoice_terms=terms,
    ).text
    assert "With CleanBoost\u2122" in text


def test_a_comma_bearing_clause_is_omitted(registry, book, terms, truth_rows):
    """Row 2's clause names two features. A comma inside a comma-delimited description is
    ambiguous to read and to parse, and omitting it is what reproduces the client's row."""
    by_mpn = {r["Mfg_Part_Num"]: r for r in truth_rows}
    result = render(
        book.recipe("short"),
        _record_from(by_mpn["WDTS7024RZ"]),
        registry,
        brand="Whirlpool\u00ae",
        manufacturer="Whirlpool Corporation",
        invoice_terms=terms,
    )
    assert "With" not in result.text
    assert any(source == "with_clause" for source, _ in result.dropped)


# --------------------------------------------------------------------------- the completeness gate


def test_a_fragment_is_withheld_not_published(registry, book, terms):
    """The trap this gate exists for.

    With only a material known, the invoice recipe would assemble "DISHWASHER SST". That scores as a
    *wrong* value where an empty cell scores as *missed*, and it tells a picker less than the part
    number printed beside it. So it is withheld.
    """
    record = ProductRecord(tenant_id="unilog", sku="X", mpn="X", class_code=DISHWASHER)
    record.add_value(_accepted("primary_material", "Stainless Steel"))

    result = render(book.recipe("invoice"), record, registry, invoice_terms=terms)
    assert result.text == ""
    reasons = [r for s, r in result.dropped if s == "<completeness>"]
    assert reasons and "2 of a required 4" in reasons[0]


def test_the_gate_releases_once_enough_is_known(registry, book, terms):
    record = ProductRecord(tenant_id="unilog", sku="X", mpn="X", class_code=DISHWASHER)
    record.add_value(_accepted("primary_material", "Stainless Steel"))
    record.add_value(_accepted("mounting_type", "Leg"))
    record.add_value(_accepted("voltage_rating", "120 V"))

    result = render(book.recipe("invoice"), record, registry, invoice_terms=terms)
    assert result.text == "DISHWASHER LEG SST 120V"
    assert len(result.used) == 4


def test_every_recipe_declares_a_completeness_floor(book):
    """Without one a recipe can emit a one-word fragment, which is the failure mode that matters."""
    for recipe in book.recipes:
        assert recipe.min_components is not None, recipe.name
        assert recipe.min_components >= 3, recipe.name


def test_a_short_result_is_reported_never_padded(registry, terms):
    """Padding to reach a minimum would mean adding words that carry no information."""
    recipe = Recipe(
        name="t",
        column="MOBILE_DESC",
        min_chars=60,
        components=(
            Component(source="product_name", separator=""),
            Component(source="primary_material"),
        ),
    )
    record = ProductRecord(tenant_id="t", sku="S", mpn="S", class_code=DISHWASHER)
    record.add_value(_accepted("primary_material", "Stainless Steel"))
    out = render(recipe, record, load_default(), invoice_terms=terms)
    assert out.text == "Dishwasher, Stainless Steel"
    assert any(source == "<length>" for source, _ in out.dropped)


def test_unpublishable_values_do_not_reach_a_description(registry, book, terms):
    """A description composed from a queued value would leak past the risk gate."""
    record = ProductRecord(tenant_id="unilog", sku="X", mpn="X", class_code=DISHWASHER)
    queued = _accepted("mounting_type", "Leg")
    queued.status = ValueStatus.QUEUED_FOR_REVIEW
    record.add_value(queued)
    record.add_value(_accepted("primary_material", "Stainless Steel"))

    result = render(book.recipe("invoice"), record, registry, invoice_terms=terms)
    assert "LEG" not in result.text
    assert "mounting_type" not in result.used


# --------------------------------------------------------------------------- recipe integrity


def test_recipes_reference_only_bound_attributes(registry, books):
    """A recipe naming an unbound attribute can never render — dead configuration."""
    for class_code, recipe_book in books.items():
        assert recipe_book.validate_against(registry) == [], class_code


def test_record_tokens_are_a_closed_set(book):
    bound = {b.code for b in load_default().product_class(DISHWASHER).attributes}
    for recipe in book.recipes:
        for component in recipe.components:
            assert component.source in RECORD_TOKENS or component.source in bound


def test_recipe_dir_exists(books):
    assert default_recipe_dir().is_dir()
    assert DISHWASHER in books


def test_unknown_casing_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "descriptions:\n  class: X\n  recipes:\n    - name: t\n      column: C\n"
        "      casing: sideways\n      components:\n        - {source: mpn}\n",
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="unknown casing"):
        RecipeBook.load(path)


def test_unknown_overflow_policy_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "descriptions:\n  class: X\n  recipes:\n    - name: t\n      column: C\n"
        "      on_overflow: shrug\n      components:\n        - {source: mpn}\n",
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="on_overflow"):
        RecipeBook.load(path)


def test_a_recipe_with_no_components_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "descriptions:\n  class: X\n  recipes:\n    - name: t\n      column: C\n"
        "      components: []\n",
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="no components"):
        RecipeBook.load(path)


def test_a_recipe_with_no_column_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "descriptions:\n  class: X\n  recipes:\n    - name: t\n"
        "      components:\n        - {source: mpn}\n",
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="no column"):
        RecipeBook.load(path)


def test_component_casing_is_applied_after_assembly():
    component = Component(source="x", phrase="{value} Mounting")
    assert component.render("Leg") == "Leg Mounting"
    assert Casing.UPPER.applies_to("LEG MOUNTING")


def test_component_drops_an_empty_value():
    assert Component(source="x").render("   ") is None


def test_invoice_terms_load_from_the_repository(terms):
    assert terms.get("Stainless Steel") == "SST"
    assert terms.get("Built-in") == "BLTLN"


def test_missing_invoice_terms_file_degrades_quietly(tmp_path):
    assert load_invoice_terms(tmp_path / "nope.yaml") == {}
