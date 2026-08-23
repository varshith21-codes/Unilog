"""Retrieval: deciding what to read, reading it once, and reusing it everywhere it applies.

The step the pipeline was missing. Everything downstream of *here is a document* already worked —
hash, parse, extract with verified citations, score, certify — and everything upstream worked too.
Between them sat a gap that no code occupied: nothing chose the document. So a part whose datasheet
sits on the manufacturer's website recorded ``no_source_available``, and the only way to close it
was for a person to paste a URL.

Three modules, one per rule:

:mod:`axiom.retrieve.policy`
    Where data may be read from. Manufacturer sites are preferred and are the only tier citable as
    ``MFR URL``; marketplaces, mass retail, distributors, datasheet aggregators and user-generated
    content are refused outright; everything else is ``unknown`` — fetchable, cited, never promoted.

:mod:`axiom.retrieve.resolver`
    Part number in, ranked candidate URLs out. Supplied URLs first, then declared manufacturer
    patterns, then a ``site:``-restricted search of the manufacturer's own domain, then the open
    web. Every result is classified before it is fetched.

:mod:`axiom.retrieve.library`
    Fetch once, use everywhere. An index over the artifact store recording which part numbers each
    stored document covers — and which it was checked against and does not — so one accessory
    catalogue serves every part in it and a negative result is paid for once.

:mod:`axiom.retrieve.session`
    The polite, policy-gated fetch loop: the gate runs before the request, ``robots.txt`` is
    honoured, requests to one origin are spaced, and the library is consulted first.

:mod:`axiom.retrieve.discover`
    Searching the manufacturer's own site by reading the search form it published, with no search
    API and no key. The arm that makes *part number plus manufacturer* sufficient on its own.

:mod:`axiom.retrieve.search_duckduckgo`
    The open-web arm that is **on by default**, because it needs no key, no account and no bill.
    Returns URLs and discards every snippet.

:mod:`axiom.retrieve.search_bedrock`
    The same arm over Amazon Bedrock's Web Search tool, for a deployment that would rather keep the
    query inside the AWS boundary. Needs OpenAI GPT model access on the account, which is not
    universal — see that module. Returns URLs and discards every snippet — a snippet is a third
    party's summary, so a value extracted from one would carry a citation whose bytes we never held.

Nothing here makes a model call. The expensive, non-deterministic step is extraction, which happens
after a document has been chosen and is unchanged by any of this.
"""

from axiom.retrieve.discover import Discovery, DiscoveryStep, SiteDiscovery, rank_links
from axiom.retrieve.library import Coverage, DocumentEntry, DocumentLibrary
from axiom.retrieve.policy import (
    ExcludedGroup,
    Manufacturer,
    SourcePolicy,
    SourceTier,
    SourceVerdict,
    SpecHints,
    host_of,
    load_default,
)
from axiom.retrieve.resolver import (
    Candidate,
    Resolution,
    Resolver,
    SearchProvider,
)
from axiom.retrieve.search_duckduckgo import DuckDuckGoSearch
from axiom.retrieve.session import (
    FetchOutcome,
    FetchStatus,
    RetrievalSession,
    RobotsCache,
)

__all__ = [
    "Candidate",
    "Coverage",
    "Discovery",
    "DiscoveryStep",
    "DocumentEntry",
    "DocumentLibrary",
    "DuckDuckGoSearch",
    "ExcludedGroup",
    "FetchOutcome",
    "FetchStatus",
    "Manufacturer",
    "Resolution",
    "Resolver",
    "RetrievalSession",
    "RobotsCache",
    "SearchProvider",
    "SiteDiscovery",
    "SourcePolicy",
    "SourceTier",
    "SourceVerdict",
    "SpecHints",
    "host_of",
    "load_default",
    "rank_links",
]
