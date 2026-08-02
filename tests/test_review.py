"""Tests for the review workspace: session assembly, decisions, and the HTTP surface.

Two properties carry most of the weight here.

**A citation must arrive at the UI with somewhere to highlight.** A review session whose
evidence cannot be pointed at is a session where the reviewer has to go and find the number
themselves, which is the manual job the system exists to remove. So the tests assert that
table-backed citations carry *both* a cell reference and a line index, not one or the other.

**A decision must move the priors.** If reviewing does not change what gets auto-accepted next
time, the review queue is a cost centre rather than a flywheel, and the "coverage rises with
volume" claim is marketing. The prior-movement assertions are that claim, made checkable.
"""

from __future__ import annotations

import json

import pytest
from axiom.confidence import (
    AcceptanceDecision,
    Priors,
    RiskPolicy,
    select_threshold,
)
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.gaps import Gap, GapReason
from axiom.core.product import Classification, ClassificationScheme, ProductRecord
from axiom.core.validation import ValidationLayer, ValidationResult
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.review import (
    ACCEPT,
    CORRECT,
    REJECT,
    ReviewSession,
    build_session,
    queue_summary,
    record_decision,
)
from axiom.schema import load_default

CLASS_CODE = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60

# Quotes chosen because they exercise different resolution paths in the parser. Verified
# against the fixture: the first two are dot-leader spec lines, the last two are table cells.
LINE_QUOTE = "600 PSI WOG @ 73 degF"
MATERIAL_QUOTE = "Bronze C84400"
CELL_QUOTE = "Lever"
SHORT_CELL_QUOTE = "12"
ABSENT_QUOTE = "Hastelloy C276 wetted parts, 1500 psi"


@pytest.fixture(scope="module")
def registry():
    return load_default()


def span(quote: str, *, page: int = 1, verified: bool = True, table_ref: str | None = None):
    return EvidenceSpan(
        span_id=f"sp-{abs(hash(quote)) % 10_000}",
        document_id="milwaukee-ba100@9f2c0000",
        document_sha256=SHA,
        quote=quote,
        page=page,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        quote_verified=verified,
        match_score=1.0 if verified else None,
        table_ref=table_ref,
    )


def value(
    code: str,
    canonical,
    quote: str,
    *,
    display: str | None = None,
    method: DerivationMethod = DerivationMethod.DOCUMENT_EXTRACTION,
    verified: bool = True,
    tier: str = "volume",
) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=display if display is not None else str(canonical),
        method=method,
        confidence=0.9,
        status=ValueStatus.CANDIDATE,
        model_tier=tier,
        evidence=[span(quote, verified=verified)],
    )


def record() -> ProductRecord:
    product = ProductRecord(
        tenant_id="demo",
        sku="BA-100-075",
        mpn="BA-100-075",
        mpn_normalized="BA-100-075",
        brand="Milwaukee Valve",
        supplier_id="milwaukee",
        class_code=CLASS_CODE,
        schema_version=f"{CLASS_CODE}@v1",
    )
    product.classifications.append(
        Classification(
            scheme=ClassificationScheme.INTERNAL,
            code=CLASS_CODE,
            path=["Plumbing", "Valves", "Ball Valves", "Two-Piece"],
            confidence=0.92,
        )
    )
    for attribute_value in (
        value(
            "pressure_rating_wog",
            Quantity(magnitude=600.0, unit="psi"),
            LINE_QUOTE,
            display="600 psi",
        ),
        value("body_material", "Bronze C84400", MATERIAL_QUOTE),
        value("handle_type", "Lever", CELL_QUOTE),
        value("port_type", "Full Port", SHORT_CELL_QUOTE),
        value(
            "temperature_range",
            ValueRange(minimum=-28.9, maximum=185.6, unit="degC"),
            "-20 degF to 366 degF",
            display="-20 to 366 degF",
        ),
    ):
        product.add_value(attribute_value)
    product.add_gap(Gap(attribute_code="cv_flow_coefficient", reason=GapReason.REFERRED_ELSEWHERE))
    return product


def permissive_policy() -> RiskPolicy:
    """A policy that accepts everything, so tests can control acceptance via decisions."""
    return RiskPolicy(
        epsilon=0.05,
        confidence_level=0.95,
        threshold=0.5,
        coverage=1.0,
        accepted=40,
        accepted_errors=0,
        observed_error_rate=0.0,
        error_upper_bound=0.04,
        calibration_size=40,
        achievable=True,
        reason="test policy",
    )


def decisions_for(product: ProductRecord, scores: dict[str, float]) -> list[AcceptanceDecision]:
    """Hand-built decisions so each test can pin the exact reason-code mix it needs."""
    made = []
    for attribute_value in product.current_values():
        code = attribute_value.attribute_code
        score = scores.get(code, 0.9)
        accepted = score >= 0.8
        made.append(
            AcceptanceDecision(
                code,
                score,
                accepted,
                ValueStatus.AUTO_ACCEPTED if accepted else ValueStatus.QUEUED_FOR_REVIEW,
                "auto_accepted" if accepted else "below_threshold",
                f"score {score:.3f}",
            )
        )
    return made


@pytest.fixture
def session(registry, parsed_datasheet):
    product = record()
    scores = {
        "pressure_rating_wog": 0.95,
        "body_material": 0.91,
        "handle_type": 0.44,
        "port_type": 0.62,
        "temperature_range": 0.88,
    }
    return build_session(
        product,
        parsed_datasheet,
        registry,
        decisions_for(product, scores),
        scores,
        permissive_policy(),
        quality={"completeness": 0.42, "verifiability": 1.0},
    )


# ===================================================================== session assembly


def test_session_carries_the_source_text(session):
    """Without the source on screen, a reviewer is back to opening the PDF by hand."""
    assert session.pages, "a session with no source pages cannot show evidence"
    assert any("SPECIFICATIONS" in line for line in session.pages[0].lines)


def test_session_identifies_the_document_it_reviewed(session):
    assert session.document_sha256 == SHA
    assert session.document_id


def test_line_citation_resolves_to_a_page_and_line(session):
    evidence = session.item("pressure_rating_wog").evidence[0]
    assert evidence.page == 1
    assert evidence.line_index is not None
    assert evidence.method == "exact_line"
    assert evidence.verified is True


def test_table_citation_carries_both_a_cell_reference_and_a_line_index(session):
    """The regression that matters most for the evidence viewer.

    Table values are exactly the ones a reviewer must see in situ, because the whole question
    is whether the value came from *this* part number's row rather than a neighbour's. A cell
    reference alone tells the UI which logical cell matched but not where to draw, so the
    highlight silently did nothing on precisely the values that needed it.
    """
    evidence = session.item("handle_type").evidence[0]
    assert evidence.table_ref is not None, "a table match must report its cell"
    assert evidence.table_ref.startswith("t1:")
    assert evidence.line_index is not None, "a table match must also be locatable as text"
    assert session.pages[0].lines[evidence.line_index], "the line index must address real text"


def test_short_numeric_quote_binds_to_an_exact_table_cell(session):
    """Single- and double-digit carton quantities are real data, not fabrication.

    The anti-fabrication guard rejects short quotes as evidence *except* where an exact cell
    match supplies the precision the quote itself lacks. Without that carve-out the guard
    discarded correct values from the last row of every ordering table.
    """
    evidence = session.item("port_type").evidence[0]
    assert evidence.quote == SHORT_CELL_QUOTE
    assert evidence.table_ref is not None
    assert evidence.table_ref.count(":") >= 2, "a short quote is only evidence as a full cell ref"


def test_unlocatable_quote_keeps_the_quote_but_reports_no_position(registry, parsed_datasheet):
    """A quote that cannot be found must not be quietly dropped or quietly relocated."""
    product = record()
    product.add_value(value("seat_material", "Hastelloy", ABSENT_QUOTE))
    built = build_session(
        product,
        parsed_datasheet,
        registry,
        decisions_for(product, {}),
        {},
        permissive_policy(),
    )
    evidence = built.item("seat_material").evidence[0]
    assert evidence.quote == ABSENT_QUOTE
    assert evidence.line_index is None
    assert evidence.method is None
    assert evidence.locator == "p.1", "the model's page claim is retained even when unverified"


def test_required_attributes_are_flagged_from_the_schema(session):
    assert session.item("pressure_rating_wog").is_required is True
    assert session.item("handle_type").is_required is False


def test_enum_attributes_carry_their_allowed_values(session):
    """A correction UI without the option list forces free text, which reintroduces dirt."""
    allowed = session.item("port_type").allowed_values
    assert "Full Port" in allowed
    assert "Reduced Port" in allowed


def test_quantity_is_rendered_as_a_plain_structure(session):
    """The API hands this dict straight to a JSON encoder."""
    assert session.item("pressure_rating_wog").value_canonical == {
        "magnitude": 600.0,
        "unit": "psi",
    }


def test_range_is_rendered_with_both_bounds(session):
    assert session.item("temperature_range").value_canonical == {
        "minimum": -28.9,
        "maximum": 185.6,
        "unit": "degC",
    }


def test_session_is_json_serialisable_without_coercion(session):
    """The HTTP layer cannot rely on ``default=str``; anything unencodable is a 500."""
    encoded = json.dumps(session.to_dict())
    assert '"BA-100-075"' in encoded


def test_session_carries_gaps_and_policy_and_quality(session):
    assert session.gaps[0]["code"] == "cv_flow_coefficient"
    assert session.policy["threshold"] == 0.5
    assert session.policy["achievable"] is True
    assert session.quality["verifiability"] == 1.0


def test_class_and_category_are_resolved_for_the_header(session):
    assert session.class_code == CLASS_CODE
    assert session.class_name
    assert session.category_path == ["Plumbing", "Valves", "Ball Valves", "Two-Piece"]


def test_attribute_names_come_from_the_dictionary_not_the_code(session):
    item = session.item("pressure_rating_wog")
    assert item.attribute_name != "pressure_rating_wog"
    assert item.datatype


# ===================================================================== the queue


def test_accepted_values_are_not_in_the_queue(session):
    queued = {item.attribute_code for item in session.queue}
    assert "pressure_rating_wog" not in queued
    assert {"handle_type", "port_type"} <= queued


def test_queue_groups_by_reason_then_ascends_by_score(registry, parsed_datasheet):
    """Batching one failure mode at a time beats context-switching per SKU.

    Within a reason code the least confident value comes first, because that is where a
    reviewer's attention is worth the most.
    """
    product = record()
    scores = {
        "pressure_rating_wog": 0.30,
        "body_material": 0.10,
        "handle_type": 0.20,
        "port_type": 0.95,
        "temperature_range": 0.40,
    }
    built = build_session(
        product,
        parsed_datasheet,
        registry,
        decisions_for(product, scores),
        scores,
        permissive_policy(),
    )
    reasons = [item.reason_code for item in built.queue]
    assert reasons == sorted(reasons), "items must be grouped by failure mode"

    within = [item.score for item in built.queue if item.reason_code == "below_threshold"]
    assert within == sorted(within), "least confident first inside a group"


def test_needs_attention_is_the_inverse_of_accepted(session):
    for item in session.items:
        assert item.needs_attention is (not item.accepted)


def test_queue_summary_counts_by_reason(session):
    summary = queue_summary(session)
    assert summary["total"] == 5
    assert summary["pending"] + summary["accepted"] == summary["total"]
    assert summary["by_reason"]["below_threshold"] == summary["pending"]


def test_queue_summary_counts_blocking_failures_and_warnings(registry, parsed_datasheet):
    product = record()
    poisoned = value("seat_material", "RPTFE", "Seat Material .................. RPTFE")
    poisoned.validations = [
        ValidationResult.failed(ValidationLayer.L2_DOMAIN_RULE, "R_SEAT", "incompatible"),
        ValidationResult.warned(ValidationLayer.L3_STATISTICAL, "R_DIST", "unusual"),
    ]
    product.add_value(poisoned)

    built = build_session(
        product,
        parsed_datasheet,
        registry,
        decisions_for(product, {}),
        {},
        permissive_policy(),
    )
    summary = queue_summary(built)
    assert summary["blocking_failures"] == 1
    assert summary["warnings"] == 1


def test_validation_findings_reach_the_reviewer_with_a_reason(registry, parsed_datasheet):
    product = record()
    poisoned = value("seat_material", "RPTFE", "Seat Material .................. RPTFE")
    poisoned.validations = [
        ValidationResult.failed(
            ValidationLayer.L2_DOMAIN_RULE,
            "R_SEAT_TEMP",
            "seat material is not rated to the stated temperature",
            counterexample="RPTFE at 366 degF",
            suggested_fix="confirm the seat material against the derating chart",
        )
    ]
    product.add_value(poisoned)
    built = build_session(
        product, parsed_datasheet, registry, decisions_for(product, {}), {}, permissive_policy()
    )

    finding = built.item("seat_material").validations[0]
    assert finding.blocking is True
    assert finding.reason
    assert finding.counterexample == "RPTFE at 366 degF"
    assert finding.suggested_fix


# ===================================================================== decisions


def test_accept_marks_the_value_approved_and_clears_it_from_the_queue(session):
    priors = Priors()
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=priors)

    item = session.item("handle_type")
    assert item.accepted is True
    assert item.status == ValueStatus.HUMAN_APPROVED.value
    assert "handle_type" not in {q.attribute_code for q in session.queue}


def test_reject_marks_the_value_rejected_and_keeps_it_out_of_publication(session):
    priors = Priors()
    record_decision(session, "handle_type", REJECT, reviewer="ana", priors=priors)

    item = session.item("handle_type")
    assert item.accepted is False
    assert item.status == ValueStatus.REJECTED.value


def test_correction_replaces_the_value_and_records_the_method(session):
    priors = Priors()
    outcome = record_decision(
        session,
        "handle_type",
        CORRECT,
        reviewer="ana",
        priors=priors,
        corrected_value="Locking Lever",
    )

    item = session.item("handle_type")
    assert item.value_display == "Locking Lever"
    assert item.value_raw == "Locking Lever"
    assert item.method == "human_correction"
    assert item.accepted is True
    assert outcome.before == "Lever"
    assert outcome.after == "Locking Lever"


def test_correction_without_a_value_is_refused(session):
    with pytest.raises(ValueError, match="corrected value"):
        record_decision(
            session, "handle_type", CORRECT, reviewer="ana", priors=Priors()
        )


def test_unknown_action_is_refused(session):
    with pytest.raises(ValueError, match="unknown review action"):
        record_decision(session, "handle_type", "maybe", reviewer="ana", priors=Priors())


def test_decision_on_an_unknown_attribute_is_refused(session):
    with pytest.raises(KeyError):
        record_decision(session, "not_an_attribute", ACCEPT, reviewer="ana", priors=Priors())


def test_accept_raises_the_attribute_prior(session):
    """The flywheel. Without this, reviewing is pure cost and coverage never improves."""
    priors = Priors()
    outcome = record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=priors)
    assert outcome.prior_after > outcome.prior_before


def test_reject_lowers_the_attribute_prior(session):
    priors = Priors()
    outcome = record_decision(session, "handle_type", REJECT, reviewer="ana", priors=priors)
    assert outcome.prior_after < outcome.prior_before


def test_a_correction_is_evidence_the_extractor_was_wrong(session):
    """A correction must not be scored as a success just because the value ends up right."""
    priors = Priors()
    outcome = record_decision(
        session,
        "handle_type",
        CORRECT,
        reviewer="ana",
        priors=priors,
        corrected_value="Locking Lever",
    )
    assert outcome.prior_after < outcome.prior_before


def test_a_single_observation_does_not_claim_certainty(session):
    """One accept is not evidence of a perfect attribute; the prior is shrunk toward neutral."""
    priors = Priors()
    outcome = record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=priors)
    assert outcome.prior_after < 0.6, "one sample must not produce a confident prior"


def test_supplier_prior_is_updated_when_a_supplier_is_known(session):
    priors = Priors()
    record_decision(
        session,
        "handle_type",
        ACCEPT,
        reviewer="ana",
        priors=priors,
        supplier_id="milwaukee",
    )
    assert priors.source_prior("milwaukee", "handle_type") > 0.5


def test_every_decision_is_appended_to_an_audit_trail(session):
    priors = Priors()
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=priors)
    record_decision(session, "port_type", REJECT, reviewer="bo", priors=priors)

    assert len(session.decisions) == 2
    assert [d["reviewer"] for d in session.decisions] == ["ana", "bo"]
    assert all(d["recorded_at"] for d in session.decisions)


def test_reviewed_items_report_a_human_reason_code(session):
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    assert session.item("handle_type").reason_code == "human_accept"
    assert "ana" in session.item("handle_type").detail


# ===================================================================== persistence


def test_save_and_load_round_trips_the_whole_session(session, tmp_path):
    path = session.save(tmp_path / "BA-100-075.json")
    restored = ReviewSession.load(path)

    assert restored.sku == session.sku
    assert restored.document_sha256 == session.document_sha256
    assert len(restored.items) == len(session.items)
    assert len(restored.pages) == len(session.pages)
    assert restored.pages[0].lines == session.pages[0].lines


def test_round_trip_preserves_evidence_positions(session, tmp_path):
    restored = ReviewSession.load(session.save(tmp_path / "s.json"))
    original = session.item("handle_type").evidence[0]
    reloaded = restored.item("handle_type").evidence[0]

    assert reloaded.line_index == original.line_index
    assert reloaded.table_ref == original.table_ref
    assert reloaded.verified == original.verified


def test_round_trip_preserves_validation_findings(registry, parsed_datasheet, tmp_path):
    product = record()
    poisoned = value("seat_material", "RPTFE", "Seat Material .................. RPTFE")
    poisoned.validations = [
        ValidationResult.failed(ValidationLayer.L2_DOMAIN_RULE, "R_SEAT", "incompatible")
    ]
    product.add_value(poisoned)
    built = build_session(
        product, parsed_datasheet, registry, decisions_for(product, {}), {}, permissive_policy()
    )

    restored = ReviewSession.load(built.save(tmp_path / "s.json"))
    assert restored.item("seat_material").validations[0].blocking is True


def test_the_queue_is_derived_not_stored(session, tmp_path):
    """Persisting the queue would let it drift out of step with the items it describes."""
    path = session.save(tmp_path / "s.json")
    assert "queue" in json.loads(path.read_text(encoding="utf-8")), "exported for the UI"

    restored = ReviewSession.load(path)
    assert [i.attribute_code for i in restored.queue] == [
        i.attribute_code for i in session.queue
    ]


def test_decisions_survive_a_round_trip(session, tmp_path):
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    restored = ReviewSession.load(session.save(tmp_path / "s.json"))

    assert len(restored.decisions) == 1
    assert restored.item("handle_type").accepted is True


# ===================================================================== HTTP surface


@pytest.fixture
def client(session, tmp_path, monkeypatch):
    """A TestClient wired to a throwaway session and calibration directory.

    Pointing the app at ``tmp_path`` matters: these tests write decisions, and a test that
    mutates ``data/sessions`` would corrupt the demo state.
    """
    from fastapi.testclient import TestClient

    from apps.api import main

    sessions = tmp_path / "sessions"
    calibration = tmp_path / "calibration"
    sessions.mkdir()
    calibration.mkdir()
    session.save(sessions / f"{session.sku}.json")

    monkeypatch.setattr(main, "SESSION_DIR", sessions)
    monkeypatch.setattr(main, "CALIBRATION_DIR", calibration)
    return TestClient(main.app)


def test_health_reports_session_count(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["sessions"] == 1


def test_sessions_list_carries_queue_counts(client):
    payload = client.get("/api/sessions").json()
    assert len(payload["sessions"]) == 1
    entry = payload["sessions"][0]
    assert entry["sku"] == "BA-100-075"
    assert entry["brand"] == "Milwaukee Valve"
    assert entry["pending"] >= 1


def test_get_session_includes_a_summary(client):
    payload = client.get("/api/session/BA-100-075").json()
    assert payload["sku"] == "BA-100-075"
    assert payload["summary"]["total"] == 5
    assert payload["pages"]


def test_missing_session_explains_how_to_create_one(client):
    response = client.get("/api/session/NOPE")
    assert response.status_code == 404
    assert "--save-session" in response.json()["detail"]


@pytest.mark.parametrize(
    "sku",
    ["", "..", "../pyproject", "a/b", "a\\b", "..\\..\\pyproject", "./../data"],
)
def test_the_path_guard_rejects_anything_that_could_escape(sku):
    """Tested directly rather than through routing.

    Starlette happens to reject most of these before the handler runs, because ``{sku}``
    matches a single decoded path segment. Leaning on that would be a mistake: it is
    framework behaviour rather than a property of this code, it does not cover the backslash
    form on Windows, and ``_session_path`` is also reached from the decision endpoint. The
    guard has to hold on its own.
    """
    from fastapi import HTTPException

    from apps.api.main import _session_path

    with pytest.raises(HTTPException) as raised:
        _session_path(sku)
    assert raised.value.status_code == 400
    assert raised.value.detail == "invalid sku"


def test_the_path_guard_accepts_an_ordinary_sku():
    """A guard that rejects real part numbers would be worse than no guard."""
    from apps.api.main import _session_path

    assert _session_path("BA-100-075").name == "BA-100-075.json"


@pytest.mark.parametrize(
    "path",
    [
        "/api/session/..%2F..%2Fpyproject",
        "/api/session/../../pyproject",
        "/api/session/..",
        "/api/session/a%5Cb",
        "/api/session/%2e%2e%2f%2e%2e%2fpyproject",
    ],
)
def test_traversal_attempts_never_return_a_document(client, path):
    """The property, independent of which layer refuses: nothing outside the dir comes back."""
    response = client.get(path)
    assert response.status_code in {400, 404}
    assert "[build-system]" not in response.text, "a file outside the session dir leaked"
    assert "requires-python" not in response.text


def test_a_backslash_reaching_the_handler_is_refused_by_the_app(client):
    """Windows-specific, and the case routing does not catch: ``a\\b`` is one path segment."""
    response = client.get("/api/session/a%5Cb")
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid sku"


def test_decision_endpoint_records_and_reports_prior_movement(client):
    response = client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "accept", "reviewer": "ana@example.com"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["outcome"]["action"] == "accept"
    assert payload["outcome"]["prior_after"] > payload["outcome"]["prior_before"]
    assert payload["item"]["accepted"] is True
    assert payload["summary"]["pending"] < 5


def test_decision_is_persisted_across_requests(client):
    client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "accept", "reviewer": "ana"},
    )
    payload = client.get("/api/session/BA-100-075").json()
    item = next(i for i in payload["items"] if i["attribute_code"] == "handle_type")
    assert item["accepted"] is True
    assert payload["decisions"][0]["reviewer"] == "ana"


def test_correction_through_the_api_changes_the_value(client):
    response = client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "correct", "reviewer": "ana", "corrected_value": "Locking Lever"},
    )
    assert response.status_code == 200
    assert response.json()["item"]["value_display"] == "Locking Lever"


def test_correction_without_a_value_is_a_client_error(client):
    response = client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "correct", "reviewer": "ana"},
    )
    assert response.status_code == 400


def test_decision_on_an_unknown_attribute_is_a_404(client):
    response = client.post(
        "/api/session/BA-100-075/decision/not_an_attribute",
        json={"action": "accept", "reviewer": "ana"},
    )
    assert response.status_code == 404


def test_an_unrecognised_action_is_rejected_by_the_schema(client):
    response = client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "delete", "reviewer": "ana"},
    )
    assert response.status_code == 422


def test_policy_endpoint_is_honest_when_uncalibrated(client):
    payload = client.get("/api/policy?epsilon=0.05").json()
    assert payload["achievable"] is False
    assert "run_backtest" in payload["reason"]


def test_policy_endpoint_returns_a_risk_coverage_curve(client, tmp_path, monkeypatch):
    from apps.api import main

    scores = [0.99] * 40 + [0.40] * 10
    labels = [True] * 40 + [False] * 10
    (tmp_path / "calibration" / "calibration_set.json").write_text(
        json.dumps({"scores": scores, "labels": labels}), encoding="utf-8"
    )
    monkeypatch.setattr(main, "CALIBRATION_DIR", tmp_path / "calibration")

    payload = client.get("/api/policy?epsilon=0.10").json()
    expected = select_threshold(scores, labels, epsilon=0.10)

    assert payload["achievable"] is expected.achievable
    assert payload["threshold"] == expected.summary()["threshold"]
    assert payload["curve"], "the risk dial needs the whole curve, not one point"
    assert all(0.0 <= point["coverage"] <= 1.0 for point in payload["curve"])


def test_tightening_the_budget_never_increases_coverage(client, tmp_path, monkeypatch):
    """The dial has to behave monotonically or it is not a risk control."""
    from apps.api import main

    scores = [0.99] * 30 + [0.80] * 20 + [0.30] * 10
    labels = [True] * 30 + [True] * 18 + [False] * 2 + [False] * 10
    (tmp_path / "calibration" / "calibration_set.json").write_text(
        json.dumps({"scores": scores, "labels": labels}), encoding="utf-8"
    )
    monkeypatch.setattr(main, "CALIBRATION_DIR", tmp_path / "calibration")

    loose = client.get("/api/policy?epsilon=0.20").json()
    strict = client.get("/api/policy?epsilon=0.02").json()
    assert strict.get("coverage", 0.0) <= loose.get("coverage", 0.0)


def test_console_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
