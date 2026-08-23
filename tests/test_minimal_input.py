"""The end-to-end claim: a part number and a manufacturer name, and nothing else, become cited data.

Every other test in the retrieval suite checks one link in the chain. This one checks that the chain
joins up, because each link worked in isolation for a while and the whole thing still produced
nothing
— a blank description made classification abstain, so no attributes were requested, so a perfectly
good retrieved datasheet went unread.

The input here is deliberately the minimum: a part number and a vendor code. No description, no URL,
no brand, no search API. What has to happen for this to pass:

1.  the vendor code resolves to a manufacturer domain
2.  discovery reads that site's own search form and searches it for the part number
3.  a result link carrying the part number is opened, and its datasheet PDF followed
4.  the library records that the document covers this part, from an ordering row
5.  the class is established from the *document's* title block, since the row has no description
6.  extraction reads specification lines and table cells and cites each one
7.  every value carries a quote that verifies against the stored bytes

Run against a fixture site served by a stub fetcher, so it is offline and deterministic.
"""

from __future__ import annotations

import pytest
from axiom.classify import Classifier
from axiom.delivery.batch import extract_from_documents
from axiom.delivery.source import SupplierRow
from axiom.docintel import title_block
from axiom.ingest import LocalArtifactStore
from axiom.ingest.web import FetchedResource
from axiom.retrieve import (
    DocumentLibrary,
    RetrievalSession,
    RobotsCache,
    SiteDiscovery,
    SourcePolicy,
)
from axiom.schema import load_default as load_schema

POLICY_YAML = """
version: 1
policy:
  min_seconds_between_requests: 0
  respect_robots_txt: true
excluded:
  - category: marketplace
    reason: Third-party sellers write their own listings.
    domains: [amazon.com]
spec_hints:
  paths: [/spec, /datasheet, /product, /literature]
  suffixes: [.pdf]
  negative_paths: [/cart, /where-to-buy]
manufacturers:
  - id: milwaukee_valve
    name: Milwaukee Valve
    domains: [milwaukeevalve.example]
    vendor_codes: [MILVA]
    vendors: ["milwaukee valve"]
"""

HOMEPAGE = """
<html><body>
  <form action="/search" method="get"><input type="search" name="q"></form>
</body></html>
"""

RESULTS = """
<html><body>
  <a href="https://www.amazon.com/dp/B01">BA-100-075 on Amazon</a>
  <a href="/product/ba-100-075">BA-100-075 Two-Piece Bronze Ball Valve</a>
</body></html>
"""

PRODUCT = """
<html><body>
  <h1>BA-100-075</h1>
  <a href="/literature/ba-100-series.pdf">BA-100 Series Specification Sheet</a>
</body></html>
"""

# The same fixture datasheet the rest of the suite uses, so the values below are the ones a real
# deterministic extraction produces rather than numbers chosen to make a test pass.
DATASHEET = b"""\
MILWAUKEE VALVE - BA-100 SERIES
Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08

SPECIFICATIONS
  Body Material .................. Bronze C84400
  Seat Material .................. RPTFE
  Pressure Rating ................ 600 PSI WOG @ 73 degF
  End Connection ................. NPT threaded, female both ends
  Approvals ...................... UL listed, CSA certified, NSF/ANSI 61

ORDERING INFORMATION
  Part Number      Size        Handle       Carton Qty
  BA-100-050       1/2"        Lever        24
  BA-100-075       3/4"        Lever        12
"""

SITE = {
    "https://milwaukeevalve.example/": (HOMEPAGE.encode(), "text/html"),
    "https://milwaukeevalve.example/search?q=BA-100-075": (RESULTS.encode(), "text/html"),
    "https://milwaukeevalve.example/product/ba-100-075": (PRODUCT.encode(), "text/html"),
    "https://milwaukeevalve.example/literature/ba-100-series.pdf": (DATASHEET, "text/plain"),
}


class SiteFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        self.urls.append(url)
        from axiom.ingest.web import UrlFetchError

        if url not in SITE:
            raise UrlFetchError(f"{url} returned HTTP 404 Not Found")
        data, content_type = SITE[url]
        return FetchedResource(data=data, url=url, content_type=content_type)


# The whole input. Two fields carry information; the rest are the sentinels a real item
# master sends.
MINIMAL_ROW = {
    "Mfg_Part_Num": "BA-100-075",
    "Part_Desc": "",
    "E1_Brand": "-- Unbranded --",
    "Unilog_Brand": "-- No Unilog Brand --",
    "DIB_Brand": "-- No DIB Brand --",
    "Part_Manuf": "Milwaukee Valve (MILVA)",
}


@pytest.fixture
def enriched(tmp_path):
    """Drive the real chain over the minimal row and return everything worth asserting on."""
    policy_path = tmp_path / "sourcing.yaml"
    policy_path.write_text(POLICY_YAML, encoding="utf-8")
    policy = SourcePolicy.load(policy_path)

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

    row = SupplierRow.parse(dict(MINIMAL_ROW))
    assert row.description is None, "the fixture must genuinely carry no description"

    # 1. identity -> manufacturer
    maker = policy.manufacturer_for(
        vendor_code=row.manufacturer.supplier_code,
        vendor_name=row.manufacturer.name,
        brand=row.brand.brand,
    )
    assert maker is not None

    # 2-4. discovery -> a covering document
    discovery = SiteDiscovery(policy=policy, session=session, library=library)
    found = discovery.discover(row.mpn or "", maker)
    coverage = library.coverage_for(row.mpn or "")

    # 5. the class, from the document because the row cannot supply it
    registry = load_schema()
    classifier = Classifier(registry)
    parsed = library.parsed(coverage[0].entry) if coverage else None
    classification = (
        classifier.classify(title_block(parsed), sku=row.mpn) if parsed is not None else None
    )

    # 6. extraction, deterministic and cited
    from axiom.core.product import ProductRecord

    record = ProductRecord(
        tenant_id="test",
        sku=row.mpn or "",
        mpn=row.mpn,
        class_code=classification.class_code if classification else None,
    )
    added = refused = 0
    if classification and classification.class_code and parsed is not None:
        added, refused = extract_from_documents(
            record, [parsed], registry, classification.class_code, row.mpn or ""
        )

    return {
        "row": row,
        "maker": maker,
        "discovery": found,
        "coverage": coverage,
        "classification": classification,
        "record": record,
        "added": added,
        "refused": refused,
        "fetcher": fetcher,
        "library": library,
    }


def test_the_manufacturer_is_resolved_from_the_vendor_code(enriched):
    assert enriched["maker"].primary_domain == "milwaukeevalve.example"


def test_discovery_reaches_the_datasheet(enriched):
    assert enriched["discovery"].found, enriched["discovery"].notes
    assert (
        "https://milwaukeevalve.example/literature/ba-100-series.pdf"
        in enriched["fetcher"].urls
    )


def test_the_marketplace_result_was_never_fetched(enriched):
    """The gate runs before the request, so those bytes never entered the store."""
    assert not any("amazon" in url for url in enriched["fetcher"].urls)


def test_the_document_covers_the_part_in_an_ordering_row(enriched):
    coverage = enriched["coverage"]
    assert coverage, "the datasheet must be recorded as covering this part"
    assert coverage[0].is_ordering_row


def test_the_class_comes_from_the_document_since_the_row_has_none(enriched):
    """The link that was missing. Without it the chain produced a document and then read nothing."""
    classification = enriched["classification"]
    assert classification is not None
    assert classification.class_code == "PLB.VLV.BALL.2PC"


def test_attributes_are_extracted_from_a_part_number_and_a_manufacturer_alone(enriched):
    assert enriched["added"] >= 5, f"only {enriched['added']} values extracted"
    codes = {value.attribute_code for value in enriched["record"].current_values()}
    # A spread across both readers: dot-leader specification lines and the ordering table.
    assert {"body_material", "pressure_rating_wog", "end_connection"} <= codes
    assert "nominal_size" in codes, "the ordering row was not read"


def test_every_extracted_value_carries_a_verified_quote(enriched):
    """The invariant the whole system rests on. A value without evidence is not a deliverable here,
    and retrieval must not become the hole in that rule."""
    values = enriched["record"].current_values()
    assert values
    for value in values:
        assert value.evidence, value.attribute_code
        span = value.evidence[0]
        assert span.quote_verified, value.attribute_code
        assert span.document_sha256, value.attribute_code


def test_the_citations_point_at_the_stored_bytes(enriched):
    """A citation has to resolve to the artifact that was actually read, not to a URL we hope holds
    the same content."""
    library = enriched["library"]
    stored = {entry.sha256 for entry in library.entries}
    for value in enriched["record"].current_values():
        assert value.evidence[0].document_sha256 in stored


def test_the_value_agrees_with_its_own_quote(enriched):
    """Spot-check one, because "carries a quote" and "the quote supports the value" are different
    claims and only the second one matters."""
    body = next(
        value
        for value in enriched["record"].current_values()
        if value.attribute_code == "body_material"
    )
    assert "Bronze" in body.evidence[0].quote
    assert "C84400" in body.evidence[0].quote
