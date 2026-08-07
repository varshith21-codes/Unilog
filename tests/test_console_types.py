"""Tests for the console type-drift checker.

The checker exists because `apps/console/src/lib/types.ts` mirrors the Python models by hand, and
the failure mode is silent: add an enum member in Python and the console keeps compiling, keeps
passing `tsc`, and quietly cannot represent the new case. `tsc` sees only the TypeScript, so nothing
in the frontend toolchain can catch it.

Which makes these tests the guard on the guard. A drift checker that passes unconditionally is worse
than none, because it converts an unnoticed problem into a false assurance — so most of what follows
feeds it deliberately broken TypeScript and insists it fails.
"""

from __future__ import annotations

import pytest
from axiom.core.values import DerivationMethod

from scripts.check_console_types import (
    DEFAULT_TYPES,
    ENUM_MAP,
    MODEL_MAP,
    check,
    ts_enums,
    ts_interfaces,
)


@pytest.fixture(scope="module")
def source() -> str:
    return DEFAULT_TYPES.read_text(encoding="utf-8")


# ===================================================================== the real file


def test_the_committed_types_do_not_drift(source: str):
    """The assertion that matters. Everything else here proves this one can fail."""
    report = check(source)

    assert report.passed, "\n".join(f"{f.subject}: {f.detail}" for f in report.blocking)


def test_the_checker_actually_compares_something(source: str):
    """A checker that maps nothing passes everything."""
    report = check(source)

    assert report.checked_enums == len(ENUM_MAP)
    assert report.checked_models == len(MODEL_MAP)
    assert report.checked_enums >= 15
    assert report.checked_models >= 15


# ===================================================================== parsing


def test_enum_unions_are_parsed_across_line_breaks(source: str):
    """Most of these unions are formatted one member per line, so a single-line regex would
    silently parse none of them — and then report no drift, forever."""
    parsed = ts_enums(source)

    assert "DerivationMethod" in parsed
    assert "legacy_record" in parsed["DerivationMethod"]
    assert len(parsed["DerivationMethod"]) == len(list(DerivationMethod))


def test_interface_field_parsing_ignores_nested_object_keys(source: str):
    """Fields are read at one indent level only. A nested object literal leaking its keys into the
    parent's field set would make the model comparison meaningless."""
    parsed = ts_interfaces(source)

    # CrossSourceView holds an inline array of objects with `document_id`, `parser` and friends.
    assert "sources" in parsed["CrossSourceView"]
    assert "parser" not in parsed["CrossSourceView"]


def test_an_interface_with_extends_is_still_parsed(source: str):
    """`RiskPolicyView extends RiskPolicySummary` must not be skipped by the header pattern."""
    assert "curve" in ts_interfaces(source)["RiskPolicyView"]


# ===================================================================== catching drift


def test_a_removed_enum_member_fails(source: str):
    """The exact drift this session introduced four times over: a new Python enum value that the
    console cannot express."""
    broken = source.replace('  | "legacy_record"\n', "")
    report = check(broken)

    assert report.passed is False
    finding = next(f for f in report.blocking if f.subject == "DerivationMethod")
    assert "legacy_record" in finding.detail


def test_an_invented_enum_member_fails(source: str):
    """Checked in both directions. A TypeScript value Python never emits is a dead branch that
    reads as a supported case."""
    broken = source.replace('  | "human_correction"', '  | "human_correction"\n  | "telepathy"')
    report = check(broken)

    assert report.passed is False
    assert any("telepathy" in f.detail for f in report.blocking)


def test_a_dropped_model_field_fails(source: str):
    """A field the API serialises and the console does not declare is data the UI cannot reach."""
    broken = source.replace("  composite: number;\n", "")
    report = check(broken)

    assert report.passed is False
    finding = next(f for f in report.blocking if f.subject == "QualityIndex")
    assert "composite" in finding.detail


def test_a_missing_interface_fails(source: str):
    """Renaming an interface out from under the map must not read as "nothing to check"."""
    broken = source.replace("export interface QualityIndex {", "export interface QualityIdx {")
    report = check(broken)

    assert report.passed is False
    assert any(f.subject == "QualityIndex" for f in report.blocking)


# ===================================================================== what is allowed


def test_projection_fields_are_reported_but_not_failures(source: str):
    """The projection layer legitimately adds fields no Pydantic model holds — a value arrives
    carrying `score`, `decision` and `features`. Failing on those would make the checker
    unusable, so they are notes."""
    report = check(source)

    assert report.passed is True
    assert any("AttributeValue" in note and "projection" in note for note in report.notes)


def test_a_stale_allowlist_entry_is_reported(source: str):
    """An allowlist that only ever grows becomes a list of excuses nobody revisits, so an entry
    that has become unnecessary is surfaced."""
    from scripts import check_console_types

    original = check_console_types.ALLOWED_MISSING
    try:
        check_console_types.ALLOWED_MISSING = {"QualityIndex": {"composite": "no longer true"}}
        report = check(source)
        assert report.passed is True, "a stale entry is a note, not a failure"
        assert any("can be removed from the allowlist" in note for note in report.notes)
    finally:
        check_console_types.ALLOWED_MISSING = original
