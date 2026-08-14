"""The Built-In Dishwasher class, pinned against the client's ground truth.

The attribute order in this class is a published column layout: slot *n* of the delivery format's
attribute grid is binding *n*. So the sequence is asserted against the client's real CSV rather
than against a list retyped here, which would only ever agree with our own mistakes.

Inserting an attribute above slot 15 shifts every subsequent column in a file the client ingests.
That is what these tests exist to prevent.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.normalize.units import QuantityKind
from axiom.normalize.units import registry as unit_registry
from axiom.schema import load_default
from axiom.schema.models import Datatype

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DELIVERY_CSV = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
CLASS_CODE = "APP.KIT.DISHWASHER.BUILTIN"

GRID_SLOTS = 15
"""How many of the class's bindings occupy numbered grid slots. Bindings past this point map to
their own named delivery columns (Warranty, With, Standard/Approvals...) instead."""


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def dishwasher(registry):
    return registry.product_class(CLASS_CODE)


def _ground_truth_labels() -> list[str]:
    """The label sequence the client actually published, read off their delivery file."""
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    labels: list[str] = []
    for slot in range(1, 51):
        label = rows[0][f"ATTRIBUTE_LABEL {slot}"].strip()
        if label:
            labels.append(label)
    return labels


requires_ground_truth = pytest.mark.skipif(
    not CLIENT_DELIVERY_CSV.exists(), reason="client delivery-format CSV not present"
)


# --------------------------------------------------------------------------- the slot template


@requires_ground_truth
def test_grid_labels_match_the_ground_truth_exactly(registry, dishwasher) -> None:
    """The whole basis for treating the grid as a per-class template."""
    expected = _ground_truth_labels()
    bindings = dishwasher.slot_bindings()[:GRID_SLOTS]
    actual = [b.label or registry.attribute(b.code).name for b in bindings]
    assert actual == expected


@requires_ground_truth
def test_ground_truth_uses_the_same_template_on_both_rows(registry, dishwasher) -> None:
    """Row 1 and row 2 share the label sequence; only the values are sparse against it."""
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 2
    for slot in range(1, 51):
        column = f"ATTRIBUTE_LABEL {slot}"
        assert rows[0][column] == rows[1][column], f"{column} differs between rows"


@requires_ground_truth
def test_grid_tail_is_unlabelled_in_ground_truth() -> None:
    """Slots past the class's binding count stay entirely blank, labels included."""
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    for slot in range(GRID_SLOTS + 1, 51):
        assert row[f"ATTRIBUTE_LABEL {slot}"] == ""
        assert row[f"ATTRIBUTE_VALUE {slot}"] == ""


def test_slot_bindings_preserve_declared_order(dishwasher) -> None:
    assert [b.code for b in dishwasher.slot_bindings()[:5]] == [
        "product_series",
        "model_number",
        "wash_cycle_count",
        "voltage_rating",
        "amperage_rating",
    ]


def test_named_column_attributes_sit_after_the_grid(dishwasher) -> None:
    """Anything inserted above slot 15 would shift a published column layout."""
    tail = [b.code for b in dishwasher.slot_bindings()[GRID_SLOTS:]]
    assert tail == [
        "warranty_terms",
        "included_technology",
        "approvals",
        "country_of_origin",
        "gtin",
    ]


# --------------------------------------------------------------------------- per-class labels


def test_shared_attributes_carry_a_class_specific_label(registry, dishwasher) -> None:
    """`product_series` is 'Product Series' in the dictionary and must print 'Series' here.

    Renaming the dictionary entry would change the label for the valve classes that also bind it.
    """
    assert registry.attribute("product_series").name == "Product Series"
    binding = dishwasher.binding("product_series")
    assert binding.label == "Series"
    assert binding.display_label == "Series"


def test_unlabelled_bindings_fall_back_to_the_dictionary_name(registry, dishwasher) -> None:
    binding = dishwasher.binding("voltage_rating")
    assert binding.label is None
    assert registry.attribute(binding.code).name == "Voltage Rating"


def test_valve_classes_are_unaffected_by_the_new_binding_field(registry) -> None:
    """The field is additive; existing classes declare no labels and must keep the dictionary."""
    valve = registry.product_class("PLB.VLV.BALL.2PC")
    assert all(b.label is None for b in valve.attributes)
    assert valve.binding("product_series").label is None


# --------------------------------------------------------------------------- the two hierarchies


def test_browse_path_renders_the_clients_classpath(dishwasher) -> None:
    assert ">".join(dishwasher.browse_path) == (
        "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers"
    )


def test_reporting_path_renders_dept_class_fine(dishwasher) -> None:
    assert dishwasher.reporting_path == ("Appliances", "Large Appliances", "Dishwashers")


@requires_ground_truth
def test_both_hierarchies_match_ground_truth_and_differ_from_each_other(dishwasher) -> None:
    """Neither tree derives from the other, which is why both are declared."""
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    assert ">".join(dishwasher.browse_path) == row["Classpath"]
    assert list(dishwasher.reporting_path) == [row["Dept"], row["Class"], row["Fine"]]
    assert ">".join(dishwasher.browse_path) != ">".join(dishwasher.reporting_path)


# --------------------------------------------------------------------------- unit typing


def test_uom_bearing_slots_are_exactly_the_typed_ones(registry, dishwasher) -> None:
    """Only quantity-like attributes produce an ATTRIBUTE_UOM value.

    Ground truth populates UOM at slots 4, 5, 9 and 12 (plus slot 10 on row 2 only, a known
    limitation recorded on `minimum_height`). Those four are voltage, amperage, depth and sound.
    """
    bindings = dishwasher.slot_bindings()[:GRID_SLOTS]
    unit_slots = [
        slot
        for slot, binding in enumerate(bindings, 1)
        if registry.attribute(binding.code).datatype.needs_unit
    ]
    assert unit_slots == [4, 5, 9, 12]


@requires_ground_truth
def test_ground_truth_uom_slots_agree(registry, dishwasher) -> None:
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    populated = [s for s in range(1, 51) if row[f"ATTRIBUTE_UOM {s}"].strip()]
    assert populated == [4, 5, 9, 12]


def test_declared_units_resolve_in_the_registry(registry, dishwasher) -> None:
    """A mistyped canonical unit is invisible in YAML review and corrupts every value."""
    for binding in dishwasher.attributes:
        attribute = registry.attribute(binding.code)
        if attribute.canonical_unit:
            assert unit_registry.resolve(attribute.canonical_unit) is not None, (
                f"{binding.code} declares unresolvable unit {attribute.canonical_unit!r}"
            )


def test_sound_level_uses_the_new_acoustic_kind(registry) -> None:
    attribute = registry.attribute("sound_level")
    assert attribute.canonical_unit == "dBA"
    resolved = unit_registry.resolve("dBA")
    assert resolved is not None and resolved.kind is QuantityKind.SOUND_LEVEL


def test_dba_spellings_resolve(registry) -> None:
    for spelling in ("dBA", "dba", "DBA", "db(a)"):
        resolved = unit_registry.resolve(spelling)
        assert resolved is not None and resolved.code == "dBA", spelling


def test_plain_db_is_deliberately_unresolvable() -> None:
    """dB and dBA are not interconvertible without the signal spectrum.

    Defining both with factor 1.0 would let "47 dB" and "47 dBA" read as the same measurement.
    For an appliance sound rating, where a few dB is the entire marketing claim, that is the same
    class of error as converting NPT into BSPT. Failing to resolve surfaces it instead.
    """
    assert unit_registry.resolve("dB") is None


def test_sound_level_prefers_quieter_candidates(registry) -> None:
    """Substitution direction: a louder appliance is not a substitute for a quieter one."""
    from axiom.schema.models import SubstitutionRule

    assert registry.attribute("sound_level").substitution is SubstitutionRule.AT_MOST


# --------------------------------------------------------------------------- honesty guards


def test_lov_dependent_attributes_are_not_enums(registry, dishwasher) -> None:
    """Declaring an enum from two observed rows would invent a closed vocabulary.

    `mounting_type` would permit exactly Leg and Built-in, then reject Free-standing. Enum
    snapping against a wrong vocabulary converts an unknown value into a confidently wrong one,
    which is worse than not snapping. These become enums when the client's LOV file arrives.
    """
    for code in ("mounting_type", "plug_type", "primary_material", "finish_color"):
        attribute = registry.attribute(code)
        assert attribute.datatype is Datatype.STRING
        assert attribute.allowed_values == ()


def test_size_and_heights_stay_strings(registry) -> None:
    """Their published form varies in axis count and carries qualifiers; typing them loses data."""
    for code in ("overall_size", "minimum_height", "maximum_height"):
        assert registry.attribute(code).datatype is Datatype.STRING


def test_delivery_channel_profile_requires_only_identity(registry, dishwasher) -> None:
    """Pre-flight must not refuse a whole row for a missing specification.

    A long required list would make a batch of rows with no manufacturer document emit nothing.
    Specs are gated individually by their own evidence requirement instead, so a row publishes
    what it can prove and leaves the rest blank — which is what ground truth looks like.
    """
    profile = dishwasher.channel("unilog_delivery")
    assert profile is not None
    assert profile.required == ("mpn",)


def test_class_declares_an_unspsc_even_though_ground_truth_omits_it(dishwasher) -> None:
    """We hold the code; the client left the column blank and the guide says that is deliberate.

    Declared here so it is available, but the builder must not populate a column the client
    intentionally left empty just because it can.
    """
    assert dishwasher.mappings.get("unspsc") == "52141505"
