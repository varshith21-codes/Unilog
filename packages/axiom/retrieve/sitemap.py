"""The site's own list of its pages, which is the best way to find a product URL.

Discovery started with site search, because that is what a person does. On real manufacturer sites
it
mostly does not work: ``milwaukeetool.com`` publishes **zero** ``<form>`` elements on its homepage
and
its ``/search?q=49-94-0013`` page echoes the query into the markup but renders the *results* with
JavaScript, so there is not one link to extract. HTML-only search is a dead end there, and a
headless
browser is a heavy dependency to run a thousand times.

Sitemaps solve it outright, and they are the *right* mechanism rather than a workaround:

*   They exist **specifically for machine consumption**. Reading one is the intended use, not
    scraping around an intention.
*   They are announced by the site's own ``robots.txt``, so nothing is guessed — the same principle
as
    reading a published search form rather than inventing an endpoint.
*   They are plain XML. No JavaScript, no rendering.
*   They are **canonical and complete**, not a ranked guess. A search returns what a relevance
engine
    thinks you meant; a sitemap is the list.
*   One fetch serves every part number for that manufacturer.

Measured on the client's sample: Milwaukee's sitemap carries 12,523 URLs, and **all 108** Milwaukee
part numbers in the file match exactly on the URL's last path segment
(``/products/details/5-x-045-x-7-8-metal-cut-off-wheel-type-1/49-94-0013``). One request, 108 rows
resolved.

Matching is on the **last path segment first**, then anywhere in the URL. That ordering matters: a
last-segment match is the site saying "this page *is* that part", while an anywhere match can be a
category page that happens to contain the digits. Both are reported; they are not the same claim.

Fetched through :meth:`~axiom.retrieve.session.RetrievalSession.fetch_transient`, so a sitemap is
never stored and never indexed. It is a map, not a source — and a 12 MB XML file listing twelve
thousand part numbers would otherwise be recorded as "covering" every one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from axiom.retrieve.session import RetrievalSession

MAX_SITEMAP_BYTES = 40 * 1024 * 1024
"""A sitemap is read once and discarded, so the ceiling is about not buffering something absurd
rather than about storage. Milwaukee's is 12 MB, which sets the scale."""

MAX_CHILDREN = 30
"""Child sitemaps followed from an index. Large retailers publish hundreds, split by locale and
category; a manufacturer publishes a handful. This is a cap on a pathological case, not a target."""

MAX_URLS = 400_000
"""Stop accumulating past this. Guards memory on a site that publishes its entire catalogue."""

# `<loc>` inside either `<url>` or `<sitemap>`. Parsed with a regex rather than an XML parser on
# purpose: sitemaps in the wild carry undeclared entities, mismatched namespaces and stray bytes
# that
# make a strict parser refuse the whole file. A tolerant read of a slightly broken sitemap is worth
# far more here than a correct rejection, and there is nothing security-sensitive in the result —
# every
# URL that comes out is re-classified by the policy before anything is fetched.
_LOC = re.compile(r"<loc>\s*([^<\s]+?)\s*</loc>", re.IGNORECASE)
_SITEMAP_BLOCK = re.compile(r"<sitemap[\s>]", re.IGNORECASE)

# A URL that is itself a sitemap. Used to recognise an index that does not declare itself as one.
_SITEMAP_NAME = re.compile(r"sitemap[^/]*\.xml(?:\.gz)?$|/sitemap[^/]*$", re.IGNORECASE)
_ROBOTS_SITEMAP = re.compile(r"(?im)^\s*sitemap:\s*(\S+)")


@dataclass(frozen=True)
class SitemapHit:
    """A URL from the sitemap that matches a part number, and how strongly."""

    url: str
    how: str
    """``segment`` when the part number is the URL's last path segment, ``path`` when it appears
    elsewhere in the URL."""

    @property
    def is_exact(self) -> bool:
        return self.how == "segment"


@dataclass
class SiteMap:
    """One domain's published URLs, indexed for part-number lookup.

    Built once per domain and reused for every row of that manufacturer, which is where nearly all
    of
    the saving is: a hundred rows sharing one maker cost one request rather than four hundred.
    """

    domain: str
    urls: tuple[str, ...] = ()
    by_segment: dict[str, str] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    """The sitemap URLs actually read, so a result is traceable to the file it came from."""

    notes: tuple[str, ...] = field(default=())

    @property
    def available(self) -> bool:
        return bool(self.urls)

    def find(self, mpn: str) -> list[SitemapHit]:
        """Every URL matching this part number, exact matches first."""
        folded = _fold(mpn)
        # Below three characters a part number matches half the catalogue. The same floor
        # `axiom.docintel.find_sku` applies, and for the same reason.
        if len(folded) < 4:
            return []

        hits: list[SitemapHit] = []
        if exact := self.by_segment.get(folded):
            hits.append(SitemapHit(exact, "segment"))

        seen = {hit.url for hit in hits}
        for url in self.urls:
            if url in seen:
                continue
            if folded in _fold(url):
                hits.append(SitemapHit(url, "path"))
                if len(hits) >= 8:
                    break
        return hits

    def summary(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "urls": len(self.urls),
            "sources": list(self.sources),
            "notes": list(self.notes),
        }


class SitemapReader:
    """Fetches and caches one sitemap per domain."""

    def __init__(self, session: RetrievalSession, *, max_children: int = MAX_CHILDREN) -> None:
        self._session = session
        self._max_children = max_children
        self._cache: dict[str, SiteMap] = {}

    def for_domain(self, domain: str) -> SiteMap:
        """The domain's sitemap, fetched on first use and cached for the run."""
        key = domain.lower().lstrip(".")
        if key in self._cache:
            return self._cache[key]
        site = self._load(key)
        self._cache[key] = site
        return site

    @property
    def domains_read(self) -> int:
        return sum(1 for site in self._cache.values() if site.available)

    # ------------------------------------------------------------------ internals

    def _load(self, domain: str) -> SiteMap:
        notes: list[str] = []
        declared = self._declared_sitemaps(domain, notes)
        if not declared:
            # The protocol's registered default location. A fallback rather than a guess — but it is
            # still weaker than a declaration, so it is recorded as such.
            declared = [f"https://www.{domain}/sitemap.xml"]
            notes.append(
                "robots.txt declared no sitemap; trying the protocol's default /sitemap.xml"
            )

        urls: list[str] = []
        sources: list[str] = []
        queue = list(declared)
        children_followed = 0
        seen: set[str] = set()

        while queue and len(urls) < MAX_URLS:
            target = queue.pop(0)
            if target in seen:
                continue
            seen.add(target)

            result = self._session.fetch_transient(target, max_bytes=MAX_SITEMAP_BYTES)
            if not result.ok:
                notes.append(f"{target}: {result.status.value} ({result.detail[:80]})")
                continue

            body = result.text()
            sources.append(target)
            found = _LOC.findall(body)
            if not found:
                notes.append(f"{target}: no <loc> entries")
                continue

            # An index either declares itself with `<sitemap>` wrappers, or gives itself away by
            # listing sitemaps as its entries. Both happen: dewalt.com serves a `<urlset>` whose
            # entries are `/en-us/page/sitemap.xml` and friends, which is not what the protocol says
            # and is what a real site does. Detecting only the declared form read those 15 child
            # sitemaps as 15 product pages and found nothing behind them: 55 rows of the
            # sample lost to a wrapper element.
            if _SITEMAP_BLOCK.search(body) or _mostly_sitemaps(found):
                # An index: its <loc> entries are more sitemaps, not pages.
                for child in found:
                    if children_followed >= self._max_children:
                        notes.append(
                            f"stopped after {self._max_children} child sitemaps; "
                            f"{len(found) - children_followed} more were listed"
                        )
                        break
                    queue.append(urljoin(target, child))
                    children_followed += 1
                continue

            urls.extend(urljoin(target, url) for url in found)

        return SiteMap(
            domain=domain,
            urls=tuple(urls[:MAX_URLS]),
            by_segment=_index_by_segment(urls),
            sources=tuple(sources),
            notes=tuple(notes),
        )

    def _declared_sitemaps(self, domain: str, notes: list[str]) -> list[str]:
        """Sitemaps the site announces in robots.txt: the site telling us where its own list is.

        Both ``www.`` and the bare domain are tried, because manufacturers are inconsistent about
        which one serves and which redirects, and a redirect that drops the path is common enough to
        be worth the second request.
        """
        for host in (f"www.{domain}", domain):
            result = self._session.fetch_transient(f"https://{host}/robots.txt", max_bytes=1 << 20)
            if not result.ok:
                continue
            declared = _ROBOTS_SITEMAP.findall(result.text())
            if declared:
                return [url.strip() for url in declared]
            notes.append(f"https://{host}/robots.txt published no Sitemap: line")
        return []


def _mostly_sitemaps(locs: list[str]) -> bool:
    """Whether these entries are themselves sitemaps rather than pages.

    A majority test rather than "any", because a real product sitemap may legitimately list one XML
    document among thousands of pages, and treating that whole file as an index would discard the
    pages. A file whose entries are *predominantly* sitemaps is an index whatever it calls itself.
    """
    if not locs:
        return False
    looks_like = sum(1 for loc in locs if _SITEMAP_NAME.search(loc))
    return looks_like > len(locs) / 2


def _index_by_segment(urls: list[str]) -> dict[str, str]:
    """Folded last path segment -> URL. First writer wins.

    First rather than last because sitemaps tend to list the canonical page before its variants, and
    where they do not, both are the same product anyway.
    """
    index: dict[str, str] = {}
    for url in urls:
        segment = _fold(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
        if segment and segment not in index:
            index[segment] = url
    return index


def _fold(text: str) -> str:
    """Lowercase alphanumerics only.

    So ``49-94-0013`` matches a ``49940013`` slug, and the reverse.
    """
    return "".join(char for char in (text or "").lower() if char.isalnum())


__all__ = [
    "MAX_CHILDREN",
    "MAX_SITEMAP_BYTES",
    "MAX_URLS",
    "SiteMap",
    "SitemapHit",
    "SitemapReader",
]
