"""Offline contract tests for the Serper Search provider."""

from __future__ import annotations

import json

import pytest
from axiom.ingest.web import USER_AGENT
from axiom.retrieve import DuckDuckGoSearch, SerperSearch, SerperSearchError
from axiom.retrieve.search_serper import ENDPOINT, MAX_RESPONSE_BYTES, result_urls

PAYLOAD = {
    "searchParameters": {"q": "Acme SKU-1 datasheet"},
    "organic": [
        {
            "title": "Manufacturer product",
            "link": "https://manufacturer.example/product/sku-1",
            "snippet": "A search-engine snippet that must never become evidence",
        },
        {"link": "https://manufacturer.example/docs/sku-1.pdf"},
        {"link": "https://manufacturer.example/product/sku-1"},
        {"link": "javascript:alert(1)"},
    ],
    "answerBox": {"answer": "unsupported search summary"},
}


def provider(payload: dict = PAYLOAD):
    sent: list[tuple[str, dict[str, str], bytes, float]] = []

    def transport(
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> dict:
        sent.append((url, headers, body, timeout))
        return payload

    instance = SerperSearch(api_key="test-token", transport=transport)
    instance.sent = sent  # type: ignore[attr-defined]
    return instance


def test_results_are_ordered_deduplicated_urls_only():
    urls = provider()("Acme SKU-1 datasheet", limit=8)

    assert urls == (
        "https://manufacturer.example/product/sku-1",
        "https://manufacturer.example/docs/sku-1.pdf",
    )
    assert not any("snippet" in url or "summary" in url for url in urls)


def test_query_limit_and_api_header_are_sent_in_a_json_post():
    instance = provider()
    instance("Acme SKU-1", limit=7)

    url, headers, body, timeout = instance.sent[0]  # type: ignore[attr-defined]
    assert url == ENDPOINT
    assert json.loads(body) == {"q": "Acme SKU-1", "num": 7}
    assert headers["X-API-KEY"] == "test-token"
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == USER_AGENT
    assert timeout > 0


def test_country_and_language_are_optional_request_fields():
    sent: list[dict] = []

    def transport(url, headers, body, timeout):
        sent.append(json.loads(body))
        return PAYLOAD

    SerperSearch(
        api_key="test-token",
        country="us",
        language="en",
        transport=transport,
    )("x", limit=2)

    assert sent == [{"q": "x", "num": 2, "gl": "us", "hl": "en"}]


def test_limit_is_honoured_and_capped_for_the_provider():
    instance = provider()
    assert len(instance("x", limit=1)) == 1
    assert instance("x", limit=0) == ()
    instance("x", limit=500)
    assert json.loads(instance.sent[-1][2])["num"] == 20  # type: ignore[attr-defined]


def test_missing_or_malformed_organic_results_are_empty():
    assert result_urls({}) == []
    assert result_urls({"organic": "not-a-list"}) == []


def test_missing_key_is_actionable(monkeypatch):
    monkeypatch.delenv("AXIOM_SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    with pytest.raises(SerperSearchError, match="AXIOM_SERPER_API_KEY"):
        SerperSearch.from_env()


def test_transport_errors_use_the_provider_error_contract():
    def broken(url, headers, body, timeout):
        raise TimeoutError("timed out")

    instance = SerperSearch(api_key="test-token", transport=broken)
    with pytest.raises(SerperSearchError, match="timed out"):
        instance("x", limit=1)


def test_default_transport_refuses_an_oversized_response(monkeypatch):
    read_limits: list[int] = []

    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            pass

        def read(self, limit: int) -> bytes:
            read_limits.append(limit)
            return b"x" * limit

    monkeypatch.setattr(
        "axiom.retrieve.search_serper.urllib.request.urlopen",
        lambda _request, timeout: OversizedResponse(),
    )

    with pytest.raises(SerperSearchError, match="byte ceiling"):
        SerperSearch(api_key="test-token")("Acme SKU-1", limit=1)

    assert read_limits == [MAX_RESPONSE_BYTES + 1]


def test_api_auto_mode_prefers_serper_when_a_key_is_present(monkeypatch):
    from apps.api import main

    monkeypatch.setenv("AXIOM_SEARCH", "auto")
    monkeypatch.setenv("AXIOM_SERPER_API_KEY", "test-token")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    selected = main.search_provider()

    assert isinstance(selected, SerperSearch)
    assert "test-token" not in repr(selected)


def test_api_auto_mode_uses_duckduckgo_without_a_serper_key(monkeypatch):
    from apps.api import main

    monkeypatch.setenv("AXIOM_SEARCH", "auto")
    monkeypatch.delenv("AXIOM_SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    assert isinstance(main.search_provider(), DuckDuckGoSearch)


def test_explicit_serper_without_a_key_degrades_to_duckduckgo(monkeypatch, capsys):
    from apps.api import main

    monkeypatch.setenv("AXIOM_SEARCH", "serper")
    monkeypatch.delenv("AXIOM_SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    selected = main.search_provider()

    assert isinstance(selected, DuckDuckGoSearch)
    assert "falling back to DuckDuckGo" in capsys.readouterr().err
