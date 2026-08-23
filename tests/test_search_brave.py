"""Offline contract tests for the Brave Search provider."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from axiom.ingest.web import USER_AGENT
from axiom.retrieve import BraveSearch, BraveSearchError
from axiom.retrieve.search_brave import ENDPOINT, MAX_RESPONSE_BYTES, result_urls

PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "Manufacturer product",
                "url": "https://manufacturer.example/product/sku-1",
                "description": "A search-engine snippet that must never become evidence",
            },
            {"url": "https://manufacturer.example/docs/sku-1.pdf"},
            {"url": "https://manufacturer.example/product/sku-1"},
            {"url": "javascript:alert(1)"},
        ]
    }
}


def provider(payload: dict = PAYLOAD):
    sent: list[tuple[str, dict[str, str], float]] = []

    def transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        sent.append((url, headers, timeout))
        return payload

    instance = BraveSearch(api_key="secret-token", transport=transport)
    instance.sent = sent  # type: ignore[attr-defined]
    return instance


def test_results_are_ordered_deduplicated_urls_only():
    urls = provider()("Acme SKU-1 datasheet", limit=8)

    assert urls == (
        "https://manufacturer.example/product/sku-1",
        "https://manufacturer.example/docs/sku-1.pdf",
    )
    assert not any("snippet" in url for url in urls)


def test_query_limit_and_subscription_header_are_sent():
    instance = provider()
    instance("Acme SKU-1", limit=7)

    url, headers, _timeout = instance.sent[0]  # type: ignore[attr-defined]
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == ENDPOINT
    assert parse_qs(parsed.query)["q"] == ["Acme SKU-1"]
    assert parse_qs(parsed.query)["count"] == ["7"]
    assert headers["X-Subscription-Token"] == "secret-token"
    assert headers["User-Agent"] == USER_AGENT


def test_limit_is_honoured():
    assert len(provider()("x", limit=1)) == 1
    assert provider()("x", limit=0) == ()


def test_missing_or_malformed_web_results_are_empty():
    assert result_urls({}) == []
    assert result_urls({"web": {"results": "not-a-list"}}) == []


def test_missing_key_is_actionable(monkeypatch):
    monkeypatch.delenv("AXIOM_BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    with pytest.raises(BraveSearchError, match="AXIOM_BRAVE_API_KEY"):
        BraveSearch.from_env()


def test_transport_errors_use_the_provider_error_contract():
    def broken(url: str, headers: dict[str, str], timeout: float) -> dict:
        raise TimeoutError("timed out")

    instance = BraveSearch(api_key="secret-token", transport=broken)
    with pytest.raises(BraveSearchError, match="timed out"):
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
        "axiom.retrieve.search_brave.urllib.request.urlopen",
        lambda _request, timeout: OversizedResponse(),
    )

    with pytest.raises(BraveSearchError, match="byte ceiling"):
        BraveSearch(api_key="secret-token")("Acme SKU-1", limit=1)

    assert read_limits == [MAX_RESPONSE_BYTES + 1]
