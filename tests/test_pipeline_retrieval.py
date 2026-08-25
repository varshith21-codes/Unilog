"""Focused orchestration regressions for search budgets and browser escalation."""

from __future__ import annotations

from axiom.ingest import LocalArtifactStore
from axiom.ingest.web import FetchedResource, UrlFetchError
from axiom.pipeline.retrieval import retrieve_documents
from axiom.retrieve import PlaywrightRenderer, RenderedResult, SourcePolicy

POLICY = """
version: 1
policy:
  min_seconds_between_requests: 0
  respect_robots_txt: false
  max_candidates_per_sku: 4
spec_hints:
  paths: [/product, /docs, /datasheet]
  suffixes: [.pdf]
manufacturers:
  - id: acme
    name: Acme
    domains: [acme.example]
    vendor_codes: [ACME]
    vendors: [acme]
"""


def policy(tmp_path) -> SourcePolicy:
    path = tmp_path / "sourcing.yaml"
    path.write_text(POLICY, encoding="utf-8")
    return SourcePolicy.load(path)


def test_static_shell_escalates_to_rendered_pdf_and_preserves_coverage(tmp_path):
    product_url = "https://acme.example/product/opaque-slug"
    pdf_url = "https://acme.example/docs/acme-backing-pad.pdf"
    events: list[str] = []

    def search(query: str, *, limit: int):
        events.append(f"search:{query}")
        return [product_url] if query.startswith("site:") else []

    def fetch(url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        events.append(f"fetch:{url}")
        if url == product_url:
            return FetchedResource(
                data=b"<html><body><div id='app'></div></body></html>",
                url=url,
                content_type="text/html",
            )
        if url == pdf_url:
            return FetchedResource(
                data=(
                    b"ACME TECHNICAL DATA SHEET\n"
                    b"Part Number      Pad Size      Thread\n"
                    b"9190153001       127 mm        M14\n"
                ),
                url=url,
                content_type="text/plain",
            )
        raise UrlFetchError(f"unexpected URL: {url}")

    class Renderer:
        def __call__(self, url, *, timeout, max_bytes, authorize):
            events.append(f"render:{url}")
            authorize(url)
            return RenderedResult(
                resources=(
                    FetchedResource(
                        data=(
                            b"<html><body><h1>Backing Pad</h1>"
                            b"<a href='/docs/acme-backing-pad.pdf'>Technical data sheet</a>"
                            b"</body></html>"
                        ),
                        url=url,
                        content_type="text/html",
                    ),
                ),
                requests_made=3,
            )

    store = LocalArtifactStore(tmp_path / "store")
    attempt = retrieve_documents(
        "9190153001",
        store=store,
        library_path=tmp_path / "index.json",
        manufacturer="Acme",
        vendor_code="ACME",
        policy=policy(tmp_path),
        search=search,
        fetcher=fetch,
        renderer=Renderer(),
        discover=False,
    )

    assert attempt.found, attempt.notes
    assert attempt.primary is not None
    assert "127 mm" in attempt.primary.full_text
    assert attempt.primary.document.uri == pdf_url
    assert attempt.browser_requests_made == 3
    assert events.index(f"fetch:{product_url}") < events.index(f"render:{product_url}")
    assert events.index(f"render:{product_url}") < events.index(f"fetch:{pdf_url}")


def test_manufacturer_page_without_body_match_is_returned_as_supplementary(tmp_path):
    """A manufacturer product page whose SKU renders client-side is read, not discarded.

    The page's static bytes never name the part — the "Technical details" tab is JavaScript — so
    ``find_sku`` cannot cover it and it must not gate escalation. But it links to the datasheet that
    does cover the part, which becomes the primary. The product page is then the manufacturer's own
    statement about this part, and retrieval hands it back as supplementary so the pipeline reads it
    alongside the datasheet rather than throwing it away. This is the exact source the original bug
    fetched and ignored.
    """
    product_url = "https://acme.example/product/opaque-slug"
    pdf_url = "https://acme.example/docs/acme-backing-pad.pdf"

    def search(query: str, *, limit: int):
        return [product_url] if query.startswith("site:") else []

    def fetch(url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        if url == product_url:
            # A real product page: names the part nowhere in its static shell (it renders in a
            # JS tab), but links to the datasheet PDF.
            return FetchedResource(
                data=(
                    b"<html><body><h1>Backing Pad</h1>"
                    b"<a href='/docs/acme-backing-pad.pdf'>Technical data sheet</a>"
                    b"</body></html>"
                ),
                url=url,
                content_type="text/html",
            )
        if url == pdf_url:
            return FetchedResource(
                data=(
                    b"ACME TECHNICAL DATA SHEET\n"
                    b"Part Number      Pad Size      Thread\n"
                    b"9190153001       127 mm        M14\n"
                ),
                url=url,
                content_type="text/plain",
            )
        raise UrlFetchError(f"unexpected URL: {url}")

    attempt = retrieve_documents(
        "9190153001",
        store=LocalArtifactStore(tmp_path / "store"),
        library_path=tmp_path / "index.json",
        manufacturer="Acme",
        vendor_code="ACME",
        policy=policy(tmp_path),
        search=search,
        fetcher=fetch,
        discover=False,
    )

    # The datasheet body-matched and is the primary; the shell page did not and is not.
    assert attempt.primary is not None
    assert attempt.primary.document.uri == pdf_url
    primary_shas = {document.document.sha256 for document in attempt.documents}
    supplementary_uris = {document.document.uri for document in attempt.supplementary}
    # The manufacturer product page is read alongside, not discarded, and not double-counted.
    assert product_url in supplementary_uris
    supplementary_shas = {document.document.sha256 for document in attempt.supplementary}
    assert primary_shas.isdisjoint(supplementary_shas)


def test_sitemap_failure_cannot_consume_the_reserved_search_budget(tmp_path):
    pdf_url = "https://acme.example/datasheet/sku-9.pdf"
    fetched: list[str] = []
    searched: list[str] = []

    def search(query: str, *, limit: int):
        searched.append(query)
        return [pdf_url]

    def fetch(url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        fetched.append(url)
        if url == pdf_url:
            return FetchedResource(
                data=b"ACME DATASHEET\nPart Number SKU-9\nPressure 600 PSI\n",
                url=url,
                content_type="text/plain",
            )
        raise UrlFetchError(f"{url} returned HTTP 404")

    attempt = retrieve_documents(
        "SKU-9",
        store=LocalArtifactStore(tmp_path / "store"),
        library_path=tmp_path / "index.json",
        manufacturer="Acme",
        vendor_code="ACME",
        policy=policy(tmp_path),
        search=search,
        fetcher=fetch,
        max_fetches=5,
    )

    assert attempt.found, attempt.notes
    assert searched, "open search must run even after sitemap discovery spends its allowance"
    assert pdf_url in fetched
    assert attempt.requests_made <= 5


def test_playwright_renderer_satisfies_the_rendered_fetcher_shape():
    renderer = PlaywrightRenderer()
    assert renderer.max_requests > 0
    assert renderer.max_pdfs > 0


def _install_fake_playwright(
    monkeypatch,
    *,
    request_urls=(),
    status=200,
    markup="<html><body>rendered</body></html>",
    download=None,
    responses=(),
    final_url=None,
    open_websocket=False,
    failure=None,
    tampered_text_encoder=False,
    download_path_response=None,
):
    """Install a deterministic in-memory Playwright surface for renderer safety tests."""
    import sys
    from types import ModuleType, SimpleNamespace

    actions: list[str] = []

    class FakePlaywrightError(Exception):
        pass

    class FakePlaywrightTimeoutError(FakePlaywrightError):
        pass

    class FakeRoute:
        def __init__(self, request_url: str) -> None:
            self.request = SimpleNamespace(url=request_url, resource_type="xhr")

        def abort(self) -> None:
            actions.append("abort")

        def continue_(self) -> None:
            actions.append("continue")

    class FakeContext:
        def __init__(self) -> None:
            self.route_handler = None
            self.websocket_handler = None
            self.callbacks = {}

        def route(self, _pattern, handler) -> None:
            self.route_handler = handler

        def route_web_socket(self, _pattern, handler) -> None:
            self.websocket_handler = handler

        def new_page(self):
            context = self

            class FakePage:
                def __init__(self) -> None:
                    self.url = "https://acme.example/product/sku-9"
                    self.main_frame = object()

                def on(self, event, callback) -> None:
                    context.callbacks[event] = callback

                def goto(self, requested_url, **_kwargs):
                    assert context.route_handler is not None
                    for request_url in request_urls:
                        context.route_handler(FakeRoute(request_url))

                    if open_websocket:
                        assert context.websocket_handler is not None

                        class FakeWebSocket:
                            def close(self, *, code, reason) -> None:
                                actions.append(f"websocket-close:{code}:{reason}")

                        context.websocket_handler(FakeWebSocket())

                    def emit_response(spec):
                        body = spec.get("body", b"")
                        is_navigation = bool(spec.get("navigation", False))

                        def response_body(*, _body=body, _spec=spec):
                            if _spec.get("body_error"):
                                raise FakePlaywrightError("response body unavailable")
                            return _body

                        response = SimpleNamespace(
                            url=spec["url"],
                            status=spec.get("status", 200),
                            headers={
                                "content-type": spec.get("content_type", "text/html"),
                                "content-length": str(
                                    spec.get("content_length", len(body))
                                ),
                            },
                            request=SimpleNamespace(
                                is_navigation_request=lambda _value=is_navigation: _value,
                                frame=self.main_frame if is_navigation else None,
                            ),
                            body=response_body,
                        )
                        context.callbacks["response"](response)
                        return is_navigation

                    last_navigation_url = requested_url
                    for spec in responses:
                        if emit_response(spec):
                            last_navigation_url = spec["url"]

                    if download is not None:
                        download_url, download_path = download

                        def resolve_download_path():
                            if download_path_response is not None:
                                emit_response(download_path_response)
                            return str(download_path)

                        context.callbacks["download"](
                            SimpleNamespace(
                                url=download_url,
                                path=resolve_download_path,
                            )
                        )
                    if failure is not None:
                        raise RuntimeError(failure)
                    self.url = final_url or last_navigation_url
                    return SimpleNamespace(status=status, url=requested_url)

                def wait_for_load_state(self, _state, **_kwargs) -> None:
                    pass

                def evaluate(self, _script, limit: int):
                    if tampered_text_encoder:
                        return markup
                    raise AssertionError("renderer must not measure in the page's main world")

            return FakePage()

        def new_cdp_session(self, _page):
            class FakeCdpSession:
                def send(self, method, params=None):
                    if method == "Page.getFrameTree":
                        return {"frameTree": {"frame": {"id": "main-frame"}}}
                    if method == "Page.createIsolatedWorld":
                        return {"executionContextId": 42}
                    if method == "Runtime.callFunctionOn":
                        assert params["executionContextId"] == 42
                        limit = params["arguments"][0]["value"]
                        if len(markup.encode("utf-8")) > limit:
                            return {"result": {"type": "object", "subtype": "null"}}
                        return {"result": {"type": "string", "value": markup}}
                    raise AssertionError(f"unexpected CDP method: {method}")

                def detach(self) -> None:
                    pass

            return FakeCdpSession()

        def close(self) -> None:
            pass

    context = FakeContext()

    class FakeBrowser:
        def new_context(self, **_kwargs):
            return context

        def close(self) -> None:
            pass

    class FakeChromium:
        def launch(self, **_kwargs):
            return FakeBrowser()

    class FakePlaywrightManager:
        def __enter__(self):
            return SimpleNamespace(chromium=FakeChromium())

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            pass

    sync_api = ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError
    sync_api.TimeoutError = FakePlaywrightTimeoutError
    sync_api.sync_playwright = FakePlaywrightManager
    playwright = ModuleType("playwright")
    playwright.__path__ = []
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return actions


def test_repeated_browser_polling_consumes_the_request_cap(monkeypatch):
    repeated_url = "https://acme.example/api/status"
    actions = _install_fake_playwright(
        monkeypatch,
        request_urls=[repeated_url] * 5,
    )

    result = PlaywrightRenderer(max_requests=2)(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert result.requests_made == 2
    assert actions == ["continue", "continue", "abort", "abort", "abort"]


def test_rendered_http_error_page_is_discarded(monkeypatch):
    url = "https://acme.example/product/sku-9"
    _install_fake_playwright(monkeypatch, request_urls=[url], status=404)

    result = PlaywrightRenderer()(url, max_bytes=1_024, authorize=lambda _url: None)

    assert result.resources == ()
    assert any("HTTP 404" in note for note in result.notes)


def test_page_tampering_cannot_bypass_the_rendered_dom_ceiling(monkeypatch):
    url = "https://acme.example/product/sku-9"
    _install_fake_playwright(
        monkeypatch,
        request_urls=[url],
        markup="x" * 11,
        # The fake main-world encoder lies and would return the oversized markup. Production must
        # serialize from the clean CDP isolated world instead.
        tampered_text_encoder=True,
    )

    result = PlaywrightRenderer()(url, max_bytes=10, authorize=lambda _url: None)

    assert result.resources == ()
    assert any("rendered HTML exceeded" in note for note in result.notes)


def test_late_main_frame_error_replaces_the_initial_navigation_status(monkeypatch):
    initial_url = "https://acme.example/product/sku-9"
    error_url = "https://acme.example/login-expired"
    _install_fake_playwright(
        monkeypatch,
        request_urls=[initial_url, error_url],
        status=200,
        final_url=error_url,
        responses=[
            {"url": initial_url, "status": 200, "navigation": True},
            {"url": error_url, "status": 404, "navigation": True},
        ],
    )

    result = PlaywrightRenderer()(
        initial_url,
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert result.resources == ()
    assert any(error_url in note and "HTTP 404" in note for note in result.notes)


def test_non_successful_download_cannot_become_pdf_evidence(monkeypatch, tmp_path):
    download_url = "https://acme.example/docs/error.pdf"
    downloaded = tmp_path / "error.pdf"
    downloaded.write_bytes(b"%PDF-error-page")
    _install_fake_playwright(
        monkeypatch,
        request_urls=["https://acme.example/product/sku-9"],
        download=(download_url, downloaded),
        responses=[
            {
                "url": download_url,
                "status": 404,
                "content_type": "application/pdf",
                "body": b"%PDF-error-page",
            }
        ],
    )

    result = PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert all(resource.media_type != "application/pdf" for resource in result.resources)
    assert any(download_url in note and "HTTP 404" in note for note in result.notes)


def test_same_url_download_cannot_reuse_stale_success_metadata(monkeypatch, tmp_path):
    download_url = "https://acme.example/docs/reused"
    downloaded = tmp_path / "later-error.pdf"
    downloaded.write_bytes(b"%PDF-later-error")
    _install_fake_playwright(
        monkeypatch,
        request_urls=[download_url, download_url],
        download=(download_url, downloaded),
        responses=[
            {
                "url": download_url,
                "status": 200,
                "content_type": "application/pdf",
            },
            {
                "url": download_url,
                "status": 404,
                "content_type": "text/html",
            },
        ],
    )

    result = PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert all(resource.media_type != "application/pdf" for resource in result.resources)
    assert any("repeated responses were ambiguous" in note for note in result.notes)


def test_download_wait_rechecks_same_url_response_ambiguity(monkeypatch, tmp_path):
    download_url = "https://acme.example/docs/during-wait"
    downloaded = tmp_path / "during-wait.pdf"
    downloaded.write_bytes(b"%PDF-must-not-survive")
    _install_fake_playwright(
        monkeypatch,
        request_urls=[download_url],
        download=(download_url, downloaded),
        responses=[
            {
                "url": download_url,
                "status": 200,
                "content_type": "application/pdf",
            }
        ],
        download_path_response={
            "url": download_url,
            "status": 404,
            "content_type": "text/html",
        },
    )

    result = PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert all(resource.media_type != "application/pdf" for resource in result.resources)
    assert any("repeated responses were ambiguous" in note for note in result.notes)


def test_mislabeled_browser_download_is_not_retained(monkeypatch, tmp_path):
    download_url = "https://acme.example/docs/spec.pdf"
    downloaded = tmp_path / "not-a-pdf.bin"
    downloaded.write_bytes(b"this is not a PDF")
    _install_fake_playwright(
        monkeypatch,
        request_urls=["https://acme.example/product/sku-9"],
        download=(download_url, downloaded),
        responses=[
            {
                "url": download_url,
                "content_type": "application/pdf",
                "body_error": True,
            }
        ],
    )

    result = PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert all(resource.media_type != "application/pdf" for resource in result.resources)
    assert any("non-PDF" in note for note in result.notes)


def test_oversized_browser_download_is_not_read_or_retained(monkeypatch, tmp_path):
    download_url = "https://acme.example/docs/spec.pdf"
    downloaded = tmp_path / "oversized.pdf"
    downloaded.write_bytes(b"%PDF-" + b"x" * 20)
    _install_fake_playwright(
        monkeypatch,
        request_urls=["https://acme.example/product/sku-9"],
        download=(download_url, downloaded),
        responses=[
            {
                "url": download_url,
                "content_type": "application/pdf",
                "body_error": True,
            }
        ],
    )

    result = PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=10,
        authorize=lambda _url: None,
    )

    assert result.resources == ()
    assert any("oversized download" in note for note in result.notes)


def test_browser_websocket_is_closed_by_policy(monkeypatch):
    actions = _install_fake_playwright(monkeypatch, open_websocket=True)

    PlaywrightRenderer()(
        "https://acme.example/product/sku-9",
        max_bytes=1_024,
        authorize=lambda _url: None,
    )

    assert actions == [
        "websocket-close:1008:browser retrieval does not permit WebSockets"
    ]


def test_failed_browser_rendering_still_reports_requests_made(tmp_path, monkeypatch):
    product_url = "https://acme.example/product/opaque-slug"
    _install_fake_playwright(
        monkeypatch,
        request_urls=[product_url] * 3,
        failure="renderer stopped safely",
    )

    def search(query: str, *, limit: int):
        return [product_url] if query.startswith("site:") else []

    def fetch(url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        return FetchedResource(
            data=b"<html><body><div id='app'></div></body></html>",
            url=url,
            content_type="text/html",
        )

    attempt = retrieve_documents(
        "9190153001",
        store=LocalArtifactStore(tmp_path / "store"),
        library_path=tmp_path / "index.json",
        manufacturer="Acme",
        vendor_code="ACME",
        policy=policy(tmp_path),
        search=search,
        fetcher=fetch,
        renderer=PlaywrightRenderer(),
        discover=False,
    )

    assert not attempt.found
    assert attempt.browser_requests_made == 3
    assert any("renderer stopped safely" in note for note in attempt.notes)
