"""A :class:`~axiom.retrieve.resolver.SearchProvider` over Amazon Bedrock's Web Search tool.

The open-web arm, on the stack this project already runs on. No third-party search vendor, no second
key, and with ``external_web_access=False`` the request is served from Bedrock's own index and cache
so the query does not leave the AWS boundary.

**It returns URLs and discards every snippet, and that is the load-bearing decision here.**

Two independent reasons, and they happen to agree:

*Provenance.* A search snippet is a third party's summary of a page. Extracting an attribute from
one
would produce a citation whose bytes we never held and cannot hash — the evidence chain would
terminate in someone else's excerpt while looking exactly like a citation into a manufacturer's
datasheet. So the snippet is used for nothing: the URL goes back to the resolver, the policy gate
runs on it, and if it survives, :class:`~axiom.retrieve.session.RetrievalSession` fetches the
manufacturer's own bytes and *those* are what get hashed, parsed and cited.

*Licensing.* Bedrock's acceptable-use terms for Web Search state that it may not be used to extract,
store or reproduce content from search results in bulk, or to build or populate a competing index or
database. Reading snippets into a product database across a thousand SKUs is squarely that. Taking
only the link is not — it is the same use a person makes of a result list. This module is written so
the compliant path is the only one available: there is no code here that can return snippet text,
because that is not a limitation worth leaving to a caller's discretion.

Model and endpoint constraints, current as of the AWS documentation this was written against
(``docs.aws.amazon.com/bedrock/latest/userguide/web-search.html``):

*   Web Search is a **server-side tool on the Responses API**, reachable on the ``bedrock-mantle``
    endpoint and *not* on ``bedrock-runtime``. It is therefore a separate client from
    ``axiom.extract.BedrockModelClient``, which talks to the runtime endpoint — the two are not
    interchangeable and are deliberately not merged.
*   Supported on the OpenAI GPT families served through Bedrock. ``ModelCascade`` in this repo
points
    at open-weight models on ``bedrock-runtime`` for extraction; search needs a GPT model here, and
    that is a genuine second dependency rather than an oversight.
*   Regional: ``us-east-1``, ``us-east-2``, ``us-west-2``. Queries stay in-Region.

**This has not been run against the live service.** There are no credentials for it in this
environment, so what is verified is the request shape and the parsing of the documented response
shape — the ``url_citation`` annotation contract — against fixtures taken from the AWS docs. The
transport is a thin, injectable seam for exactly that reason: :class:`BedrockWebSearch` takes a
``responder`` callable, so the provider is testable and swappable without a network. Treat the live
path as unverified until someone runs ``scripts/preflight_bedrock.py`` equivalent for it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

DEFAULT_MODEL = "openai.gpt-5.6-terra"
DEFAULT_REGION = "us-east-2"

SUPPORTED_REGIONS = frozenset({"us-east-1", "us-east-2", "us-west-2"})
"""Web Search processes queries in-Region and does not route across Regions."""

# The instruction sent with each query. Blunt on purpose: the model's prose is thrown away and only
# its citations are read, so asking it to *write* anything would spend tokens on output nobody
# consumes.
PROMPT = (
    "Find the manufacturer's official product page or datasheet for this industrial product. "
    "Prefer the manufacturer's own website. Do not use retailers, marketplaces or distributors. "
    "Answer in one sentence; the citations are what matter.\n\nQuery: {query}"
)


class BedrockWebSearchError(RuntimeError):
    """Raised when the search call itself fails. Never raised for an empty result."""


@dataclass
class BedrockWebSearch:
    """Calls Bedrock Web Search and returns the cited URLs.

    Usable directly as a ``SearchProvider``::

        provider = BedrockWebSearch.from_env()
        resolver = Resolver(policy, search=provider)

    Or from the CLI, which imports it by name::

        python scripts/retrieve_sources.py input.csv `
          --search-module axiom.retrieve.search_bedrock:from_env
    """

    api_key: str
    model: str = DEFAULT_MODEL
    region: str = DEFAULT_REGION
    external_web_access: bool = False
    """Left False, which is both the safe and the cheap default.

    False keeps retrieval inside the AWS boundary, needs no extra IAM permission, and — per the AWS
    note — avoids the data-exfiltration shape where an agent encodes query data into a URL and
    fetches it. It costs freshness: a page published since the last index refresh will not be found.
    That trade is the right way round here, because a *missing* result costs one unresolved row
    while
    a leaked one is unrecoverable.
    """

    timeout: float = 30.0
    responder: Callable[[dict], dict] | None = field(default=None, repr=False)
    """Sends the request body and returns the parsed JSON response. Injectable so the provider is
    testable without credentials or a network."""

    def __post_init__(self) -> None:
        if self.region not in SUPPORTED_REGIONS:
            raise BedrockWebSearchError(
                f"Web Search is not available in {self.region!r}; supported regions are "
                f"{', '.join(sorted(SUPPORTED_REGIONS))}"
            )

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_env(cls, **overrides) -> BedrockWebSearch:
        """Build from the environment, or explain exactly what is missing.

        Reads ``AXIOM_BEDROCK_API_KEY`` first and falls back to ``OPENAI_API_KEY``, which is what
        the
        AWS examples set for the ``bedrock-mantle`` endpoint. Named separately so an operator who
        already has an unrelated OpenAI key in their shell does not have it silently used to bill
        Bedrock.
        """
        key = os.environ.get("AXIOM_BEDROCK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise BedrockWebSearchError(
                "no API key: set AXIOM_BEDROCK_API_KEY (or OPENAI_API_KEY) to a Bedrock API key "
                "for the bedrock-mantle endpoint. Web Search is a server-side tool on the "
                "Responses API and is not reachable on bedrock-runtime, so the extraction "
                "credentials this project already uses will not work for it."
            )
        return cls(
            api_key=key,
            model=os.environ.get("AXIOM_WEBSEARCH_MODEL", DEFAULT_MODEL),
            region=os.environ.get("AXIOM_WEBSEARCH_REGION", DEFAULT_REGION),
            **overrides,
        )

    @property
    def base_url(self) -> str:
        return f"https://bedrock-mantle.{self.region}.api.aws/openai/v1"

    # ------------------------------------------------------------------ the protocol

    def __call__(self, query: str, *, limit: int) -> Sequence[str]:
        """Search, and return up to ``limit`` cited URLs in the order the model cited them.

        Returns an empty sequence when the search found nothing. An empty result is an ordinary
        outcome for a long-tail industrial part and must not look like a failure — the resolver
        records it as "no candidate", which is true and actionable, whereas an exception would abort
        a thousand-row batch over one obscure part number.
        """
        body = self.request_body(query)
        respond = self.responder or self._post
        try:
            payload = respond(body)
        except BedrockWebSearchError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures are reported, not leaked
            raise BedrockWebSearchError(f"Web Search request failed: {exc}") from exc

        return citation_urls(payload)[:limit]

    def request_body(self, query: str) -> dict:
        """The Responses API request. Separated out so a test can assert on it without a network."""
        return {
            "model": self.model,
            "input": PROMPT.format(query=query),
            "tools": [
                {"type": "web_search", "external_web_access": self.external_web_access}
            ],
        }

    # ------------------------------------------------------------------ transport

    def _post(self, body: dict) -> dict:
        """The default transport: stdlib, matching ``axiom.ingest.web``'s reasoning.

        The only outbound calls in this project are AWS ones, and this is not worth a dependency.
        """
        import urllib.error
        import urllib.request

        request = urllib.request.Request(  # noqa: S310 - fixed https host, not caller-supplied
            f"{self.base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code == 403:
                raise BedrockWebSearchError(
                    f"HTTP 403 from Web Search. If external_web_access is true, the calling "
                    f"identity also needs bedrock-websearch:ExternalWebAccess, which "
                    f"AmazonBedrockFullAccess does not grant. Detail: {detail}"
                ) from exc
            raise BedrockWebSearchError(f"HTTP {exc.code} from Web Search: {detail}") from exc


def citation_urls(payload: dict) -> list[str]:
    """Every ``url_citation`` URL in a Responses API payload, in order, de-duplicated.

    Reads ``output[].content[].annotations[]``, which is the documented location. Written to
    tolerate
    a partly-shaped response rather than to validate one: a missing key means this response carried
    no citations, which is an empty result, not a malformed payload. Being strict here would turn a
    harmless response-shape change into a hard failure across a whole batch.

    **The annotation's ``title`` and the surrounding answer text are deliberately not returned.**
    Only the URL. See the module docstring.
    """
    urls: list[str] = []
    seen: set[str] = set()

    for item in _iter_list(payload.get("output")):
        if not isinstance(item, dict):
            continue
        for block in _iter_list(item.get("content")):
            if not isinstance(block, dict):
                continue
            for annotation in _iter_list(block.get("annotations")):
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") != "url_citation":
                    continue
                url = str(annotation.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                urls.append(url)
    return urls


def _iter_list(value) -> list:
    return value if isinstance(value, list) else []


def from_env(query: str, *, limit: int) -> Sequence[str]:
    """Module-level ``SearchProvider``, so the CLI can name it without constructing anything.

    ``--search-module axiom.retrieve.search_bedrock:from_env``

    The client is built on first use and reused, because ``--search-module`` resolves to a plain
    callable and there is nowhere else to keep it.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = BedrockWebSearch.from_env()
    return _SHARED(query, limit=limit)


_SHARED: BedrockWebSearch | None = None


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_REGION",
    "PROMPT",
    "SUPPORTED_REGIONS",
    "BedrockWebSearch",
    "BedrockWebSearchError",
    "citation_urls",
    "from_env",
]
