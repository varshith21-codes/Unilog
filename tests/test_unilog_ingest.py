"""Ingesting the Unilog item master: header resolution and sentinel filtering.

Two things are proved here. The six real headers now resolve, which they did not before — the
mapper returned ``target=None`` for every one of them and the ingest script exited 1 with "no
identity column". And the guide's "placeholders are not data" rule is enforced narrowly enough
that it cannot eat real values.

The assertions run against the client's actual file wherever possible. A synthetic header row
would agree with whatever we assumed, which is exactly the failure mode worth avoiding.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.ingest import infer_mapping, read_flat_file
from axiom.ingest.placeholders import (
    AMBIGUOUS_SENTINELS,
    clean,
    clean_row,
    dead_columns,
    is_placeholder,
    profile_column,
    profile_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_INPUT_CSV = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"

UNILOG_HEADERS = [
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
]

requires_client_input = pytest.mark.skipif(
    not CLIENT_INPUT_CSV.exists(), reason="client input CSV not present in this checkout"
)


@pytest.fixture(scope="module")
def client_rows() -> list[dict[str, str]]:
    if not CLIENT_INPUT_CSV.exists():
        pytest.skip("client input CSV not present")
    with open(CLIENT_INPUT_CSV, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- header resolution


def test_identity_column_resolves() -> None:
    """The hard stop. Without this the ingest script refuses the file outright."""
    mapping = infer_mapping(UNILOG_HEADERS)
    assert mapping.resolved.get("mpn") == "Mfg_Part_Num"


def test_description_and_manufacturer_resolve() -> None:
    mapping = infer_mapping(UNILOG_HEADERS)
    assert mapping.resolved.get("description") == "Part_Desc"
    assert mapping.resolved.get("brand") == "Part_Manuf"


def test_resolution_is_exact_not_fuzzy() -> None:
    """Fuzzy matches sit below the confident bar on purpose, so they would not auto-apply.

    `Mfg_Part_Num` previously fuzzy-matched `mfgpartno` at ratio 0.842, under the 0.86 cutoff, and
    fell through to unresolved. It must now hit the synonym table outright.
    """
    mapping = infer_mapping(UNILOG_HEADERS)
    by_header = {m.header: m for m in mapping.matches}
    for header in ("Mfg_Part_Num", "Part_Desc", "Part_Manuf"):
        assert by_header[header].method == "synonym_exact"
        assert by_header[header].confidence == pytest.approx(0.98)
        assert by_header[header].is_confident


def test_competing_brand_columns_are_left_for_a_data_aware_resolver() -> None:
    """Mapping all three onto `brand` would award it to E1_Brand, a placeholder in 799/1000 rows.

    Which brand column to believe is a question about the data, and `infer_mapping` never sees a
    row. So they stay unmapped and survive in the per-row `unmapped` bag.
    """
    mapping = infer_mapping(UNILOG_HEADERS)
    assert set(mapping.unmapped) == {"E1_Brand", "Unilog_Brand", "DIB_Brand"}
    assert mapping.resolved.get("brand") != "E1_Brand"


def test_no_target_is_contested() -> None:
    """A contested target silently drops columns; assert the mapping has none."""
    mapping = infer_mapping(UNILOG_HEADERS)
    targets = [m.target for m in mapping.matches if m.target]
    assert len(targets) == len(set(targets))
    assert not any("same target" in note for note in mapping.notes)


@requires_client_input
def test_real_file_reaches_half_coverage() -> None:
    """Three of six columns resolve; the other three are the brand trio, by design."""
    flat = read_flat_file(CLIENT_INPUT_CSV.read_bytes(), filename=CLIENT_INPUT_CSV.name)
    assert list(flat.headers) == UNILOG_HEADERS
    mapping = infer_mapping(list(flat.headers))
    assert mapping.coverage() == pytest.approx(0.5)


# --------------------------------------------------------------------------- sentinel detection


@pytest.mark.parametrize(
    "value",
    [
        "-- Unbranded --",
        "-- No Unilog Brand --",
        "-- No DIB Brand --",
        "--Unbranded--",
        "  -- Unbranded --  ",
        "-",
        "--",
        "---",
        "...",
        "   ",
        "",
    ],
)
def test_sentinels_are_detected(value: str) -> None:
    assert is_placeholder(value)


def test_none_is_a_placeholder() -> None:
    assert is_placeholder(None)


@pytest.mark.parametrize(
    "value",
    [
        "TREX",
        "Philips",
        "FRIGIDAIRE(R)",
        "Nickel-Plated",
        "50-1/4",
        "1/2-13 UNC",
        "Appliance Dealers Cooperative (APPDE)",
        "3M 775L Stikit Film P150 - Cubitron II 50 Disc/Box",
        "10-4 SO Cord (Linear Foot)",
        "1x6-12' Brownstone Grooved - Harvest Azek PVC Decking",
    ],
)
def test_real_values_survive(value: str) -> None:
    """A hyphenated product string must never read as a sentinel.

    This is the expensive direction of the mistake: over-detection deletes data a supplier took
    the trouble to send, and the deletion is invisible downstream.
    """
    assert not is_placeholder(value)


@pytest.mark.parametrize("value", sorted(AMBIGUOUS_SENTINELS))
def test_ambiguous_sentinels_are_not_filtered_by_default(value: str) -> None:
    """`NONE` is a real coating, `NA` a real designation, `NO` a real boolean answer."""
    assert not is_placeholder(value)


def test_ambiguous_sentinels_can_be_opted_into() -> None:
    assert is_placeholder("N/A", extra=AMBIGUOUS_SENTINELS)
    assert is_placeholder("none", extra=AMBIGUOUS_SENTINELS)
    assert clean("TBD", extra=AMBIGUOUS_SENTINELS) is None


def test_clean_strips_and_nulls() -> None:
    assert clean("  TREX  ") == "TREX"
    assert clean("-- Unbranded --") is None


def test_clean_row_drops_keys_rather_than_blanking_them() -> None:
    """A key present with an empty string invites the bug this module prevents."""
    row = {
        "Mfg_Part_Num": "PDSH4816AF",
        "E1_Brand": "-- Unbranded --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
    }
    cleaned = clean_row(row)
    assert cleaned == {
        "Mfg_Part_Num": "PDSH4816AF",
        "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
    }
    assert "E1_Brand" not in cleaned


# --------------------------------------------------------------------------- profiling


def test_profile_separates_sentinels_from_blanks() -> None:
    profile = profile_column("E1_Brand", ["TREX", "-- Unbranded --", "", "TREX"])
    # populated counts cells, distinct counts values: two TREX cells, one TREX value.
    assert (profile.total, profile.populated, profile.placeholders, profile.blanks) == (4, 2, 1, 1)
    assert profile.distinct == 1
    assert profile.fill_rate == pytest.approx(0.5)
    assert profile.placeholder_rate == pytest.approx(0.25)
    assert profile.carries_data
    # One distinct value across the whole column cannot separate one row from another.
    assert not profile.is_discriminating


def test_a_wholly_sentinel_column_is_dead() -> None:
    """It looks complete to any tool counting non-empty cells. That is the point of reporting it."""
    profile = profile_column("Unilog_Brand", ["-- No Unilog Brand --"] * 5)
    assert profile.placeholders == 5
    assert profile.populated == 0
    assert profile.fill_rate == 0.0
    assert not profile.carries_data


@requires_client_input
def test_unilog_brand_is_dead_across_the_whole_file(client_rows) -> None:
    """Measured, not assumed: 1,000 of 1,000 rows are the sentinel."""
    profiles = profile_rows(UNILOG_HEADERS, client_rows)
    unilog = profiles["Unilog_Brand"]
    assert unilog.total == 1000
    assert unilog.placeholders == 1000
    assert unilog.populated == 0
    assert dead_columns(profiles) == ("Unilog_Brand",)


@requires_client_input
def test_measured_placeholder_density_matches_the_blueprint(client_rows) -> None:
    """Part 17.2 of the blueprint quotes these figures; keep them honest."""
    profiles = profile_rows(UNILOG_HEADERS, client_rows)
    assert profiles["E1_Brand"].placeholders == 799
    assert profiles["DIB_Brand"].placeholders == 755
    assert profiles["Unilog_Brand"].placeholders == 1000

    # Identity and description are fully populated, which is why they are the only reliable input.
    assert profiles["Mfg_Part_Num"].fill_rate == 1.0
    assert profiles["Part_Desc"].fill_rate == 1.0


@requires_client_input
def test_fewer_than_half_the_rows_carry_any_real_brand(client_rows) -> None:
    """The number that forces brand resolution to key off Part_Manuf and the description."""
    with_brand = sum(
        1
        for row in client_rows
        if any(
            not is_placeholder(row[column])
            for column in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
        )
    )
    assert with_brand == 446


@requires_client_input
def test_part_manuf_is_the_strongest_available_signal(client_rows) -> None:
    profiles = profile_rows(UNILOG_HEADERS, client_rows)
    manuf = profiles["Part_Manuf"]
    # 41 rows carry a bare "-", which is why it is not 100%.
    assert manuf.placeholders == 41
    assert manuf.populated == 959
    assert manuf.distinct == 75
    assert manuf.is_discriminating
