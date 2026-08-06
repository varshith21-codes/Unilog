"""Tests for validation layer L4: agreement between independent sources.

L4 is the only layer that can catch a *confidently wrong* value. L0-L3 each reason about a single
observation, and all four pass on a figure that is well-formed and false, because a wrong number
printed in a datasheet is a well-formed number.

What these guard is the three-way verdict, and specifically the two ways it could be corrupted:

* a single source must never read as corroborated — that would claim evidence the corpus does not
  contain;
* and a conflict that cannot be ordered must never be resolved anyway, because silently picking a
  side is the exact behaviour this layer exists to prevent.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from axiom.core.evidence import BoundingBox, DocumentType, EvidenceSpan, SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.validation import Severity, ValidationLayer, Verdict
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.docintel import find_revision
from axiom.schema import load_default
from axiom.validate import CrossSourceValidator, promote_resolved, revision_rank

CLASS_CODE = "PLB.VLV.BALL.2PC"


@pytest.fixture(scope="module")
def registry():
    return load_default()


def sha_for(document_id: str) -> str:
    """A real 64-hex-character digest. SourceDocument validates the shape, correctly."""
    return hashlib.sha256(document_id.encode()).hexdigest()


def document(
    document_id: str,
    *,
    revision: str | None = None,
    supplier: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        uri=f"local://{document_id}",
        sha256=sha_for(document_id),
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        revision_label=revision,
        supplier_id=supplier,
    )


def value(code: str, canonical, *, document_id: str) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.9,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[
            EvidenceSpan(
                span_id=f"{document_id}-{code}",
                document_id=document_id,
                document_sha256=sha_for(document_id),
                quote=str(canonical),
                page=1,
                bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
                quote_verified=True,
                match_score=1.0,
            )
        ],
    )


def record_from(*observations: tuple[str, object, str]) -> ProductRecord:
    """Build a record where every observation coexists as a candidate."""
    product = ProductRecord(
        tenant_id="demo", sku="BA-100-075", class_code=CLASS_CODE, schema_version="v1"
    )
    for code, canonical, document_id in observations:
        product.add_candidate(value(code, canonical, document_id=document_id))
    return product


# ===================================================================== the record model


def test_add_candidate_does_not_supersede(registry):
    """The precondition for L4 existing at all. `add_value` supersedes, so using it for a second
    source would resolve every disagreement by read order — the last document parsed would win,
    silently, and there would be nothing left to adjudicate."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )

    assert len(product.current_values()) == 2
    assert "pressure_rating_wog" in product.conflicts()


def test_add_value_still_supersedes(registry):
    """The existing behaviour must be untouched: a *revision* of a value replaces it."""
    product = ProductRecord(tenant_id="demo", sku="X", class_code=CLASS_CODE)
    product.add_value(value("pressure_rating_wog", "600 PSI", document_id="sheet"))
    product.add_value(value("pressure_rating_wog", "700 PSI", document_id="sheet"))

    assert len(product.current_values()) == 1


# ===================================================================== agreement


def test_two_sources_agreeing_is_corroboration(registry):
    product = record_from(
        ("body_material", "Bronze C84400", "sheet"),
        ("body_material", "Bronze C84400", "catalog"),
    )
    documents = {"sheet": document("sheet"), "catalog": document("catalog")}

    report = CrossSourceValidator(registry).validate(product, documents)

    assert report.corroborated == ["body_material"]
    assert report.passed is True
    assert report.applicable is True
    result = report.results[0]
    assert result.layer is ValidationLayer.L4_CROSS_SOURCE
    assert result.verdict is Verdict.PASS
    assert result.rule_id == "L4_CORROBORATED"


def test_a_single_source_is_skipped_not_passed(registry):
    """The dishonesty this must avoid. One document cannot corroborate itself, and reporting a
    clean L4 on a single source would claim evidence the corpus does not contain."""
    product = record_from(("body_material", "Bronze C84400", "sheet"))
    report = CrossSourceValidator(registry).validate(
        product, {"sheet": document("sheet"), "catalog": document("catalog")}
    )

    assert report.corroborated == []
    assert report.single_source == ["body_material"]
    result = report.results[0]
    assert result.verdict is Verdict.SKIPPED
    assert result.verdict is not Verdict.PASS
    assert result.severity is Severity.INFO
    assert "not corroboration" in result.reason


def test_l4_is_not_applicable_with_one_document(registry):
    product = record_from(("body_material", "Bronze C84400", "sheet"))
    report = CrossSourceValidator(registry).validate(product, {"sheet": document("sheet")})

    assert report.applicable is False
    assert report.documents == 1


def test_agreement_respects_the_attribute_tolerance(registry):
    """Uses the same comparison primitive the backtest scores ground truth with. If the two could
    drift, a value could be correct against the golden set and in conflict between two sources
    that both stated it — a contradiction with no resolution."""
    definition = registry.attribute("each_weight")
    if not definition.tolerance:
        pytest.skip("each_weight declares no tolerance in this schema")

    nudged = 1.0 + definition.tolerance / 2
    product = record_from(
        ("each_weight", 1.0, "sheet"),
        ("each_weight", nudged, "catalog"),
    )
    report = CrossSourceValidator(registry).validate(
        product, {"sheet": document("sheet"), "catalog": document("catalog")}
    )

    assert report.corroborated == ["each_weight"], "inside tolerance is agreement"


# ===================================================================== disagreement


def test_a_newer_revision_wins_and_is_only_a_warning(registry):
    """An older catalogue printing last year's rating is out of date, not wrong. Treating that as
    a failure would bury real conflicts under a pile of stale-but-honest ones."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )
    documents = {
        "sheet": document("sheet", revision="Rev C 2024-08"),
        "catalog": document("catalog", revision="Rev A 2022-03"),
    }

    report = CrossSourceValidator(registry).validate(product, documents)

    assert report.passed is True, "a resolved conflict does not block publication"
    disagreement = report.disagreements[0]
    assert disagreement.resolved is True
    assert disagreement.winner is not None
    assert disagreement.winner.document.document_id == "sheet"

    result = report.results[0]
    assert result.verdict is Verdict.WARN
    assert result.is_blocking is False
    assert result.rule_id == "L4_SUPERSEDED"


def test_an_unorderable_conflict_blocks(registry):
    """With no revision marker there is no basis for precedence, and the system must not choose."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )
    documents = {"sheet": document("sheet"), "catalog": document("catalog")}

    report = CrossSourceValidator(registry).validate(product, documents)

    assert report.passed is False
    assert len(report.unresolved) == 1
    result = report.results[0]
    assert result.verdict is Verdict.FAIL
    assert result.is_blocking is True
    assert result.rule_id == "L4_UNRESOLVED_CONFLICT"
    assert "600 PSI" in result.counterexample
    assert "400 PSI" in result.counterexample


def test_one_missing_revision_marker_leaves_the_conflict_unresolved(registry):
    """The subtle case. If only one document carries a marker, the newest *known* document might
    not be the newest document, so preferring it would be a guess dressed as precedence."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )
    documents = {
        "sheet": document("sheet", revision="Rev C 2024-08"),
        "catalog": document("catalog"),  # no marker
    }

    report = CrossSourceValidator(registry).validate(product, documents)

    assert report.passed is False
    assert "no revision marker on catalog" in report.disagreements[0].reason


def test_equal_revisions_leave_the_conflict_unresolved(registry):
    """Two documents at the same revision that disagree is a genuine contradiction, not a
    currency problem, and no ordering can fix it."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "a"),
        ("pressure_rating_wog", "400 PSI", "b"),
    )
    documents = {
        "a": document("a", revision="Rev C 2024-08"),
        "b": document("b", revision="Rev C 2024-08"),
    }

    report = CrossSourceValidator(registry).validate(product, documents)
    assert report.passed is False
    assert "equivalent revisions" in report.disagreements[0].reason


# ===================================================================== supplier trust


def test_trust_breaks_a_tie_only_when_revisions_cannot(registry):
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )
    documents = {
        "sheet": document("sheet", supplier="milwaukee"),
        "catalog": document("catalog", supplier="acme"),
    }

    report = CrossSourceValidator(
        registry, trust={"milwaukee": 0.95, "acme": 0.4}
    ).validate(product, documents)

    disagreement = report.disagreements[0]
    assert disagreement.resolved is True
    assert disagreement.winner.document.supplier_id == "milwaukee"
    assert "more reliable supplier" in disagreement.reason


def test_trust_never_overrules_a_revision_marker(registry):
    """A supplier being historically reliable is a reason to prefer their figure, not evidence
    that a newer document is wrong. Letting a prior beat a printed revision would reintroduce
    exactly the unaccountable guessing this system removes."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "new_but_untrusted"),
        ("pressure_rating_wog", "400 PSI", "old_but_trusted"),
    )
    documents = {
        "new_but_untrusted": document(
            "new_but_untrusted", revision="Rev C 2024-08", supplier="acme"
        ),
        "old_but_trusted": document(
            "old_but_trusted", revision="Rev A 2022-03", supplier="milwaukee"
        ),
    }

    report = CrossSourceValidator(
        registry, trust={"milwaukee": 0.99, "acme": 0.10}
    ).validate(product, documents)

    winner = report.disagreements[0].winner
    assert winner.document.document_id == "new_but_untrusted"
    assert "newer revision" in report.disagreements[0].reason


def test_equal_trust_does_not_decide(registry):
    """A tie-break that cannot break the tie has nothing to say, and saying nothing is right."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "a"),
        ("pressure_rating_wog", "400 PSI", "b"),
    )
    documents = {"a": document("a", supplier="x"), "b": document("b", supplier="y")}

    report = CrossSourceValidator(registry, trust={"x": 0.7, "y": 0.7}).validate(
        product, documents
    )
    assert report.passed is False


def test_an_unknown_supplier_does_not_decide(registry):
    """Absent trust data is not zero trust."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "a"),
        ("pressure_rating_wog", "400 PSI", "b"),
    )
    documents = {"a": document("a", supplier="known"), "b": document("b", supplier="stranger")}

    report = CrossSourceValidator(registry, trust={"known": 0.9}).validate(product, documents)
    assert report.passed is False


# ===================================================================== promotion


def test_promotion_supersedes_only_the_resolved_losers(registry):
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "sheet"),
        ("pressure_rating_wog", "400 PSI", "catalog"),
    )
    documents = {
        "sheet": document("sheet", revision="Rev C 2024-08"),
        "catalog": document("catalog", revision="Rev A 2022-03"),
    }
    report = CrossSourceValidator(registry).validate(product, documents)

    assert promote_resolved(product, report) == 1
    remaining = product.current_values()
    assert len(remaining) == 1
    assert remaining[0].value_raw == "600 PSI"


def test_an_unresolved_conflict_stays_visible_after_promotion(registry):
    """Collapsing it would hide the one thing L4 exists to surface, and the reviewer would never
    see that two sources disagreed."""
    product = record_from(
        ("pressure_rating_wog", "600 PSI", "a"),
        ("pressure_rating_wog", "400 PSI", "b"),
    )
    report = CrossSourceValidator(registry).validate(
        product, {"a": document("a"), "b": document("b")}
    )

    assert promote_resolved(product, report) == 0
    assert len(product.current_values()) == 2
    assert "pressure_rating_wog" in product.conflicts()


# ===================================================================== revision ranking


def test_a_date_outranks_a_bare_letter():
    """A date is a stronger claim about recency than a letter, whose sequence is a convention."""
    dated = revision_rank(document("a", revision="Rev C 2024-08"))
    lettered = revision_rank(document("b", revision="Rev Z"))

    assert dated is not None and lettered is not None
    assert dated > lettered


def test_letters_order_alphabetically():
    assert revision_rank(document("a", revision="Rev F")) > revision_rank(
        document("b", revision="Rev C")
    )


def test_dates_order_chronologically():
    assert revision_rank(document("a", revision="2025-03")) > revision_rank(
        document("b", revision="2024-08")
    )


def test_no_marker_is_unrankable():
    """None rather than a `fetched_at` fallback. When a copy was downloaded says nothing about
    when the specification was written."""
    assert revision_rank(document("a")) is None
    assert revision_rank(document("a", revision="see manufacturer")) is None


# ===================================================================== revision parsing


@pytest.mark.parametrize(
    ("text", "letter", "date"),
    [
        ("Widget\nBronze Ball Valve            Rev C  2024-08\n", "C", "2024-08"),
        ("Widget\nGate Valve                   Rev F  2025-03\n", "F", "2025-03"),
        ("Catalog\n2023-11 Revision B\n", "B", "2023-11"),
        ("Datasheet\nRevision 4\n", "4", None),
        ("Datasheet\nEffective date: 2024-01-15\n", None, "2024-01-15"),
    ],
)
def test_a_revision_marker_is_read_off_the_page(text: str, letter, date):
    """The marker is already printed on the document. Module M2's revision awareness, without
    which L4's precedence has nothing to work with."""
    marker = find_revision(text)

    assert marker is not None
    assert marker.letter == letter
    assert marker.date == date
    assert marker.is_orderable


def test_a_reference_to_another_documents_revision_is_ignored():
    """"supersedes Rev B" describes a different document. Matching it would reliably pick the
    older label off the page — precisely inverting the ordering."""
    marker = find_revision("Datasheet Rev D 2025-01\nThis supersedes Rev B 2019-04\n")

    assert marker is not None
    assert marker.letter == "D"


def test_a_marker_buried_in_body_prose_is_not_used():
    """A revision block lives in a header or a footer, so the middle of a long document is not
    searched. A "Rev" mention there is far more likely to refer to some other document.

    Note the marker below sits well clear of both windows — a *footer* marker is legitimate and is
    deliberately still read.
    """
    body = "\n".join(
        [
            "Product page",
            *[f"paragraph {i}" for i in range(20)],
            "Rev Q 1999-01",
            *[f"more prose {i}" for i in range(20)],
            "End of document",
        ]
    )

    assert find_revision(body) is None


def test_a_footer_marker_is_read():
    """Plenty of catalogues print the revision block at the bottom of the page."""
    footer = "\n".join(["Product page", *[f"line {i}" for i in range(20)], "Rev D 2024-11"])
    marker = find_revision(footer)

    assert marker is not None
    assert marker.letter == "D"


def test_a_header_marker_wins_over_a_footer_one():
    """Both windows are searched, but the header is the document's own identity block."""
    text = "\n".join(
        ["Datasheet Rev C 2024-08", *[f"line {i}" for i in range(20)], "Form Rev A 2019-01"]
    )
    marker = find_revision(text)

    assert marker is not None
    assert marker.letter == "C"


def test_no_marker_returns_none():
    assert find_revision("A plain document with no revision block") is None
