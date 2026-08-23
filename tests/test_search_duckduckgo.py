"""Tests for the keyless open-web search arm.

Two things are worth pinning here, and neither is "it parses HTML".

**The markup contract.** This provider reads a public HTML page, so a layout change turns results
into silence rather than an error. The fixture below is captured verbatim from a real response, so a
change in what we *expect* shows up as a failing test rather than as an unexplained decline in
retrieval coverage months later.

**The failure contract.** Every failure path must return an empty sequence, because the resolver
treats "no candidates" as an ordinary outcome for a long-tail part and an exception here would abort
a
thousand-row batch over one obscure part number. That is easy to write and easy to regress, so it is
tested per failure mode rather than once.

Entirely offline: the transport is injected. A test that needed the internet to check URL extraction
is a test that gets skipped, and then this code rots.
"""

from __future__ import annotations

import pytest
from axiom.ingest.web import USER_AGENT
from axiom.retrieve.search_duckduckgo import (
    ENDPOINT,
    DuckDuckGoSearch,
)

# Captured from a real POST to the lite endpoint for "Kichler 43911BK specifications". Trimmed to
# four results and the surrounding table shape, with the quoting style left exactly as served —
# `href="..."` beside `class='...'`, which a stricter regex would miss.
LITE_PAGE = """\
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html><head><title>Kichler 43911BK specifications at DuckDuckGo</title></head>
<body>
  <form action="/lite/" method="post">
    <input name="q" type="text" value="Kichler 43911BK specifications">
  </form>
  <table border="0">
      <!-- Web results are present -->
      <tr><td valign="top">1.&nbsp;</td><td>
        <a rel="nofollow"
        href="https://www.kichler.com/products/indoor-lighting/pendants/avery-pendant-43911bk"
        class='result-link'>Avery Pendant 43911BK</a>
      </td></tr>
      <tr><td valign="top">2.&nbsp;</td><td>
        <a rel="nofollow" href="https://buykichlerlighting.com/glass-down/kichler-43911bk-avery/"
        class='result-link'>Kichler 43911BK</a>
      </td></tr>
      <tr><td valign="top">3.&nbsp;</td><td>
        <a rel="nofollow"
        href="https://www.lightology.com/index.php?module=prod_detail&amp;prod_id=537063"
        class='result-link'>Lightology</a>
      </td></tr>
      <tr><td valign="top">4.&nbsp;</td><td>
        <a rel="nofollow" href="https://www.kichler.com/api/spec-sheets?sku=43911BK"
        class='result-link'>Spec sheet</a>
      </td></tr>
  </table>
  <a href="https://duckduckgo.com/settings">Settings</a>
  <a href="https://duck.co/help">Help</a>
</body></html>
"""

CHALLENGE_PAGE = """\
<!DOCTYPE html><html><head><title>DuckDuckGo</title></head>
<body><p>Our systems have detected unusual traffic from your computer network.
This anomaly may indicate automated queries. Please complete the challenge.</p></body></html>
"""


def provider(body: str | Exception = LITE_PAGE, **kwargs) -> DuckDuckGoSearch:
    """A provider whose transport is a canned response, and which never sleeps."""
    sent: list[tuple[str, bytes, dict[str, str]]] = []

    def transport(url: str, data: bytes, headers: dict[str, str], timeout: float) -> str:
        sent.append((url, data, headers))
        if isinstance(body, Exception):
            raise body
        return body

    instance = DuckDuckGoSearch(
        transport=transport, sleep=lambda _s: None, clock=lambda: 0.0, **kwargs
    )
    instance.sent = sent  # type: ignore[attr-defined]
    return instance


# ===================================================================== parsing


def test_result_urls_are_returned_in_rank_order():
    urls = provider()("Kichler 43911BK specifications", limit=8)

    assert urls[0] == (
        "https://www.kichler.com/products/indoor-lighting/pendants/avery-pendant-43911bk"
    )
    assert len(urls) == 4


def test_html_entities_in_a_url_are_decoded():
    """`&amp;` is routine in these URLs, and an unescaped one either 404s or — worse — silently
    fetches a different page than the one that was ranked."""
    urls = provider()("x", limit=8)

    assert "https://www.lightology.com/index.php?module=prod_detail&prod_id=537063" in urls
    assert not any("&amp;" in u for u in urls)


def test_the_engines_own_pages_are_not_results():
    """Settings and help links are navigation. Handing them to the fetcher would spend a request and
    store a page about the search engine under a product's citation."""
    urls = provider()("x", limit=8)

    assert not any("duckduckgo.com" in u or "duck.co" in u for u in urls)


def test_duplicates_collapse_but_order_survives():
    doubled = LITE_PAGE.replace(
        "</table>",
        "<tr><td><a rel=\"nofollow\" href=\"https://www.kichler.com/products/indoor-lighting/"
        "pendants/avery-pendant-43911bk\" class='result-link'>again</a></td></tr></table>",
    )
    urls = provider(doubled)("x", limit=8)

    assert len(urls) == 4
    assert urls[0].endswith("avery-pendant-43911bk")


def test_the_limit_is_honoured():
    assert len(provider()("x", limit=2)) == 2


def test_parse_is_callable_without_a_transport():
    """So a captured page can be asserted on directly, which is what keeps the markup contract
    pinned without a network."""
    assert len(DuckDuckGoSearch.parse(LITE_PAGE)) == 4


# ===================================================================== the request


def test_the_query_is_posted_as_a_form_field():
    """A GET against the lite path answers differently from a POST, so the method is part of the
    contract rather than a style choice."""
    instance = provider()
    instance("Kichler 43911BK", limit=4)

    url, data, _headers = instance.sent[0]  # type: ignore[attr-defined]
    assert url == ENDPOINT
    assert b"q=Kichler+43911BK" in data


def test_the_user_agent_is_the_honest_one():
    """Load-bearing, and counter-intuitive enough to pin.

    A spoofed ``Mozilla/5.0`` gets HTTP 202 and a challenge page from this endpoint; the project's
    own
    User-Agent — which says what this is and who to contact — gets results. So the honesty the
    ingest
    layer argues for on principle is also the only thing that works, and a future "fix" that adds a
    browser string would silently break retrieval.
    """
    instance = provider()
    instance("x", limit=4)

    _url, _data, headers = instance.sent[0]  # type: ignore[attr-defined]
    assert headers["User-Agent"] == USER_AGENT
    assert "Mozilla" not in headers["User-Agent"]


def test_queries_are_spaced_rather_than_fired_back_to_back():
    """The courtesy that makes the terms-of-service reading in the module docstring defensible. A
    thousand-row batch firing a thousand queries in thirty seconds would be abuse whatever the
    User-Agent said."""
    slept: list[float] = []
    now = 0.0

    # A clock the test moves explicitly, rather than a list of ticks consumed in an order that
    # depends on how many times `_wait` happens to read it. The first version of this test asserted
    # 1.5 and got 2.0 for exactly that reason.
    instance = DuckDuckGoSearch(
        transport=lambda *a: LITE_PAGE,
        sleep=slept.append,
        clock=lambda: now,
        min_interval=2.0,
    )

    instance("first", limit=1)
    assert slept == [], "nothing to wait for on the first query"

    now = 0.5
    instance("second", limit=1)
    # Half a second in, so it waits the remaining one and a half.
    assert slept == [pytest.approx(1.5)]

    now = 10.0
    instance("third", limit=1)
    assert slept == [pytest.approx(1.5)], "well past the interval, so no further wait"


# ===================================================================== failure


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(CHALLENGE_PAGE, id="anti-bot challenge"),
        pytest.param("<html><body>no results at all</body></html>", id="markup changed"),
        pytest.param("", id="empty body"),
        pytest.param(TimeoutError("timed out"), id="transport timeout"),
        pytest.param(OSError("connection reset"), id="connection reset"),
    ],
)
def test_every_failure_is_an_empty_result_not_an_exception(body):
    """The search-provider contract. An obscure part number legitimately has no results, and an
    exception here would abort a batch over the one row nobody could have found anyway."""
    assert provider(body)("x", limit=8) == ()


# ===================================================================== diagnosis


def test_health_distinguishes_a_challenge_from_a_markup_change():
    """Because the provider fails silently by design, and an empty result is otherwise
    indistinguishable from a part number nobody has published."""
    challenged = provider(CHALLENGE_PAGE).health()
    assert challenged["ok"] is False
    assert "challenge" in str(challenged["reason"])

    changed = provider("<html><body>nothing familiar</body></html>").health()
    assert changed["ok"] is False
    assert "markup" in str(changed["reason"])

    down = provider(OSError("no route to host")).health()
    assert down["ok"] is False
    assert "no response" in str(down["reason"])


def test_health_reports_success_with_a_sample():
    healthy = provider().health()

    assert healthy["ok"] is True
    assert healthy["results"] == 4
    assert healthy["sample"][0].endswith("avery-pendant-43911bk")


# ===================================================================== the resolver seam


def test_the_provider_satisfies_the_search_provider_protocol():
    """Structural, not nominal: `SearchProvider` is a Protocol, so this asserts the call shape the
    resolver actually uses rather than an inheritance claim."""
    from axiom.retrieve import Resolver, load_default

    resolver = Resolver(load_default(), search=provider())
    assert resolver.has_search


def test_the_open_query_names_the_manufacturer_even_when_no_domain_is_declared():
    """The reason the open arm exists at all.

    Its rows are exactly the ones with no declared domain, and the query used to degrade to a bare
    part number the moment it was needed most — "PDSH4816AF specifications" rather than "Frigidaire
    PDSH4816AF specifications".
    """
    from axiom.retrieve import Resolver, load_default

    seen: list[str] = []

    def recording(query: str, *, limit: int):
        seen.append(query)
        return []

    Resolver(load_default(), search=recording).resolve(
        "PDSH4816AF", vendor_name="Frigidaire"
    )

    assert seen, "the open arm did not run"
    assert "Frigidaire" in seen[-1]
    assert "PDSH4816AF" in seen[-1]


def test_a_declared_distributors_name_is_kept_out_of_the_query():
    """On those rows the vendor field names a buying co-op rather than a maker, so naming it would
    spend the query asking for the distributor's own listings — which the policy then refuses."""
    from axiom.retrieve import Resolver, load_default

    policy = load_default()
    seen: list[str] = []

    def recording(query: str, *, limit: int):
        seen.append(query)
        return []

    distributor = next(iter(policy.unresolved_distributors), None)
    if distributor is None:
        pytest.skip("the shipped policy declares no unresolved distributors")
    _code, name = distributor

    Resolver(policy, search=recording).resolve("X-1", vendor_name=name)

    assert seen
    assert name.lower() not in seen[-1].lower()
