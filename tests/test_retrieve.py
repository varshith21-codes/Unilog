"""Retrieval: what may be read, what to read, and reading it once.

The three rules under test are the ones that make web retrieval safe to turn on at all:

1.  The manufacturer's own site is preferred and is the only tier citable as ``MFR URL``.
2.  Marketplaces, retailers, distributors and aggregators are refused **before** the request, so
    their bytes never enter the store and cannot be cited by accident.
3.  A document fetched once serves every part number it covers, and a part checked and not found is
    not checked again.

The domain-matching tests carry most of the weight. A substring check would pass a naive reading of
"exclude amazon.com" while classifying ``notamazon.com`` as a marketplace and letting
``amazon.com.evil.example`` through, and both directions are silent failures.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core.evidence import DocumentType, SourceDocument
from axiom.ingest import LocalArtifactStore, ingest_bytes
from axiom.ingest.web import FetchedResource
from axiom.retrieve import (
    DocumentLibrary,
    FetchStatus,
    Resolver,
    RetrievalSession,
    RobotsCache,
    SourcePolicy,
    SourceTier,
    host_of,
    load_default,
)
from axiom.retrieve.resolver import DISTRIBUTOR_ONLY, NO_MANUFACTURER, NO_PROVIDER

# A policy small enough to reason about, with the same shape as the real file.
POLICY_YAML = """
version: 1
policy:
  min_seconds_between_requests: 5.0
  respect_robots_txt: true
  max_candidates_per_sku: 3
excluded:
  - category: marketplace
    reason: Third-party sellers write their own listings.
    domains: [amazon.com, ebay.com]
  - category: industrial_distributor
    reason: Distributors syndicate each other's data.
    domains: [grainger.com]
spec_hints:
  paths: [/spec, /datasheet]
  suffixes: [.pdf]
  negative_paths: [/cart, /where-to-buy]
manufacturers:
  - id: milwaukee_tool
    name: Milwaukee Tool
    domains: [milwaukeetool.com]
    vendor_codes: ["4031"]
    vendors: ["milwaukee accessory"]
    brands: [milwaukee]
  - id: trex
    name: Trex Company
    domains: [trex.com]
    brands: [trex]
    patterns: ["https://www.trex.com/products/{mpn_lower}/spec"]
unresolved:
  distributors:
    - { code: BOICA, name: Boise Cascade Building Materials, rows: 85 }
"""


@pytest.fixture
def policy(tmp_path) -> SourcePolicy:
    path = tmp_path / "sourcing.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return SourcePolicy.load(path)


# ------------------------------------------------------------------ domain matching


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        # The exact host, and any subdomain of it.
        ("https://amazon.com/dp/1", SourceTier.EXCLUDED),
        ("https://www.amazon.com/dp/1", SourceTier.EXCLUDED),
        ("https://smile.amazon.com/dp/1", SourceTier.EXCLUDED),
        # A trailing dot is a legal fully-qualified form and resolves identically, so leaving it on
        # would let the rule be bypassed with one keystroke.
        ("https://AMAZON.COM./dp/1", SourceTier.EXCLUDED),
        ("https://www.grainger.com/product/1", SourceTier.EXCLUDED),
        # The two substring traps, in both directions.
        ("https://notamazon.com/dp/1", SourceTier.UNKNOWN),
        ("https://amazon.com.evil.example/dp/1", SourceTier.UNKNOWN),
        # Declared manufacturer domains.
        ("https://www.milwaukeetool.com/x", SourceTier.MANUFACTURER),
        ("https://milwaukeetool.com/", SourceTier.MANUFACTURER),
        # Neither. Fetchable, never promoted.
        ("https://www.nsf.org/standard/61", SourceTier.UNKNOWN),
    ],
)
def test_tier_is_decided_on_the_registrable_domain(policy, url, tier):
    assert policy.classify(url).tier is tier


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "example.com/no-scheme",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "//example.com/protocol-relative",
    ],
)
def test_anything_that_is_not_a_web_url_is_refused_rather_than_unknown(policy, url):
    """``unknown`` means *fetchable*, so garbage must not land there.

    A search result arriving as a fragment or a ``javascript:`` link would otherwise be handed to
    the fetcher on the grounds that nobody recognised its host.
    """
    verdict = policy.classify(url)
    assert verdict.tier is SourceTier.EXCLUDED
    assert verdict.category == "malformed"
    assert not verdict.fetchable


def test_only_a_manufacturer_tier_url_is_citable_as_the_manufacturers_own(policy):
    assert policy.classify("https://milwaukeetool.com/x").citable_as_manufacturer
    assert not policy.classify("https://www.nsf.org/x").citable_as_manufacturer
    assert not policy.classify("https://amazon.com/x").citable_as_manufacturer


def test_a_refusal_carries_the_reason_it_was_refused(policy):
    """A refusal a reviewer cannot explain to a supplier is one they will override."""
    verdict = policy.classify("https://www.grainger.com/product/1")
    assert verdict.category == "industrial_distributor"
    assert "syndicate" in verdict.reason
    assert verdict.matched_domain == "grainger.com"


def test_host_of_accepts_a_bare_host_as_well_as_a_url():
    assert host_of("https://www.example.com/a/b") == "www.example.com"
    assert host_of("www.example.com") == "www.example.com"
    assert host_of("https://EXAMPLE.com.") == "example.com"


# ------------------------------------------------------------------ manufacturer resolution


def test_vendor_code_resolves_the_manufacturer(policy):
    """The most reliable key: exact, and unaffected by how the name is spelled that day."""
    maker = policy.manufacturer_for(vendor_code="4031", vendor_name="Milwaukee Accessory")
    assert maker is not None
    assert maker.id == "milwaukee_tool"
    assert maker.primary_domain == "milwaukeetool.com"


def test_the_brand_resolves_a_distributor_row(policy):
    """The case that motivates keying on the brand at all.

    222 of the client's 1,000 rows name a distributor in ``Part_Manuf``. On those rows the brand
    column is the *only* thing that names the manufacturer, and without it the row is unresolvable.
    """
    maker = policy.manufacturer_for(
        vendor_name="Boise Cascade Building Materials", brand="TREX"
    )
    assert maker is not None and maker.id == "trex"


def test_vendor_names_match_despite_punctuation_and_spacing(policy):
    assert policy.manufacturer_for(vendor_name="milwaukee  ACCESSORY") is not None


def test_a_declared_distributor_is_reported_as_one(policy):
    """So the resolver can say "there is no datasheet here" rather than "we found nothing"."""
    assert policy.is_known_distributor(
        vendor_code="BOICA", vendor_name="Boise Cascade Building Materials"
    )
    assert not policy.is_known_distributor(vendor_code="4031", vendor_name="Milwaukee Accessory")


def test_spec_score_prefers_a_datasheet_over_a_shop_page(policy):
    assert policy.spec_score("https://x.example/datasheet/a.pdf") > policy.spec_score(
        "https://x.example/about"
    )
    # A manufacturer's own shop page is retail copy like any other, so it sorts below their
    # specification page.
    assert policy.spec_score("https://x.example/where-to-buy") < 0


# ------------------------------------------------------------------ resolution


def marketplace_heavy_search(query: str, *, limit: int):
    """A search provider whose top results are exactly what must not be fetched."""
    if query.startswith("site:"):
        return []
    return [
        "https://www.amazon.com/dp/B000",
        "https://www.grainger.com/product/x",
        "https://www.milwaukeetool.com/Products/49-94-0013/datasheet.pdf",
        "https://www.nsf.org/note",
    ]


def test_marketplaces_are_dropped_before_they_can_be_fetched(policy):
    resolver = Resolver(policy, search=marketplace_heavy_search)
    resolution = resolver.resolve("49-94-0013", vendor_code="4031")

    urls = [c.url for c in resolution.candidates]
    assert not any("amazon" in u or "grainger" in u for u in urls)
    # Recorded rather than silently discarded: "every result was a marketplace" and "we could not
    # think of anywhere to look" are different problems with different fixes.
    rejected = {v.category for v in resolution.rejected}
    assert rejected == {"marketplace", "industrial_distributor"}


def test_the_manufacturer_outranks_an_unknown_host(policy):
    resolver = Resolver(policy, search=marketplace_heavy_search)
    resolution = resolver.resolve("49-94-0013", vendor_code="4031")
    assert resolution.candidates[0].verdict.tier is SourceTier.MANUFACTURER


def test_a_supplied_url_outranks_everything_derived(policy):
    resolver = Resolver(policy, search=marketplace_heavy_search)
    resolution = resolver.resolve(
        "49-94-0013",
        vendor_code="4031",
        supplied=["https://www.nsf.org/supplied-by-a-human"],
    )
    top = resolution.candidates[0]
    assert top.origin == "supplied"
    # Even though it is only an unknown-tier host. Somebody knew the answer.
    assert top.verdict.tier is SourceTier.UNKNOWN


def test_a_supplied_marketplace_url_is_still_refused(policy):
    """The policy is not a default that a caller can opt out of by passing a URL."""
    resolver = Resolver(policy)
    resolution = resolver.resolve("x", supplied=["https://www.amazon.com/dp/1"])
    assert not resolution.candidates
    assert resolution.rejected[0].category == "marketplace"


def test_a_declared_pattern_needs_no_search_provider(policy):
    """The deterministic arm. Only present where somebody established the URL shape."""
    resolver = Resolver(policy)
    resolution = resolver.resolve("Trex-Enhance", brand="TREX")
    assert [c.url for c in resolution.candidates] == [
        "https://www.trex.com/products/trex-enhance/spec"
    ]
    assert resolution.candidates[0].origin == "pattern"


def test_without_a_search_provider_the_absence_is_reported(policy):
    """Rather than an empty list that looks like "there is nothing out there"."""
    resolver = Resolver(policy)
    resolution = resolver.resolve("49-94-0013", vendor_code="4031")
    assert not resolution.resolved
    assert NO_PROVIDER in resolution.notes
    assert not resolver.has_search


def test_an_unresolvable_vendor_says_so(policy):
    resolver = Resolver(policy)
    resolution = resolver.resolve("X-1", vendor_name="Someone Nobody Declared")
    assert NO_MANUFACTURER in resolution.notes


def test_a_distributor_row_is_distinguished_from_an_unknown_one(policy):
    """Different problems: one needs a brand master, the other needs a line in sourcing.yaml."""
    resolver = Resolver(policy)
    resolution = resolver.resolve(
        "X-1", vendor_code="BOICA", vendor_name="Boise Cascade Building Materials"
    )
    assert DISTRIBUTOR_ONLY in resolution.notes
    assert NO_MANUFACTURER not in resolution.notes


def test_the_candidate_budget_is_honoured(policy):
    resolver = Resolver(policy, search=marketplace_heavy_search)
    resolution = resolver.resolve(
        "x",
        vendor_code="4031",
        supplied=[f"https://www.nsf.org/{n}" for n in range(10)],
    )
    assert len(resolution.candidates) == policy.max_candidates_per_sku == 3


def test_the_site_restricted_query_runs_before_the_open_one(policy):
    seen: list[str] = []

    def recording_search(query: str, *, limit: int):
        seen.append(query)
        return ["https://www.milwaukeetool.com/x"] if query.startswith("site:") else []

    Resolver(policy, search=recording_search).resolve("49-94-0013", vendor_code="4031")
    assert seen[0] == "site:milwaukeetool.com 49-94-0013"
    # Candidate discovery cannot know whether that URL actually covers the SKU. The open query is
    # retained so downstream coverage failure still has somewhere authoritative to recover from.
    assert len(seen) == 2
    assert seen[1] == "Milwaukee Tool 49-94-0013 specifications datasheet"


def test_the_candidate_budget_reserves_an_open_web_recovery_slot(policy):
    def crowded_search(query: str, *, limit: int):
        if query.startswith("site:"):
            return [f"https://milwaukeetool.com/product/{n}" for n in range(8)]
        return ["https://www.nsf.org/datasheet/recovery.pdf"]

    resolution = Resolver(policy, search=crowded_search).resolve(
        "49-94-0013", vendor_code="4031", limit=3
    )

    assert len(resolution.candidates) == 3
    assert resolution.candidates[-1].origin == "open_search"
    assert any("recovery.pdf" in candidate.url for candidate in resolution.candidates)


def test_open_search_never_evicts_supplied_candidates(policy):
    supplied = [f"https://milwaukeetool.com/known/{n}" for n in range(3)]

    def search(query: str, *, limit: int):
        if query.startswith("site:"):
            return ["https://milwaukeetool.com/product/search-result"]
        return ["https://www.nsf.org/datasheet/recovery.pdf"]

    resolution = Resolver(policy, search=search).resolve(
        "49-94-0013",
        vendor_code="4031",
        supplied=supplied,
        limit=3,
    )

    assert [candidate.url for candidate in resolution.candidates] == supplied
    assert all(candidate.origin == "supplied" for candidate in resolution.candidates)


def test_open_search_replaces_only_site_search_and_preserves_patterns(policy):
    def crowded_search(query: str, *, limit: int):
        if query.startswith("site:"):
            return [f"https://trex.com/product/{n}" for n in range(4)]
        return ["https://www.nsf.org/datasheet/trex-recovery.pdf"]

    resolution = Resolver(policy, search=crowded_search).resolve(
        "TX-1",
        brand="trex",
        limit=3,
    )

    assert [candidate.origin for candidate in resolution.candidates] == [
        "pattern",
        "site_search",
        "open_search",
    ]
    assert resolution.candidates[0].url == "https://www.trex.com/products/tx-1/spec"


def test_open_search_fills_the_budget_when_only_one_preferred_candidate_exists(policy):
    def open_search(query: str, *, limit: int):
        return [f"https://www.nsf.org/datasheet/{n}.pdf" for n in range(5)]

    resolution = Resolver(policy, search=open_search).resolve(
        "SKU-9",
        supplied=["https://milwaukeetool.com/known/sku-9"],
        limit=3,
    )

    assert len(resolution.candidates) == 3
    assert resolution.candidates[0].origin == "supplied"
    assert [candidate.origin for candidate in resolution.candidates[1:]] == [
        "open_search",
        "open_search",
    ]


def test_an_explicit_zero_candidate_budget_is_zero(policy):
    resolution = Resolver(policy, search=marketplace_heavy_search).resolve(
        "49-94-0013", vendor_code="4031", limit=0
    )
    assert resolution.candidates == ()


def test_a_search_provider_failure_is_reported_without_aborting_resolution(policy):
    def broken_search(query: str, *, limit: int):
        raise TimeoutError("provider timed out")

    resolution = Resolver(policy, search=broken_search).resolve(
        "49-94-0013", vendor_code="4031"
    )

    assert resolution.candidates == ()
    assert len(resolution.queries) == 2
    assert any("provider timed out" in note for note in resolution.notes)


# ------------------------------------------------------------------ the library


DATASHEET = b"""\
ACME CATALOGUE - AB SERIES

ORDERING INFORMATION
  Part Number      Size     Qty
  AB-100-025       1/4"     24
  AB-100-050       1/2"     24
  AB-100-075       3/4"     12

  NOTE 1: Catalog No AB-100-999 is discontinued and superseded by AB-100-075.
          Do not order.
"""


@pytest.fixture
def library(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    lib = DocumentLibrary.load(store, tmp_path / "index.json")
    artifact = ingest_bytes(DATASHEET, store, filename="ab100.txt")
    lib.register(artifact, host="acme.example", tier=SourceTier.MANUFACTURER.value)
    return lib


def test_one_document_covers_every_part_it_lists(library):
    """Rule 3, and the reason the index exists at all.

    A thousand-row batch against a catalogue that lists a hundred parts per file should make one
    request, not a hundred. The store already made re-fetching identical bytes free; it could not
    answer "do we already have something covering this part?", which is the expensive question.
    """
    for mpn in ("AB-100-025", "AB-100-050", "AB-100-075"):
        coverage = library.coverage_for(mpn)
        assert coverage, mpn
        # Found in an ordering row, which is the strong signal: the document *offers* the part
        # rather than merely mentioning it.
        assert coverage[0].is_ordering_row


def test_a_part_the_document_withdraws_is_not_coverage(library):
    """The distinction that stops enrichment from a note that kills the product.

    An absent part means the wrong file was attached: find the right document. A withdrawn one
    means the file is right and the *part* is dead. Collapsing them would send someone hunting for
    a datasheet that does not exist, and would enrich a discontinued part from its own obituary.
    """
    assert library.coverage_for("AB-100-999") == []
    entry = library.entries[0]
    assert "AB-100-999" in entry.withdrawn
    assert "discontinued" in entry.withdrawn["AB-100-999"]
    assert "AB-100-999" not in entry.covers


def test_a_part_that_is_absent_is_remembered_as_absent(library):
    """A negative result is a result, and re-parsing a 200-page PDF to re-learn it is waste."""
    assert library.coverage_for("NOT-IN-HERE") == []
    entry = library.entries[0]
    assert "NOT-IN-HERE" in entry.absent
    assert "NOT-IN-HERE" not in entry.covers


def test_a_part_number_too_short_to_search_is_neither_present_nor_absent(library):
    """``find_sku`` refuses to look below four characters, and caching that as absent would be a
    false negative on every future run."""
    assert library.coverage_for("AB") == []
    assert "AB" not in library.entries[0].absent


def test_registration_is_idempotent_on_the_hash(library, tmp_path):
    """Two rows resolving to the same PDF cost one entry, and coverage already learned survives."""
    store = LocalArtifactStore(tmp_path / "store")
    library.coverage_for("AB-100-025")
    before = len(library)

    again = ingest_bytes(DATASHEET, store, filename="renamed.txt")
    entry = library.register(again, host="other.example", tier=SourceTier.UNKNOWN.value)

    assert len(library) == before
    # The bytes have not changed, so what is inside them has not either.
    assert "AB-100-025" in entry.covers
    assert entry.host == "acme.example"


def test_the_index_round_trips(library, tmp_path):
    library.coverage_for("AB-100-050")
    library.coverage_for("NOT-IN-HERE")
    path = library.save()

    reloaded = DocumentLibrary.load(LocalArtifactStore(tmp_path / "store"), path)
    assert len(reloaded) == 1
    entry = reloaded.entries[0]
    assert entry.covers["AB-100-050"] == "table"
    assert "NOT-IN-HERE" in entry.absent
    assert entry.tier == SourceTier.MANUFACTURER.value


def test_a_corrupt_index_rebuilds_rather_than_refusing_to_start(tmp_path):
    """Derived data. Losing it costs re-parsing, not correctness."""
    path = tmp_path / "index.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(DocumentLibrary.load(LocalArtifactStore(tmp_path / "store"), path)) == 0


def test_stats_separate_ordering_rows_from_mentions(library):
    library.coverage_for("AB-100-025")
    library.coverage_for("NOT-IN-HERE")
    library.coverage_for("AB-100-999")
    stats = library.stats()
    assert stats["documents"] == 1
    assert stats["skus_covered"] == 1
    assert stats["skus_in_an_ordering_row"] == 1
    assert stats["skus_checked_absent"] == 1
    assert stats["skus_withdrawn"] == 1


# ------------------------------------------------------------------ the fetch session


class RecordingFetcher:
    """Stands in for the network, and records every URL it was actually asked for.

    The assertion that matters most in this file is a *negative* one — that a refused URL never
    reaches here — and that cannot be checked by looking at the outcome alone.
    """

    def __init__(self, body: bytes = b"spec sheet body") -> None:
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        self.urls.append(url)
        return FetchedResource(data=self.body, url=url, content_type="text/plain")


def session_for(
    policy,
    tmp_path,
    fetcher,
    *,
    robots_text: str | None = None,
    max_requests: int | None = None,
    renderer=None,
):
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    clock = {"now": 0.0}
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: robots_text),
        sleep=sleep,
        clock=lambda: clock["now"],
        max_requests=max_requests,
        renderer=renderer,
    )
    return session, library, slept, clock


def candidate_for(policy, url: str, origin: str = "supplied"):
    from axiom.retrieve.resolver import Candidate

    verdict = policy.classify(url)
    return Candidate(url=url, verdict=verdict, origin=origin, rank=1.0)


def test_a_refused_url_never_reaches_the_network(policy, tmp_path):
    """The gate runs before the request, which is what keeps the bytes out of the store entirely.

    Filtering the output instead would leave a laundered citation one bug away.
    """
    fetcher = RecordingFetcher()
    session, library, _slept, _clock = session_for(policy, tmp_path, fetcher)

    outcome = session.fetch(candidate_for(policy, "https://www.amazon.com/dp/1"))

    assert outcome.status is FetchStatus.REFUSED_POLICY
    assert fetcher.urls == []
    assert len(library) == 0
    assert session.requests_made == 0


def test_a_permitted_url_is_fetched_stored_and_indexed(policy, tmp_path):
    fetcher = RecordingFetcher()
    session, library, _slept, _clock = session_for(policy, tmp_path, fetcher)

    outcome = session.fetch(candidate_for(policy, "https://milwaukeetool.com/spec.txt"))

    assert outcome.status is FetchStatus.FETCHED
    assert outcome.entry is not None
    assert len(library) == 1
    assert library.entries[0].tier == SourceTier.MANUFACTURER.value
    # The URL, not the store path. This is the field that makes a web citation resolvable.
    assert library.entries[0].source_uri == "https://milwaukeetool.com/spec.txt"


def test_the_session_request_ceiling_blocks_before_the_network(policy, tmp_path):
    fetcher = RecordingFetcher()
    session, _library, _slept, _clock = session_for(
        policy, tmp_path, fetcher, max_requests=1
    )

    first = session.fetch(candidate_for(policy, "https://milwaukeetool.com/first.txt"))
    second = session.fetch(candidate_for(policy, "https://milwaukeetool.com/second.txt"))

    assert first.status is FetchStatus.FETCHED
    assert second.status is FetchStatus.OVER_BUDGET
    assert fetcher.urls == ["https://milwaukeetool.com/first.txt"]
    assert session.requests_made == 1


def test_a_url_already_in_the_library_is_not_requested_again(policy, tmp_path):
    fetcher = RecordingFetcher()
    session, _library, _slept, _clock = session_for(policy, tmp_path, fetcher)
    url = "https://milwaukeetool.com/spec.txt"

    session.fetch(candidate_for(policy, url))
    second = session.fetch(candidate_for(policy, url))

    assert second.status is FetchStatus.REUSED
    assert second.usable
    assert fetcher.urls == [url], "the second fetch made no request"


def test_robots_txt_is_honoured(policy, tmp_path):
    """We disclose an honest User-Agent. Ignoring the file that says what we may read would make
    the disclosure worse than useless."""
    fetcher = RecordingFetcher()
    session, _library, _slept, _clock = session_for(
        policy, tmp_path, fetcher, robots_text="User-agent: *\nDisallow: /private/\n"
    )

    blocked = session.fetch(candidate_for(policy, "https://milwaukeetool.com/private/x.pdf"))
    allowed = session.fetch(candidate_for(policy, "https://milwaukeetool.com/public/x.pdf"))

    assert blocked.status is FetchStatus.REFUSED_ROBOTS
    assert allowed.status is FetchStatus.FETCHED
    assert fetcher.urls == ["https://milwaukeetool.com/public/x.pdf"]


def test_no_robots_file_permits_the_fetch(policy, tmp_path):
    """Absence of a policy is not a prohibition, and failing closed would make an unrelated 500
    look like a refusal to be read."""
    fetcher = RecordingFetcher()
    session, _library, _slept, _clock = session_for(policy, tmp_path, fetcher, robots_text=None)
    assert session.fetch(candidate_for(policy, "https://milwaukeetool.com/x")).status is (
        FetchStatus.FETCHED
    )


def test_requests_to_one_origin_are_spaced(policy, tmp_path):
    """A thousand-row batch against one manufacturer is a thousand requests to one origin."""
    fetcher = RecordingFetcher()
    session, _library, slept, _clock = session_for(policy, tmp_path, fetcher)

    session.fetch(candidate_for(policy, "https://milwaukeetool.com/a.txt"))
    session.fetch(candidate_for(policy, "https://milwaukeetool.com/b.txt"))

    assert slept == [pytest.approx(5.0)], "the configured gap was waited out"


def test_a_different_origin_is_not_made_to_wait(policy, tmp_path):
    """The courtesy is owed per server, not globally."""
    fetcher = RecordingFetcher()
    session, _library, slept, _clock = session_for(policy, tmp_path, fetcher)

    session.fetch(candidate_for(policy, "https://milwaukeetool.com/a.txt"))
    session.fetch(candidate_for(policy, "https://trex.com/b.txt"))

    assert slept == []


def test_a_longer_crawl_delay_is_honoured_over_our_own_floor(policy, tmp_path):
    fetcher = RecordingFetcher()
    session, _library, slept, _clock = session_for(
        policy, tmp_path, fetcher, robots_text="User-agent: *\nCrawl-delay: 20\n"
    )

    session.fetch(candidate_for(policy, "https://milwaukeetool.com/a.txt"))
    session.fetch(candidate_for(policy, "https://milwaukeetool.com/b.txt"))

    assert slept == [pytest.approx(20.0)]


def test_a_failed_fetch_is_reported_not_raised(policy, tmp_path):
    """A batch must survive one bad URL."""

    def failing(url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        from axiom.ingest.web import UrlFetchError

        raise UrlFetchError(f"{url} returned HTTP 404 Not Found")

    session, library, _slept, _clock = session_for(policy, tmp_path, failing)
    outcome = session.fetch(candidate_for(policy, "https://milwaukeetool.com/missing.pdf"))

    assert outcome.status is FetchStatus.FAILED
    assert "404" in outcome.detail
    assert len(library) == 0


def test_a_fetched_document_records_where_it_came_from_for_the_certificate(policy, tmp_path):
    """Crawled manufacturer content carries terms a supplier-sent PDF does not, and the certificate
    is where an auditor looks for them."""
    session, library, _slept, _clock = session_for(policy, tmp_path, RecordingFetcher())
    session.fetch(candidate_for(policy, "https://milwaukeetool.com/x.txt"))

    note = library.entries[0].license_note or ""
    assert "milwaukeetool.com" in note
    assert "manufacturer" in note


# ------------------------------------------------------------------ the shipped policy


def test_the_shipped_policy_loads_and_refuses_the_obvious_cases():
    """A smoke test over ``schema/sourcing.yaml`` itself, not a fixture.

    The file is the deliverable here — the code is generic and the domain knowledge is the asset —
    so a typo that emptied a category would otherwise pass every test above.
    """
    policy = load_default()
    assert len(policy.manufacturers) > 30
    assert {group.category for group in policy.excluded} >= {
        "marketplace",
        "mass_retail",
        "industrial_distributor",
        "aggregator",
    }
    for url in (
        "https://www.amazon.com/dp/1",
        "https://www.homedepot.com/p/1",
        "https://www.grainger.com/product/1",
        "https://octopart.com/x",
        "https://www.reddit.com/r/tools",
    ):
        assert policy.classify(url).tier is SourceTier.EXCLUDED, url


def test_the_shipped_policy_keeps_the_two_milwaukees_apart():
    """`Milwaukee Accessory` is Milwaukee Tool; `brands.yaml` also aliases "milwaukee" to Milwaukee
    Valve, a different company. 108 rows of cut-off wheels resolving to a valve maker would fetch a
    valve catalogue and extract pressure ratings for an abrasive disc."""
    policy = load_default()
    maker = policy.manufacturer_for(vendor_code="4031", vendor_name="Milwaukee Accessory")
    assert maker is not None
    assert maker.primary_domain == "milwaukeetool.com"


def test_the_shipped_policy_resolves_the_distributor_rows_through_the_brand():
    """The single highest-value key in the file: 177 rows reach a manufacturer only this way."""
    policy = load_default()
    for vendor, brand, expected in [
        ("Parksite", "TREX", "trex.com"),
        ("Boise Cascade Building Materials", "TIMBERTECH", "timbertech.com"),
        ("U S Lumber", "TREX", "trex.com"),
    ]:
        maker = policy.manufacturer_for(vendor_name=vendor, brand=brand)
        assert maker is not None, (vendor, brand)
        assert maker.primary_domain == expected


VERIFIED_PATTERN_OWNERS = {
    # Checked against real part numbers from the item master, not inferred from one URL's shape:
    # satco.com addresses product pages by the bare order code. See schema/sourcing.yaml for the
    # standing note that the site answers automated requests with HTTP 429.
    "satco",
}
"""Manufacturers permitted to declare a URL pattern, because someone verified theirs.

An allowlist rather than a free-for-all, and the point is the *edit*: adding an id here is the
checkpoint where "did you check this against a real part?" gets asked. A test that merely validated
the shape of any pattern would pass a well-formed guess, which is the failure mode this guards.
"""


def test_only_verified_manufacturers_declare_a_url_pattern():
    """Establishing a site's URL shape means checking it against a real part. A guessed template
    produces a confident 404 on every row of that brand, and the failure then looks like a
    retrieval bug rather than the missing fact it is.

    This used to assert that *no* manufacturer declared a pattern, which was the right default while
    none had been verified. It is now an allowlist, so the discipline survives the first entry
    instead of being deleted along with the assertion.
    """
    for maker in load_default().manufacturers:
        if maker.id in VERIFIED_PATTERN_OWNERS:
            continue
        assert maker.patterns == (), (
            f"{maker.id} declares a URL pattern but is not in VERIFIED_PATTERN_OWNERS. Check the "
            f"template against a real part number from the item master, then add the id there."
        )


def test_a_declared_pattern_is_per_part_and_on_the_manufacturers_own_domain():
    """The two ways a verified-looking pattern can still be wrong.

    Without ``{mpn}`` the template resolves every part of a brand to one page, so the first row
    fetched would supply values for all of them — a wrong-row error at brand scale. And a pattern
    pointing somewhere other than the manufacturer's own declared domains would launder a
    distributor page into a manufacturer-tier citation, which is the whole thing
    ``schema/sourcing.yaml`` refuses e-commerce for.
    """
    for maker in load_default().manufacturers:
        for pattern in maker.patterns:
            assert "{mpn" in pattern, (maker.id, pattern)
            assert pattern.startswith("https://"), (maker.id, pattern)
            assert any(domain in pattern for domain in maker.domains), (maker.id, pattern)


def test_every_shipped_manufacturer_has_at_least_one_domain_and_a_key():
    """An entry with no domain resolves to nothing; one with no key is unreachable. Either is a
    silently dead line in the file."""
    for maker in load_default().manufacturers:
        assert maker.domains, maker.id
        assert maker.vendor_codes or maker.vendors or maker.brands, maker.id


def test_a_stored_document_keeps_its_identity_across_a_reload(tmp_path):
    """Citations already written name ``document_id``. Re-deriving it on read would mint a new
    identity for the same bytes and orphan them."""
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    artifact = ingest_bytes(DATASHEET, store, filename="ab100.txt")
    entry = library.register(artifact)
    original_id = entry.document_id
    path = library.save()

    reloaded = DocumentLibrary.load(store, path)
    parsed = reloaded.parsed(reloaded.entries[0])
    assert parsed is not None
    assert parsed.document.document_id == original_id
    assert parsed.document.sha256 == artifact.sha256


def test_a_missing_artifact_degrades_rather_than_failing_the_run(tmp_path):
    """The index is derived and the store is the truth. A document whose bytes are gone
    contributes nothing to this run; it does not invalidate it."""
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary(store, tmp_path / "index.json")
    document = SourceDocument(
        document_id="ghost@00000000",
        uri="local://00/00/" + "0" * 64 + ".txt",
        sha256="0" * 64,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime.now(UTC),
    )
    from axiom.ingest.fabric import IngestedArtifact

    library.register(
        IngestedArtifact(
            document=document,
            storage_uri=document.uri,
            size_bytes=1,
            original_filename="ghost.txt",
            was_already_stored=False,
        )
    )
    assert library.parsed(library.entries[0]) is None
    assert library.coverage_for("AB-100-025") == []


# ------------------------------------------------------------------ classifying from a document
#
# The step that makes *part number plus manufacturer* a usable input on its own. With no description
# in the row there is nothing to classify, so the row abstains, so no class is established, so no
# attributes are requested — and the retrieved datasheet is never read. One blank field silently
# disabled the whole retrieval chain.


def test_the_title_block_is_the_opening_lines_of_a_parsed_document(parsed_datasheet):
    from axiom.docintel import title_block

    block = title_block(parsed_datasheet, max_lines=2)
    assert block.splitlines() == [
        "MILWAUKEE VALVE - BA-100 SERIES",
        "Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08",
    ]


def test_the_title_block_skips_blank_lines_rather_than_counting_them(parsed_datasheet):
    from axiom.docintel import title_block

    lines = title_block(parsed_datasheet, max_lines=4).splitlines()
    assert len(lines) == 4
    assert all(line.strip() for line in lines)


def test_a_title_block_classifies_where_the_full_document_is_ambiguous(parsed_datasheet):
    """The measurement that justifies reading a header instead of the whole file.

    A bronze ball valve's datasheet discusses bronze, NPT threads, WSP steam ratings and temperature
    derating — all of which a bronze *gate* valve also has. So the two classes score close together
    and the dominance test correctly refuses to choose. The title block names the product and
    nothing else, and decides it.

    If this test ever fails because the full text became decisive, that is fine and the fallback is
    simply no longer load-bearing. If it fails because the title block stopped deciding, retrieval
    can no longer classify a row that has no description, and that is a regression worth a failure.
    """
    from axiom.classify import Classifier
    from axiom.docintel import title_block
    from axiom.schema import load_default as load_schema

    classifier = Classifier(load_schema())

    whole = classifier.classify(parsed_datasheet.full_text, sku="BA-100-075")
    assert whole.class_code is None, "the full document is ambiguous to retrieval alone"
    assert "ambiguous" in whole.method

    header = classifier.classify(title_block(parsed_datasheet), sku="BA-100-075")
    assert header.class_code == "PLB.VLV.BALL.2PC"
    assert header.method == "retrieval_decisive"


def test_the_ambiguous_verdict_still_ranks_the_right_class_first(parsed_datasheet):
    """Abstention here is a margin judgement, not a failure to understand the document.

    Worth pinning separately: it is what makes the title-block fallback a reasonable response rather
    than a workaround for a broken classifier.
    """
    from axiom.classify import Classifier
    from axiom.schema import load_default as load_schema

    result = Classifier(load_schema()).classify(parsed_datasheet.full_text, sku="BA-100-075")
    assert result.candidates[0].code == "PLB.VLV.BALL.2PC"
