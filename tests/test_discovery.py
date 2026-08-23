"""Discovery: getting from *part number plus manufacturer* to a datasheet, with no search API.

The chain under test is the one that makes minimal input sufficient:

    homepage -> read the site's OWN search form -> search for the part number
             -> rank links that actually contain it -> open the product page
             -> take the datasheet PDF it links to

Every step is exercised against a fixture site served by a stub fetcher, so the whole thing runs
offline. The fixture is deliberately awkward in the ways real sites are: the search form is not the
first form on the page, results include a near-miss part number and a marketplace link, and the
datasheet is named after the series rather than the part.
"""

from __future__ import annotations

import pytest
from axiom.docintel import extract_links, find_search_form
from axiom.ingest import LocalArtifactStore
from axiom.ingest.web import FetchedResource
from axiom.retrieve import (
    DocumentLibrary,
    RetrievalSession,
    RobotsCache,
    SiteDiscovery,
    SourcePolicy,
    rank_links,
)

POLICY_YAML = """
version: 1
policy:
  min_seconds_between_requests: 0
  respect_robots_txt: true
  max_candidates_per_sku: 3
excluded:
  - category: marketplace
    reason: Third-party sellers write their own listings.
    domains: [amazon.com]
spec_hints:
  paths: [/spec, /datasheet, /product]
  suffixes: [.pdf]
  negative_paths: [/cart, /where-to-buy]
manufacturers:
  - id: acme
    name: Acme Tool
    domains: [acmetool.example]
    vendor_codes: ["4031"]
    brands: [acme]
"""

# --- the fixture site ---------------------------------------------------------------
#
# A newsletter form comes first, so "the first form on the page" would submit the part number to a
# mailing list. The search form is the second, and declares `type="search"`.
HOMEPAGE = """
<html><head><base href="https://acmetool.example/"></head><body>
  <form action="/newsletter" method="post">
    <input type="email" name="email">
  </form>
  <form action="/catalogsearch/result/" method="get">
    <input type="search" name="term">
    <input type="hidden" name="scope" value="products">
  </form>
  <nav><a href="/products">All products</a></nav>
</body></html>
"""

# A near-miss part number, a marketplace link, and the real thing. Only one link carries 49-94-0013.
RESULTS = """
<html><body>
  <ul>
    <li><a href="/product/49-94-0014-cut-off-wheel">49-94-0014 Cut-Off Wheel 5 inch</a></li>
    <li><a href="https://www.amazon.com/dp/B000">49-94-0013 on Amazon</a></li>
    <li><a href="/product/49-94-0013-cut-off-wheel">
          <img src="x.png"></a></li>
    <li><a href="/product/49-94-0013-cut-off-wheel">49-94-0013 Metal Cut-Off Disc</a></li>
    <li><a href="/where-to-buy?sku=49-94-0013">Where to buy</a></li>
  </ul>
</body></html>
"""

# The datasheet is named for the series, not the part. Requiring the part number here would reject
# the very document being looked for.
PRODUCT_PAGE = """
<html><body>
  <h1>49-94-0013 Metal Cut-Off Disc</h1>
  <a href="/literature/cut-off-wheels-catalog.pdf">Specification Sheet (PDF)</a>
  <a href="/cart/add?sku=49-94-0013">Add to cart</a>
</body></html>
"""

DATASHEET = b"""\
ACME TOOL - CUT-OFF WHEEL CATALOG
Bonded Abrasive Cut-Off Wheels                                  Rev B  2025-04

ORDERING INFORMATION
  Part Number      Diameter   Arbor    Qty
  49-94-0013       5"         7/8"     25
  49-94-0014       6"         7/8"     25
"""

SITE = {
    "https://acmetool.example/": (HOMEPAGE.encode(), "text/html"),
    "https://acmetool.example/catalogsearch/result/?scope=products&term=49-94-0013": (
        RESULTS.encode(),
        "text/html",
    ),
    "https://acmetool.example/product/49-94-0013-cut-off-wheel": (
        PRODUCT_PAGE.encode(),
        "text/html",
    ),
    "https://acmetool.example/literature/cut-off-wheels-catalog.pdf": (
        DATASHEET,
        "text/plain",
    ),
}


class SiteFetcher:
    """Serves the fixture site and records every request, so budgets can be asserted."""

    def __init__(self, site: dict | None = None) -> None:
        self.site = site if site is not None else SITE
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        self.urls.append(url)
        from axiom.ingest.web import UrlFetchError

        if url not in self.site:
            raise UrlFetchError(f"{url} returned HTTP 404 Not Found")
        data, content_type = self.site[url]
        return FetchedResource(data=data, url=url, content_type=content_type)


@pytest.fixture
def policy(tmp_path) -> SourcePolicy:
    path = tmp_path / "sourcing.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return SourcePolicy.load(path)


@pytest.fixture
def rig(policy, tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    fetcher = SiteFetcher()
    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    discovery = SiteDiscovery(policy=policy, session=session, library=library)
    return discovery, library, fetcher, policy


# ------------------------------------------------------------------ reading the site's own form


def test_the_search_form_is_read_from_the_markup_not_guessed():
    """The site publishes its endpoint and its field name. Guessing either is how discovery 404s."""
    form = find_search_form(HOMEPAGE, "https://acmetool.example/")
    assert form is not None
    assert form.action == "https://acmetool.example/catalogsearch/result/"
    assert form.field == "term"
    assert form.method == "get"
    # Hidden inputs carried through: a catalogue search that loses its scope returns an empty page
    # rather than an error, which is the worst kind of failure to debug.
    assert form.hidden == (("scope", "products"),)


def test_a_newsletter_form_is_not_mistaken_for_a_search():
    """It comes first in the document and has a single text-ish input. "First form" would submit the
    part number to a mailing list."""
    form = find_search_form(HOMEPAGE, "https://acmetool.example/")
    assert form is not None and form.field != "email"


def test_a_post_only_search_is_reported_unusable_rather_than_coerced():
    """A POST search cannot be expressed as a URL, so it cannot be cited or re-run from an index.
    Submitting one we inferred would also be acting on the site rather than reading it."""
    html = '<form action="/find" method="post"><input type="search" name="q"></form>'
    assert find_search_form(html, "https://x.example/") is None


def test_the_search_url_is_built_from_the_form():
    form = find_search_form(HOMEPAGE, "https://acmetool.example/")
    assert form is not None
    assert form.url_for("49-94-0013") == (
        "https://acmetool.example/catalogsearch/result/?scope=products&term=49-94-0013"
    )


def test_a_base_href_reassigns_relative_links():
    """Ignoring one silently produces a page full of wrong URLs."""
    html = '<html><head><base href="https://cdn.example/x/"></head><body><a href="a.pdf">A</a>'
    links = extract_links(html, "https://acmetool.example/page")
    assert [link.url for link in links] == ["https://cdn.example/x/a.pdf"]


@pytest.mark.parametrize(
    "href",
    ["javascript:void(0)", "mailto:sales@x.example", "tel:+15551234", "data:text/html,x", "#top"],
)
def test_unfetchable_hrefs_are_dropped_at_extraction(href):
    """So no caller has to remember to."""
    assert extract_links(f'<a href="{href}">x</a>', "https://x.example/") == []


def test_duplicate_links_keep_the_more_descriptive_anchor():
    """A product is routinely linked twice, once from an image with no text and once from a caption.
    The caption is the half that says what it is."""
    links = extract_links(RESULTS, "https://acmetool.example/s")
    product = [
        link for link in links if link.url.endswith("/product/49-94-0013-cut-off-wheel")
    ]
    assert len(product) == 1
    assert product[0].text == "49-94-0013 Metal Cut-Off Disc"


# ------------------------------------------------------------------ ranking


def test_only_links_carrying_the_part_number_are_followed(policy):
    """The filter is exact, not plausible. A link qualifies by containing the part number."""
    links = extract_links(RESULTS, "https://acmetool.example/s")
    maker = policy.manufacturer_for(vendor_code="4031")
    ranked = rank_links(links, mpn="49-94-0013", policy=policy, domain_of=maker)

    urls = [link.url for link in ranked]
    assert "https://acmetool.example/product/49-94-0013-cut-off-wheel" in urls
    # The near-miss part number is not this part.
    assert not any("49-94-0014" in url for url in urls)
    # A marketplace link is refused even though it names the part.
    assert not any("amazon" in url for url in urls)


def test_a_where_to_buy_link_sorts_below_the_product_page(policy):
    """A manufacturer's own shop page is retail copy like any other."""
    links = extract_links(RESULTS, "https://acmetool.example/s")
    maker = policy.manufacturer_for(vendor_code="4031")
    ranked = rank_links(links, mpn="49-94-0013", policy=policy, domain_of=maker)
    assert ranked[0].url.endswith("/product/49-94-0013-cut-off-wheel")


def test_the_part_number_is_matched_across_punctuation(policy):
    """`49-94-0013` has to match a `49940013` slug: sites strip separators from URLs constantly."""
    html = '<a href="/p/49940013">Cut-off disc</a>'
    maker = policy.manufacturer_for(vendor_code="4031")
    ranked = rank_links(
        extract_links(html, "https://acmetool.example/s"),
        mpn="49-94-0013",
        policy=policy,
        domain_of=maker,
    )
    assert len(ranked) == 1


def test_discovery_never_leaves_the_manufacturers_domain(policy):
    """Scoping is stronger than refusing: a link farm on the manufacturer's own page cannot lead
    discovery anywhere at all."""
    html = '<a href="https://elsewhere.example/49-94-0013">49-94-0013 spec</a>'
    maker = policy.manufacturer_for(vendor_code="4031")
    assert (
        rank_links(
            extract_links(html, "https://acmetool.example/s"),
            mpn="49-94-0013",
            policy=policy,
            domain_of=maker,
        )
        == []
    )


# ------------------------------------------------------------------ the whole chain


def test_discovery_finds_the_datasheet_from_a_part_number_alone(rig):
    """The end-to-end claim: part number plus manufacturer, no URL, no search API, no key."""
    discovery, library, fetcher, policy = rig
    maker = policy.manufacturer_for(vendor_code="4031")

    found = discovery.discover("49-94-0013", maker)

    assert found.found, found.notes
    assert found.search_url.endswith("term=49-94-0013")
    # And the document it found actually covers the part, in an ordering row.
    coverage = library.coverage_for("49-94-0013")
    assert coverage and coverage[0].is_ordering_row
    assert coverage[0].entry.source_uri.endswith("cut-off-wheels-catalog.pdf")


def test_the_datasheet_is_followed_even_though_it_is_named_for_the_series(rig):
    """Manufacturers name literature after a family, not a size. Requiring the part number in the
    PDF's own URL would reject the document being looked for."""
    discovery, _library, fetcher, policy = rig
    discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))
    assert "https://acmetool.example/literature/cut-off-wheels-catalog.pdf" in fetcher.urls


def test_discovery_stays_within_its_page_budget(rig):
    """A courtesy budget, multiplied by every row in the batch.

    Counted in *pages that returned something*, which is what the budget guards. A 404 on a
    speculative ``/sitemap.xml`` costs the server almost nothing and must not consume the budget for
    the pages that matter.
    """
    discovery, _library, fetcher, policy = rig
    discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))
    delivered = [url for url in fetcher.urls if url in SITE]
    assert len(delivered) <= 4, delivered


def test_the_homepage_is_read_once_per_manufacturer_not_once_per_part(rig):
    """Most of the saving when a hundred rows share one maker."""
    discovery, _library, fetcher, policy = rig
    maker = policy.manufacturer_for(vendor_code="4031")

    discovery.discover("49-94-0013", maker)
    before = fetcher.urls.count("https://acmetool.example/")
    discovery.discover("49-94-0014", maker)

    assert before == 1
    assert fetcher.urls.count("https://acmetool.example/") == 1


def test_every_step_is_recorded_so_a_failure_is_explainable(rig):
    """"Discovery found nothing" is not actionable. Which stage failed is."""
    discovery, _library, _fetcher, policy = rig
    found = discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))
    stages = [step.stage for step in found.steps]
    assert stages[0] == "homepage"
    assert "search" in stages
    assert "candidate" in stages


def test_a_site_with_no_search_form_says_so_and_names_the_fix(policy, tmp_path):
    """Rather than guessing an endpoint, which is how discovery 404s and looks like a bug."""
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    fetcher = SiteFetcher({"https://acmetool.example/": (b"<html><body>no form</body></html>",
    "text/html")})
    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    discovery = SiteDiscovery(policy=policy, session=session, library=library)

    found = discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    assert not found.found
    assert any("no usable GET search form" in note for note in found.notes)
    assert any("patterns:" in note for note in found.notes), "the note names the fix"


def test_discovery_honours_robots_like_every_other_fetch(policy, tmp_path):
    """There is no second fetch path. Discovery goes through the same session as a supplied URL.

    ``robots.txt`` itself is exempt, which is not a loophole: you cannot know the rules without
    reading them, and Python's ``RobotFileParser`` permits that path under ``Disallow: /`` for
    exactly
    that reason. Nothing else is requested and nothing is stored.
    """
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    fetcher = SiteFetcher()
    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: "User-agent: *\nDisallow: /\n"),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    discovery = SiteDiscovery(policy=policy, session=session, library=library)

    found = discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    assert not found.found
    assert len(library) == 0, "nothing was stored"
    assert all(url.endswith("/robots.txt") for url in fetcher.urls), fetcher.urls


# ------------------------------------------------------------------ the sitemap path
#
# Site search turned out to be the weaker strategy on real sites. `milwaukeetool.com` publishes zero
# `<form>` elements and renders its search results with JavaScript, so an HTML-only reader finds no
# links at all. Sitemaps are published *for* machines, announced by robots.txt, plain XML, and
# canonical rather than ranked. Measured live: Milwaukee lists 12,523 URLs and all 108 of the
# client's Milwaukee part numbers match on the URL's last path segment.

ROBOTS_WITH_SITEMAP = """\
User-agent: *
Disallow: /cart/

Sitemap: https://www.acmetool.example/sitemap.xml
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.acmetool.example/sitemap-products.xml</loc></sitemap>
  <sitemap><loc>https://www.acmetool.example/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""

# Real shape: the part number is the last path segment. A category page also contains the digits,
# which is why a last-segment match and an anywhere match are not the same claim.
SITEMAP_PRODUCTS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acmetool.example/products/details/5-metal-cut-off-wheel/49-94-0013</loc></url>
  <url><loc>https://acmetool.example/products/details/6-metal-cut-off-wheel/49-94-0014</loc></url>
  <url><loc>https://acmetool.example/category/49-94-0013-accessories/</loc></url>
</urlset>
"""

SITEMAP_PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acmetool.example/about</loc></url>
</urlset>
"""

PRODUCT_FROM_SITEMAP = """
<html><body>
  <h1>49-94-0013 Metal Cut-Off Disc</h1>
  <a href="/literature/cut-off-wheels-catalog.pdf">Specification Sheet (PDF)</a>
</body></html>
"""

SITEMAP_SITE = {
    "https://www.acmetool.example/robots.txt": (ROBOTS_WITH_SITEMAP.encode(), "text/plain"),
    "https://www.acmetool.example/sitemap.xml": (SITEMAP_INDEX.encode(), "application/xml"),
    "https://www.acmetool.example/sitemap-products.xml": (
        SITEMAP_PRODUCTS.encode(),
        "application/xml",
    ),
    "https://www.acmetool.example/sitemap-pages.xml": (SITEMAP_PAGES.encode(), "application/xml"),
    "https://acmetool.example/products/details/5-metal-cut-off-wheel/49-94-0013": (
        PRODUCT_FROM_SITEMAP.encode(),
        "text/html",
    ),
    "https://acmetool.example/literature/cut-off-wheels-catalog.pdf": (DATASHEET, "text/plain"),
}


@pytest.fixture
def sitemap_rig(policy, tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    library = DocumentLibrary.load(store, tmp_path / "index.json")
    fetcher = SiteFetcher(SITEMAP_SITE)
    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    discovery = SiteDiscovery(policy=policy, session=session, library=library)
    return discovery, library, fetcher, policy


def test_the_sitemap_is_found_through_robots_txt(sitemap_rig):
    """Announced by the site, not guessed. Same principle as reading a published search form."""
    from axiom.retrieve.sitemap import SitemapReader

    _discovery, library, fetcher, policy = sitemap_rig
    session = RetrievalSession(
        policy=policy,
        store=LocalArtifactStore("."),
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    site = SitemapReader(session).for_domain("acmetool.example")

    assert site.available
    assert "https://www.acmetool.example/sitemap.xml" in site.sources
    # The index was followed into both children.
    assert len(site.urls) == 4


def test_a_last_segment_match_outranks_a_match_anywhere_in_the_url(sitemap_rig):
    """The site saying "this page *is* that part" is a stronger claim than a category page that
    happens to contain the digits."""
    from axiom.retrieve.sitemap import SitemapReader

    _discovery, library, fetcher, policy = sitemap_rig
    session = RetrievalSession(
        policy=policy,
        store=LocalArtifactStore("."),
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    hits = SitemapReader(session).for_domain("acmetool.example").find("49-94-0013")

    assert hits[0].is_exact
    assert hits[0].url.endswith("/49-94-0013")
    assert any(not hit.is_exact for hit in hits), "the category page is reported, not discarded"


def test_a_near_miss_part_number_is_not_matched(sitemap_rig):
    from axiom.retrieve.sitemap import SitemapReader

    _discovery, library, fetcher, policy = sitemap_rig
    session = RetrievalSession(
        policy=policy,
        store=LocalArtifactStore("."),
        library=library,
        fetcher=fetcher,
        robots=RobotsCache(reader=lambda _url: None),
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    hits = SitemapReader(session).for_domain("acmetool.example").find("49-94-0014")
    assert [hit.url for hit in hits] == [
        "https://acmetool.example/products/details/6-metal-cut-off-wheel/49-94-0014"
    ]


def test_discovery_reaches_the_datasheet_through_the_sitemap(sitemap_rig):
    """The end-to-end path that works on a JS-rendered site, where search returns no links."""
    discovery, library, _fetcher, policy = sitemap_rig

    found = discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    assert found.found, found.notes
    assert [step.stage for step in found.steps][0] == "sitemap"
    coverage = library.coverage_for("49-94-0013")
    assert coverage and coverage[0].is_ordering_row


def test_the_sitemap_is_read_once_per_manufacturer(sitemap_rig):
    """Where nearly all of the saving is: one request serves every row of that maker, rather than
    four pages per part."""
    discovery, _library, fetcher, policy = sitemap_rig
    maker = policy.manufacturer_for(vendor_code="4031")

    discovery.discover("49-94-0013", maker)
    discovery.discover("49-94-0014", maker)

    assert fetcher.urls.count("https://www.acmetool.example/sitemap.xml") == 1
    assert fetcher.urls.count("https://www.acmetool.example/robots.txt") == 1


def test_the_sitemap_is_never_stored_or_indexed(sitemap_rig):
    """The bug this separation exists to prevent, and it was found on a real run.

    A sitemap lists twelve thousand part numbers. Indexed as a document, `coverage_for` would find
    every one of them in it and record the sitemap as *covering* them — so a citation could name a
    sitemap as the source of a specification. Same for a search-results page. Navigation is fetched
    through `fetch_transient`, which stores nothing.
    """
    discovery, library, _fetcher, policy = sitemap_rig
    discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    stored = {entry.source_uri for entry in library.entries}
    assert not any("sitemap" in uri or "robots" in uri for uri in stored), stored
    # Only the product page and its datasheet were archived.
    assert stored == {
        "https://acmetool.example/products/details/5-metal-cut-off-wheel/49-94-0013",
        "https://acmetool.example/literature/cut-off-wheels-catalog.pdf",
    }


def test_a_search_results_page_is_never_stored_either(rig):
    """Same reasoning, on the other strategy: a results page listing forty products would otherwise
    be recorded as covering all forty."""
    discovery, library, _fetcher, policy = rig
    discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    stored = {entry.source_uri for entry in library.entries}
    assert not any("/catalogsearch/" in uri for uri in stored), stored
    assert not any(uri.rstrip("/") == "https://acmetool.example" for uri in stored), stored


def test_a_site_with_no_sitemap_falls_through_to_search(rig):
    """The `rig` fixture publishes no robots.txt and no sitemap. Discovery still works."""
    discovery, library, _fetcher, policy = rig
    found = discovery.discover("49-94-0013", policy.manufacturer_for(vendor_code="4031"))

    assert found.found, found.notes
    assert "search" in [step.stage for step in found.steps]
    assert library.coverage_for("49-94-0013")
