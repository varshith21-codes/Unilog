"""The delivery contract, checked against the client's own file.

The first test here is the most important test in the repository for this deliverable. Every
other quality measure is a matter of degree — an attribute can be 90% right and still useful. The
header is binary: if our columns do not line up with theirs, the file cannot be ingested and the
enrichment behind it is worth nothing. So it is asserted against the real CSV rather than against
a fixture we wrote, because a fixture we wrote would agree with our mistakes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.delivery import (
    Casing,
    DeliveryFormat,
    DeliveryFormatError,
    Provenance,
    default_format_path,
    load_default,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DELIVERY_CSV = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"


def _client_header() -> list[str]:
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        return next(csv.reader(handle))


@pytest.fixture(scope="module")
def fmt() -> DeliveryFormat:
    return load_default()


# --------------------------------------------------------------------------- the contract


@pytest.mark.skipif(
    not CLIENT_DELIVERY_CSV.exists(),
    reason="client delivery-format CSV not present in this checkout",
)
def test_header_is_byte_identical_to_the_clients(fmt: DeliveryFormat) -> None:
    """Our expanded header must equal the client's, exactly and in order.

    Reported as a positional diff rather than a bare assertion. A 252-column mismatch shown as
    "tuples differ" is unactionable; the first differing index tells you which section drifted.
    """
    expected = _client_header()
    actual = list(fmt.header)

    assert len(actual) == len(expected), (
        f"column count differs: contract has {len(actual)}, client file has {len(expected)}"
    )

    mismatches = [
        (i, e, a) for i, (e, a) in enumerate(zip(expected, actual, strict=True)) if e != a
    ]
    assert not mismatches, "column mismatch at " + "; ".join(
        f"index {i}: client {e!r} != contract {a!r}" for i, e, a in mismatches[:5]
    )


def test_contract_declares_252_columns(fmt: DeliveryFormat) -> None:
    assert len(fmt) == 252


def test_column_names_are_unique(fmt: DeliveryFormat) -> None:
    """A duplicate header silently drops one column on export, with no error anywhere."""
    header = fmt.header
    assert len(set(header)) == len(header)


def test_indices_are_dense_and_ordered(fmt: DeliveryFormat) -> None:
    assert [c.index for c in fmt.columns] == list(range(len(fmt)))


# --------------------------------------------------------------------------- repeating blocks


def test_attribute_grid_is_slot_major(fmt: DeliveryFormat) -> None:
    """Slots interleave label/value/UOM. Role-major expansion would misalign every value."""
    grid = [c.name for c in fmt.group("attribute_grid")]
    assert grid[:6] == [
        "ATTRIBUTE_LABEL 1",
        "ATTRIBUTE_VALUE 1",
        "ATTRIBUTE_UOM 1",
        "ATTRIBUTE_LABEL 2",
        "ATTRIBUTE_VALUE 2",
        "ATTRIBUTE_UOM 2",
    ]
    assert grid[-1] == "ATTRIBUTE_UOM 50"
    assert len(grid) == 150


def test_attribute_grid_offers_fifty_slots_per_role(fmt: DeliveryFormat) -> None:
    for role in ("label", "value", "uom"):
        assert fmt.slots("attribute_grid", role) == 50


def test_item_features_offers_twenty_slots(fmt: DeliveryFormat) -> None:
    assert fmt.slots("item_features", "feature") == 20
    assert [c.name for c in fmt.group("item_features")][:2] == [
        "ITEM_FEATURES_1",
        "ITEM_FEATURES_2",
    ]


def test_slot_column_lookup_round_trips(fmt: DeliveryFormat) -> None:
    assert fmt.slot_column("attribute_grid", "value", 12).name == "ATTRIBUTE_VALUE 12"
    assert fmt.slot_column("attribute_grid", "uom", 9).name == "ATTRIBUTE_UOM 9"
    with pytest.raises(KeyError):
        fmt.slot_column("attribute_grid", "value", 51)


# --------------------------------------------------------------------------- split groups


def test_logical_groups_may_be_non_contiguous(fmt: DeliveryFormat) -> None:
    """The client interleaves their keys with the taxonomy, and we mirror their order.

    `PART_NUMBER, Dept, Class, Fine, SKU - MY_PART_NUMBER` then, twelve columns later,
    `Classpath`. So a group label is gathered from every section declaring it rather than
    assumed to be one run. Reordering to make the sections tidy would break the header.
    """
    assert [c.name for c in fmt.group("client_keys")] == [
        "PART_NUMBER",
        "SKU - MY_PART_NUMBER",
    ]
    assert [c.name for c in fmt.group("taxonomy")] == ["Dept", "Class", "Fine", "Classpath"]

    # The interleaving itself, asserted positionally so a "tidy-up" cannot pass unnoticed.
    header = fmt.header
    assert header[6:11] == ("PART_NUMBER", "Dept", "Class", "Fine", "SKU - MY_PART_NUMBER")
    assert header[22] == "Classpath"


def test_classpath_is_not_derived_from_dept_class_fine(fmt: DeliveryFormat) -> None:
    """They genuinely disagree in the ground truth, so one cannot be computed from the other."""
    if not CLIENT_DELIVERY_CSV.exists():
        pytest.skip("client delivery-format CSV not present")
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    assert row["Classpath"] != ">".join((row["Dept"], row["Class"], row["Fine"]))


# --------------------------------------------------------------------------- provenance


def test_client_keys_are_unavailable(fmt: DeliveryFormat) -> None:
    """These are the client's internal identifiers; inventing one corrupts their key space."""
    for name in ("PART_NUMBER", "SKU - MY_PART_NUMBER"):
        assert fmt.provenance_of(name) is Provenance.UNAVAILABLE
        assert not fmt.provenance_of(name).may_be_populated


def test_input_echo_is_passthrough(fmt: DeliveryFormat) -> None:
    for name in ("Mfg_Part_Num", "Part_Desc", "E1_Brand", "Part_Manuf"):
        assert fmt.provenance_of(name) is Provenance.PASSTHROUGH


def test_taxonomy_is_derived_not_extracted(fmt: DeliveryFormat) -> None:
    """Classification output, not a document read — so it inherits rather than needing a span."""
    for name in ("Dept", "Class", "Fine", "Classpath"):
        assert fmt.provenance_of(name) is Provenance.DERIVED


def test_attribute_values_are_extracted_but_labels_are_derived(fmt: DeliveryFormat) -> None:
    """The label is a projection of the class schema; only the value makes a claim about a part."""
    assert fmt.provenance_of("ATTRIBUTE_VALUE 1") is Provenance.EXTRACTED
    assert fmt.provenance_of("ATTRIBUTE_LABEL 1") is Provenance.DERIVED
    assert fmt.provenance_of("ATTRIBUTE_UOM 1") is Provenance.DERIVED


def test_only_extracted_requires_an_evidence_span(fmt: DeliveryFormat) -> None:
    assert Provenance.EXTRACTED.requires_evidence_span
    for other in (
        Provenance.PASSTHROUGH,
        Provenance.DERIVED,
        Provenance.GENERATED,
        Provenance.EVIDENCE,
        Provenance.UNAVAILABLE,
    ):
        assert not other.requires_evidence_span


def test_generated_may_not_draw_on_generated(fmt: DeliveryFormat) -> None:
    """Otherwise a chain of inventions compounds and each step looks locally justified."""
    assert not Provenance.GENERATED.is_established
    assert Provenance.PASSTHROUGH.is_established
    assert Provenance.DERIVED.is_established
    assert Provenance.EXTRACTED.is_established
    assert not Provenance.UNAVAILABLE.is_established


def test_unspsc_is_derived_from_the_class_mapping(fmt: DeliveryFormat) -> None:
    assert fmt.provenance_of("UNSPSC") is Provenance.DERIVED
    assert fmt.provenance_of("GTIN") is Provenance.EXTRACTED


def test_list_price_is_never_emitted(fmt: DeliveryFormat) -> None:
    """Pricing belongs to the distributor, not the manufacturer's datasheet."""
    assert fmt.provenance_of("List Price") is Provenance.UNAVAILABLE


def test_unavailable_columns_are_enumerable(fmt: DeliveryFormat) -> None:
    unavailable = set(fmt.unavailable_columns())
    assert {"PART_NUMBER", "SKU - MY_PART_NUMBER", "List Price"} <= unavailable


# --------------------------------------------------------------------------- constraints


def test_invoice_desc_is_capped_at_forty_and_uppercase(fmt: DeliveryFormat) -> None:
    column = fmt.column("INVOICE_DESC")
    assert column.max_chars == 40
    assert column.casing is Casing.UPPER
    assert column.constraint_source == "guide"


def test_mobile_desc_carries_the_sixty_to_eighty_window(fmt: DeliveryFormat) -> None:
    column = fmt.column("MOBILE_DESC")
    assert (column.min_chars, column.max_chars) == (60, 80)
    assert column.constraint_source == "guide"


def test_only_guide_stated_limits_are_declared(fmt: DeliveryFormat) -> None:
    """We do not invent limits for the fields the guide leaves unspecified.

    Scoring ourselves against a threshold we made up would manufacture a passing grade, so
    SHORT_DESC / LONG_DESC1 / RETAIL_DESC carry observed lengths as notes and no constraint.
    """
    for name in ("SHORT_DESC", "LONG_DESC1", "RETAIL_DESC", "MARKETING_DESCRIPTION"):
        assert not fmt.column(name).is_constrained
    assert {c.name for c in fmt.constrained_columns()} == {"MOBILE_DESC", "INVOICE_DESC"}


def test_ground_truth_copy_satisfies_the_declared_constraints() -> None:
    """The client's own rows must pass our validators, or our validators are wrong."""
    if not CLIENT_DELIVERY_CSV.exists():
        pytest.skip("client delivery-format CSV not present")
    fmt = load_default()
    with open(CLIENT_DELIVERY_CSV, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "expected at least one ground-truth row"
    for index, row in enumerate(rows):
        for column in fmt.constrained_columns():
            problems = column.violations(row[column.name])
            assert not problems, (
                f"ground-truth row {index} column {column.name!r} violates our own "
                f"constraint: {problems}"
            )


def test_violations_tolerate_an_empty_value(fmt: DeliveryFormat) -> None:
    """A blank cell is a legitimate answer; a minimum that rejected it would force invention."""
    assert fmt.column("MOBILE_DESC").violations("") == []
    assert fmt.column("INVOICE_DESC").violations("") == []


def test_violations_report_each_breach(fmt: DeliveryFormat) -> None:
    invoice = fmt.column("INVOICE_DESC")
    assert invoice.violations("DISHWASHER LEG 5 SST 120V 15A 50-1/4IN") == []
    too_long = invoice.violations("X" * 41)
    assert len(too_long) == 1 and "41 chars" in too_long[0]
    lower = invoice.violations("dishwasher leg")
    assert lower == ["must be uppercase"]
    both = invoice.violations("x" * 41)
    assert len(both) == 2


def test_mobile_desc_flags_a_short_value(fmt: DeliveryFormat) -> None:
    problems = fmt.column("MOBILE_DESC").violations("Too short")
    assert len(problems) == 1 and "under the 60-char minimum" in problems[0]


# --------------------------------------------------------------------------- row scaffolding


def test_blank_row_covers_every_column(fmt: DeliveryFormat) -> None:
    row = fmt.blank_row()
    assert len(row) == 252
    assert set(row) == set(fmt.header)
    assert set(row.values()) == {""}


def test_join_key_is_a_real_column(fmt: DeliveryFormat) -> None:
    assert fmt.join_key == "Mfg_Part_Num"
    assert fmt.join_key in fmt


def test_default_format_path_exists() -> None:
    assert default_format_path().is_file()


def test_load_default_is_cached() -> None:
    assert load_default() is load_default()


# --------------------------------------------------------------------------- loader integrity


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fmt.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_unknown_provenance_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n    - group: g\n      provenance: vibes\n"
        "      columns: [A]\n",
    )
    with pytest.raises(DeliveryFormatError, match="not one of"):
        DeliveryFormat.load(path)


def test_duplicate_column_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n"
        "    - group: a\n      provenance: derived\n      columns: [Dup]\n"
        "    - group: b\n      provenance: derived\n      columns: [Dup]\n",
    )
    with pytest.raises(DeliveryFormatError, match="declared twice"):
        DeliveryFormat.load(path)


def test_repeat_template_without_slot_token_is_rejected(tmp_path: Path) -> None:
    """All N slots would collide on one name and the file would be silently short."""
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n    - group: g\n      provenance: derived\n"
        "      repeat:\n        count: 3\n        columns:\n          - template: FLAT\n"
        "            role: r\n",
    )
    with pytest.raises(DeliveryFormatError, match="does not contain"):
        DeliveryFormat.load(path)


def test_section_with_both_columns_and_repeat_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n    - group: g\n      provenance: derived\n"
        "      columns: [A]\n      repeat:\n        count: 2\n        columns:\n"
        "          - template: B{n}\n            role: r\n",
    )
    with pytest.raises(DeliveryFormatError, match="not both"):
        DeliveryFormat.load(path)


def test_constraint_on_an_unavailable_column_is_rejected(tmp_path: Path) -> None:
    """Dead configuration: the column is always empty, so the limit can never apply."""
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n    - group: g\n      provenance: unavailable\n"
        "      columns:\n        - name: A\n          max_chars: 10\n",
    )
    with pytest.raises(DeliveryFormatError, match="dead configuration"):
        DeliveryFormat.load(path)


def test_bad_join_key_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "format:\n  name: t\n  join_key: Nope\n  sections:\n    - group: g\n"
        "      provenance: derived\n      columns: [A]\n",
    )
    with pytest.raises(DeliveryFormatError, match="join_key"):
        DeliveryFormat.load(path)


def test_min_above_max_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "format:\n  name: t\n  sections:\n    - group: g\n      provenance: generated\n"
        "      columns:\n        - name: A\n          min_chars: 90\n          max_chars: 40\n",
    )
    with pytest.raises(DeliveryFormatError, match="exceeds max_chars"):
        DeliveryFormat.load(path)
