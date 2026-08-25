"""Finding the document from a part number and a manufacturer name, with no URL supplied.

This is the arm that makes the enrichment form work the way somebody actually expects it to: type a
part number, name the manufacturer, get enriched data back. No link to paste, no file to prepare.

It is wiring, not new machinery. :mod:`axiom.retrieve` already does all of this and is already
tested
offline — what was missing was that :func:`~axiom.pipeline.single.enrich_one` never called it, so
the
endpoint's only route to a real datasheet was a URL somebody had already found by hand.

The order below is the order ``scripts/retrieve_sources.py`` established, and each step is tried
only
because the one before it produced nothing:

1.  **The library.** A document already stored that covers this part means **no request at all**.
    This is the step that matters most in practice: ``data/library/index.json`` currently indexes 79
    manufacturer documents covering 121 part numbers, and it also records the 999 parts it has
    checked and found *absent* — so a negative answer is paid for once rather than re-fetched.
2.  **The manufacturer's own site.** :class:`~axiom.retrieve.SiteDiscovery` reads the search form
the
    site published, searches it for the part number, and follows a result link that carries the part
    number. **No search API and no key** — this is the arm that makes part number plus manufacturer
    sufficient on its own, and it only works for a manufacturer declared in ``schema/sourcing.yaml``
    (57 of them, with real domains), because that is where the domain comes from.
3.  **The open web**, through a :class:`~axiom.retrieve.SearchProvider`. There is deliberately no
    default: search is an external service with a key and a bill, and wiring one in silently would
    make this function's behaviour depend on ambient credentials. Pass one and the arm turns on;
    without it, this step reports that it was skipped rather than pretending to have searched.

**Nothing here calls a model.** Every step is HTTP and parsing. The expensive, non-deterministic
stage is extraction, which happens afterwards and is unchanged by any of this. So a retrieval
attempt
that finds nothing has cost bandwidth, not tokens — which is why it is worth attempting before
falling back to treating the submission itself as the source.

**Every fetch is policy-gated before the request is made.** Marketplaces, mass retail, distributors
and datasheet aggregators are refused; ``robots.txt`` is honoured; requests to one origin are
spaced.
Those are :mod:`axiom.retrieve.session`'s rules, and routing through it is why this module has no
fetch path of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from axiom.docintel import ParsedDocument, extract_links
from axiom.ingest.store import LocalArtifactStore
from axiom.retrieve import (
    Candidate,
    Discovery,
    DocumentEntry,
    DocumentLibrary,
    FetchOutcome,
    Manufacturer,
    RenderedFetcher,
    Resolution,
    Resolver,
    RetrievalSession,
    SearchProvider,
    SiteDiscovery,
    SourcePolicy,
)
from axiom.retrieve import load_default as load_source_policy

MAX_FETCHES = 12
"""Static page/document fetch attempts across discovery and candidate retrieval.

Search-provider calls, robots.txt checks, and bounded browser subrequests have independent limits
and accounting; this ceiling does not claim to include them.
"""

MIN_RESOLVER_FETCHES = 4
"""Capacity reserved for supplied, patterned and search-derived candidates.

A large sitemap must never consume the whole submission budget before open search gets a chance.
"""

MAX_DISCOVERY_FETCHES = 8
"""Maximum sitemap and manufacturer-site requests before candidate resolution takes over."""

MAX_REFRESH_PRIMARY_PAGES = 25
"""Largest document treated as a focused live source during an explicit richness refresh.

The refresh control exists to escape broad catalogue coverage. A replacement catalogue may be
newer and still no richer for one SKU, so it remains a candidate while focused product pages and
technical data sheets are preferred. The normal non-refresh path retains its existing ranking.
"""


@dataclass(frozen=True)
class RetrievalAttempt:
    """What retrieval tried, what it found, and — when it found nothing — which step ran out.

    The steps are kept rather than reduced to a boolean because "we found nothing" is not
    actionable. "This manufacturer has no declared domain, so there was nowhere to search first" and
    "we searched the manufacturer's site and no result carried the part number" are different
    problems with different fixes, and one of them is a one-line addition to
    ``schema/sourcing.yaml``.
    """

    mpn: str
    documents: tuple[ParsedDocument, ...] = ()
    entries: tuple[DocumentEntry, ...] = ()
    supplementary: tuple[ParsedDocument, ...] = ()
    """Manufacturer-owned documents worth reading that no body-text match covered.

    Fetched during the run — a JavaScript product page whose part number renders client-side, a
    datasheet PDF whose drawing carries no searchable token — and admitted on the maker's authority
    rather than a located mention. Read alongside the primary and merged, never used to satisfy
    coverage or to suppress escalation. Empty unless the maker is known."""
    manufacturer: Manufacturer | None = None
    from_library: bool = False
    """True when a stored document already covered this part, so no request was made at all."""
    refresh_requested: bool = False
    """True when stored coverage was deliberately bypassed to look for a richer live source."""

    discovery: Discovery | None = None
    resolution: Resolution | None = None
    outcomes: tuple[FetchOutcome, ...] = ()
    requests_made: int = 0
    browser_requests_made: int = 0
    bytes_fetched: int = 0
    notes: tuple[str, ...] = field(default=())

    @property
    def found(self) -> bool:
        return bool(self.documents)

    @property
    def primary(self) -> ParsedDocument | None:
        """The document extraction should read. Highest coverage first; see :meth:`coverage_for`."""
        return self.documents[0] if self.documents else None

    def summary(self) -> dict[str, object]:
        return {
            "attempted": True,
            "found": self.found,
            "from_library": self.from_library,
            "refresh_requested": self.refresh_requested,
            "manufacturer": (
                {
                    "id": self.manufacturer.id,
                    "name": self.manufacturer.name,
                    "domain": self.manufacturer.primary_domain,
                }
                if self.manufacturer
                else None
            ),
            "documents": [
                {
                    "document_id": parsed.document.document_id,
                    "sha256": parsed.document.sha256,
                    "uri": parsed.document.uri,
                    "doc_type": parsed.document.doc_type.value,
                    "pages": parsed.page_count,
                    "tables": len(parsed.all_tables()),
                }
                for parsed in self.documents
            ],
            "requests_made": self.requests_made,
            "browser_requests_made": self.browser_requests_made,
            "bytes_fetched": self.bytes_fetched,
            "discovery": self.discovery.summary() if self.discovery else None,
            "resolution": self.resolution.summary() if self.resolution else None,
            "rejected": [
                outcome.summary() for outcome in self.outcomes if not outcome.usable
            ],
            "notes": list(self.notes),
        }


def retrieve_documents(
    mpn: str,
    *,
    store: LocalArtifactStore,
    library_path: Path | str,
    manufacturer: str | None = None,
    vendor_code: str | None = None,
    brand: str | None = None,
    supplied: Sequence[str] = (),
    policy: SourcePolicy | None = None,
    search: SearchProvider | None = None,
    renderer: RenderedFetcher | None = None,
    fetcher=None,
    max_fetches: int = MAX_FETCHES,
    max_browser_pages: int = 2,
    discover: bool = True,
    refresh_sources: bool = False,
    library: DocumentLibrary | None = None,
) -> RetrievalAttempt:
    """Find documents covering ``mpn``, then attach the manufacturer's own supplementary reading.

    The search itself is :func:`_retrieve_documents`. This wrapper adds one thing on top: once the
    search is over, it reads back the manufacturer-owned documents that were fetched but never
    matched by body text — a JavaScript product page, a datasheet PDF — and hands them to the caller
    as :attr:`RetrievalAttempt.supplementary`. Done here rather than inside the search so it cannot
    disturb the escalation logic, which must keep asking the stricter body-text question.
    """
    library = (
        library if library is not None else DocumentLibrary.load(store, Path(library_path))
    )
    attempt = _retrieve_documents(
        mpn,
        store=store,
        library_path=library_path,
        manufacturer=manufacturer,
        vendor_code=vendor_code,
        brand=brand,
        supplied=supplied,
        policy=policy,
        search=search,
        renderer=renderer,
        fetcher=fetcher,
        max_fetches=max_fetches,
        max_browser_pages=max_browser_pages,
        discover=discover,
        refresh_sources=refresh_sources,
        library=library,
    )
    maker = attempt.manufacturer
    if maker is None or not maker.id:
        return attempt

    covered = {document.document.sha256 for document in attempt.documents}
    # Only documents this run actually fetched for this part. Scoping to the run's own entries is
    # what keeps authority from reaching across the library into the maker's unrelated catalogues.
    fetched = {entry.sha256 for entry in attempt.entries}
    supplementary_coverage = library.supplementary_for(
        mpn, manufacturer_id=maker.id, among=fetched, exclude=covered
    )
    supplementary = tuple(
        parsed
        for coverage in supplementary_coverage
        if (parsed := library.parsed(coverage.entry)) is not None
    )
    if not supplementary:
        return attempt
    return replace(attempt, supplementary=supplementary)


def _retrieve_documents(
    mpn: str,
    *,
    store: LocalArtifactStore,
    library_path: Path | str,
    manufacturer: str | None = None,
    vendor_code: str | None = None,
    brand: str | None = None,
    supplied: Sequence[str] = (),
    policy: SourcePolicy | None = None,
    search: SearchProvider | None = None,
    renderer: RenderedFetcher | None = None,
    fetcher=None,
    max_fetches: int = MAX_FETCHES,
    max_browser_pages: int = 2,
    discover: bool = True,
    refresh_sources: bool = False,
    library: DocumentLibrary | None = None,
) -> RetrievalAttempt:
    """Find documents covering ``mpn``. Makes HTTP requests; makes **no** model call.

    ``fetcher`` is injectable for the same reason it is throughout ``axiom.ingest``: a test that
    needs
    the internet to check this path is a test that gets skipped, and then this code rots.

    ``library`` is injectable so a caller processing several parts in one process is not reloading a
    megabyte of index per part.
    """
    policy = policy if policy is not None else load_source_policy()
    library = (
        library if library is not None else DocumentLibrary.load(store, Path(library_path))
    )
    notes: list[str] = []

    maker = policy.manufacturer_for(
        vendor_code=vendor_code, vendor_name=manufacturer, brand=brand
    )

    # --- step 1: the library, before anything touches the network -------------------
    #
    # Also the step that makes a *negative* answer cheap: `coverage_for` consults the
    # checked-and-absent record, so a part a stored catalogue was already searched for is not
    # re-searched.
    coverage = library.coverage_for(mpn)
    stored_documents: tuple[ParsedDocument, ...] = ()
    stored_entries: tuple[DocumentEntry, ...] = ()
    baseline_hashes: set[str] = set()
    if coverage:
        parsed = [library.parsed(c.entry) for c in coverage]
        stored_documents = tuple(p for p in parsed if p is not None)
        stored_entries = tuple(c.entry for c in coverage)
        baseline_hashes = {entry.sha256 for entry in stored_entries}
        if stored_documents and not refresh_sources:
            how = "an ordering row" if coverage[0].is_ordering_row else "the document body"
            notes.append(
                f"a stored document already covers {mpn} in {how}, so no request was made. "
                f"Retrieval is a one-time cost per document, not per part."
            )
            return RetrievalAttempt(
                mpn=mpn,
                documents=stored_documents,
                entries=stored_entries,
                manufacturer=maker,
                from_library=True,
                notes=tuple(notes),
            )
        if stored_documents:
            notes.append(
                f"stored coverage for {mpn} was retained as a fallback while refresh searched "
                "for a richer live manufacturer source."
            )
        else:
            # Indexed but the bytes are gone: the store is gitignored, so a fresh clone hits this.
            notes.append(
                "the library indexes a document covering this part but its bytes are not in the "
                "store, which is gitignored — re-fetching."
            )

    if maker is None:
        if policy.is_known_distributor(vendor_code=vendor_code, vendor_name=manufacturer):
            notes.append(
                f"{manufacturer!r} is a declared distributor or buying co-op, so no domain will "
                f"ever be right for it. The manufacturer has to come from the brand."
            )
        else:
            notes.append(
                f"no manufacturer domain is declared for {manufacturer!r}, so there was no site to "
                f"search. Add it to schema/sourcing.yaml — one entry with a domain is what turns "
                f"this part number into a retrievable one."
            )

    # Reserve candidate capacity before site discovery starts. Sitemap indexes can fan out into
    # dozens of child files; they are useful, but never more useful than leaving room for a direct
    # search result or supplied URL.
    reserved = min(MIN_RESOLVER_FETCHES, max(0, max_fetches))
    discovery_limit = min(MAX_DISCOVERY_FETCHES, max(0, max_fetches - reserved))
    discovery_session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        max_requests=discovery_limit,
    )

    discovery: Discovery | None = None
    resolution: Resolution | None = None
    outcomes: list[FetchOutcome] = []

    def _is_refresh_upgrade(document: ParsedDocument) -> bool:
        """Whether a fresh source is focused enough to replace broad cached coverage."""
        return (
            not stored_documents
            or document.page_count <= MAX_REFRESH_PRIMARY_PAGES
        )

    def _harvest() -> tuple[ParsedDocument, ...]:
        found = library.coverage_for(mpn)
        if not refresh_sources:
            parsed = [library.parsed(c.entry) for c in found]
            return tuple(p for p in parsed if p is not None)

        fresh_coverage = [c for c in found if c.entry.sha256 not in baseline_hashes]
        fresh_documents = tuple(
            parsed
            for coverage in fresh_coverage
            if (parsed := library.parsed(coverage.entry)) is not None
        )
        upgrades = tuple(
            document for document in fresh_documents if _is_refresh_upgrade(document)
        )
        if not upgrades:
            return ()

        def priority(document: ParsedDocument) -> tuple[int, int, int, str]:
            doc_type = document.document.doc_type.value
            return (
                0 if _is_refresh_upgrade(document) else 1,
                0 if doc_type == "spec_sheet" else 1,
                document.page_count,
                document.document.sha256,
            )

        all_documents = [*fresh_documents, *stored_documents]
        return tuple(sorted(all_documents, key=priority))

    # --- step 2: the manufacturer's own site, via its own search form ---------------
    #
    # First because it needs no API key and no bill, and because a manufacturer's own page is the
    # only tier citable as `MFR URL`.
    if discover and maker is not None and discovery_limit > 0:
        discovery = SiteDiscovery(
            policy=policy, session=discovery_session, library=library
        ).discover(mpn, maker)
        if discovery.found:
            documents = _harvest()
            if documents:
                library.save()
                notes.append(
                    f"found on {maker.primary_domain} by reading the site's own search form — no "
                    f"search API and no key."
                )
                return RetrievalAttempt(
                    mpn=mpn,
                    documents=documents,
                    entries=discovery.documents,
                    manufacturer=maker,
                    refresh_requested=refresh_sources,
                    discovery=discovery,
                    requests_made=discovery_session.requests_made,
                    bytes_fetched=discovery_session.bytes_fetched,
                    notes=tuple(notes),
                )
        notes.extend(discovery.notes)

    # --- step 3: supplied URLs, declared patterns, then search ---------------------
    # A fresh session gets every request discovery did not use. The separate hard ceiling is what
    # prevents sitemap fan-out from suppressing this arm while preserving one total budget.
    resolver_limit = max(0, max_fetches - discovery_session.requests_made)
    session = RetrievalSession(
        policy=policy,
        store=store,
        library=library,
        fetcher=fetcher,
        max_requests=resolver_limit,
        renderer=renderer,
    )
    if resolver_limit > 0:
        resolution = Resolver(policy, search=search).resolve(
            mpn,
            brand=brand,
            vendor_code=vendor_code,
            vendor_name=manufacturer,
            supplied=supplied,
        )
        notes.extend(resolution.notes)
        rendered_pages = 0

        def _completed_attempt() -> RetrievalAttempt | None:
            documents = _harvest()
            if not documents:
                return None
            library.save()
            return RetrievalAttempt(
                mpn=mpn,
                documents=documents,
                entries=tuple(o.entry for o in outcomes if o.entry is not None),
                manufacturer=maker,
                refresh_requested=refresh_sources,
                discovery=discovery,
                resolution=resolution,
                outcomes=tuple(outcomes),
                requests_made=discovery_session.requests_made + session.requests_made,
                browser_requests_made=session.browser_requests_made,
                bytes_fetched=discovery_session.bytes_fetched + session.bytes_fetched,
                notes=tuple(notes),
            )

        def _fetch_linked_pdfs(
            entry: DocumentEntry | None,
            parent: Candidate,
            *,
            origin: str,
        ) -> None:
            """Fetch up to two PDFs linked by a product page before accepting the thinner HTML."""
            if entry is None or session.exhausted:
                return
            markup = library.text(entry)
            if not markup:
                return
            links = [
                link
                for link in extract_links(markup, entry.source_uri)
                if link.is_pdf and policy.allows(link.url)
            ]
            links.sort(key=lambda link: -policy.spec_score(link.url))
            for link in links[:2]:
                if session.exhausted:
                    break
                verdict = policy.classify(link.url)
                pdf_candidate = Candidate(
                    url=link.url,
                    verdict=verdict,
                    origin=origin,
                    rank=parent.rank + policy.spec_score(link.url),
                    query=parent.query,
                    manufacturer_id=verdict.manufacturer_id,
                )
                outcomes.append(session.fetch(pdf_candidate))

        for candidate in resolution.candidates:
            if session.exhausted:
                notes.append(
                    f"stopped at the {max_fetches}-request ceiling for one submission; "
                    f"{len(resolution.candidates)} candidate(s) were available."
                )
                break
            outcome = session.fetch(candidate)
            outcomes.append(outcome)
            # Coverage, not merely a successful fetch. A landing page that never names the part
            # number is not a source for it, and storing it as one would put an unrelated document
            # behind a citation.
            if not outcome.usable:
                continue
            # A product page can already cover the SKU while linking to a much richer technical
            # data sheet. Fetch those bounded, policy-checked PDFs before accepting the thinner
            # HTML; otherwise exact coverage becomes an accidental early-exit from enrichment.
            _fetch_linked_pdfs(outcome.entry, candidate, origin="product_page_link")
            if completed := _completed_attempt():
                return completed

            if renderer is None or rendered_pages >= max_browser_pages:
                continue
            rendered_pages += 1
            rendered_outcomes, rendered_notes = session.render(candidate)
            outcomes.extend(rendered_outcomes)
            notes.extend(rendered_notes)

            # A rendered DOM often reveals a normal PDF link absent from the server-side shell.
            # Fetch it through RetrievalSession rather than browser APIs so redirects, robots, byte
            # limits and final-URL policy remain exactly the static path's rules.
            for rendered_outcome in rendered_outcomes:
                _fetch_linked_pdfs(
                    rendered_outcome.entry,
                    candidate,
                    origin="browser_link",
                )
                if completed := _completed_attempt():
                    return completed
            if completed := _completed_attempt():
                return completed

    total_requests = discovery_session.requests_made + session.requests_made
    total_bytes = discovery_session.bytes_fetched + session.bytes_fetched
    if total_requests:
        library.save()

    if refresh_sources and stored_documents:
        notes.append(
            "refresh found no richer exact-SKU source, so the previously stored document remains "
            "the extraction source."
        )
        return RetrievalAttempt(
            mpn=mpn,
            documents=stored_documents,
            entries=stored_entries,
            manufacturer=maker,
            refresh_requested=True,
            discovery=discovery,
            resolution=resolution,
            outcomes=tuple(outcomes),
            requests_made=total_requests,
            browser_requests_made=session.browser_requests_made,
            bytes_fetched=total_bytes,
            notes=tuple(notes),
        )

    return RetrievalAttempt(
        mpn=mpn,
        manufacturer=maker,
        refresh_requested=refresh_sources,
        discovery=discovery,
        resolution=resolution,
        outcomes=tuple(outcomes),
        requests_made=total_requests,
        browser_requests_made=session.browser_requests_made,
        bytes_fetched=total_bytes,
        notes=tuple(notes),
    )


__all__ = ["MAX_FETCHES", "RetrievalAttempt", "retrieve_documents"]
