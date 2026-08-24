"""Serper API adapter for authoritative product-source discovery.

Only ordered result URLs leave this module. Titles, snippets, answer boxes, and other search-engine
summaries are not source evidence, so they are deliberately discarded. The resolver still
classifies every URL, the retrieval session fetches it under policy, and only immutable source
bytes that verify the requested SKU can support an extracted value.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from axiom.ingest.web import USER_AGENT

ENDPOINT = "https://google.serper.dev/search"
DEFAULT_TIMEOUT = 15.0
MAX_RESULTS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

SerperTransport = Callable[[str, dict[str, str], bytes, float], dict]


class SerperSearchError(RuntimeError):
    """The Serper provider is misconfigured or a search request failed."""


@dataclass
class SerperSearch:
    """Return ranked URLs from Serper's Google Search API."""

    api_key: str = field(repr=False)
    timeout: float = DEFAULT_TIMEOUT
    country: str | None = None
    language: str | None = None
    transport: SerperTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key.strip()
        if not self.api_key:
            raise SerperSearchError(
                "no API key: set AXIOM_SERPER_API_KEY (or SERPER_API_KEY)"
            )

    @classmethod
    def from_env(cls, **overrides) -> SerperSearch:
        key = os.environ.get("AXIOM_SERPER_API_KEY") or os.environ.get(
            "SERPER_API_KEY"
        )
        if not key:
            raise SerperSearchError(
                "no API key: set AXIOM_SERPER_API_KEY (or SERPER_API_KEY)"
            )
        country = os.environ.get("AXIOM_SERPER_COUNTRY") or None
        language = os.environ.get("AXIOM_SERPER_LANGUAGE") or None
        return cls(
            api_key=key,
            country=country,
            language=language,
            **overrides,
        )

    def __call__(self, query: str, *, limit: int) -> Sequence[str]:
        if not query.strip() or limit <= 0:
            return ()

        count = min(MAX_RESULTS, max(1, limit))
        request_payload: dict[str, object] = {"q": query, "num": count}
        if self.country:
            request_payload["gl"] = self.country
        if self.language:
            request_payload["hl"] = self.language
        body = json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-API-KEY": self.api_key,
        }

        send = self.transport or _post
        try:
            payload = send(ENDPOINT, headers, body, self.timeout)
        except SerperSearchError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider/transport failures
            raise SerperSearchError(f"Serper request failed: {exc}") from exc
        return tuple(result_urls(payload)[:limit])

    def health(self) -> dict[str, object]:
        """Make one explicit diagnostic query without exposing the API key."""
        try:
            urls = self("industrial product technical data sheet", limit=1)
        except SerperSearchError as exc:
            return {"ok": False, "reason": str(exc), "results": 0}
        return {
            "ok": bool(urls),
            "reason": "" if urls else "provider returned no organic results",
            "results": len(urls),
            "sample": list(urls),
        }


def result_urls(payload: dict) -> list[str]:
    """Return ordered, de-duplicated URLs from Serper's ``organic`` results."""
    results = payload.get("organic")
    if not isinstance(results, list):
        return []

    urls: list[str] = []
    for result in results:
        url = result.get("link") if isinstance(result, dict) else None
        if (
            isinstance(url, str)
            and url.startswith(("https://", "http://"))
            and url not in urls
        ):
            urls.append(url)
    return urls


def _post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )  # noqa: S310 - fixed HTTPS endpoint
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise SerperSearchError(
                    f"Serper response exceeded the {MAX_RESPONSE_BYTES:,}-byte ceiling"
                )
    except urllib.error.HTTPError as exc:
        raise SerperSearchError(
            f"Serper returned HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SerperSearchError(f"could not reach Serper: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SerperSearchError(f"Serper timed out after {timeout:g}s") from exc

    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SerperSearchError("Serper returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SerperSearchError("Serper returned a non-object JSON response")
    return payload


_shared: SerperSearch | None = None


def from_env(query: str, *, limit: int) -> Sequence[str]:
    """Module provider for ``--search-module axiom.retrieve.search_serper:from_env``."""
    global _shared  # noqa: PLW0603 - provider reuse avoids rebuilding configuration per query
    if _shared is None:
        _shared = SerperSearch.from_env()
    return _shared(query, limit=limit)


__all__ = [
    "DEFAULT_TIMEOUT",
    "ENDPOINT",
    "MAX_RESPONSE_BYTES",
    "MAX_RESULTS",
    "SerperSearch",
    "SerperSearchError",
    "from_env",
    "result_urls",
]
