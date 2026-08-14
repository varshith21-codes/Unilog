"""Scoring against ground truth.

A scorer is only worth having if it can fail. So most of these tests establish that it refuses to
flatter: that inventing data lowers the score, that both-empty cells stay out of the accuracy
denominator, and that a small sample is never dressed up as a percentage.

The end-to-end test at the bottom runs the real projection against the real ground-truth file and
pins the measured numbers, so a regression in any layer shows up as a changed score rather than as
a passing test suite and a quietly worse submission.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from axiom.core.product import ProductRecord
from axiom.delivery import DeliveryRowBuilder, SupplierRow, load_default
from axiom.delivery.scoring import (
    PERCENTAGE_FLOOR,
    Verdict,
    compare,
    format_ratio,
    normalize_for_match,
    render_report,
    score_rows,
)
from axiom.extract.description import (
    AbbreviationTable,
    extract_from_description,
    to_attribute_values,
)
from axiom.schema import load_default as load_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = REPO_ROOT / "Unihack_ Expected Output - Delivery Format.csv"
SAMPLE_INPUT = REPO_ROOT / "Unihack_ Sample Dataset - Input.csv"
DISHWASHER = "APP.KIT.DISHWASHER.BUILTIN"


@pytest.fixture(scope="module")
def fmt():
    return load_default()


@pytest.fixture(scope="module")
def registry():
    return load_schema()


def rows_from(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- comparison


def test_identical_values_are_exact():
    assert compare("47", "47") is Verdict.EXACT


def test_both_empty_is_correct_not_ignored():
    """The majority verdict on a 252-column format where 173 columns are blank by design."""
    verdict = compare("", "")
    assert verdict is Verdict.BOTH_EMPTY
    assert verdict.is_correct


def test_missing_value_is_missed():
    assert compare("Stainless Steel", "") is Verdict.MISSED


def test_inventing_a_value_is_overfilled_and_incorrect():
    """The verdict a naive scorer would have counted as a win."""
    verdict = compare("", "52141505")
    assert verdict is Verdict.OVERFILLED
    assert not verdict.is_correct
    assert not verdict.is_acceptable


def test_different_values_are_wrong():
    assert compare("47", "41") is Verdict.WRONG


def test_case_and_symbol_differences_are_normalized_not_exact():
    """The client requires brands to match 'exactly, symbols and all'.

    So this must not score as exact — but it is reported separately from a wrong value, because
    the remedy is completely different.
    """
    verdict = compare("FRIGIDAIRE\u00ae", "frigidaire")
    assert verdict is Verdict.NORMALIZED
    assert not verdict.is_correct
    assert verdict.is_acceptable


def test_whitespace_collapse_counts_as_normalized():
    assert compare("Stainless  Steel", "Stainless Steel") is Verdict.NORMALIZED


def test_punctuation_is_not_folded_away():
    """'50-1/4' and '50 1 4' are different measurements; folding them manufactures agreement."""
    assert compare("50-1/4", "50 1 4") is Verdict.WRONG


def test_normalize_strips_symbols_and_folds_case():
    assert normalize_for_match("FRIGIDAIRE\u00ae") == "frigidaire"
    assert normalize_for_match("  CleanBoost\u2122  ") == "cleanboost"


def test_only_exact_and_both_empty_count_as_correct():
    correct = {v for v in Verdict if v.is_correct}
    assert correct == {Verdict.EXACT, Verdict.BOTH_EMPTY}


def test_populated_comparison_excludes_the_empty_agreements():
    """Including both-empty in the denominator would report ~90% before any enrichment."""
    assert not Verdict.BOTH_EMPTY.is_populated_comparison
    assert not Verdict.OVERFILLED.is_populated_comparison
    for verdict in (Verdict.EXACT, Verdict.NORMALIZED, Verdict.MISSED, Verdict.WRONG):
        assert verdict.is_populated_comparison


# --------------------------------------------------------------------------- honest ratios


def test_small_samples_print_as_fractions():
    assert format_ratio(14, 15) == "14/15"
    assert "%" not in format_ratio(1, 2)


def test_large_samples_get_a_percentage():
    rendered = format_ratio(50, 100)
    assert rendered.startswith("50/100")
    assert "50.0%" in rendered


def test_the_percentage_floor_is_the_boundary():
    assert "%" not in format_ratio(1, PERCENTAGE_FLOOR - 1)
    assert "%" in format_ratio(1, PERCENTAGE_FLOOR)


def test_empty_denominator_does_not_divide_by_zero():
    assert format_ratio(0, 0) == "0/0 (n/a)"


# --------------------------------------------------------------------------- joining


def test_rows_are_joined_on_key_not_position(fmt):
    """Zipping by position would compare row n against row n+1 whenever a row is skipped.

    That reports catastrophic and entirely fictional inaccuracy, so the join is on the key.
    """
    expected = [
        {"Mfg_Part_Num": "A", "Product Name": "Dishwasher"},
        {"Mfg_Part_Num": "B", "Product Name": "Refrigerator"},
    ]
    actual = [{"Mfg_Part_Num": "B", "Product Name": "Refrigerator"}]

    report = score_rows(fmt, expected, actual, columns=["Mfg_Part_Num", "Product Name"])
    assert len(report.rows) == 1
    assert report.rows[0].key == "B"
    assert report.unmatched_expected == ["A"]
    assert report.counts()[Verdict.EXACT] == 2


def test_output_rows_absent_from_ground_truth_are_reported(fmt):
    report = score_rows(
        fmt,
        [{"Mfg_Part_Num": "A"}],
        [{"Mfg_Part_Num": "A"}, {"Mfg_Part_Num": "Z"}],
        columns=["Mfg_Part_Num"],
    )
    assert report.unmatched_actual == ["Z"]


def test_unknown_join_key_raises(fmt):
    with pytest.raises(KeyError, match="join key"):
        score_rows(fmt, [], [], key="NotAColumn")


# --------------------------------------------------------------------------- aggregation


def test_accuracy_denominator_is_only_what_ground_truth_populated(fmt):
    expected = [{"Mfg_Part_Num": "A", "Product Name": "Dishwasher", "UNSPSC": ""}]
    actual = [{"Mfg_Part_Num": "A", "Product Name": "Dishwasher", "UNSPSC": ""}]
    report = score_rows(
        fmt, expected, actual, columns=["Mfg_Part_Num", "Product Name", "UNSPSC"]
    )
    # Three cells compared, but UNSPSC was empty on both sides so it is not in the denominator.
    assert report.cells == 3
    assert report.exact_of_expected() == (2, 2)


def test_overfill_is_surfaced_as_its_own_number(fmt):
    expected = [{"Mfg_Part_Num": "A", "UNSPSC": ""}]
    actual = [{"Mfg_Part_Num": "A", "UNSPSC": "52141505"}]
    report = score_rows(fmt, expected, actual, columns=["Mfg_Part_Num", "UNSPSC"])
    assert report.overfilled == 1
    agreed, total = report.fill_discipline()
    assert (agreed, total) == (1, 2)


def test_constraint_violations_are_detected_during_scoring(fmt):
    expected = [{"Mfg_Part_Num": "A", "INVOICE_DESC": "SHORT"}]
    actual = [{"Mfg_Part_Num": "A", "INVOICE_DESC": "x" * 60}]
    report = score_rows(fmt, expected, actual, columns=["Mfg_Part_Num", "INVOICE_DESC"])
    assert not report.compliant
    assert "INVOICE_DESC" in report.rows[0].constraint_violations


def test_failures_are_ordered_worst_first(fmt):
    expected = [
        {"Mfg_Part_Num": "A", "UNSPSC": "", "Product Name": "Dishwasher", "Dept": "Appliances"}
    ]
    actual = [
        {"Mfg_Part_Num": "A", "UNSPSC": "invented", "Product Name": "", "Dept": "Wrong"}
    ]
    report = score_rows(
        fmt, expected, actual, columns=["Mfg_Part_Num", "UNSPSC", "Product Name", "Dept"]
    )
    verdicts = [cell.verdict for cell in report.rows[0].failures()]
    assert verdicts == [Verdict.OVERFILLED, Verdict.WRONG, Verdict.MISSED]


def test_group_breakdown_uses_contract_groups(fmt):
    report = score_rows(
        fmt,
        [{"Mfg_Part_Num": "A", "Dept": "Appliances"}],
        [{"Mfg_Part_Num": "A", "Dept": "Appliances"}],
        columns=["Mfg_Part_Num", "Dept"],
    )
    groups = report.by_group()
    assert groups["taxonomy"][Verdict.EXACT] == 1
    assert groups["input_echo"][Verdict.EXACT] == 1


def test_summary_is_json_serialisable(fmt):
    import json

    report = score_rows(
        fmt, [{"Mfg_Part_Num": "A"}], [{"Mfg_Part_Num": "A"}], columns=["Mfg_Part_Num"]
    )
    assert json.loads(json.dumps(report.summary()))["rows_scored"] == 1


def test_report_renders_without_non_ascii_chrome(fmt):
    """The Windows console mangles an em-dash, which looks careless in a fidelity report."""
    report = score_rows(
        fmt, [{"Mfg_Part_Num": "A"}], [{"Mfg_Part_Num": "A"}], columns=["Mfg_Part_Num"]
    )
    rendered = render_report(report)
    assert "\u2014" not in rendered
    assert "Delivery-format score" in rendered


# --------------------------------------------------------------------------- end to end


@pytest.mark.skipif(
    not (GROUND_TRUTH.exists() and SAMPLE_INPUT.exists()),
    reason="client input and ground-truth CSVs not both present",
)
def test_measured_score_against_real_ground_truth(fmt, registry):
    """The real number, pinned.

    Built the same way `scripts/export_delivery.py` builds it: classify offline, project, score.
    No source documents are attached, so specification values are legitimately absent and the
    accuracy figure reflects structure rather than enrichment. That is the honest baseline the
    retrieval stage has to improve on, and pinning it means an improvement is visible and a
    regression is loud.
    """
    truth = {r["Mfg_Part_Num"]: r for r in rows_from(GROUND_TRUTH)}
    inputs = {r["Mfg_Part_Num"]: r for r in rows_from(SAMPLE_INPUT)}
    builder = DeliveryRowBuilder(fmt, registry)
    table = AbbreviationTable.load()

    produced = []
    for mpn in truth:
        assert mpn in inputs, f"{mpn} is in ground truth but not in the input file"
        source = SupplierRow.parse(inputs[mpn])
        record = ProductRecord(
            tenant_id="unilog", sku=mpn, mpn=source.mpn, class_code=DISHWASHER
        )
        # Whatever the description itself evidences, exactly as the batch driver does it.
        extraction = extract_from_description(
            source.description or "",
            registry=registry,
            class_code=DISHWASHER,
            abbreviations=table,
        )
        for value in to_attribute_values(
            extraction, document_id="item-master", document_sha256="e" * 64
        ):
            record.add_value(value)
        produced.append(builder.build(record, source=source).as_dict())

    report = score_rows(fmt, list(truth.values()), produced)

    assert len(report.rows) == 2
    assert report.cells == 504

    # Nothing invented. This is the assertion that must never regress.
    assert report.overfilled == 0
    assert report.compliant
    assert report.unmatched_expected == []

    exact, expected = report.exact_of_expected()
    assert expected == 134, "ground truth populates 134 of 504 compared cells"
    assert exact == 56, f"expected 56 exact matches, measured {exact}"

    # Of those 56, exactly 2 are enrichment — `Material: Stainless Steel` on each row, read from
    # the "SS" in the description. The other 54 are structure. Split out so the distinction cannot
    # quietly erode: a rise in the headline number that is all scaffolding is not progress.
    material = [
        cell
        for row in report.rows
        for cell in row.cells
        if cell.column == "ATTRIBUTE_VALUE 13"
    ]
    assert len(material) == 2
    assert all(c.verdict is Verdict.EXACT for c in material)
    assert all(c.actual == "Stainless Steel" for c in material)

    groups = report.by_group()
    # Structure is fully correct: both hierarchies, the input echo, and every grid label.
    assert groups["taxonomy"][Verdict.MISSED] == 0
    assert groups["taxonomy"][Verdict.WRONG] == 0
    assert groups["input_echo"][Verdict.WRONG] == 0
    assert groups["attribute_grid"][Verdict.WRONG] == 0

    # Specifications are absent rather than wrong, which is the whole point of the gate.
    assert groups["attribute_grid"][Verdict.MISSED] > 0
    assert groups["descriptions"][Verdict.MISSED] > 0


@pytest.mark.skipif(not GROUND_TRUTH.exists(), reason="ground truth not present")
def test_ground_truth_scores_perfectly_against_itself(fmt):
    """A scorer that cannot recognise a correct answer is measuring nothing."""
    truth = rows_from(GROUND_TRUTH)
    report = score_rows(fmt, truth, truth)
    exact, expected = report.exact_of_expected()
    assert exact == expected
    assert report.overfilled == 0
    assert report.counts()[Verdict.WRONG] == 0
    assert report.counts()[Verdict.MISSED] == 0
    assert report.compliant


# --------------------------------------------------------------------------- the CI gate

# Imported for its logic, the way tests/test_ingest.py imports from ingest_supplier_file. The
# exit-code policy is the whole contract between the scorer and CI, and a gate that cannot fail is
# not a gate — so it is tested here rather than trusted.
from scripts.score_delivery import _verdict  # noqa: E402


def _report_with(fmt, expected: dict[str, str], actual: dict[str, str], columns: list[str]):
    return score_rows(fmt, [expected], [actual], columns=columns)


def test_gate_passes_a_clean_report(fmt, capsys):
    report = _report_with(
        fmt,
        {"Mfg_Part_Num": "A", "Product Name": "Dishwasher", "UNSPSC": ""},
        {"Mfg_Part_Num": "A", "Product Name": "Dishwasher", "UNSPSC": ""},
        ["Mfg_Part_Num", "Product Name", "UNSPSC"],
    )
    assert _verdict(report, strict=False) == 0
    assert "PASS" in capsys.readouterr().out


def test_gate_fails_on_overfill(fmt, capsys):
    """Filling a column the client left blank must break the build.

    173 of their 252 columns are blank by design, so this is the failure mode most likely to look
    like progress on a dashboard while being a regression in fact.
    """
    report = _report_with(
        fmt,
        {"Mfg_Part_Num": "A", "UNSPSC": ""},
        {"Mfg_Part_Num": "A", "UNSPSC": "52141505"},
        ["Mfg_Part_Num", "UNSPSC"],
    )
    assert _verdict(report, strict=False) == 1
    assert "populated where the client's format is empty" in capsys.readouterr().out


def test_gate_fails_on_a_character_limit_breach(fmt, capsys):
    report = _report_with(
        fmt,
        {"Mfg_Part_Num": "A", "INVOICE_DESC": "SHORT ENOUGH"},
        {"Mfg_Part_Num": "A", "INVOICE_DESC": "x" * 60},
        ["Mfg_Part_Num", "INVOICE_DESC"],
    )
    assert _verdict(report, strict=False) == 1
    assert "character-limit breach" in capsys.readouterr().out


def test_gate_fails_when_a_ground_truth_row_produced_no_output(fmt, capsys):
    report = score_rows(
        fmt,
        [{"Mfg_Part_Num": "A"}, {"Mfg_Part_Num": "B"}],
        [{"Mfg_Part_Num": "A"}],
        columns=["Mfg_Part_Num"],
    )
    assert _verdict(report, strict=False) == 1
    assert "produced no output" in capsys.readouterr().out


def test_gate_tolerates_missing_values_by_default(fmt, capsys):
    """Missing specifications are expected until documents are attached; they must not block CI.

    The asymmetry is deliberate. A missing value is an honest gap that the retrieval stage will
    close. An invented value is a defect that ships.
    """
    report = _report_with(
        fmt,
        {"Mfg_Part_Num": "A", "Product Name": "Dishwasher"},
        {"Mfg_Part_Num": "A", "Product Name": ""},
        ["Mfg_Part_Num", "Product Name"],
    )
    assert report.counts()[Verdict.MISSED] == 1
    assert _verdict(report, strict=False) == 0
    capsys.readouterr()

    # ...but --strict exists for when a run is supposed to be complete.
    assert _verdict(report, strict=True) == 1
    assert "missing value" in capsys.readouterr().out
