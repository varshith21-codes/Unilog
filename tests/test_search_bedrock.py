"""The Bedrock Web Search provider: request shape, citation parsing, and what it refuses to return.

**Scope of what these tests establish.** There are no Bedrock credentials in this environment, so
nothing here calls the live service. What is verified is the contract on both sides of the transport
seam: the request body sent, and the parsing of the response shape AWS documents. The fixture below
is
taken from the ``url_citation`` example in
``docs.aws.amazon.com/bedrock/latest/userguide/web-search.html``.

That is a real limit and worth stating plainly: these tests would still pass if the live endpoint
changed its response shape. They exist to stop *this code* drifting, not to prove the integration
works. Proving that needs one live call.

The most important assertion in the file is a negative one — that snippets and titles never come
back
out. Two reasons that agree: a snippet is a third party's summary, so a value extracted from one
would
carry a citation whose bytes were never held; and Bedrock's acceptable-use terms forbid using Web
Search to extract or store search-result content in bulk or to populate a database, which is exactly
what a PIM enrichment run would otherwise be doing.
"""

from __future__ import annotations

import pytest
from axiom.retrieve.search_bedrock import (
    DEFAULT_MODEL,
    SUPPORTED_REGIONS,
    BedrockWebSearch,
    BedrockWebSearchError,
    citation_urls,
)

# Shaped after the documented example: citations live at output[].content[].annotations[].
DOCUMENTED_RESPONSE = {
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": "The 49-94-0013 is a 5-inch metal cut-off wheel.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "title": "49-94-0013 | Milwaukee Tool",
                            "url": "https://www.milwaukeetool.com/Products/49-94-0013",
                            "start_index": 4,
                            "end_index": 40,
                        },
                        {
                            "type": "url_citation",
                            "title": "Cut-Off Wheels Catalog (PDF)",
                            "url": "https://www.milwaukeetool.com/literature/cut-off.pdf",
                            "start_index": 41,
                            "end_index": 46,
                        },
                    ],
                }
            ],
        }
    ]
}


def provider(responder, **overrides) -> BedrockWebSearch:
    return BedrockWebSearch(api_key="test-key", responder=responder, **overrides)


# ------------------------------------------------------------------ citation parsing


def test_citations_are_read_from_the_documented_location():
    assert citation_urls(DOCUMENTED_RESPONSE) == [
        "https://www.milwaukeetool.com/Products/49-94-0013",
        "https://www.milwaukeetool.com/literature/cut-off.pdf",
    ]


def test_only_url_citation_annotations_are_read():
    payload = {
        "output": [
            {
                "content": [
                    {
                        "annotations": [
                            {"type": "file_citation", "url": "https://ignored.example/"},
                            {"type": "url_citation", "url": "https://kept.example/"},
                        ]
                    }
                ]
            }
        ]
    }
    assert citation_urls(payload) == ["https://kept.example/"]


def test_duplicate_citations_collapse_in_first_cited_order():
    """A model grounds several sentences in one page. The order it first cited them is the only
    ranking signal the response carries, so it is preserved."""
    payload = {
        "output": [
            {
                "content": [
                    {
                        "annotations": [
                            {"type": "url_citation", "url": "https://b.example/"},
                            {"type": "url_citation", "url": "https://a.example/"},
                            {"type": "url_citation", "url": "https://b.example/"},
                        ]
                    }
                ]
            }
        ]
    }
    assert citation_urls(payload) == ["https://b.example/", "https://a.example/"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output": None},
        {"output": []},
        {"output": [{}]},
        {"output": [{"content": None}]},
        {"output": [{"content": [{}]}]},
        {"output": [{"content": [{"annotations": None}]}]},
        {"output": [{"content": [{"annotations": [{}]}]}]},
        {"output": [{"content": [{"annotations": [{"type": "url_citation"}]}]}]},
        {"output": ["not a dict"]},
        {"output": [{"content": ["not a dict"]}]},
    ],
)
def test_a_partly_shaped_response_is_an_empty_result_not_an_error(payload):
    """Deliberately permissive. A missing key means this response carried no citations, which is an
    ordinary outcome for a long-tail industrial part — being strict would turn a harmless
    response-shape change into a hard failure across a whole batch."""
    assert citation_urls(payload) == []


# ------------------------------------------------------------------ what it will not return


def test_the_provider_returns_urls_and_nothing_else():
    """The load-bearing negative. Snippets and titles are in the payload and must not come back.

    A search snippet is a third party's summary of a page, so a value extracted from one would have
    a
    citation whose bytes were never held and cannot be hashed. It is also what Bedrock's acceptable
    use forbids storing in bulk. There is deliberately no code path here that can return it.
    """
    urls = provider(lambda _body: DOCUMENTED_RESPONSE)("49-94-0013 Milwaukee", limit=8)

    assert all(isinstance(url, str) and url.startswith("https://") for url in urls)
    joined = " ".join(urls)
    assert "Milwaukee Tool" not in joined, "a citation title leaked into the result"
    assert "cut-off wheel" not in joined.lower(), "answer prose leaked into the result"


def test_the_limit_is_honoured():
    urls = provider(lambda _body: DOCUMENTED_RESPONSE)("q", limit=1)
    assert len(urls) == 1


# ------------------------------------------------------------------ the request


def test_the_request_carries_the_web_search_tool():
    body = provider(lambda _body: {}).request_body("49-94-0013 Milwaukee")
    assert body["model"] == DEFAULT_MODEL
    assert body["tools"] == [{"type": "web_search", "external_web_access": False}]
    assert "49-94-0013 Milwaukee" in body["input"]


def test_external_web_access_defaults_to_false():
    """Keeps retrieval inside the AWS boundary, needs no extra IAM permission, and avoids the
    documented exfiltration shape where an agent encodes query data into a URL and fetches it.

    It costs freshness, and that trade is the right way round: a missing result costs one unresolved
    row, a leaked query is unrecoverable.
    """
    assert provider(lambda _b: {}).external_web_access is False


def test_external_web_access_can_be_turned_on_deliberately():
    body = provider(lambda _b: {}, external_web_access=True).request_body("q")
    assert body["tools"][0]["external_web_access"] is True


def test_the_endpoint_is_bedrock_mantle_not_bedrock_runtime():
    """Web Search is a server-side tool on the Responses API and is not reachable on
    bedrock-runtime, which is where this project's extraction client talks. The two are genuinely
    different endpoints and are deliberately not merged."""
    assert provider(lambda _b: {}, region="us-west-2").base_url == (
        "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
    )


@pytest.mark.parametrize("region", sorted(SUPPORTED_REGIONS))
def test_supported_regions_are_accepted(region):
    assert provider(lambda _b: {}, region=region).region == region


def test_an_unsupported_region_is_refused_at_construction():
    """Web Search is strictly regional and does not route across Regions, so a wrong region is a
    failure at every call. Better to refuse once, with the list."""
    with pytest.raises(BedrockWebSearchError, match="not available in"):
        provider(lambda _b: {}, region="eu-west-1")


# ------------------------------------------------------------------ failure handling


def test_a_transport_failure_is_raised_as_our_own_error():
    def explode(_body):
        raise TimeoutError("connection timed out")

    with pytest.raises(BedrockWebSearchError, match="request failed"):
        provider(explode)("q", limit=4)


def test_from_env_explains_which_key_is_missing(monkeypatch):
    """And why the extraction credentials will not do: different endpoint."""
    monkeypatch.delenv("AXIOM_BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(BedrockWebSearchError) as raised:
        BedrockWebSearch.from_env()
    assert "AXIOM_BEDROCK_API_KEY" in str(raised.value)
    assert "bedrock-runtime" in str(raised.value)


def test_from_env_prefers_the_project_specific_key(monkeypatch):
    """So an unrelated OpenAI key sitting in a shell is not silently used to bill Bedrock."""
    monkeypatch.setenv("AXIOM_BEDROCK_API_KEY", "ours")
    monkeypatch.setenv("OPENAI_API_KEY", "someone-elses")
    assert BedrockWebSearch.from_env().api_key == "ours"


def test_the_provider_satisfies_the_search_provider_protocol():
    """It is passed straight to Resolver, so the shape has to match: (query, *, limit) -> URLs."""
    from axiom.retrieve import Resolver, SourcePolicy

    resolver = Resolver(SourcePolicy(), search=provider(lambda _b: DOCUMENTED_RESPONSE))
    assert resolver.has_search
