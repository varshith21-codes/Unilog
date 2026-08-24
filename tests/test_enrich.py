"""Tests for ``POST /api/enrich`` and its download route.

The endpoint runs the online pipeline, so the model client is stubbed and every test here runs
offline — no ``live`` marker, no credentials. That is deliberate rather than convenient: an
endpoint whose only test needs AWS is an endpoint with no tests, which is why
:func:`~axiom.pipeline.enrich_one` takes its client as an argument and why
``main.model_client`` is a replaceable module-level function.

Most of these are refusals, and that is the right emphasis. The success path is one shape;
the ways a submission can be wrong are many, and each one has to say something a person can
act on. The particular refusals worth guarding:

*   **Neither a description nor a URL.** The only case where the request is well-formed and
    running it would still be a waste of two model calls.
*   **An already-enriched SKU.** A re-run replaces a session a reviewer may have worked, so
    it needs to be asked for.
*   **An unreachable URL.** 502 with the fetch error verbatim, because a caller looking at a link
    that works in their browser needs to know what this process saw.
*   **No credentials.** Currently a traceback, which reads as a console bug rather than an operator
    one.
"""

from __future__ import annotations

import json

import pytest
from axiom.extract import StubModelClient
from axiom.ingest.web import UrlFetchError

MPN = "PDSH4816AF"
DESCRIPTION = "24IN BUILT IN DISHWASHER STAINLESS STEEL 47DBA"
MANUFACTURER = "Frigidaire"


@pytest.fixture
def searches() -> list[str]:
    """Every query the endpoint's open-web arm was asked, recorded rather than served.

    A list rather than a mock so a test can assert on the *query text* — which is the part that
    decides result quality, and the part that silently degraded to a bare part number before the
    manufacturer name was threaded through.
    """
    return []


@pytest.fixture
def enrich_dirs(tmp_path, monkeypatch, searches):
    """Point every directory the endpoint writes to at ``tmp_path``.

    All five, not just the obvious two. A test that left ``CONSOLE_DIR`` or ``ENRICH_DIR``
    pointing at the repository would write a bundle into the demo dataset and a delivery file
    into the tree, and the next run of the console would render it as real output.
    """
    from apps.api import main

    dirs = {
        "SESSION_DIR": tmp_path / "sessions",
        "CONSOLE_DIR": tmp_path / "console",
        "CALIBRATION_DIR": tmp_path / "calibration",
        "ENRICH_DIR": tmp_path / "enrich",
        "ARTIFACT_DIR": tmp_path / "artifacts",
    }
    for name, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main, name, path)

    # The document library, redirected too — and this one matters more than the rest. Left pointing
    # at
    # the repository, retrieval would read the real 79-document index, and a test could then "pass"
    # because a datasheet somebody fetched last week happened to cover the part.
    monkeypatch.setattr(main, "LIBRARY_INDEX", tmp_path / "library" / "index.json")

    # And no network, ever — through *either* seam. Retrieval reaches the internet two ways: it
    # fetches documents, and it asks a search engine which documents exist. Both refuse here, so a
    # test that unexpectedly starts retrieving fails loudly rather than quietly sending traffic to a
    # third party from whatever machine ran the suite.
    #
    # Both, because stubbing only the fetcher stopped being enough once open-web search became the
    # default: the search provider is a separate object with its own transport.
    # Fetching refuses outright, because no test here should ever pull bytes from a third party.
    def refuse_fetch(url, *, timeout, max_bytes):
        raise AssertionError(f"a test tried to fetch {url}")

    # Searching records and finds nothing, which is a legitimate outcome rather than an accident —
    # open-web search is on by default now, so the endpoint asking a question is expected. Returning
    # no results exercises the "searched, found nothing" path these tests assert on, without a
    # request leaving the machine.
    def no_results(query, *, limit):
        searches.append(query)
        return ()

    monkeypatch.setattr(main, "retrieval_fetcher", lambda: refuse_fetch)
    monkeypatch.setattr(main, "search_provider", lambda: no_results)
    return dirs


@pytest.fixture
def stub_model(monkeypatch):
    """Replace the Bedrock client factory, and hand the test the stub it produced.

    Returns a callable so a test can decide what the model says. The default is an empty extraction
    contract, which is the honest stub for "the document does not state these attributes" — the
    description pass still runs and still produces cited values.
    """
    from apps.api import main

    holder: dict[str, StubModelClient] = {}

    def install(*payloads: str) -> dict[str, StubModelClient]:
        client = StubModelClient(list(payloads) or [json.dumps([])])
        holder["client"] = client
        monkeypatch.setattr(main, "model_client", lambda profile=None: client)
        return holder

    install()
    return install


@pytest.fixture
def client(enrich_dirs, stub_model):
    from fastapi.testclient import TestClient

    from apps.api import main

    return TestClient(main.app)


def submit(client, **overrides):
    body = {"mpn": MPN, "manufacturer": MANUFACTURER, "description": DESCRIPTION}
    body.update(overrides)
    return client.post("/api/enrich", json=body)


# ===================================================================== the contract


def test_limits_are_served_rather_than_hard_coded(client):
    """The form describes the caps this API enforces, not a copy of them that can drift."""
    from apps.api import main

    payload = client.get("/api/enrich/limits").json()

    assert payload["required"] == ["mpn", "manufacturer"]
    assert payload["one_of"] == ["description", "source_url"]
    assert payload["max_description_chars"] == main.MAX_DESCRIPTION_CHARS
    assert payload["max_document_bytes"] == main.ENRICH_MAX_BYTES
    assert payload["url_schemes"] == ["https"]
    # The cost is stated, because a form that spends money should say so before it is used.
    assert payload["model_calls_per_run"]["without_copy"] == 2
    assert any("costs money" in note for note in payload["notes"])


# ===================================================================== refusals


def test_a_part_number_alone_is_refused_only_with_retrieval_off(client, stub_model):
    """With nowhere to look and nothing to read, running would waste two model calls.

    ``retrieve=false`` is what makes this the honest refusal it used to be unconditionally. With
    retrieval on the manufacturer name is a lead in its own right, so the same submission is
    accepted — which is the next test.
    """
    holder = stub_model()
    response = client.post(
        "/api/enrich",
        json={"mpn": MPN, "manufacturer": MANUFACTURER, "retrieve": False},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "insufficient_input"
    assert detail["missing"] == ["description", "source_url"]
    assert "does not describe one" in detail["message"]
    # Nothing was spent finding that out.
    assert holder["client"].calls == []


def test_a_part_number_and_a_manufacturer_are_enough_with_retrieval_on(client, searches):
    """No description, no URL — the submission this whole feature exists for.

    ``Frigidaire`` has no declared domain in ``schema/sourcing.yaml``, so site discovery has nowhere
    to look and the open-web arm is the only route. Here it returns nothing (the fixture's provider
    finds nothing), so the run falls back to the submission as its source — which the response must
    say rather than leaving a caller to infer it from a thin row.
    """
    response = client.post("/api/enrich", json={"mpn": MPN, "manufacturer": MANUFACTURER})

    assert response.status_code == 200, response.text
    retrieval = response.json()["summary"]["retrieval"]
    assert retrieval["attempted"] is True
    assert retrieval["found"] is False
    assert retrieval["manufacturer"] is None
    # It names the fix rather than reporting a dead end.
    assert any("schema/sourcing.yaml" in note for note in retrieval["notes"])
    # Nothing was fetched, because nothing was found to fetch.
    assert retrieval["requests_made"] == 0


def test_the_open_web_arm_names_the_manufacturer_in_its_query(client, searches):
    """The query text decides result quality, and this is the case that exercises it.

    With no declared domain there is no ``site:`` query to fall back on, so the open query is the
    only one that runs — and it has to carry the manufacturer name. A bare "PDSH4816AF
    specifications" finds retailers; "Frigidaire PDSH4816AF specifications" finds the spec sheet.
    """
    response = client.post("/api/enrich", json={"mpn": MPN, "manufacturer": MANUFACTURER})
    assert response.status_code == 200, response.text

    assert searches, "the open-web arm did not run"
    assert any(MANUFACTURER in q and MPN in q for q in searches), searches


def test_the_library_is_consulted_before_the_network(client, enrich_dirs, monkeypatch):
    """A stored document that covers the part means no request at all.

    The economic claim behind the whole library: retrieval is a one-time cost per *document*, not
    per
    part. One accessory catalogue answers for every part listed in it, and this asserts the endpoint
    actually takes that path rather than re-fetching.

    The fetcher in this fixture raises on any call, so reaching the network here would fail the test
    outright rather than merely slowing it down.
    """
    from axiom.ingest import LocalArtifactStore, ingest_bytes
    from axiom.retrieve import DocumentLibrary

    from apps.api import main

    datasheet = (
        b"FRIGIDAIRE BUILT-IN DISHWASHER\n"
        b"Model PDSH4816AF\n"
        b"SPECIFICATIONS\n"
        b"  Sound Level .................... 47 dBA\n"
        b"  Primary Material ............... Stainless Steel\n"
    )
    store = LocalArtifactStore(enrich_dirs["ARTIFACT_DIR"])
    artifact = ingest_bytes(datasheet, store, filename="pdsh4816af.txt")

    index = enrich_dirs["ARTIFACT_DIR"].parent / "library" / "index.json"
    library = DocumentLibrary.load(store, index)
    library.register(artifact, host="frigidaire.com", tier="manufacturer")
    # Coverage is *discovered*, not declared: `coverage_for` searches the stored text for the part
    # number. So this asserts the document genuinely names the part rather than that a test said so.
    assert library.coverage_for(MPN), "the fixture datasheet must name the part number"
    library.save()
    monkeypatch.setattr(main, "LIBRARY_INDEX", index)

    response = client.post("/api/enrich", json={"mpn": MPN, "manufacturer": MANUFACTURER})

    assert response.status_code == 200, response.text
    payload = response.json()
    retrieval = payload["summary"]["retrieval"]
    assert retrieval["found"] is True
    assert retrieval["from_library"] is True
    assert retrieval["requests_made"] == 0
    assert any("no request was made" in note for note in retrieval["notes"])

    # And the run genuinely read that document rather than the typed fields.
    assert payload["summary"]["source"]["kind"] == "document"
    assert payload["summary"]["source"]["sha256"] == artifact.sha256


def test_a_missing_manufacturer_is_a_validation_error(client):
    response = client.post("/api/enrich", json={"mpn": MPN, "description": DESCRIPTION})
    assert response.status_code == 422


def test_plain_http_is_refused_by_field(client):
    """Bytes fetched over http carry no integrity guarantee, so hashing them as provenance would
    defeat the citation. Named against the field rather than explained as a trade-off, because the
    form never offered one."""
    response = submit(client, source_url="http://example.com/ds.pdf")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "insecure_url"
    assert detail["field"] == "source_url"


def test_an_over_long_description_is_refused(client):
    from apps.api import main

    response = submit(client, description="x" * (main.MAX_DESCRIPTION_CHARS + 1))
    assert response.status_code == 422


def test_an_unknown_class_code_is_refused_with_the_known_ones(client):
    response = submit(client, class_code="NOPE.NOT.A.CLASS")
    assert response.status_code == 400
    assert "unknown class" in response.json()["detail"]


def test_an_unreachable_url_is_a_bad_gateway_with_the_fetch_error(
    client, monkeypatch, stub_model
):
    """502 rather than 400: the request was fine, the upstream document was not retrievable.

    The fetch error travels verbatim because a caller looking at a link that works in their browser
    needs to know what this process actually saw — a 403 from a portal reads very differently from a
    DNS failure.
    """
    from axiom.pipeline import single

    def refuse(*args, **kwargs):
        raise UrlFetchError("https://example.com/ds.pdf returned HTTP 403 Forbidden")

    monkeypatch.setattr(single, "resolve_source", refuse)

    response = submit(client, source_url="https://example.com/ds.pdf")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error"] == "source_unreachable"
    assert "403" in detail["message"]
    assert detail["field"] == "source_url"


def test_the_profile_is_resolved_without_reading_the_launching_shell(monkeypatch):
    """The regression this exists for, and it bit twice before being understood.

    With no ``AWS_PROFILE``, boto3 selects the profile literally named ``default``. If that profile
    is present in ``~/.aws/config`` — giving it a region, so it looks configured — but has no entry
    in ``~/.aws/credentials``, the result is ``NoCredentialsError`` on a machine where working
    credentials are sitting right there under another name. "Unable to locate credentials" then
    reads as *none exist*, which sends somebody hunting for a key that was never missing.

    So the fallback picks the one profile that resolves, and only when it is unambiguous.
    """
    from apps.api import main

    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AXIOM_AWS_PROFILE", raising=False)
    # No ambient credentials, and exactly one named profile that works — the real situation.
    monkeypatch.setattr(main, "credential_profiles", lambda: {"default": False, "axiom": True})

    import boto3

    monkeypatch.setattr(boto3, "Session", lambda **kw: _NoCredentialSession())

    assert main.resolve_profile() == "axiom"


class _NoCredentialSession:
    """A boto3 session with nothing in the ambient chain."""

    available_profiles = ["default", "axiom"]

    def get_credentials(self):
        return None


def test_an_explicit_profile_is_never_second_guessed(monkeypatch):
    """A wrong profile name must surface rather than be silently replaced with a working one.

    Substituting would mean an operator who typed the wrong account sees a successful run against
    the right one, which is worse than the error.
    """
    from apps.api import main

    monkeypatch.delenv("AXIOM_AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "typo-profile")
    assert main.resolve_profile() == "typo-profile"

    monkeypatch.setenv("AXIOM_AWS_PROFILE", "chosen")
    assert main.resolve_profile() == "chosen", "AXIOM_AWS_PROFILE outranks AWS_PROFILE"


def test_ambient_credentials_are_left_alone(monkeypatch):
    """The path that must keep working on EC2, ECS and Lambda, where there is no profile at all."""
    from apps.api import main

    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AXIOM_AWS_PROFILE", raising=False)

    import boto3

    class _WithCredentials:
        available_profiles: list[str] = []

        def get_credentials(self):
            return object()

    monkeypatch.setattr(boto3, "Session", lambda **kw: _WithCredentials())

    assert main.resolve_profile() is None, "an instance role must not be overridden by a profile"


def test_the_credentials_error_names_every_profile_and_whether_it_works(client, monkeypatch):
    """So the next occurrence diagnoses itself instead of repeating the same investigation."""
    from apps.api import main

    monkeypatch.setattr(main, "credential_profiles", lambda: {"default": False, "axiom": True})
    monkeypatch.setattr(main, "resolve_profile", lambda: "axiom")

    def no_credentials(profile=None):
        raise RuntimeError("Unable to locate credentials")

    monkeypatch.setattr(main, "model_client", no_credentials)

    detail = submit(client).json()["detail"]

    assert detail["profile_used"] == "axiom"
    assert detail["profiles_with_credentials"] == ["axiom"]
    # The one that looks configured and is not, named explicitly.
    assert detail["profiles_without_credentials"] == ["default"]


def test_a_missing_credential_says_so_instead_of_raising(client, monkeypatch):
    """``ModelError`` reaching a browser as a traceback reads as a console bug. It is an operator
    problem, and the message names the fix."""
    from apps.api import main

    def no_credentials(profile=None):
        raise RuntimeError("Unable to locate credentials")

    monkeypatch.setattr(main, "model_client", no_credentials)

    response = submit(client)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "model_unavailable"
    assert "AWS_PROFILE" in detail["message"]
    # And it points at the path that needs no credentials at all.
    assert "Publish" in detail["message"]


def test_a_second_run_is_refused_while_one_is_in_flight(client):
    """A mutex, not a queue. Concurrent runs multiply the spend, and the honest answer to the second
    caller is "one at a time".

    The real lock is held rather than stubbed, because the thing worth testing is that the endpoint
    consults it and does not block on it — a mock that returned False would prove neither.
    """
    from apps.api import main

    assert main._ENRICH_LOCK.acquire(blocking=False) is True
    try:
        response = submit(client)
    finally:
        main._ENRICH_LOCK.release()

    assert response.status_code == 429
    assert "already in flight" in response.json()["detail"]
    # And the lock is free again afterwards, so one refusal does not wedge the endpoint.
    assert main._ENRICH_LOCK.acquire(blocking=False) is True
    main._ENRICH_LOCK.release()


# ===================================================================== the success path


def test_a_typed_submission_produces_a_signed_certified_record(client):
    response = submit(client)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["sku"] == MPN
    assert payload["slug"] == MPN
    summary = payload["summary"]
    assert summary["class_code"] is not None
    assert summary["certificate"]["signature_verified"] is True
    assert summary["from_description"]["extracted"] > 0
    # The submission was the source, and the response says so rather than leaving it to be inferred.
    assert summary["source"]["kind"] == "submission"
    assert summary["source"]["uri"] == f"submission:{MPN}"


def test_the_response_carries_the_bundle_the_console_already_renders(client):
    """So the stage cards render from the run that just happened without a second request.

    ``pipelineStages()`` is a pure projection over a bundle plus a document summary, so if those two
    are in the response the whole stage view comes for free.
    """
    payload = submit(client).json()
    bundle = payload["bundle"]

    for key in (
        "sku",
        "class_code",
        "classification_summary",
        "extraction",
        "normalization_issues",
        "validation",
        "certificate",
        "channels",
        "metrics",
    ):
        assert key in bundle, f"the console's SkuBundle needs {key}"

    document = payload["document"]
    for key in ("sha256", "doc_type", "size_bytes", "warnings"):
        assert key in document

    assert "threshold" in payload["policy"]
    # `pages` is deliberately absent: nothing on this screen draws line geometry, and a multi-page
    # datasheet's would be most of the response.
    assert "pages" not in payload


def test_submission_api_keeps_the_new_specification_collections_compatible_and_empty(client):
    """Customer-entered descriptions must not be relabeled as manufacturer-published facts."""
    payload = submit(client).json()

    assert payload["summary"]["manufacturer_specifications"] == {
        "total": 0,
        "mapped": 0,
        "unmapped": 0,
    }
    assert payload["bundle"]["manufacturer_specifications"] == []

    session = client.get(f"/api/session/{payload['slug']}")
    assert session.status_code == 200
    assert session.json()["manufacturer_specifications"] == []


def test_a_cold_start_queues_everything_and_says_why(client):
    """No calibration data means no validated threshold, so nothing auto-publishes.

    The correct opening state for a brand-new SKU, and the response has to be able to explain it
    rather than looking like a failed run.
    """
    payload = submit(client).json()

    assert payload["calibrator"] == "untrained-heuristic"
    assert payload["policy"]["achievable"] is False
    assert payload["summary"]["values"]["publishable"] == 0
    assert payload["summary"]["values"]["needing_review"] >= 0
    assert payload["queue"]["total"] >= 0


def test_the_run_is_persisted_where_every_other_screen_reads_from(client, enrich_dirs):
    """The SKU joins the corpus. Without this the result is a screenshot rather than a record."""
    payload = submit(client).json()
    slug = payload["slug"]

    assert (enrich_dirs["SESSION_DIR"] / f"{slug}.json").is_file()
    assert (enrich_dirs["CONSOLE_DIR"] / f"{slug}.bundle.json").is_file()

    # And it is readable through the endpoints that serve those screens.
    session = client.get(f"/api/session/{slug}")
    assert session.status_code == 200
    assert session.json()["sku"] == MPN

    listing = client.get("/api/sessions").json()["sessions"]
    assert MPN in {entry["sku"] for entry in listing}

    dataset = client.get("/api/console/dataset").json()
    assert MPN in {entry["sku"] for entry in dataset["skus"]}


def test_delivery_artifacts_are_written_at_run_time(client, enrich_dirs):
    """All three, now — so a download never re-runs a model call."""
    payload = submit(client).json()
    slug = payload["slug"]

    assert (enrich_dirs["ENRICH_DIR"] / f"{slug}.delivery.csv").is_file()
    assert (enrich_dirs["ENRICH_DIR"] / f"{slug}.delivery.xlsx").is_file()
    assert (enrich_dirs["ENRICH_DIR"] / f"{slug}.provenance.json").is_file()

    delivery = payload["delivery"]
    assert delivery["columns"] == 252
    # Sparse is correct: the client's own ground truth leaves 173 of 252 blank.
    assert 0 < delivery["populated"] < 252
    assert delivery["blank"] == 252 - delivery["populated"]


def test_the_source_document_is_servable_by_its_own_hash(client):
    """The evidence viewer needs the bytes a citation names, and a submission is stored like any
    other arrival, so the existing artifact route serves it."""
    payload = submit(client).json()

    response = client.get(payload["links"]["source_artifact"])
    assert response.status_code == 200
    assert DESCRIPTION in response.text
    # Third-party-shaped bytes, so never reinterpreted by the browser.
    assert response.headers["x-content-type-options"] == "nosniff"


# ===================================================================== conflict and replace


def test_re_enriching_is_refused_with_the_existing_timestamp(client, stub_model):
    """A re-run replaces a session a reviewer may already have worked: it must be asked for."""
    first = submit(client)
    assert first.status_code == 200

    stub_model()  # A fresh stub, since the first run consumed the last one.
    second = submit(client)

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["error"] == "already_enriched"
    assert detail["sku"] == MPN
    # The timestamp comes from the session rather than the file's mtime: a recorded decision
    # rewrites the file without a new run having happened.
    assert detail["enriched_at"]
    assert "replace=true" in detail["message"]


def test_replace_true_overwrites_deliberately(client, stub_model):
    assert submit(client).status_code == 200

    stub_model()
    again = submit(client, replace=True)

    assert again.status_code == 200
    assert again.json()["replaced"] is True


# ===================================================================== the download


def test_the_download_serves_the_persisted_file(client):
    payload = submit(client).json()
    slug = payload["slug"]

    csv = client.get(f"/api/enrich/{slug}/delivery?output=csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert f'filename="{slug}.delivery.csv"' in csv.headers["content-disposition"]
    # 252 headers in the client's order, then one row.
    assert csv.text.count("\n") >= 2

    xlsx = client.get(f"/api/enrich/{slug}/delivery?output=xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content.startswith(b"PK\x03\x04")


def test_the_download_reports_the_run_in_headers(client):
    """The body is a file, so a caller wanting the numbers has nowhere else to read them."""
    payload = submit(client).json()
    slug = payload["slug"]

    response = client.get(f"/api/enrich/{slug}/delivery?output=csv")

    assert response.headers["x-axiom-rows"] == "1"
    assert response.headers["x-axiom-columns-total"] == "252"
    assert int(response.headers["x-axiom-columns-populated"]) == payload["delivery"]["populated"]
    # The hash describes the bytes being served, which is a stronger claim than repeating what the
    # run recorded about bytes it wrote earlier.
    assert response.headers["x-axiom-content-hash"] == payload["delivery"]["content_hash"]


def test_the_download_refuses_a_traversal_shaped_sku(client):
    """The path segment builds a filesystem path, so it is whitelist-validated rather than
    pattern-rejected — the same guard ``/api/session/{sku}`` uses."""
    response = client.get("/api/enrich/..%2F..%2Fetc%2Fpasswd/delivery?output=csv")
    assert response.status_code in {400, 404}
    if response.status_code == 400:
        assert "slug" in response.json()["detail"]


def test_the_download_refuses_an_unknown_output(client):
    payload = submit(client).json()
    response = client.get(f"/api/enrich/{payload['slug']}/delivery?output=pdf")
    assert response.status_code == 400


def test_a_sku_with_no_enrichment_run_is_a_404_that_explains_itself(client):
    response = client.get("/api/enrich/NEVER-RUN/delivery?output=csv")
    assert response.status_code == 404
    assert "POST /api/enrich" in response.json()["detail"]


# ===================================================================== separators


def test_a_part_number_with_a_separator_is_addressable_end_to_end(client, enrich_dirs):
    """``52C3-5/8-UPC`` is a fractional size, not an attack.

    The response hands back the slug, which is the identifier every other route accepts. Returning
    only the raw part number would leave the caller to derive it and get it wrong.
    """
    response = submit(
        client, mpn="52C3-5/8-UPC", manufacturer="Nibco", description="5/8IN COPPER TUBE"
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["sku"] == "52C3-5/8-UPC"
    assert payload["slug"] == "52C3-5~2F8-UPC"

    slug = payload["slug"]
    assert (enrich_dirs["SESSION_DIR"] / f"{slug}.json").is_file()
    assert client.get(f"/api/session/{slug}").json()["sku"] == "52C3-5/8-UPC"
    assert client.get(f"/api/enrich/{slug}/delivery?output=csv").status_code == 200
