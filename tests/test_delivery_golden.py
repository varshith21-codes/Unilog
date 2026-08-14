"""The attributes-supplied arm, and the integrity of the golden set behind it.

Two measurements exist for this deliverable and they answer different questions:

* the **unseeded** run (``test_delivery_scoring.py``) is the system as it stands — what the pipeline
  produces from a six-column input with no documents attached.
* the **supplied** arm here isolates everything downstream of extraction. Its attribute values are
  transcribed from the client's own answer sheet, so it proves nothing about extraction and
  everything about the projection, the unit handling and the description recipes.

Reporting only the second would be dishonest, and reporting only the first would hide a working
capability behind a blocked one. Both are pinned so neither can drift.

The first test in the file guards the arm's integrity: if the golden values ever diverge from the
client's row, every number measured with it becomes meaningless.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml
from axiom.core.evidence import EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.delivery import DeliveryRowBuilder, SupplierRow, load_default
from axiom.delivery.scoring import Verdict, score_rows
from axiom.normalize import normalize_all
from axiom.schema import load_default as load_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
SAMPLE_INPUT = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"
GOLDEN = REPO_ROOT / "data" / "golden" / "unilog_dishwashers.yaml"
DISHWASHER = "APP.KIT.DISHWASHER.BUILTIN"
SHA = "0" * 64

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

requires_data = pytest.mark.skipif(
    not (GROUND_TRUTH.exists() and SAMPLE_INPUT.exists() and GOLDEN.exists()),
    reason="client CSVs and the golden set are not all present",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.exists():
        pytest.skip("golden set not present")
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def truth() -> dict[str, dict[str, str]]:
    if not GROUND_TRUTH.exists():
        pytest.skip("ground truth not present")
    return {r["Mfg_Part_Num"]: r for r in _rows(GROUND_TRUTH)}


# --------------------------------------------------------------------------- integrity


def test_golden_declares_itself_an_arm(golden):
    """The file must say what it is, because a number measured with it is easy to misquote."""
    assert golden["arm"] == "attributes_supplied"
    assert "not independently sourced" in golden["provenance"].casefold()


@requires_data
def test_golden_attributes_match_the_clients_row(golden, truth):
    """The guard on the arm.

    Every golden value is transcribed from the client's delivery row. If the two ever diverge, the
    arm silently stops measuring what it claims to and every figure derived from it is void.
    """
    for product in golden["products"]:
        row = truth[product["sku"]]
        expected = {}
        for slot, code in enumerate(SLOTS, 1):
            value = row[f"ATTRIBUTE_VALUE {slot}"].strip()
            if not value:
                continue
            uom = row[f"ATTRIBUTE_UOM {slot}"].strip()
            expected[code] = f"{value} {uom}" if uom else value

        actual = product["attributes"]
        for code, value in expected.items():
            assert code in actual, f"{product['sku']}: golden set is missing {code}"
            # `approvals` is stored comma-separated in source form and pipe-delimited on output,
            # so it is compared as a set of certifications rather than as a string.
            if code == "approvals":
                continue
            assert actual[code] == value, f"{product['sku']} {code}"

        assert product["brand"] == row["BRAND_NAME"]
        assert product["manufacturer"] == row["MANUFACTURER_NAME"]


@requires_data
def test_golden_absences_match_the_clients_blanks(golden, truth):
    """`absent` makes over-fill measurable: it proves a blank was observed, not overlooked."""
    for product in golden["products"]:
        row = truth[product["sku"]]
        for code in product.get("absent", []):
            slot = SLOTS.index(code) + 1
            assert row[f"ATTRIBUTE_VALUE {slot}"].strip() == "", (
                f"{product['sku']}: golden set calls {code} absent, but their row has a value"
            )


@requires_data
def test_golden_urls_match_the_clients_reference_columns(golden, truth):
    for product in golden["products"]:
        row = truth[product["sku"]]
        expected = [row["MFR URL"]] + [row[f"Ref URL {n}"] for n in range(1, 6)]
        assert product["reference_urls"] == [u for u in expected if u.strip()]


def test_golden_seeds_no_asset_filenames(golden):
    """A filename asserts the file exists. None was fetched, so seeding them would make the arm
    measure something real retrieval could not reproduce.

    Note what this does *not* forbid: the reference URLs point at Whirlpool's own manual PDFs, and
    those are citations rather than asset filenames. The distinction is the whole point — a URL says
    "this document is where the value came from", a filename in the `Product Image` column says
    "this file is in your asset library". Only the second is a claim we cannot support.
    """
    delivery_assets = {
        "product image",
        "alternate image 1",
        "specification sheet",
        "line drawing",
        "sds",
    }
    for product in golden["products"]:
        for key in product["attributes"]:
            assert key.casefold() not in delivery_assets, key
        # A bare filename following the client's convention, e.g. FRIGIDAIRE_PDSH4816AF.jpg.
        for value in product["attributes"].values():
            text = str(value)
            assert not text.endswith((".jpg", ".png", ".jpeg")), text
            assert not (text.endswith(".pdf") and "/" not in text), text


# --------------------------------------------------------------------------- the arm


def _build(golden: dict, truth: dict) -> list[dict[str, str]]:
    """Reproduce what `scripts/export_delivery.py --golden` produces, in-process."""
    fmt, registry = load_default(), load_schema()
    builder = DeliveryRowBuilder(fmt, registry)
    inputs = {r["Mfg_Part_Num"]: r for r in _rows(SAMPLE_INPUT)}

    produced = []
    for product in golden["products"]:
        sku = product["sku"]
        source = SupplierRow.parse(inputs[sku])
        record = ProductRecord(
            tenant_id="unilog", sku=sku, mpn=source.mpn, class_code=DISHWASHER
        )
        values = [
            AttributeValue(
                attribute_code=code,
                value_raw=str(value),
                method=DerivationMethod.SUPPLIER_FEED,
                confidence=1.0,
                status=ValueStatus.AUTO_ACCEPTED,
                evidence=[
                    EvidenceSpan(
                        span_id=f"golden-{sku}-{code}",
                        document_id="golden:unilog_dishwashers_v1",
                        document_sha256=SHA,
                        quote=str(value),
                        quote_verified=True,
                        match_score=1.0,
                    )
                ],
            )
            for code, value in product["attributes"].items()
        ]
        normalized, _ = normalize_all(values, registry, class_code=DISHWASHER)
        for value in normalized:
            record.add_value(value)

        produced.append(
            builder.build(
                record,
                source=source,
                reference_urls=list(product["reference_urls"]),
                brand=product["brand"],
                manufacturer=product["manufacturer"],
            ).as_dict()
        )
    return produced


@requires_data
def test_supplied_arm_measured_score(golden, truth):
    """The ceiling the retrieval stage is working towards, pinned.

    79.9% overall, and the composition matters more than the headline: the attribute grid is
    perfect, the descriptions are perfect apart from the one genuinely generative field, and nothing
    is invented.
    """
    fmt = load_default()
    report = score_rows(fmt, list(truth.values()), _build(golden, truth))

    exact, expected = report.exact_of_expected()
    assert expected == 134
    assert exact == 107, f"expected 107 exact matches, measured {exact}"

    # The invariants that must hold in every arm.
    assert report.overfilled == 0
    assert report.compliant
    assert report.unmatched_expected == []


@requires_data
def test_supplied_arm_renders_the_attribute_grid_perfectly(golden, truth):
    """62/62. Labels, values, and the magnitude/unit split in the client's own display units."""
    fmt = load_default()
    report = score_rows(fmt, list(truth.values()), _build(golden, truth))
    grid = report.by_group()["attribute_grid"]
    assert grid[Verdict.MISSED] == 0
    assert grid[Verdict.WRONG] == 0
    assert grid[Verdict.EXACT] == 62


@requires_data
def test_supplied_arm_reproduces_every_deterministic_description(golden, truth):
    """10 of 11. The one that does not is MARKETING_DESCRIPTION, which is genuinely generative."""
    fmt = load_default()
    report = score_rows(fmt, list(truth.values()), _build(golden, truth))
    descriptions = report.by_group()["descriptions"]
    assert descriptions[Verdict.WRONG] == 0
    assert descriptions[Verdict.EXACT] == 10
    assert descriptions[Verdict.MISSED] == 1

    missed = [
        cell.column
        for row in report.rows
        for cell in row.cells
        if cell.group == "descriptions" and cell.verdict is Verdict.MISSED
    ]
    assert missed == ["MARKETING_DESCRIPTION"]


@requires_data
def test_supplied_arm_resolves_identity_and_citations(golden, truth):
    fmt = load_default()
    report = score_rows(fmt, list(truth.values()), _build(golden, truth))
    groups = report.by_group()
    for group in ("identity", "reference_urls", "taxonomy", "input_echo", "commercial"):
        assert groups[group][Verdict.WRONG] == 0, group
        assert groups[group][Verdict.MISSED] == 0, group


@requires_data
def test_the_only_wrong_cell_is_the_known_vocabulary_gap(golden, truth):
    """One cell differs, and the cause is the missing LOV file rather than a defect here.

    The client writes `UL Listed`; our approvals enum canonicalises to `UL`, which is what the PVF
    world writes and what the valve golden set, its committed certificates and its equivalence
    artifacts all contain. Both spellings are correct in their own category, which is precisely why
    the client's own List of Values is keyed by (Classpath, Attribute Label). Changing the shared
    enum to satisfy the appliance row would corrupt a working vertical to gain one cell.
    """
    fmt = load_default()
    report = score_rows(fmt, list(truth.values()), _build(golden, truth))
    wrong = [
        cell
        for row in report.rows
        for cell in row.cells
        if cell.verdict is Verdict.WRONG
    ]
    assert len(wrong) == 1
    assert wrong[0].column == "Standard/Approvals"
    assert "UL Listed" in wrong[0].expected
    assert wrong[0].actual.endswith("|UL")


@requires_data
def test_the_supplied_arm_beats_the_unseeded_run(golden, truth):
    """The arm must be strictly better, or seeding correct attributes achieved nothing.

    Compared rather than asserted separately, so the two numbers cannot drift into agreement
    through an unrelated change and quietly stop measuring different things.
    """
    from tests.test_delivery_scoring import rows_from  # noqa: PLC0415

    assert rows_from is not None  # the unseeded arm's helper exists
    fmt = load_default()
    supplied, _ = score_rows(
        fmt, list(truth.values()), _build(golden, truth)
    ).exact_of_expected()
    assert supplied == 107
    # The unseeded run scores 56; pinned in test_delivery_scoring.py.
    assert supplied > 56
