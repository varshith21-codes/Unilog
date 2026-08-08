"""Tests for the console projection and the dataset endpoint.

The property carrying the most weight here is that **a review decision must not rewrite what
the pipeline said.** A bundle is the record of what the machine produced under a given schema
and model version; every accuracy number in the project is computed against it. If clicking
accept edited that record in place, "how often was the extractor right?" would become
unanswerable, because the only surviving copy would already agree with the reviewer.

So `overlay_review_decisions` returns a *new* bundle and leaves its input untouched. The tests
below assert that directly, because it is the kind of invariant that a plausible-looking
refactor breaks silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from axiom.confidence import AcceptanceDecision, Priors, RiskPolicy
from axiom.console import (
    build_bundle,
    build_dataset,
    dataset_stats,
    jsonable,
    overlay_cross_source,
    overlay_equivalence,
    overlay_review_decisions,
    serialise_class,
    serialise_document,
    serialise_pages,
    serialise_values,
)
from axiom.core.certificate import build_certificate
from axiom.core.evidence import BoundingBox, DocumentType, EvidenceSpan, SourceDocument
from axiom.core.gaps import Gap, GapReason
from axiom.core.product import Classification, ClassificationScheme, ProductRecord
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueStatus,
)
from axiom.review import ACCEPT, CORRECT, build_session, record_decision
from axiom.schema import load_default
from axiom.syndicate import export_all
from axiom.validate import Validator

CLASS_CODE = "PLB.VLV.BALL.2PC"
SHA = "9f2c" + "0" * 60
LINE_QUOTE = "600 PSI WOG @ 73 degF"
CELL_QUOTE = "Lever"


@pytest.fixture(scope="module")
def registry():
    return load_default()


# --------------------------------------------------------------------------- stubs
#
# The projection takes already-computed artifacts. Standing these in keeps the tests about
# flattening rather than about re-running a pipeline; the real objects are exercised end to end
# by scripts/run_pipeline.py.


@dataclass
class StubArtifact:
    document: SourceDocument
    size_bytes: int = 4096
    storage_uri: str = "local://9f/2c/artifact.txt"


@dataclass
class StubCandidate:
    code: str
    score: float
    path_text: str


@dataclass
class StubClassification:
    candidates: list[StubCandidate]

    def summary(self) -> dict:
        return {"class_code": CLASS_CODE, "confidence": 0.92, "method": "stub"}


@dataclass
class StubExtraction:
    def summary(self) -> dict:
        return {
            "requested": 23,
            "values": 3,
            "gaps": 1,
            "rejected_unverifiable": 0,
            "citation_coverage": 1.0,
            "input_tokens": 1200,
            "output_tokens": 400,
            "escalations": 0,
            "latency_ms": 3100,
        }


def source_document() -> SourceDocument:
    return SourceDocument(
        document_id="ba100@9f2c0000",
        uri="local://9f/2c/ba100.txt",
        sha256=SHA,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        revision_label="Rev C 2024-08",
        supplier_id="milwaukee",
    )


def span(quote: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"sp-{abs(hash(quote)) % 9999}",
        document_id="ba100@9f2c0000",
        document_sha256=SHA,
        quote=quote,
        page=1,
        bbox=BoundingBox(x0=1, y0=1, x1=2, y1=2),
        quote_verified=True,
        match_score=1.0,
    )


def value(code: str, canonical, quote: str, display: str | None = None) -> AttributeValue:
    return AttributeValue(
        attribute_code=code,
        value_raw=str(canonical),
        value_canonical=canonical,
        value_display=display if display is not None else str(canonical),
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.9,
        status=ValueStatus.CANDIDATE,
        model_tier="volume",
        evidence=[span(quote)],
    )


def product() -> ProductRecord:
    record = ProductRecord(
        tenant_id="demo",
        sku="BA-100-075",
        mpn="BA-100-075",
        mpn_normalized="BA-100-075",
        brand="Milwaukee Valve",
        supplier_id="milwaukee",
        class_code=CLASS_CODE,
        schema_version=f"{CLASS_CODE}@v1",
    )
    record.classifications.append(
        Classification(
            scheme=ClassificationScheme.INTERNAL,
            code=CLASS_CODE,
            path=["Plumbing", "Valves", "Ball Valves", "Two-Piece"],
            confidence=0.92,
        )
    )
    for attribute_value in (
        value("pressure_rating_wog", Quantity(magnitude=600.0, unit="psi"), LINE_QUOTE, "600 psi"),
        value("body_material", "Bronze C84400", LINE_QUOTE),
        value("handle_type", "Lever", CELL_QUOTE),
    ):
        record.add_value(attribute_value)
    record.add_gap(Gap(attribute_code="cv_flow_coefficient", reason=GapReason.REFERRED_ELSEWHERE))
    return record


SCORES = {"pressure_rating_wog": 0.95, "body_material": 0.88, "handle_type": 0.44}
FEATURES = {code: {"evidence_verified": 1.0, "attribute_prior": 0.5} for code in SCORES}


def decisions_for(record: ProductRecord) -> list[AcceptanceDecision]:
    made = []
    for attribute_value in record.current_values():
        code = attribute_value.attribute_code
        accepted = SCORES[code] >= 0.7
        status = ValueStatus.AUTO_ACCEPTED if accepted else ValueStatus.QUEUED_FOR_REVIEW
        attribute_value.status = status
        made.append(
            AcceptanceDecision(
                code,
                SCORES[code],
                accepted,
                status,
                "auto_accepted" if accepted else "below_threshold",
                f"score {SCORES[code]:.3f}",
            )
        )
    return made


@pytest.fixture
def bundle(registry):
    record = product()
    made = decisions_for(record)
    validation = Validator(registry).validate(record)
    certificate = build_certificate(
        record,
        required_attribute_codes=registry.required_codes(CLASS_CODE),
        pipeline_version="axiom-test",
    )
    return build_bundle(
        registry=registry,
        record=record,
        artifact=StubArtifact(document=source_document()),
        classification=StubClassification(
            candidates=[StubCandidate(CLASS_CODE, 0.92, "Plumbing > Valves")]
        ),
        extraction=StubExtraction(),
        normalization_issues=[],
        validation=validation,
        scores=SCORES,
        features=FEATURES,
        decisions=made,
        certificate=certificate,
        exports=export_all(record, registry),
    )


def policy() -> RiskPolicy:
    return RiskPolicy(
        epsilon=0.05,
        confidence_level=0.95,
        threshold=0.7,
        coverage=1.0,
        accepted=40,
        accepted_errors=0,
        observed_error_rate=0.0,
        error_upper_bound=0.04,
        calibration_size=40,
        achievable=True,
        reason="test",
    )


# ===================================================================== jsonable


def test_jsonable_unwraps_pydantic_dataclasses_enums_and_datetimes():
    payload = jsonable(
        {
            "span": span("x"),
            "method": DerivationMethod.DOCUMENT_EXTRACTION,
            "when": datetime(2026, 8, 1, tzinfo=UTC),
            "nested": [StubCandidate("A", 1.0, "p")],
        }
    )
    assert payload["span"]["quote"] == "x"
    assert payload["method"] == "document_extraction"
    assert payload["when"].startswith("2026-08-01")
    assert payload["nested"][0]["code"] == "A"
    json.dumps(payload), "the whole point is that the result encodes without a default hook"


# ===================================================================== serialisation


def test_pages_carry_line_and_cell_geometry(parsed_datasheet):
    """Without coordinates the evidence viewer cannot draw a highlight anywhere."""
    pages = serialise_pages(parsed_datasheet)
    assert pages[0]["number"] == 1
    assert pages[0]["width"] > 0 and pages[0]["height"] > 0
    assert len(pages[0]["lines"][0]["bbox"]) == 4

    table = pages[0]["tables"][0]
    assert table["rows"], "a table without rows cannot be rendered"
    assert len(table["cells"][0]["bbox"]) == 4


def test_document_is_joined_to_what_parsing_found(parsed_datasheet):
    document = serialise_document(StubArtifact(document=source_document()), parsed_datasheet)
    assert document["sha256"] == SHA
    assert document["parser"]
    assert document["line_count"] > 0
    assert document["table_count"] >= 1
    assert document["size_bytes"] == 4096


def test_class_joins_bindings_to_dictionary_definitions(registry):
    """The binding has requirement and weight; the dictionary has name and permitted values.

    Joining here rather than in the browser is what keeps the whole attribute dictionary out of
    the client bundle.
    """
    definition = serialise_class(registry, CLASS_CODE)
    assert definition["code"] == CLASS_CODE
    assert definition["required_codes"]

    port = next(a for a in definition["attributes"] if a["code"] == "port_type")
    assert port["requirement"] in {"required", "recommended", "optional"}
    assert port["weight"] > 0
    assert port["name"]
    assert "Full Port" in [v["value"] for v in port["allowed_values"]]


def test_values_are_joined_to_score_features_and_decision(registry):
    record = product()
    made = decisions_for(record)
    values = serialise_values(record, scores=SCORES, features=FEATURES, decisions=made)

    handle = next(v for v in values if v["attribute_code"] == "handle_type")
    assert handle["score"] == 0.44
    assert handle["decision"]["reason_code"] == "below_threshold"
    assert handle["features"]["evidence_verified"] == 1.0
    assert handle["has_verified_evidence"] is True


def test_values_are_ordered_deterministically(registry):
    """An unstable order makes every diff of the output unreadable."""
    record = product()
    made = decisions_for(record)
    codes = [
        v["attribute_code"]
        for v in serialise_values(record, scores=SCORES, features=FEATURES, decisions=made)
    ]
    assert codes == sorted(codes)


# ===================================================================== bundle


def test_bundle_references_its_document_and_class_by_key(bundle):
    """The dataset stores documents once; bundles point at them."""
    assert bundle["document_id"] == "ba100@9f2c0000"
    assert bundle["class_code"] == CLASS_CODE


def test_bundle_carries_everything_a_screen_needs(bundle):
    for key in (
        "record",
        "classifications",
        "values",
        "gaps",
        "extraction",
        "validation",
        "certificate",
        "channels",
        "metrics",
    ):
        assert key in bundle, f"the console renders {key}"


def test_the_record_block_projects_the_variant_parent_pointer(bundle, registry):
    """Without this field the console cannot reconstruct a series at all.

    ``parent_sku`` is the only thing linking one orderable part number to the others cut from the
    same ordering table. The projection enumerates record fields explicitly rather than dumping the
    model, so a field that is never named is silently unreachable from every screen — which is what
    this one was.
    """
    assert "parent_sku" in bundle["record"], "the console groups variant series on this"
    assert bundle["record"]["parent_sku"] is None, "a standalone product has no parent"

    record = product()
    record.parent_sku = "BA-100-050"
    made = decisions_for(record)
    child = build_bundle(
        registry=registry,
        record=record,
        artifact=StubArtifact(document=source_document()),
        classification=StubClassification(
            candidates=[StubCandidate(CLASS_CODE, 0.92, "Plumbing > Valves")]
        ),
        extraction=StubExtraction(),
        normalization_issues=[],
        validation=Validator(registry).validate(record),
        scores=SCORES,
        features=FEATURES,
        decisions=made,
        certificate=build_certificate(
            record,
            required_attribute_codes=registry.required_codes(CLASS_CODE),
            pipeline_version="axiom-test",
        ),
        exports=export_all(record, registry),
    )

    assert child["record"]["parent_sku"] == "BA-100-050"


def test_bundle_certificate_signature_is_verified_server_side(bundle):
    """A browser cannot check an HMAC it has no key for, so the verdict travels with it."""
    assert bundle["certificate"]["signature_verified"] is True


def test_bundle_metrics_distinguish_publishable_from_pending(bundle):
    metrics = bundle["metrics"]
    assert metrics["values_total"] == 3
    assert metrics["values_publishable"] == 2
    assert metrics["values_needing_review"] == 1
    assert metrics["gaps_total"] == 1


def test_bundle_is_json_serialisable_without_coercion(bundle):
    assert json.loads(json.dumps(bundle))["sku"] == "BA-100-075"


# ===================================================================== dataset


def test_dataset_normalises_documents_and_classes(bundle, registry, parsed_datasheet):
    """Several SKUs off one datasheet must not each carry a copy of its page geometry."""
    second = {**bundle, "sku": "BA-100-100"}
    dataset = build_dataset(
        [bundle, second],
        documents={
            "ba100@9f2c0000": {
                "document": serialise_document(
                    StubArtifact(document=source_document()), parsed_datasheet
                ),
                "pages": serialise_pages(parsed_datasheet),
            }
        },
        class_definitions={CLASS_CODE: serialise_class(registry, CLASS_CODE)},
        policy=policy().summary(),
        meta={"generator": "test", "live": True},
    )

    assert len(dataset["skus"]) == 2
    assert len(dataset["documents"]) == 1, "one document, stored once"
    assert len(dataset["class_definitions"]) == 1
    assert all(b["document_id"] in dataset["documents"] for b in dataset["skus"])


def test_dataset_stamps_a_generation_time(bundle):
    dataset = build_dataset(
        [bundle], documents={}, class_definitions={}, policy={}, meta={"generator": "test"}
    )
    assert dataset["meta"]["generated_at"]
    assert dataset["meta"]["generator"] == "test"


def test_dataset_stats_counts_outcomes(bundle):
    dataset = build_dataset(
        [bundle], documents={}, class_definitions={}, policy={}, meta={"generator": "test"}
    )
    stats = dataset_stats(dataset)
    assert stats["skus"] == 1
    assert stats["values"] == 3
    assert stats["verified"] == 3
    assert stats["auto_accepted"] == 2
    assert stats["queued"] == 1


# ===================================================================== overlay


@pytest.fixture
def session(registry, parsed_datasheet):
    record = product()
    made = decisions_for(record)
    return build_session(record, parsed_datasheet, registry, made, SCORES, policy())


def test_overlay_is_a_no_op_before_anyone_reviews(bundle, session):
    assert overlay_review_decisions(bundle, session) is bundle


def test_overlay_applies_an_acceptance(bundle, session):
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    merged = overlay_review_decisions(bundle, session)

    handle = next(v for v in merged["values"] if v["attribute_code"] == "handle_type")
    assert handle["status"] == "human_approved"
    assert handle["is_publishable"] is True
    assert handle["review_reason"] == "human_accept"


def test_overlay_applies_a_correction_to_the_value_itself(bundle, session):
    record_decision(
        session,
        "handle_type",
        CORRECT,
        reviewer="ana",
        priors=Priors(),
        corrected_value="Locking Lever",
    )
    merged = overlay_review_decisions(bundle, session)

    handle = next(v for v in merged["values"] if v["attribute_code"] == "handle_type")
    assert handle["value_display"] == "Locking Lever"
    assert handle["method"] == "human_correction"
    assert handle["review_reason"] == "human_correct"


def test_overlay_does_not_mutate_the_pipeline_record(bundle, session):
    """The invariant this whole module rests on.

    Accuracy metrics are computed against what the *machine* produced. If a reviewer's accept
    edited that in place, the extractor would appear to have been right all along and the
    measurement would be worthless.
    """
    before = json.dumps(bundle, sort_keys=True)

    record_decision(
        session,
        "handle_type",
        CORRECT,
        reviewer="ana",
        priors=Priors(),
        corrected_value="Locking Lever",
    )
    merged = overlay_review_decisions(bundle, session)

    assert json.dumps(bundle, sort_keys=True) == before, "the input bundle was mutated"
    assert merged is not bundle

    original = next(v for v in bundle["values"] if v["attribute_code"] == "handle_type")
    assert original["value_display"] == "Lever", "the model's answer must survive the correction"
    assert original["status"] == "queued_for_review"


def test_overlay_recomputes_the_publication_counts(bundle, session):
    assert bundle["metrics"]["values_needing_review"] == 1

    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    merged = overlay_review_decisions(bundle, session)

    assert merged["metrics"]["values_needing_review"] == 0
    assert merged["metrics"]["values_publishable"] == 3
    assert merged["metrics"]["values_reviewed"] == 1


def test_overlay_carries_the_audit_trail(bundle, session):
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    merged = overlay_review_decisions(bundle, session)

    assert len(merged["decisions"]) == 1
    assert merged["decisions"][0]["reviewer"] == "ana"


def test_overlay_leaves_undecided_values_alone(bundle, session):
    record_decision(session, "handle_type", ACCEPT, reviewer="ana", priors=Priors())
    merged = overlay_review_decisions(bundle, session)

    pressure = next(v for v in merged["values"] if v["attribute_code"] == "pressure_rating_wog")
    assert pressure["status"] == "auto_accepted"
    assert "review_reason" not in pressure


def test_a_rejection_makes_a_value_unpublishable(bundle, session):
    from axiom.review import REJECT

    record_decision(session, "pressure_rating_wog", REJECT, reviewer="ana", priors=Priors())
    merged = overlay_review_decisions(bundle, session)

    pressure = next(v for v in merged["values"] if v["attribute_code"] == "pressure_rating_wog")
    assert pressure["status"] == "rejected"
    assert pressure["is_publishable"] is False


# ===================================================================== HTTP surface


@pytest.fixture
def client(bundle, session, tmp_path, monkeypatch, registry, parsed_datasheet):
    from fastapi.testclient import TestClient

    from apps.api import main

    console_dir = tmp_path / "console"
    sessions = tmp_path / "sessions"
    console_dir.mkdir()
    sessions.mkdir()

    (console_dir / "BA-100-075.bundle.json").write_text(
        json.dumps(
            {
                "bundle": bundle,
                "document": serialise_document(
                    StubArtifact(document=source_document()), parsed_datasheet
                ),
                "pages": serialise_pages(parsed_datasheet),
                "class_definition": serialise_class(registry, CLASS_CODE),
                "policy": policy().summary(),
                "calibrator": "untrained-heuristic",
            },
            default=str,
        ),
        encoding="utf-8",
    )
    session.save(sessions / "BA-100-075.json")

    monkeypatch.setattr(main, "CONSOLE_DIR", console_dir)
    monkeypatch.setattr(main, "SESSION_DIR", sessions)
    monkeypatch.setattr(main, "CALIBRATION_DIR", tmp_path / "calibration")
    return TestClient(main.app)


def test_dataset_endpoint_assembles_from_disk(client):
    payload = client.get("/api/console/dataset").json()
    assert [b["sku"] for b in payload["skus"]] == ["BA-100-075"]
    assert "ba100@9f2c0000" in payload["documents"]
    assert CLASS_CODE in payload["class_definitions"]
    assert payload["meta"]["live"] is True
    assert payload["policy"]["threshold"] == 0.7


def test_dataset_endpoint_reports_no_warnings_when_healthy(client):
    assert client.get("/api/console/dataset").json()["meta"]["warnings"] == []


def test_stats_endpoint_matches_the_dataset(client):
    stats = client.get("/api/console/stats").json()
    assert stats["skus"] == 1
    assert stats["values"] == 3


def test_health_reports_the_bundle_count(client):
    assert client.get("/api/health").json()["bundles"] == 1


def test_dataset_endpoint_is_honest_when_there_is_nothing_to_show(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api import main

    monkeypatch.setattr(main, "CONSOLE_DIR", tmp_path / "nope")
    payload = TestClient(main.app).get("/api/console/dataset").json()

    assert payload["skus"] == []
    assert payload["meta"]["warnings"], "an empty dashboard must say why it is empty"
    assert "run_pipeline" in payload["meta"]["notes"]


def test_one_corrupt_bundle_does_not_blank_the_console(client, tmp_path):
    """A dashboard that renders nothing because of one bad file is worse than a partial one."""
    (tmp_path / "console" / "BROKEN.bundle.json").write_text("{not json", encoding="utf-8")

    payload = client.get("/api/console/dataset").json()
    assert [b["sku"] for b in payload["skus"]] == ["BA-100-075"], "the good bundle still loads"
    assert any("BROKEN" in w for w in payload["meta"]["warnings"])


def test_mismatched_thresholds_are_reported_rather_than_hidden(
    client, tmp_path, bundle, registry, parsed_datasheet
):
    """Two bundles decided at different budgets cannot share one threshold badge."""
    other = policy().summary()
    other["threshold"] = 0.42
    (tmp_path / "console" / "OTHER.bundle.json").write_text(
        json.dumps(
            {
                "bundle": {**bundle, "sku": "OTHER-SKU"},
                "document": serialise_document(
                    StubArtifact(document=source_document()), parsed_datasheet
                ),
                "pages": serialise_pages(parsed_datasheet),
                "class_definition": serialise_class(registry, CLASS_CODE),
                "policy": other,
                "calibrator": "untrained-heuristic",
            },
            default=str,
        ),
        encoding="utf-8",
    )

    warnings = client.get("/api/console/dataset").json()["meta"]["warnings"]
    assert any("threshold" in w for w in warnings)


def test_dataset_reflects_a_recorded_decision(client):
    """The join that makes the dashboards agree with the review queue."""
    before = client.get("/api/console/dataset").json()
    handle = next(
        v for v in before["skus"][0]["values"] if v["attribute_code"] == "handle_type"
    )
    assert handle["status"] == "queued_for_review"

    posted = client.post(
        "/api/session/BA-100-075/decision/handle_type",
        json={"action": "accept", "reviewer": "ana"},
    )
    assert posted.status_code == 200

    after = client.get("/api/console/dataset").json()
    handle = next(v for v in after["skus"][0]["values"] if v["attribute_code"] == "handle_type")
    assert handle["status"] == "human_approved"
    assert handle["review_reason"] == "human_accept"


# ===================================================== serving the source bytes
#
# The evidence viewer draws a highlight over the real page, so it needs the page. This is the only
# endpoint that returns supplier bytes rather than a derived projection, which makes it the only one
# where a path bug would leak something outside the project. It is addressed by content hash for
# exactly that reason: a 64-hex-character key cannot name a file elsewhere on disk.


def store_artifact(root, payload: bytes, suffix: str) -> str:
    """Write ``payload`` into a content-addressed store the way the ingest cache lays it out."""
    digest = hashlib.sha256(payload).hexdigest()
    directory = root / digest[:2] / digest[2:4]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{digest}{suffix}").write_bytes(payload)
    return digest


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """A tmp artifact store, plus a client pointed at it.

    Returns ``(client, store_root)`` so a test can add its own files. The real store is gitignored
    and may be empty, so nothing here may depend on the developer's machine.
    """
    from fastapi.testclient import TestClient

    from apps.api import main

    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setattr(main, "ARTIFACT_DIR", root)
    return TestClient(main.app), root


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def test_a_stored_pdf_is_served_for_rendering_not_download(artifacts):
    """`inline` is the point: a download prompt in the middle of a citation is not evidence."""
    client, root = artifacts
    digest = store_artifact(root, PDF_BYTES, ".pdf")

    response = client.get(f"/api/artifact/{digest}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "inline"


def test_served_bytes_are_not_reinterpretable_by_the_browser(artifacts):
    """These are third-party bytes. Content sniffing would let a supplier choose their own type."""
    client, root = artifacts
    digest = store_artifact(root, PDF_BYTES, ".pdf")

    headers = client.get(f"/api/artifact/{digest}").headers
    assert headers["x-content-type-options"] == "nosniff"
    # Safe to the letter: the address *is* the content hash, so these bytes can never change.
    assert "immutable" in headers["cache-control"]


def test_the_bytes_served_hash_back_to_the_key_they_were_asked_for(artifacts):
    """The invariant that makes this endpoint an evidence source rather than a file server.

    If the response did not hash to the requested key, the viewer would be drawing a citation over a
    document the certificate never described.
    """
    client, root = artifacts
    digest = store_artifact(root, PDF_BYTES, ".pdf")

    served = client.get(f"/api/artifact/{digest}").content
    assert hashlib.sha256(served).hexdigest() == digest


def test_a_stored_text_source_is_served_as_text(artifacts):
    """Most of this corpus is text, not PDF.

    The viewer does not render these — there is no bitmap to render, so it shows the parser's
    reconstruction instead. The endpoint still has to serve them correctly, because the bytes are
    what a reviewer checks the reconstruction against.
    """
    client, root = artifacts
    digest = store_artifact(root, b"APOLLO VALVE 77C SERIES\n", ".txt")

    response = client.get(f"/api/artifact/{digest}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_a_well_formed_hash_with_nothing_behind_it_explains_the_empty_store(artifacts):
    """The state every fresh clone is in: bundles are committed, the cached bytes are not."""
    client, _ = artifacts

    response = client.get(f"/api/artifact/{'a' * 64}")

    assert response.status_code == 404
    assert "gitignored" in response.json()["detail"], "a 404 here has a specific, fixable cause"


@pytest.mark.parametrize(
    "artifact_id",
    [
        "A" * 64,  # uppercase hex: two URLs for one artifact breaks content addressing
        "a" * 63,  # too short
        "a" * 65,  # too long
        "a" * 64 + ".pdf",  # caller trying to choose the suffix
        "a" * 63 + "g",  # hex-shaped but not hex
        "....secrets",  # dots, without a separator to make them useful
    ],
)
def test_anything_that_is_not_a_bare_lowercase_hash_is_refused(artifacts, artifact_id):
    """Validation, not sanitisation.

    Sanitising a caller-supplied path is a game you can lose. Requiring 64 lowercase hex characters
    is one you cannot: the accepted alphabet contains no separator and no dot.
    """
    client, _ = artifacts

    response = client.get(f"/api/artifact/{artifact_id}")
    assert response.status_code == 400
    assert "SHA-256" in response.json()["detail"]


def test_a_traversal_attempt_never_returns_bytes(artifacts, tmp_path):
    """A file sitting next to the store must stay unreachable, by whichever encoding.

    Payloads carrying a separator do not reach the handler at all — the router decodes the path
    first, so they match no route and 404. The distinction matters when reading this test: a 400 is
    the endpoint refusing, a 404 is the router never dispatching. Both are correct; neither returns
    bytes, which is the property being asserted.
    """
    client, _ = artifacts
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 not yours")

    attempts = (
        "../secret.pdf",
        "..%2Fsecret.pdf",
        "..%2f..%2fetc%2fpasswd",
        "....//....//secret.pdf",
        f"{'a' * 62}/../../secret.pdf",
        f"{'a' * 64}/../../../../secret.pdf",
    )
    for attempt in attempts:
        response = client.get(f"/api/artifact/{attempt}")
        assert response.status_code in (400, 404), attempt
        assert b"not yours" not in response.content, attempt


def test_a_half_written_file_is_not_served_under_a_completed_hash(artifacts):
    """`.partial` is a write that crashed. Its hash claims complete content it does not have, and a
    truncated PDF would fail to render with no explanation of why."""
    client, root = artifacts
    digest = hashlib.sha256(PDF_BYTES).hexdigest()
    directory = root / digest[:2] / digest[2:4]
    directory.mkdir(parents=True)
    (directory / f"{digest}.partial").write_bytes(PDF_BYTES[:20])

    response = client.get(f"/api/artifact/{digest}")
    assert response.status_code == 404, "an incomplete artifact is a missing artifact"


@pytest.mark.parametrize("suffix", [".svg", ".exe", ".docx", ".zip"])
def test_a_type_the_pipeline_cannot_ingest_is_refused_rather_than_guessed(artifacts, suffix):
    """The allowlist covers what the pipeline ingests, and nothing else.

    `.svg` is the one that shows why this is an allowlist and not a `mimetypes.guess_type` call: it
    is a plausible thing to find in an image store, and browsers execute script inside it.
    """
    client, root = artifacts
    digest = store_artifact(root, b"<svg onload=alert(1)>", suffix)

    response = client.get(f"/api/artifact/{digest}")
    assert response.status_code == 415
    assert suffix in response.json()["detail"]


def test_supplier_html_is_served_as_text_so_it_cannot_run(artifacts):
    """`inline` plus `text/html` would be script execution on this API's origin, and `nosniff` does
    not help when the declared type is the dangerous one.

    Nothing needs it rendered — the console draws the *parsed* projection of a page, and the reason
    to return the original file is so a human can read it.
    """
    client, root = artifacts
    digest = store_artifact(root, b"<script>alert(1)</script>", ".html")

    response = client.get(f"/api/artifact/{digest}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "html" not in response.headers["content-type"]


def test_the_completed_file_wins_when_a_partial_sits_beside_it(artifacts):
    """Re-ingesting after a crash leaves both on disk; the good one has to be the one served."""
    client, root = artifacts
    digest = store_artifact(root, PDF_BYTES, ".pdf")
    (root / digest[:2] / digest[2:4] / f"{digest}.partial").write_bytes(b"trunc")

    response = client.get(f"/api/artifact/{digest}")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == digest


# ===================================================== the L4 overlay
#
# Cross-source findings reach the console the same way review decisions do: as a separate artifact
# joined at read time, never by rewriting the bundle. The bundle is the immutable record of what a
# *single-source* run produced, and folding a later multi-source analysis into it would destroy the
# ability to ask what that run said on its own.


def cross_source_payload(**overrides) -> dict:
    payload = {
        "sku": "BA-100-075",
        "generated_at": "2026-08-06T00:00:00+00:00",
        "dry_run": False,
        "sources": [
            {
                "source": "data/samples/ba100.txt",
                "document_id": "ba100",
                "revision_label": "Rev C 2024-08",
                "revision_method": "letter_and_date",
                "supplier_id": "milwaukee",
                "values": 5,
                "parser": "text",
            },
            {
                "source": "data/samples/ba100-catalog.txt",
                "document_id": "catalog",
                "revision_label": "Rev A 2022-03",
                "revision_method": "letter_and_date",
                "supplier_id": "acme",
                "values": 6,
                "parser": "text",
            },
        ],
        "report": {
            "applicable": True,
            "documents": 2,
            "corroborated": 1,
            "disagreements": 1,
            "unresolved": 0,
            "single_source": 1,
            "passed": True,
            "corroborated_attributes": ["body_material"],
            "single_source_attributes": ["country_of_origin"],
            "conflicts": [
                {
                    "attribute_code": "pressure_rating_wog",
                    "resolved": True,
                    "reason": "ba100 is the newer revision (Rev C 2024-08)",
                    "winner": "'600 psi' (ba100 Rev C 2024-08)",
                    "observations": [
                        {
                            "document_id": "ba100",
                            "revision_label": "Rev C 2024-08",
                            "supplier_id": "milwaukee",
                            "value_display": "600 psi",
                        },
                        {
                            "document_id": "catalog",
                            "revision_label": "Rev A 2022-03",
                            "supplier_id": "acme",
                            "value_display": "400 psi",
                        },
                    ],
                }
            ],
        },
    }
    payload.update(overrides)
    return payload


def bundle_with_values(*codes: str) -> dict:
    return {
        "sku": "BA-100-075",
        "values": [{"attribute_code": code, "value_raw": "x"} for code in codes],
    }


def test_the_report_is_attached_to_the_bundle():
    bundle = overlay_cross_source(bundle_with_values("body_material"), cross_source_payload())

    assert bundle["cross_source"]["documents"] == 2
    assert bundle["cross_source"]["corroborated"] == 1
    assert len(bundle["cross_source"]["sources"]) == 2


def test_each_value_gets_its_own_verdict():
    """The level a reviewer acts at. A record-level summary cannot tell them which attribute to
    look at."""
    bundle = overlay_cross_source(
        bundle_with_values("body_material", "pressure_rating_wog", "country_of_origin"),
        cross_source_payload(),
    )
    states = {v["attribute_code"]: v["cross_source"]["state"] for v in bundle["values"]}

    assert states == {
        "body_material": "corroborated",
        "pressure_rating_wog": "superseded",
        "country_of_origin": "single_source",
    }


def test_an_unresolved_conflict_is_marked_differently_from_a_resolved_one():
    """They look similar in a summary and mean opposite things: one is stale data, the other is
    two contradictory answers the system refused to choose between."""
    payload = cross_source_payload()
    payload["report"]["conflicts"][0]["resolved"] = False
    payload["report"]["unresolved"] = 1
    payload["report"]["passed"] = False

    bundle = overlay_cross_source(bundle_with_values("pressure_rating_wog"), payload)

    assert bundle["values"][0]["cross_source"]["state"] == "conflict"
    assert bundle["cross_source"]["passed"] is False


def test_a_value_no_source_spoke_to_is_left_unmarked():
    """Absence of a verdict is not a verdict. Marking every value would imply L4 had an opinion
    about attributes it never saw."""
    bundle = overlay_cross_source(bundle_with_values("handle_type"), cross_source_payload())

    assert bundle["values"][0].get("cross_source") is None


def test_the_dry_run_flag_survives_the_join():
    """Findings from scripted responses must not be presentable as a measurement, so the console
    needs to be able to say so."""
    bundle = overlay_cross_source(
        bundle_with_values("body_material"), cross_source_payload(dry_run=True)
    )

    assert bundle["cross_source"]["dry_run"] is True


def test_a_payload_with_no_report_leaves_the_bundle_alone():
    """A malformed artifact must not blank the workspace."""
    original = bundle_with_values("body_material")
    assert overlay_cross_source(original, {"sku": "BA-100-075"}) == original


def test_the_overlay_does_not_mutate_the_bundle_it_was_given():
    """The bundle is read from disk and may be served to other callers in the same request."""
    original = bundle_with_values("body_material")
    overlay_cross_source(original, cross_source_payload())

    assert "cross_source" not in original
    assert original["values"][0].get("cross_source") is None


# ---------------------------------------------------------------- equivalence overlay
#
# The cross-reference joins the same way L4 does, with one deliberate difference: it attaches a
# record-level block and *no* per-value verdict. An equivalence finding is about a pair of
# products, and `pressure_rating_wog` is not in conflict on this record because some other part
# rates lower — hanging that on the attribute row would read as a defect in the reviewer's own data.


def equivalence_payload(**overrides) -> dict:
    payload = {
        "generated_at": "2026-08-07T00:00:00+00:00",
        "report": {
            "reference_sku": "BA-100-075",
            "source": "golden",
            "measured": False,
            "source_note": "Records are read from the hand-authored golden set.",
            "candidates": 2,
            "substitutable": 1,
            "indeterminate": 0,
            "by_verdict": {
                "identical": 0,
                "drop_in": 1,
                "functional_equivalent": 0,
                "not_equivalent": 1,
                "indeterminate": 0,
            },
            "best_substitute": "77C-104",
            "best_verdict": "drop_in",
            "cross_brand_substitutes": 1,
            "records": 15,
            "skus": ["BA-100-075", "77C-104"],
            "failures": [],
            "candidates_detail": [],
        },
    }
    payload.update(overrides)
    return payload


def test_the_cross_reference_is_attached_to_the_bundle():
    bundle = overlay_equivalence(bundle_with_values("body_material"), equivalence_payload())

    assert bundle["equivalence"]["best_substitute"] == "77C-104"
    assert bundle["equivalence"]["records"] == 15
    assert bundle["equivalence"]["generated_at"] == "2026-08-07T00:00:00+00:00"


def test_the_catalogue_provenance_survives_the_join():
    """`measured` is the field a reader has to see before quoting a verdict. False means the
    records compared were hand-authored, so the comparison logic was exercised and the extraction
    was not."""
    bundle = overlay_equivalence(bundle_with_values("body_material"), equivalence_payload())

    assert bundle["equivalence"]["measured"] is False
    assert bundle["equivalence"]["source"] == "golden"


def test_no_per_value_verdict_is_attached():
    """Deliberately unlike the L4 overlay. An equivalence finding is a statement about a pair of
    products, not about an attribute of this one."""
    bundle = overlay_equivalence(
        bundle_with_values("pressure_rating_wog", "body_material"), equivalence_payload()
    )

    for value in bundle["values"]:
        assert "equivalence" not in value


def test_a_malformed_equivalence_payload_is_a_no_op():
    original = bundle_with_values("body_material")
    assert overlay_equivalence(original, {"sku": "BA-100-075"}) == original
    assert overlay_equivalence(original, {"report": "not an object"}) == original


def test_the_equivalence_overlay_does_not_mutate_its_input():
    original = bundle_with_values("body_material")
    overlay_equivalence(original, equivalence_payload())

    assert "equivalence" not in original
