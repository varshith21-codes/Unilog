"""Brave Search API adapter for authoritative product-source discovery.

Only result URLs leave this module. Titles, descriptions and extra snippets are search-engine
summaries rather than source evidence, so they are deliberately discarded. The resolver still
classifies every URL, the retrieval session fetches it under policy, and only stored source bytes
can support an extracted value.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from axiom.ingest.web import USER_AGENT

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 15.0
MAX_RESULTS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class BraveSearchError(RuntimeError):
    """The Brave provider is misconfigured or a search request failed."""


@dataclass
class BraveSearch:
    """Return ranked URLs from Brave's independent web index."""

    api_key: str
    timeout: float = DEFAULT_TIMEOUT
    country: str | None = None
    search_lang: str = "en"
    transport: Callable[[str, dict[str, str], float], dict] | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        self.api_key = self.api_key.strip()
        if not self.api_key:
            raise BraveSearchError(
                "no API key: set AXIOM_BRAVE_API_KEY (or BRAVE_SEARCH_API_KEY)"
            )

    @classmethod
    def from_env(cls, **overrides) -> BraveSearch:
        key = os.environ.get("AXIOM_BRAVE_API_KEY") or os.environ.get(
            "BRAVE_SEARCH_API_KEY"
        )
        if not key:
            raise BraveSearchError(
                "no API key: set AXIOM_BRAVE_API_KEY (or BRAVE_SEARCH_API_KEY)"
            )
        country = os.environ.get("AXIOM_BRAVE_COUNTRY") or None
        language = os.environ.get("AXIOM_BRAVE_SEARCH_LANG") or "en"
        return cls(api_key=key, country=country, search_lang=language, **overrides)

    def __call__(self, query: str, *, limit: int) -> Sequence[str]:
        if not query.strip() or limit <= 0:
            return ()

        count = min(MAX_RESULTS, max(1, limit))
        params = {
            "q": query,
            "count": str(count),
            "safesearch": "moderate",
            "search_lang": self.search_lang,
        }
        if self.country:
            params["country"] = self.country
        url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Subscription-Token": self.api_key,
        }

        send = self.transport or _get
        try:
            payload = send(url, headers, self.timeout)
        except BraveSearchError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider/transport failures
            raise BraveSearchError(f"Brave Search request failed: {exc}") from exc
        return tuple(result_urls(payload)[:limit])

    def health(self) -> dict[str, object]:
        """Make one diagnostic query without exposing the subscription token."""
        try:
            urls = self("industrial product technical data sheet", limit=1)
        except BraveSearchError as exc:
            return {"ok": False, "reason": str(exc), "results": 0}
        return {
            "ok": bool(urls),
            "reason": "" if urls else "provider returned no web results",
            "results": len(urls),
            "sample": list(urls),
        }


def result_urls(payload: dict) -> list[str]:
    """Ordered, de-duplicated URLs from the documented ``web.results`` shape."""
    web = payload.get("web")
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return []

    urls: list[str] = []
    for result in results:
        url = result.get("url") if isinstance(result, dict) else None
        if isinstance(url, str) and url.startswith(("https://", "http://")) and url not in urls:
            urls.append(url)
    return urls


def _get(url: str, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed HTTPS endpoint
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BraveSearchError(
                    f"Brave Search response exceeded the {MAX_RESPONSE_BYTES:,}-byte ceiling"
                )
    except urllib.error.HTTPError as exc:
        raise BraveSearchError(
            f"Brave Search returned HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise BraveSearchError(f"could not reach Brave Search: {exc.reason}") from exc
    except TimeoutError as exc:
        raise BraveSearchError(f"Brave Search timed out after {timeout:g}s") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BraveSearchError("Brave Search returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BraveSearchError("Brave Search returned a non-object JSON response")
    return payload


_shared: BraveSearch | None = None


def from_env(query: str, *, limit: int) -> Sequence[str]:
    """Module-level provider for ``--search-module axiom.retrieve.search_brave:from_env``."""
    global _shared  # noqa: PLW0603 - provider reuse avoids rebuilding configuration per query
    if _shared is None:
        _shared = BraveSearch.from_env()
    return _shared(query, limit=limit)


__all__ = [
    "DEFAULT_TIMEOUT",
    "ENDPOINT",
    "MAX_RESPONSE_BYTES",
    "MAX_RESULTS",
    "BraveSearch",
    "BraveSearchError",
    "from_env",
    "result_urls",
]
