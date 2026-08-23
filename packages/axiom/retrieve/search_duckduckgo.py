"""A :class:`~axiom.retrieve.resolver.SearchProvider` over DuckDuckGo's lite interface.

The open-web arm that needs **no API key, no account and no bill**, which is what lets it be the
default. Given ``43911BK`` and ``Kichler`` it returns the manufacturer's own product page as the
first
result; the policy gate then drops the marketplace and distributor results that come with it.

Measured against three long-tail part numbers from the client's item master, each of which resolved
to
the manufacturer's own domain:

===============  =========================================================================
part number      first manufacturer-tier result
===============  =========================================================================
43911BK          ``kichler.com/products/indoor-lighting/pendants/avery-pendant-43911bk``
PDSH4816AF       ``frigidaire.com/en/p/kitchen/dishwashers/PDSH4816AF``
DBDS14125G01F    ``diablotools.com/products/DBDS14125G01F``
===============  =========================================================================

**It returns URLs and discards every snippet**, for the same two reasons
:mod:`axiom.retrieve.search_bedrock` does, and they still hold here. A snippet is a third party's
summary, so a value extracted from one would carry a citation whose bytes we never held. And taking
only the link is the same use a person makes of a result list, where reading result text into a
product database across a thousand SKUs is not. There is no code here that can return snippet text,
because that is not a limitation worth leaving to a caller's discretion.

**The honest User-Agent is required, not merely preferred.** This is worth recording because the
intuition is backwards: sending a spoofed ``Mozilla/5.0`` browser string gets HTTP 202 and an
anti-bot challenge, while :data:`axiom.ingest.web.USER_AGENT` — which says what this is and who to
contact — gets HTTP 200 and results. So the behaviour the module docstring in ``ingest/web.py``
argues
for on principle ("a crawler that disguises itself as a browser is making a claim about who it is")
turns out to be the only thing that works. Do not "fix" this by adding a browser UA.

**On terms of service, stated plainly rather than buried.** This is DuckDuckGo's public HTML
interface, not a documented API with a usage agreement. It is used here the way a person uses it —
one query per part number, honestly identified, spaced out, link taken and page left — and no result
text is stored. That is a defensible reading and it is not a licence. Two consequences follow: keep
:data:`MIN_SECONDS_BETWEEN_QUERIES` in place, and treat the provider as replaceable. Anything
running
at real volume, or anywhere a contract matters, should swap in a paid API — the
:class:`~axiom.retrieve.resolver.SearchProvider` protocol is one method wide precisely so that is a
one-line change.

**It is HTML, so it will break.** A markup change turns results into no results. Every failure path
returns an empty sequence rather than raising, matching the search-provider contract: an empty
result
is an ordinary outcome for a long-tail part and must not abort a run.
:meth:`DuckDuckGoSearch.health`
exists so the breakage is diagnosable rather than merely quiet.
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from axiom.ingest.web import USER_AGENT

ENDPOINT = "https://lite.duckduckgo.com/lite/"
"""The lite interface. Chosen over ``html.duckduckgo.com`` because that one answers 202 with a
challenge page, while this one answers 200 with results."""

DEFAULT_TIMEOUT = 20.0

MIN_SECONDS_BETWEEN_QUERIES = 2.0
"""Spacing between queries from one provider instance.

Not a rate limit imposed on us — a courtesy we impose on ourselves, and the thing that makes the
terms-of-service reading above defensible. A thousand-row batch that fired a thousand queries in
thirty seconds would be abuse whatever the User-Agent said. Note the retrieval session spaces
requests
per *origin*; this is separate, because a search endpoint is one origin serving every part number.
"""

# Results are `<a rel="nofollow" href="URL" class='result-link'>`, with direct URLs and no redirect
# wrapper. Anchored on the class rather than on position, because the surrounding table markup is
# incidental and changes more readily than the class does. Quotes are either kind: the endpoint
# mixes
# `href="..."` with `class='...'` in the same tag.
_RESULT = re.compile(
    r"<a\b[^>]*?href=[\"'](?P<url>https?://[^\"']+)[\"'][^>]*?class=[\"']result-link[\"']",
    re.IGNORECASE,
)

# Their own domains, which appear as navigation rather than as results.
_OWN = ("duckduckgo.com", "duck.co")


@dataclass
class DuckDuckGoSearch:
    """Searches DuckDuckGo's lite interface and returns the result URLs.

    Usable directly as a ``SearchProvider``::

        resolver = Resolver(policy, search=DuckDuckGoSearch())

    Or from the CLI, which imports a callable by name::

        python scripts/retrieve_sources.py input.csv `
          --search-module axiom.retrieve.search_duckduckgo:search
    """

    timeout: float = DEFAULT_TIMEOUT
    min_interval: float = MIN_SECONDS_BETWEEN_QUERIES
    transport: Callable[[str, bytes, dict[str, str], float], str] | None = field(
        default=None, repr=False
    )
    """Sends the request and returns the response body. Injectable so the provider is testable
    without a network — which is what keeps this path covered rather than skipped."""

    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    _last_query_at: float | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ the protocol

    def __call__(self, query: str, *, limit: int) -> Sequence[str]:
        """Search, and return up to ``limit`` result URLs in the order they were ranked.

        Returns an empty sequence on any failure — a transport error, a challenge page, a markup
        change. That is the search-provider contract rather than laziness: an empty result is an
        ordinary outcome for an obscure industrial part, and an exception here would abort a
        thousand-row batch over one part number nobody could have found anyway.
        """
        body = self._fetch(query)
        if not body:
            return ()
        return tuple(self.parse(body))[:limit]

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def parse(body: str) -> list[str]:
        """Result URLs from a lite-interface response, in order, de-duplicated.

        Static and separate from the transport so a captured page can be asserted on directly, which
        is how the markup contract stays pinned without a network.
        """
        urls: list[str] = []
        for match in _RESULT.finditer(body):
            # `&amp;` is routine in these URLs — `lightology.com/...?module=x&amp;prod_id=y` — and
            # an
            # unescaped one would 404 or, worse, silently fetch a different page.
            url = html.unescape(match.group("url"))
            if any(own in url for own in _OWN):
                continue
            if url not in urls:
                urls.append(url)
        return urls

    # ------------------------------------------------------------------ transport

    def _fetch(self, query: str) -> str:
        self._wait()
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        headers = {
            # The honest one, and it is load-bearing: a browser string gets a challenge page here.
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        transport = self.transport or _post
        try:
            return transport(ENDPOINT, data, headers, self.timeout)
        except Exception:  # noqa: BLE001 - every failure is "no results", by contract
            return ""

    def _wait(self) -> None:
        if self._last_query_at is not None:
            elapsed = self.clock() - self._last_query_at
            if elapsed < self.min_interval:
                self.sleep(self.min_interval - elapsed)
        self._last_query_at = self.clock()

    # ------------------------------------------------------------------ diagnosis

    def health(self, query: str = "Kichler 43911BK specifications") -> dict[str, object]:
        """Whether the provider is working, and if not, which way it broke.

        Exists because this provider fails *silently by design* — an empty result is
        indistinguishable from an obscure part number. Without a way to ask, a markup change would
        present as a gradual, unexplained decline in retrieval coverage.
        """
        body = self._fetch(query)
        if not body:
            return {"ok": False, "reason": "no response body", "results": 0}

        urls = self.parse(body)
        lowered = body.lower()
        challenged = any(
            marker in lowered for marker in ("anomaly", "captcha", "challenge", "unusual traffic")
        )
        if not urls:
            return {
                "ok": False,
                "reason": (
                    "served a challenge page rather than results"
                    if challenged
                    else "response carried no result-link anchors; the markup has probably changed"
                ),
                "results": 0,
                "bytes": len(body),
            }
        return {"ok": True, "reason": "", "results": len(urls), "sample": urls[:3]}


def _post(url: str, data: bytes, headers: dict[str, str], timeout: float) -> str:
    """Default transport: stdlib, matching ``axiom.ingest.web``'s reasoning about dependencies."""
    import urllib.request

    request = urllib.request.Request(  # noqa: S310 - fixed https host, not caller-supplied
        url, data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


_shared: DuckDuckGoSearch | None = None


def search(query: str, *, limit: int) -> Sequence[str]:
    """Module-level ``SearchProvider``, so a CLI can name it without constructing anything.

    Backed by one shared instance rather than a fresh one per call, because the query spacing is
    per-instance state — a new object per query would space nothing.
    """
    global _shared  # noqa: PLW0603 - the shared instance *is* the rate-limit state
    if _shared is None:
        _shared = DuckDuckGoSearch()
    return _shared(query, limit=limit)


__all__ = [
    "DEFAULT_TIMEOUT",
    "ENDPOINT",
    "MIN_SECONDS_BETWEEN_QUERIES",
    "DuckDuckGoSearch",
    "search",
]
