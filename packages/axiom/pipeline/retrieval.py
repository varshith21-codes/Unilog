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
from dataclasses import dataclass, field
from pathlib import Path

from axiom.docintel import ParsedDocument
from axiom.ingest.store import LocalArtifactStore
from axiom.retrieve import (
    Discovery,
    DocumentEntry,
    DocumentLibrary,
    FetchOutcome,
    Manufacturer,
    Resolution,
    Resolver,
    RetrievalSession,
    SearchProvider,
    SiteDiscovery,
    SourcePolicy,
)
from axiom.retrieve import load_default as load_source_policy

MAX_FETCHES = 8
"""Requests one submission may make.

Deliberately low, and lower than the CLI's 50. That budget is for a batch run somebody is watching;
this one sits inside an HTTP request on an endpoint with no authentication, so it bounds what a
single caller can aim at a third party's website. Discovery's own per-part budget is 4 pages, so
this
allows roughly one discovery attempt plus a couple of resolved candidates.
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
    manufacturer: Manufacturer | None = None
    from_library: bool = False
    """True when a stored document already covered this part, so no request was made at all."""

    discovery: Discovery | None = None
    resolution: Resolution | None = None
    outcomes: tuple[FetchOutcome, ...] = ()
    requests_made: int = 0
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
    fetcher=None,
    max_fetches: int = MAX_FETCHES,
    discover: bool = True,
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
    if coverage:
        parsed = [library.parsed(c.entry) for c in coverage]
        documents = tuple(p for p in parsed if p is not None)
        if documents:
            how = "an ordering row" if coverage[0].is_ordering_row else "the document body"
            notes.append(
                f"a stored document already covers {mpn} in {how}, so no request was made. "
                f"Retrieval is a one-time cost per document, not per part."
            )
            return RetrievalAttempt(
                mpn=mpn,
                documents=documents,
                entries=tuple(c.entry for c in coverage),
                manufacturer=maker,
                from_library=True,
                notes=tuple(notes),
            )
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

    session = RetrievalSession(
        policy=policy, store=store, library=library, fetcher=fetcher
    )

    discovery: Discovery | None = None
    resolution: Resolution | None = None
    outcomes: list[FetchOutcome] = []

    def _harvest() -> tuple[ParsedDocument, ...]:
        found = library.coverage_for(mpn)
        parsed = [library.parsed(c.entry) for c in found]
        return tuple(p for p in parsed if p is not None)

    # --- step 2: the manufacturer's own site, via its own search form ---------------
    #
    # First because it needs no API key and no bill, and because a manufacturer's own page is the
    # only tier citable as `MFR URL`.
    if discover and maker is not None:
        discovery = SiteDiscovery(policy=policy, session=session, library=library).discover(
            mpn, maker
        )
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
                    discovery=discovery,
                    requests_made=session.requests_made,
                    bytes_fetched=session.bytes_fetched,
                    notes=tuple(notes),
                )
        notes.extend(discovery.notes)

    # --- step 3: supplied URLs, declared patterns, then search ---------------------
    if session.requests_made < max_fetches:
        resolution = Resolver(policy, search=search).resolve(
            mpn,
            brand=brand,
            vendor_code=vendor_code,
            vendor_name=manufacturer,
            supplied=supplied,
        )
        notes.extend(resolution.notes)
        for candidate in resolution.candidates:
            if session.requests_made >= max_fetches:
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
            if outcome.usable and library.coverage_for(mpn):
                documents = _harvest()
                if documents:
                    library.save()
                    return RetrievalAttempt(
                        mpn=mpn,
                        documents=documents,
                        entries=tuple(o.entry for o in outcomes if o.entry is not None),
                        manufacturer=maker,
                        discovery=discovery,
                        resolution=resolution,
                        outcomes=tuple(outcomes),
                        requests_made=session.requests_made,
                        bytes_fetched=session.bytes_fetched,
                        notes=tuple(notes),
                    )

    if session.requests_made:
        library.save()

    return RetrievalAttempt(
        mpn=mpn,
        manufacturer=maker,
        discovery=discovery,
        resolution=resolution,
        outcomes=tuple(outcomes),
        requests_made=session.requests_made,
        bytes_fetched=session.bytes_fetched,
        notes=tuple(notes),
    )


__all__ = ["MAX_FETCHES", "RetrievalAttempt", "retrieve_documents"]
