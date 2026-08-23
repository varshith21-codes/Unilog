"""Tests for ``axiom.pipeline``: the extracted stage runner, and single-SKU enrichment.

The suite that mattered most for the extraction in Task 1 is the pre-existing one — ``run_pipeline``
had to keep producing what it produced, and no test file was touched to make that true. What these
tests add is coverage of the things the CLI never exercised, because the CLI always had a source
document on disk and always had a fallback class.

Three properties carry the weight here.

**A document supersedes a description, and the description survives in history.** That ordering is
the whole reason the description pass is a hook inside :func:`run_stages` rather than something a
caller does before or after. If it inverted, a supplier's own abbreviation would beat the
manufacturer's published figure, and nobody would notice until a customer did.

**A citation must point at text that actually contains the quote.** A description-derived value in a
run whose primary source is a fetched datasheet cites the *submission*, not the datasheet,
because the description is not in the datasheet. Getting this wrong produces the exact failure
this system exists to prevent: a verifiable-looking citation that does not verify.

**A submission with nothing to read is refused before it costs anything.** Two model calls to
produce an identity-only row is worse than a clear refusal, and the refusal names the fields.
"""

from __future__ import annotations

import json

import pytest
from axiom.core.values import DerivationMethod, ValueStatus
from axiom.delivery import load_default as load_delivery_format
from axiom.extract import ModelCascade, StubModelClient
from axiom.ingest import LocalArtifactStore
from axiom.ingest.web import FetchedResource, UrlFetchError
from axiom.pipeline import (
    EnrichmentRequest,
    InsufficientInputError,
    build_delivery,
    enrich_one,
    persist_run,
    resolve_source,
    run_stages,
    submission_text,
)
from axiom.schema import load_default as load_schema

VALVE_CLASS = "PLB.VLV.BALL.2PC"
SKU = "BA-100-075"

# A dishwasher, because the description path needs a class whose abbreviations table has entries and
# whose attributes are readable from a short ERP string. Taken from the client's own sample file.
DISHWASHER_MPN = "PDSH4816AF"
DISHWASHER_DESC = "24IN BUILT IN DISHWASHER STAINLESS STEEL 47DBA"


@pytest.fixture(scope="module")
def registry():
    return load_schema()


@pytest.fixture(scope="module")
def fmt():
    return load_delivery_format()


@pytest.fixture(scope="module")
def cascade():
    return ModelCascade.load()


@pytest.fixture
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def calibration(tmp_path):
    """An empty calibration directory: the cold start.

    Deliberately empty rather than seeded. With no calibrator, no priors and no calibration set the
    policy is unachievable and every value queues, which is both the correct opening state and
    exactly the state a brand-new SKU arrives in. A test that seeded a trained calibrator would be
    testing a situation this feature does not have on day one.
    """
    directory = tmp_path / "calibration"
    directory.mkdir()
    return directory


def contract_item(code: str, **kwargs) -> dict:
    """One entry of the extraction contract, in the shape the model is asked to return."""
    base = {
        "attribute_code": code,
        "found": True,
        "value_raw": None,
        "evidence_quote": None,
        "evidence_page": 1,
        "certainty": "high",
        "reason": None,
    }
    base.update(kwargs)
    return base


def stub(*payloads: str) -> StubModelClient:
    return StubModelClient(list(payloads))


# ===================================================================== run_stages


def test_run_stages_produces_a_signed_certificate(
    parsed_datasheet, source_document, registry, cascade, calibration, tmp_path
):
    """The shared function produces what the CLI reported: cited values under a signed certificate.

    The same fixture and the same stub the extraction tests use, driven through the function the CLI
    now calls, so a divergence between the two shows up here rather than in a terminal nobody is
    reading.
    """
    from axiom.ingest import ingest_bytes

    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_bytes(
        parsed_datasheet.full_text.encode("utf-8"), store, filename="ba100.txt"
    )

    # Two payloads, in the order the stages consume them. The valve datasheet is genuinely ambiguous
    # to retrieval — several valve classes score closely — so classification *does* call the model
    # here and eats the first response. Queueing only the extraction contract would feed it to the
    # classifier and leave extraction with an exhausted client, which is a confusing way to discover
    # that classification is a real model call.
    client = stub(
        json.dumps({"code": VALVE_CLASS, "confidence": 0.92, "rationale": "two-piece ball valve"}),
        json.dumps(
            [
                contract_item(
                    "body_material", value_raw="Bronze C84400", evidence_quote="Bronze C84400"
                ),
                contract_item(
                    "seat_material", value_raw="RPTFE", evidence_quote="Seat Material"
                ),
            ]
        ),
    )

    run = run_stages(
        parsed_datasheet,
        artifact,
        registry=registry,
        client=client,
        cascade=cascade,
        sku=SKU,
        class_code_fallback=VALVE_CLASS,
        calibration_dir=calibration,
    )

    assert run.class_code == VALVE_CLASS
    assert run.certificate.verify_signature() is True
    assert run.record.sku == SKU
    # Cost is reported for classification *and* extraction, or not at all.
    assert run.usage.calls >= 1
    assert "body_material" in {v.attribute_code for v in run.record.current_values()}


def test_every_value_queues_when_no_policy_is_validated(
    parsed_datasheet, registry, cascade, calibration, tmp_path
):
    """The cold start. No calibration set means no threshold could be validated, so nothing
    auto-accepts — and that is the correct opening state rather than a failure."""
    from axiom.ingest import ingest_bytes

    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_bytes(b"x", store, filename="x.txt")

    run = run_stages(
        parsed_datasheet,
        artifact,
        registry=registry,
        client=stub(
            json.dumps({"code": VALVE_CLASS, "confidence": 0.9}),
            json.dumps(
                [
                    contract_item(
                        "body_material",
                        value_raw="Bronze C84400",
                        evidence_quote="Bronze C84400",
                    )
                ]
            ),
        ),
        cascade=cascade,
        sku=SKU,
        class_code_fallback=VALVE_CLASS,
        calibration_dir=calibration,
    )

    assert run.record.current_values(), "nothing to decide about would make this vacuous"
    assert run.policy.achievable is False
    assert all(not decision.accepted for decision in run.decisions)


def test_unclassified_run_skips_extraction_rather_than_guessing(
    parsed_datasheet, registry, cascade, calibration, tmp_path
):
    """No class means no attribute list, so no extraction call is made at all.

    The CLI never reaches this branch because ``--class-code`` carries a default. A typed submission
    that matches no class does, and the honest answer is an identity-only record with an explanation
    rather than a model asked to read a document with nothing declared in front of it.
    """
    from axiom.ingest import ingest_bytes

    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_bytes(b"nothing recognisable here", store, filename="x.txt")
    from axiom.docintel import parse_text

    parsed = parse_text("qqqq zzzz wwww", artifact.document)

    client = stub()  # Exhausted on the first call, so any model call would raise.
    run = run_stages(
        parsed,
        artifact,
        registry=registry,
        client=client,
        cascade=cascade,
        sku="UNKNOWN-1",
        class_code_fallback=None,
        calibration_dir=calibration,
    )

    assert run.class_code is None
    assert run.extraction.values == []
    assert client.calls == []
    assert any("extraction was skipped" in note for note in run.notes)
    # Still a real certificate over a real (identity-only) record.
    assert run.certificate.verify_signature() is True


# ===================================================================== source resolution


def test_submission_text_is_stable_and_labelled():
    text = submission_text(
        mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
    )
    assert text.splitlines() == [
        f"Manufacturer Part Number: {DISHWASHER_MPN}",
        "Manufacturer: Frigidaire",
        f"Description: {DISHWASHER_DESC}",
    ]
    assert text.endswith("\n")


def test_submission_becomes_a_hashed_citable_document(store):
    resolved = resolve_source(
        store, mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
    )

    assert resolved.from_url is False
    assert resolved.kind == "submission"
    # The URI is the provenance claim, and it says what it is.
    assert resolved.artifact.document.uri == f"submission:{DISHWASHER_MPN}"
    # The description is locatable, which is what makes a citation into it verifiable.
    assert DISHWASHER_DESC in resolved.parsed.full_text
    assert len(resolved.artifact.document.sha256) == 64


def test_submission_hash_changes_when_any_field_changes(store):
    base = resolve_source(store, mpn=DISHWASHER_MPN, description="A")
    same = resolve_source(store, mpn=DISHWASHER_MPN, description="A")
    changed_description = resolve_source(store, mpn=DISHWASHER_MPN, description="B")
    changed_manufacturer = resolve_source(
        store, mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description="A"
    )

    # Identical facts are the same document, which is the point of content addressing: resubmitting
    # a product reuses its artifact and its citations rather than forking them.
    assert same.artifact.sha256 == base.artifact.sha256
    assert changed_description.artifact.sha256 != base.artifact.sha256
    assert changed_manufacturer.artifact.sha256 != base.artifact.sha256


def test_submission_is_not_typed_as_a_spec_sheet(store):
    """A supplier feed, not a spec sheet. Magic-byte sniffing would call plain text a spec sheet,
    which is the overclaim the whole submission path exists to avoid."""
    resolved = resolve_source(store, mpn=DISHWASHER_MPN, description=DISHWASHER_DESC)
    assert resolved.artifact.document.doc_type.value == "supplier_feed"
    assert "not a manufacturer publication" in (resolved.artifact.document.license_note or "")


def test_url_source_is_fetched_and_marked_as_a_document(store):
    def fetch(url, *, timeout, max_bytes):
        return FetchedResource(
            data=b"BA-100-075\nBody Material .... Bronze C84400\n",
            url=url,
            content_type="text/plain",
        )

    resolved = resolve_source(
        store, mpn=SKU, source_url="https://example.com/docs/ba100.txt", fetcher=fetch
    )

    assert resolved.from_url is True
    assert resolved.kind == "document"
    assert resolved.artifact.document.uri == "https://example.com/docs/ba100.txt"
    assert "Bronze C84400" in resolved.parsed.full_text


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/ba100.pdf",
        "https://localhost/ba100.pdf",
        "https://127.0.0.1/ba100.pdf",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/internal.pdf",
        "file:///etc/passwd",
    ],
)
def test_url_source_refuses_the_obvious_attempts(store, url):
    """Plain http, loopback, link-local and private literals, and non-fetchable schemes.

    ``169.254.169.254`` is in the list by name because it is the cloud metadata endpoint, which is
    what an SSRF against a service that fetches caller-supplied URLs is usually aiming at.
    """
    with pytest.raises(UrlFetchError):
        resolve_source(store, mpn=SKU, source_url=url, fetcher=lambda *a, **k: None)


def test_url_source_caps_bytes_below_the_ingest_default(store):
    """The ceiling this path passes is its own, not the library's 32 MiB.

    The library default is sized for a supplier sending a catalogue. This is one document named by
    someone typing into an unauthenticated form, so the ceiling that matters is the one bounding
    what a caller can make the process buffer.
    """
    seen = {}

    def fetch(url, *, timeout, max_bytes):
        seen["max_bytes"] = max_bytes
        seen["timeout"] = timeout
        return FetchedResource(data=b"BA-100-075 x", url=url, content_type="text/plain")

    resolve_source(store, mpn=SKU, source_url="https://example.com/x.txt", fetcher=fetch)

    from axiom.ingest import DEFAULT_MAX_BYTES
    from axiom.pipeline.source import SUBMISSION_MAX_BYTES, SUBMISSION_TIMEOUT

    assert seen["max_bytes"] == SUBMISSION_MAX_BYTES < DEFAULT_MAX_BYTES
    assert seen["timeout"] == SUBMISSION_TIMEOUT


# ===================================================================== enrich_one


def test_refuses_a_submission_with_nothing_to_read_and_nowhere_to_look():
    """A part number identifies a product but does not describe one.

    Only a refusal with retrieval **off**, which is the honest condition: with nowhere to look and
    nothing to read, the run would spend two model calls to return an identity-only row. With
    retrieval on the manufacturer name is itself a lead, so refusing would be refusing the thing the
    form is for.

    The exception names the fields rather than only explaining itself, because the caller that can
    act on this is a form.
    """
    with pytest.raises(InsufficientInputError) as exc:
        EnrichmentRequest(mpn=DISHWASHER_MPN, manufacturer="Frigidaire", retrieve=False)

    assert exc.value.missing == ("description", "source_url")
    assert "does not describe one" in str(exc.value)


def test_a_part_number_and_a_manufacturer_are_accepted_when_retrieval_is_on():
    """The change retrieval bought: this is a legitimate submission, not an incomplete one."""
    request = EnrichmentRequest(mpn=DISHWASHER_MPN, manufacturer="Frigidaire")
    assert request.retrieve is True
    assert request.has_content is False


def test_refuses_an_empty_part_number():
    with pytest.raises(InsufficientInputError) as exc:
        EnrichmentRequest(mpn="   ", description=DISHWASHER_DESC)
    assert exc.value.missing == ("mpn",)


def test_description_only_run_classifies_and_cites(registry, store, calibration):
    """The cheapest useful submission: a part number, a manufacturer, and an ERP description."""
    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )

    assert result.run.class_code is not None
    assert result.from_description > 0
    assert result.source.from_url is False

    values = result.record.current_values()
    assert values, "the description should evidence at least one attribute"
    for value in values:
        span = value.evidence[0]
        # Every description-derived value cites the submission, and the quote is a real substring of
        # it — verification here is a substring check, not a fuzzy match.
        assert span.document_id == result.submission.artifact.document.document_id
        assert span.quote in result.source.parsed.full_text
        assert span.quote_verified is True


def test_description_values_are_candidates_not_auto_accepted(registry, store, calibration):
    """The offline delivery run publishes these on their own provenance because it has no policy.
    This path has one, so the policy decides — which is what ``accept=False`` is documented for."""
    result = enrich_one(
        EnrichmentRequest(mpn=DISHWASHER_MPN, description=DISHWASHER_DESC),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )

    seeded = {v.attribute_code for v in result.run.seeded}
    assert seeded
    for value in result.record.current_values():
        if value.attribute_code in seeded:
            assert value.status is not ValueStatus.AUTO_ACCEPTED


def test_document_value_supersedes_the_description_and_history_is_kept(
    registry, store, calibration
):
    """The ordering ``axiom.delivery.batch`` documents as load-bearing, enforced here.

    A datasheet *states* a fact where a description only implies it. So the document value wins, and
    because ``add_value`` supersedes rather than appends, the description's reading stays in the
    record's history rather than colliding with it or vanishing.
    """
    # A datasheet that states a different sound level from the one the description abbreviates.
    datasheet = (
        f"FRIGIDAIRE {DISHWASHER_MPN}\n"
        "Built-In Dishwasher\n"
        "  Sound Level .................... 52 dBA\n"
    )

    def fetch(url, *, timeout, max_bytes):
        return FetchedResource(data=datasheet.encode("utf-8"), url=url, content_type="text/plain")

    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN,
            manufacturer="Frigidaire",
            description=DISHWASHER_DESC,
            source_url="https://example.com/pdsh4816af.txt",
        ),
        registry=registry,
        client=stub(
            json.dumps(
                [contract_item("sound_level", value_raw="52 dBA", evidence_quote="52 dBA")]
            )
        ),
        store=store,
        calibration_dir=calibration,
        fetcher=fetch,
    )

    assert result.source.from_url is True
    # The description cited the submission; the datasheet is a separate document, and both are on
    # the record. A description value citing the datasheet would be a false citation.
    assert result.submission.artifact.sha256 != result.source.artifact.sha256
    assert result.submission.artifact.document.document_id in result.record.source_document_ids

    history = [v for v in result.record.attribute_values if v.attribute_code == "sound_level"]
    assert len(history) == 2, (
        "the description reads 47DBA and the datasheet states 52 dBA, so both should be on the "
        f"record: got {[(v.method.value, v.value_raw) for v in history]}"
    )

    winner = result.record.get("sound_level")
    assert winner is not None
    # The document reading is the one standing. A datasheet states a fact where a description only
    # implies it, so the ordering must not invert.
    assert winner.method is DerivationMethod.DOCUMENT_EXTRACTION
    assert "52" in str(winner.value_raw)

    # And the description's reading is retained behind it rather than discarded, which is what makes
    # `add_value`'s supersede-not-append behaviour load-bearing here.
    superseded = [v for v in history if v is not winner]
    assert len(superseded) == 1
    assert superseded[0].method is DerivationMethod.ITEM_MASTER_PARSE
    assert superseded[0].status is ValueStatus.SUPERSEDED
    # This is the pairing the provenance split exists to describe: the description *suggested*
    # 47DBA and the datasheet *established* 52 dBA. The suggestion is self-declared and was never
    # publishable; the datasheet reading is what the SKU is credited for.
    assert superseded[0].method.is_self_declared
    assert winner.method.is_independent
    assert superseded[0].evidence[0].document_id == (
        result.submission.artifact.document.document_id
    )


def test_a_distributor_manufacturer_is_flagged_rather_than_published(
    registry, store, calibration
):
    """``Part_Manuf`` is a vendor field and it is mixed. Writing a buying co-op into
    MANUFACTURER_NAME would be confidently wrong, so the resolution is reported with a flag."""
    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN,
            manufacturer="Appliance Dealers Cooperative (APPDE)",
            description=DISHWASHER_DESC,
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )

    assert result.manufacturer.looks_like_a_distributor is True
    assert result.manufacturer.publishable_as_manufacturer is False
    # The supplier code is kept: it is the stable key a mapping should be remembered against.
    assert result.manufacturer.supplier_code == "APPDE"
    assert any("distributor" in note for note in result.run.notes)


def test_the_run_says_when_the_submission_is_its_own_source(registry, store, calibration):
    """Rendered next to every value, because it is the difference between "the manufacturer says
    this" and "the person who submitted it says this"."""
    result = enrich_one(
        EnrichmentRequest(mpn=DISHWASHER_MPN, description=DISHWASHER_DESC),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )

    assert any("the submission itself is the source" in n for n in result.run.notes)
    assert any("weaker" in note for note in result.run.notes)
    weight = result.source.summary()["evidential_weight"]
    assert "rather than what a manufacturer published" in weight
    assert result.source.summary()["kind"] == "submission"


# ===================================================================== retrieval


def test_a_part_number_and_a_manufacturer_reach_a_retrieved_document(
    registry, store, calibration, tmp_path
):
    """The claim the form makes: type two fields, get cited data off the manufacturer's own site.

    Driven over a fixture site through an injected fetcher, so it is offline and deterministic — the
    same approach ``tests/test_minimal_input.py`` takes, and for the same reason: a test that needs
    the internet to check this is a test that gets skipped, and then this path rots.
    """
    from axiom.ingest.web import FetchedResource, UrlFetchError
    from axiom.retrieve import SourcePolicy

    policy_yaml = """
version: 1
policy:
  min_seconds_between_requests: 0
  respect_robots_txt: false
excluded:
  - category: marketplace
    reason: Third-party sellers write their own listings.
    domains: [amazon.com]
spec_hints:
  paths: [/literature, /product]
  suffixes: [.pdf]
  negative_paths: [/cart]
manufacturers:
  - id: milwaukee_valve
    name: Milwaukee Valve
    domains: [milwaukeevalve.example]
    vendor_codes: [MILVA]
    vendors: ["milwaukee valve"]
"""
    site = {
        "https://milwaukeevalve.example/": (
            b'<html><body><form action="/search" method="get">'
            b'<input type="search" name="q"></form></body></html>',
            "text/html",
        ),
        f"https://milwaukeevalve.example/search?q={SKU}": (
            b'<html><body>'
            b'<a href="https://www.amazon.com/dp/B01">BA-100-075 on Amazon</a>'
            b'<a href="/product/ba-100-075">BA-100-075 Bronze Ball Valve</a>'
            b"</body></html>",
            "text/html",
        ),
        "https://milwaukeevalve.example/product/ba-100-075": (
            b'<html><body><h1>BA-100-075</h1>'
            b'<a href="/literature/ba-100.pdf">BA-100 Specification Sheet</a>'
            b"</body></html>",
            "text/html",
        ),
        "https://milwaukeevalve.example/literature/ba-100.pdf": (
            b"MILWAUKEE VALVE - BA-100 SERIES\n"
            b"Two-Piece Full Port Bronze Ball Valve\n"
            b"SPECIFICATIONS\n"
            b"  Body Material .................. Bronze C84400\n"
            b"  Seat Material .................. RPTFE\n"
            b"ORDERING INFORMATION\n"
            b"  Part Number      Size        Handle\n"
            b"  BA-100-075       3/4\"        Lever\n",
            "text/plain",
        ),
    }
    fetched: list[str] = []

    def fetch(url, *, timeout, max_bytes):
        fetched.append(url)
        if url not in site:
            raise UrlFetchError(f"{url} returned HTTP 404 Not Found")
        data, content_type = site[url]
        return FetchedResource(data=data, url=url, content_type=content_type)

    policy_path = tmp_path / "sourcing.yaml"
    policy_path.write_text(policy_yaml, encoding="utf-8")

    # The whole input: a part number and a vendor code. No description, no URL.
    result = enrich_one(
        EnrichmentRequest(mpn=SKU, manufacturer="Milwaukee Valve (MILVA)"),
        registry=registry,
        client=stub(
            json.dumps({"code": VALVE_CLASS, "confidence": 0.92}),
            json.dumps(
                [
                    contract_item(
                        "body_material", value_raw="Bronze C84400", evidence_quote="Bronze C84400"
                    )
                ]
            ),
        ),
        store=store,
        calibration_dir=calibration,
        library_path=tmp_path / "index.json",
        source_policy=SourcePolicy.load(policy_path),
        fetcher=fetch,
    )

    retrieval = result.retrieval
    assert retrieval is not None
    assert retrieval.found, retrieval.notes
    assert retrieval.manufacturer is not None
    assert retrieval.manufacturer.primary_domain == "milwaukeevalve.example"

    # The datasheet is the source, not the typed fields.
    assert result.source.from_url is True
    assert result.source.kind == "document"
    assert "Bronze C84400" in result.source.parsed.full_text

    # The gate ran before the request, so the marketplace result never became bytes.
    assert not any("amazon" in url for url in fetched)
    assert "https://milwaukeevalve.example/literature/ba-100.pdf" in fetched

    # And the run read it: a value, cited, against the stored document.
    values = result.record.current_values()
    assert values
    body = next(v for v in values if v.attribute_code == "body_material")
    assert "Bronze" in body.evidence[0].quote
    assert body.evidence[0].quote_verified


def test_the_library_answers_without_a_request(registry, store, calibration, tmp_path):
    """Retrieval is a one-time cost per document, not per part.

    The fetcher raises on any call, so reaching the network fails the test rather than slowing it.
    """
    from axiom.ingest import ingest_bytes
    from axiom.retrieve import DocumentLibrary

    datasheet = (
        b"MILWAUKEE VALVE - BA-100 SERIES\n"
        b"  Body Material .................. Bronze C84400\n"
        b"  Part Number      Size\n"
        b"  BA-100-075       3/4\"\n"
    )
    artifact = ingest_bytes(datasheet, store, filename="ba100.txt")
    index = tmp_path / "index.json"
    library = DocumentLibrary.load(store, index)
    library.register(artifact, host="milwaukeevalve.example", tier="manufacturer")
    assert library.coverage_for(SKU), "the fixture must name the part number"
    library.save()

    def refuse(url, *, timeout, max_bytes):
        raise AssertionError(f"the library should have answered, but {url} was fetched")

    result = enrich_one(
        EnrichmentRequest(mpn=SKU, manufacturer="Milwaukee Valve (MILVA)"),
        registry=registry,
        client=stub(
            json.dumps({"code": VALVE_CLASS, "confidence": 0.9}), json.dumps([])
        ),
        store=store,
        calibration_dir=calibration,
        library_path=index,
        fetcher=refuse,
    )

    assert result.retrieval is not None
    assert result.retrieval.from_library is True
    assert result.retrieval.requests_made == 0
    assert result.source.artifact.sha256 == artifact.sha256


def test_an_undeclared_manufacturer_says_what_would_fix_it(
    registry, store, calibration, tmp_path
):
    """"Nothing found" is not actionable. "No domain is declared for this name" is, and the remedy
    is one entry in schema/sourcing.yaml."""
    result = enrich_one(
        EnrichmentRequest(mpn=DISHWASHER_MPN, manufacturer="Wholly Invented Corp"),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
        library_path=tmp_path / "index.json",
    )

    retrieval = result.retrieval
    assert retrieval is not None
    assert retrieval.found is False
    assert retrieval.manufacturer is None
    assert retrieval.requests_made == 0, "there was nowhere to send a request"
    assert any("schema/sourcing.yaml" in note for note in retrieval.notes)
    # And it falls back to the submission rather than failing the run.
    assert result.source.from_url is False


def test_retrieval_is_skipped_when_a_url_was_supplied(
    registry, store, calibration, tmp_path
):
    """Nothing beats being told. A supplied URL is arm 1, so there is nothing to search for."""

    def fetch(url, *, timeout, max_bytes):
        from axiom.ingest.web import FetchedResource

        return FetchedResource(
            data=b"BA-100-075\n  Body Material ... Bronze C84400\n",
            url=url,
            content_type="text/plain",
        )

    result = enrich_one(
        EnrichmentRequest(
            mpn=SKU,
            manufacturer="Milwaukee Valve (MILVA)",
            source_url="https://example.com/ba100.txt",
        ),
        registry=registry,
        client=stub(json.dumps({"code": VALVE_CLASS, "confidence": 0.9}), json.dumps([])),
        store=store,
        calibration_dir=calibration,
        library_path=tmp_path / "index.json",
        fetcher=fetch,
    )

    assert result.retrieval is None
    assert result.source.artifact.document.uri == "https://example.com/ba100.txt"


def test_retrieval_off_makes_the_run_offline(registry, store, calibration, tmp_path):
    """The deterministic mode. With retrieval off a description is required again, and nothing
    reaches the network."""
    with pytest.raises(InsufficientInputError):
        EnrichmentRequest(mpn=SKU, manufacturer="Milwaukee Valve (MILVA)", retrieve=False)

    def refuse(url, *, timeout, max_bytes):
        raise AssertionError(f"retrieval was off, but {url} was fetched")

    result = enrich_one(
        EnrichmentRequest(
            mpn=SKU,
            manufacturer="Milwaukee Valve (MILVA)",
            description="1/2IN BRONZE BALL VALVE",
            retrieve=False,
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
        library_path=tmp_path / "index.json",
        fetcher=refuse,
    )

    assert result.retrieval is None
    assert result.source.from_url is False


# ===================================================================== persistence


def test_a_part_number_with_a_separator_round_trips(registry, store, calibration, tmp_path):
    """``52C3-5/8-UPC`` is a fractional size, not an attack.

    Both filenames are slugged, so this writes at all — and it writes where ``_session_path`` and
    ``console_dataset`` will look for it.
    """
    from axiom.core.naming import sku_slug
    from axiom.review import ReviewSession

    result = enrich_one(
        EnrichmentRequest(mpn="52C3-5/8-UPC", manufacturer="Nibco", description="5/8IN COPPER"),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    paths = persist_run(
        result.run,
        parsed=result.source.parsed,
        artifact=result.source.artifact,
        registry=registry,
        sessions_dir=tmp_path / "sessions",
        console_dir=tmp_path / "console",
    )

    assert paths.slug == "52C3-5~2F8-UPC"
    assert paths.session.name == "52C3-5~2F8-UPC.json"
    assert paths.bundle.name == "52C3-5~2F8-UPC.bundle.json"
    assert paths.session.is_file() and paths.bundle.is_file()

    # The session reads back, and it carries the *true* part number rather than the slug.
    assert ReviewSession.load(paths.session).sku == "52C3-5/8-UPC"
    payload = json.loads(paths.bundle.read_text(encoding="utf-8"))
    assert payload["bundle"]["sku"] == "52C3-5/8-UPC"
    # Which is what lets the console find the session from the bundle.
    assert sku_slug(payload["bundle"]["sku"]) == paths.slug


def test_a_part_number_needing_no_escaping_is_its_own_slug(
    registry, store, calibration, tmp_path
):
    """The reason no migration is required: every artifact already on disk keeps resolving."""
    result = enrich_one(
        EnrichmentRequest(mpn=SKU, description="1/2IN BRONZE BALL VALVE"),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    paths = persist_run(
        result.run,
        parsed=result.source.parsed,
        artifact=result.source.artifact,
        registry=registry,
        sessions_dir=tmp_path / "sessions",
        console_dir=tmp_path / "console",
    )
    assert paths.session.name == f"{SKU}.json"
    assert paths.bundle.name == f"{SKU}.bundle.json"


# ===================================================================== delivery


def test_delivery_row_is_the_full_contract_width(registry, fmt, store, calibration, tmp_path):
    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "enrich",
        mpn=DISHWASHER_MPN,
        manufacturer="Frigidaire",
        description=DISHWASHER_DESC,
    )

    assert delivery.columns == len(fmt) == 252
    assert len(delivery.row.as_dict()) == 252
    # Sparse is correct rather than a gap: the client's own ground truth leaves 173 of 252 blank.
    assert 0 < delivery.populated < delivery.columns
    assert delivery.compliant is True


def test_typed_fields_echo_back_verbatim(registry, fmt, store, calibration, tmp_path):
    """The echo columns exist so the client can join our output back to their input."""
    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "enrich",
        mpn=DISHWASHER_MPN,
        manufacturer="Frigidaire",
        description=DISHWASHER_DESC,
    )

    cells = delivery.row.as_dict()
    assert cells["Mfg_Part_Num"] == DISHWASHER_MPN
    assert cells["Part_Desc"] == DISHWASHER_DESC
    assert cells["Part_Manuf"] == "Frigidaire"


def test_the_manufacturer_cell_records_that_a_caller_supplied_it(
    registry, fmt, store, calibration, tmp_path
):
    """A typed manufacturer is an assertion, and the sidecar says so rather than implying a document
    stated it."""
    result = enrich_one(
        EnrichmentRequest(
            mpn=DISHWASHER_MPN, manufacturer="Frigidaire", description=DISHWASHER_DESC
        ),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "enrich",
        mpn=DISHWASHER_MPN,
        manufacturer="Frigidaire",
        description=DISHWASHER_DESC,
    )

    sidecar = json.loads(delivery.provenance_path.read_text(encoding="utf-8"))
    cells = {c["column"]: c for c in sidecar["records"][0]["cells"]}

    # `caller`, not a document and not `Part_Manuf`. The distinction is the point: the name
    # was asserted by whoever submitted the form, and the sidecar is where an auditor reads that.
    assert cells["MANUFACTURER_NAME"]["source"] == "caller"
    assert delivery.row.as_dict()["MANUFACTURER_NAME"] == "Frigidaire"
    # And the input echo is passthrough: the only claim it makes is "you sent us this".
    assert cells["Part_Manuf"]["provenance"] == "passthrough"
    # Nothing extracted was invented into the manufacturer column.
    assert cells["MANUFACTURER_NAME"]["provenance"] == "derived"


def test_all_three_artifacts_are_written_at_run_time(
    registry, fmt, store, calibration, tmp_path
):
    """Written now, not on download. A download that re-ran the pipeline would spend two more model
    calls to produce bytes we already had, and could return something different."""
    result = enrich_one(
        EnrichmentRequest(mpn=DISHWASHER_MPN, description=DISHWASHER_DESC),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "enrich",
        mpn=DISHWASHER_MPN,
        description=DISHWASHER_DESC,
    )

    assert delivery.csv_path.is_file()
    assert delivery.xlsx_path.is_file()
    assert delivery.provenance_path.is_file()
    assert delivery.csv_path.read_text(encoding="utf-8").count("\n") >= 2
    assert delivery.xlsx_path.read_bytes().startswith(b"PK\x03\x04")


def test_delivery_files_are_slugged_too(registry, fmt, store, calibration, tmp_path):
    result = enrich_one(
        EnrichmentRequest(mpn="52C3-5/8-UPC", description="5/8IN COPPER"),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    delivery = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "enrich",
        mpn="52C3-5/8-UPC",
        description="5/8IN COPPER",
    )

    assert delivery.csv_path.name == "52C3-5~2F8-UPC.delivery.csv"
    # The cell still carries the real part number; only the filename is escaped.
    assert delivery.row.as_dict()["Mfg_Part_Num"] == "52C3-5/8-UPC"


def test_mfr_url_is_only_set_when_a_url_was_actually_fetched(
    registry, fmt, store, calibration, tmp_path
):
    """``MFR URL`` is a claim about who said it, not merely a link, so it stays empty when nobody
    opened one."""
    result = enrich_one(
        EnrichmentRequest(mpn=DISHWASHER_MPN, description=DISHWASHER_DESC),
        registry=registry,
        client=stub(json.dumps([])),
        store=store,
        calibration_dir=calibration,
    )
    without = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "no-url",
        mpn=DISHWASHER_MPN,
        description=DISHWASHER_DESC,
    )
    with_url = build_delivery(
        result.record,
        registry=registry,
        fmt=fmt,
        out_dir=tmp_path / "with-url",
        mpn=DISHWASHER_MPN,
        description=DISHWASHER_DESC,
        source_url="https://example.com/pdsh4816af.pdf",
    )

    assert without.row.as_dict()["MFR URL"] == ""
    assert with_url.row.as_dict()["MFR URL"] == "https://example.com/pdsh4816af.pdf"
