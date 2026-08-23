"""Search the manufacturer's own site, using the manufacturer's own search form.

The arm that makes *part number plus manufacturer name* sufficient on its own, with **no search API,
no key and no third party**. Given ``milwaukeetool.com`` and ``49-94-0013`` it does what a person
would do: open the site, type the part number into the search box, look at the results, open the
most
likely one, and take the datasheet PDF it links to.

Each of those steps is deliberate about what it does *not* do.

**It reads the site's search form rather than guessing an endpoint.** ``/search?q=``, ``/?s=``,
``/catalogsearch/result/?q=`` — a guess works on some sites and 404s on the rest, and the failure
looks like a bug rather than a wrong guess. :func:`axiom.docintel.find_search_form` takes the
``action`` and the field name out of the markup the site published. That is the site telling us how
to search it.

**It ranks candidates on the part number, not on plausibility.** A link is worth following when the
part number appears in its URL or its anchor text — an exact, checkable signal. Spec-path hints
(``/datasheet``, ``.pdf``) only break ties among links that already carry the part number. Nothing
is
followed because it *looks* like a product page.

**It never leaves the manufacturer's registrable domain.** Discovery is scoped to one site by
construction, so it cannot wander onto a marketplace, and the policy gate still runs on every URL as
a second line. Depth and page budgets are hard caps rather than heuristics: this reads a site that
did not ask to be read, and an unbounded crawl is not something to discover afterwards.

**Every fetch goes through** :class:`~axiom.retrieve.session.RetrievalSession`, so robots.txt,
per-origin spacing, the policy gate and the reuse check all apply here exactly as they do to a
supplied URL. There is no second fetch path.

What this is still not: a general crawler. It follows links from two kinds of page — the homepage
and
a search-results page — and it stops. It will not find a datasheet that is reachable only through
three levels of faceted navigation, and it says so rather than wandering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from axiom.docintel import Link, SearchForm, extract_links, find_search_form
from axiom.retrieve.library import DocumentEntry, DocumentLibrary
from axiom.retrieve.policy import Manufacturer, SourcePolicy, host_of
from axiom.retrieve.resolver import Candidate
from axiom.retrieve.session import FetchStatus, RetrievalSession
from axiom.retrieve.sitemap import SitemapReader

MAX_PAGES = 4
"""Pages fetched per part number: homepage, search results, and up to two candidates.

Low on purpose. A discovery budget is a courtesy budget — a thousand-row batch multiplies whatever
this number is by a thousand against one origin.
"""

MAX_CANDIDATES = 2
"""Result links opened per search. The ranking is exact (part number present or not), so the second
one is a hedge against the site ordering results badly, not a fishing expedition."""


@dataclass(frozen=True)
class DiscoveryStep:
    """One fetch made during discovery, and what it was for.

    Kept so a run is explainable. "Discovery found nothing" is not actionable; "the homepage
    published no search form" and "search returned no link carrying the part number" are different
    problems with different fixes.
    """

    stage: str
    """``homepage``, ``search`` or ``candidate``."""

    url: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class Discovery:
    """What discovery found for one part number on one manufacturer's site."""

    mpn: str
    manufacturer: Manufacturer
    documents: tuple[DocumentEntry, ...] = ()
    steps: tuple[DiscoveryStep, ...] = ()
    search_url: str = ""
    notes: tuple[str, ...] = field(default=())

    @property
    def found(self) -> bool:
        return bool(self.documents)

    def summary(self) -> dict[str, object]:
        return {
            "mpn": self.mpn,
            "manufacturer": self.manufacturer.id,
            "search_url": self.search_url,
            "documents": [entry.sha256 for entry in self.documents],
            "steps": [
                {"stage": s.stage, "url": s.url, "status": s.status, "detail": s.detail}
                for s in self.steps
            ],
            "notes": list(self.notes),
        }


class SiteDiscovery:
    """Finds a part's document on its manufacturer's site, within a budget."""

    def __init__(
        self,
        *,
        policy: SourcePolicy,
        session: RetrievalSession,
        library: DocumentLibrary,
        sitemaps: SitemapReader | None = None,
        max_pages: int = MAX_PAGES,
        max_candidates: int = MAX_CANDIDATES,
    ) -> None:
        self._policy = policy
        self._session = session
        self._library = library
        # Built here by default rather than required, so a caller gets the good strategy without
        # having to know it exists. Pass None explicitly to test the search-form path in isolation.
        self._sitemaps = sitemaps if sitemaps is not None else SitemapReader(session)
        self._max_pages = max_pages
        self._max_candidates = max_candidates
        self._forms: dict[str, SearchForm | None] = {}
        """Search form per domain, discovered once. The homepage is fetched for the first part
        number of a manufacturer and never again for the rest — which is most of the saving when a
        hundred rows share one maker."""

    def discover(self, mpn: str, maker: Manufacturer) -> Discovery:
        steps: list[DiscoveryStep] = []
        notes: list[str] = []
        found: list[DocumentEntry] = []
        budget = _Budget(self._max_pages)

        # --- strategy 1: the site's own sitemap ---------------------------------
        # Tried first because it is both cheaper and more reliable. One fetch per manufacturer
        # serves
        # every row of that manufacturer, and it works on the JS-rendered sites where HTML search
        # returns no links at all — which, measured on milwaukeetool.com, is the common case.
        if self._sitemaps is not None:
            site = self._sitemaps.for_domain(maker.primary_domain)
            if site.available:
                hits = site.find(mpn)
                steps.append(
                    DiscoveryStep(
                        "sitemap",
                        site.sources[0] if site.sources else maker.primary_domain,
                        "matched" if hits else "no_match",
                        f"{len(site.urls)} urls published, {len(hits)} matching",
                    )
                )
                for hit in hits[: self._max_candidates]:
                    if budget.exhausted:
                        break
                    entry = self._store(hit.url, "candidate", steps, budget)
                    if entry is None:
                        continue
                    found.append(entry)
                    self._follow_datasheet(hit.url, entry, mpn, found, steps, budget)
                if found:
                    return Discovery(
                        mpn=mpn,
                        manufacturer=maker,
                        documents=tuple(found),
                        steps=tuple(steps),
                        notes=tuple(notes),
                    )
            else:
                for note in site.notes[:2]:
                    notes.append(f"sitemap: {note}")

        # --- strategy 2: the site's own search form ------------------------------
        form = self._search_form(maker, steps, budget)
        if form is None:
            notes.append(
                f"{maker.primary_domain} published no usable GET search form on its homepage, so "
                f"there is no way to search it without guessing an endpoint. Add a verified "
                f"`patterns:` entry for {maker.id} in schema/sourcing.yaml instead."
            )
            return Discovery(mpn=mpn, manufacturer=maker, steps=tuple(steps), notes=tuple(notes))

        search_url = form.url_for(mpn)
        results = self._fetch_page(search_url, "search", steps, budget)
        if results is None:
            notes.append("the search results page could not be fetched")
            return Discovery(
                mpn=mpn,
                manufacturer=maker,
                steps=tuple(steps),
                search_url=search_url,
                notes=tuple(notes),
            )

        ranked = rank_links(
            extract_links(results, search_url), mpn=mpn, policy=self._policy, domain_of=maker
        )
        if not ranked:
            notes.append(
                f"search returned no link whose URL or anchor text contains {mpn!r}. Either the "
                f"site does not list this part, or its results are rendered by script rather than "
                f"served as links."
            )

        for link in ranked[: self._max_candidates]:
            if budget.exhausted:
                notes.append("page budget reached before every candidate was opened")
                break
            entry = self._store(link.url, "candidate", steps, budget)
            if entry is None:
                continue
            found.append(entry)
            self._follow_datasheet(link.url, entry, mpn, found, steps, budget)

        return Discovery(
            mpn=mpn,
            manufacturer=maker,
            documents=tuple(found),
            steps=tuple(steps),
            search_url=search_url,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------------ internals

    def _follow_datasheet(
        self,
        page_url: str,
        entry: DocumentEntry,
        mpn: str,
        found: list[DocumentEntry],
        steps: list[DiscoveryStep],
        budget: _Budget,
    ) -> None:
        """Take the datasheet a matched product page links to. One hop, never more.

        A product page's own PDF is the best document on the site — it is what a distributor would
        ask
        the manufacturer for. Followed only from a page that already matched the part number, so
        this
        cannot wander, and the PDF itself is not required to carry the part number because
        manufacturers name literature after a family.
        """
        if entry.doc_type == "spec_sheet" and page_url.lower().endswith(".pdf"):
            return
        if budget.exhausted:
            return
        markup = self._library.text(entry)
        if markup is None:
            return
        for pdf in _datasheet_links(
            extract_links(markup, page_url), mpn=mpn, policy=self._policy
        ):
            if budget.exhausted:
                return
            attached = self._store(pdf.url, "candidate", steps, budget)
            if attached is not None:
                found.append(attached)
            return

    def _search_form(
        self, maker: Manufacturer, steps: list[DiscoveryStep], budget: _Budget
    ) -> SearchForm | None:
        domain = maker.primary_domain
        if domain in self._forms:
            return self._forms[domain]

        homepage = f"https://{domain}/"
        html = self._fetch_page(homepage, "homepage", steps, budget)
        form = find_search_form(html, homepage) if html else None
        self._forms[domain] = form
        return form

    def _fetch_page(
        self, url: str, stage: str, steps: list[DiscoveryStep], budget: _Budget
    ) -> str | None:
        """Fetch a navigation page for its structure, **without storing it**.

        A homepage and a search-results page are maps, not sources. Archiving them was a real bug: a
        results page listing forty products was indexed and then found to "cover" all forty, so a
        citation could name a search page as the source of a specification. See
        :class:`~axiom.retrieve.session.TransientFetch`.
        """
        if budget.exhausted:
            steps.append(DiscoveryStep(stage, url, "over_budget"))
            return None
        result = self._session.fetch_transient(url)
        if result.status is FetchStatus.FETCHED:
            budget.spend()
        steps.append(DiscoveryStep(stage, url, result.status.value, result.detail))
        return result.text() if result.ok else None

    def _store(
        self, url: str, stage: str, steps: list[DiscoveryStep], budget: _Budget
    ) -> DocumentEntry | None:
        """Put one URL through the session, recording the outcome either way."""
        if budget.exhausted:
            steps.append(DiscoveryStep(stage, url, "over_budget"))
            return None

        verdict = self._policy.classify(url)
        candidate = Candidate(url=url, verdict=verdict, origin="discovery", rank=0.0)
        outcome = self._session.fetch(candidate)
        if outcome.status is FetchStatus.FETCHED:
            budget.spend()
        steps.append(DiscoveryStep(stage, url, outcome.status.value, outcome.detail))
        return outcome.entry




@dataclass
class _Budget:
    """A hard cap on fetches, counted only against requests that actually happened."""

    limit: int
    spent: int = 0

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit

    def spend(self) -> None:
        self.spent += 1


def rank_links(
    links: list[Link], *, mpn: str, policy: SourcePolicy, domain_of: Manufacturer
) -> list[Link]:
    """Links worth opening, best first.

    The filter is exact and the ranking is a tiebreak, which is the important asymmetry: a link
    qualifies only by *containing the part number*, never by looking like a product page. Ranking
    then prefers a PDF, then a spec-looking path, then a shorter URL — a shorter path on the same
    site is usually the canonical product page rather than a filtered view of it.
    """
    folded = _fold(mpn)
    if not folded:
        return []

    scored: list[tuple[float, int, Link]] = []
    for link in links:
        if not policy.allows(link.url):
            continue
        if not _same_site(link.url, domain_of):
            continue
        in_url = folded in _fold(urlparse(link.url).path + "?" + (urlparse(link.url).query or ""))
        in_text = folded in _fold(f"{link.text} {link.title}")
        if not (in_url or in_text):
            continue

        score = 0.0
        score += 3.0 if in_url else 0.0
        score += 1.0 if in_text else 0.0
        score += 4.0 if link.is_pdf else 0.0
        score += policy.spec_score(link.url)
        scored.append((score, len(link.url), link))

    scored.sort(key=lambda item: (-item[0], item[1], item[2].url))
    return [link for _score, _length, link in scored]


def _datasheet_links(links: list[Link], *, mpn: str, policy: SourcePolicy) -> list[Link]:
    """PDF links on a product page, best first.

    The part number is *not* required here, unlike :func:`rank_links`. A datasheet linked from a
    page
    that already matched the part is about that part, and manufacturers routinely name the file
    after
    the series rather than the size — requiring the part number would reject the very document being
    looked for.
    """
    folded = _fold(mpn)
    scored: list[tuple[float, Link]] = []
    for link in links:
        if not link.is_pdf or not policy.allows(link.url):
            continue
        score = policy.spec_score(link.url)
        score += 2.0 if folded and folded in _fold(link.url) else 0.0
        score += 1.0 if any(
            word in link.text.lower() for word in ("spec", "data", "sheet", "submittal", "catalog")
        ) else 0.0
        scored.append((score, link))
    scored.sort(key=lambda item: (-item[0], item[1].url))
    return [link for _score, link in scored]


def _same_site(url: str, maker: Manufacturer) -> bool:
    """Whether a URL is on one of the manufacturer's declared domains, including subdomains.

    Discovery is scoped to one site by construction. The policy gate would refuse a marketplace
    anyway, but *scoping* is stronger than *refusing*: it means a link farm on the manufacturer's
    own page cannot lead discovery anywhere at all.
    """
    host = host_of(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in maker.domains)


def _fold(text: str) -> str:
    """Lowercase, alphanumerics only. ``49-94-0013`` matches ``49940013`` in a URL slug."""
    return "".join(char for char in (text or "").lower() if char.isalnum())


__all__ = [
    "MAX_CANDIDATES",
    "MAX_PAGES",
    "Discovery",
    "DiscoveryStep",
    "SiteDiscovery",
    "rank_links",
]
