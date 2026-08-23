"""Part number in, ranked candidate URLs out. The step that was missing.

Everything downstream of "here is a document" already existed: fetch, hash, parse, extract with
verified citations. What did not exist was anything that decided *which* document. So a gap read
``no_source_available`` for a part whose datasheet is on the open web, and the only way to close it
was for a human to paste a URL.

Three arms, tried in order of how much their answer is worth:

1.  **Supplied.** A URL somebody already knows — a golden set's ``reference_urls``, a ``--url``
    flag, a supplier email. Nothing beats being told.
2.  **Declared pattern.** A URL template on the manufacturer's entry in ``schema/sourcing.yaml``,
    e.g. ``https://www.example.com/products/{mpn}``. Deterministic and free, and only present for
    manufacturers where somebody has established the shape. **Never inferred**: a guessed URL
    pattern gets fetched, lands on a 404 or a squatter, and the failure then looks like a retrieval
    bug rather than a missing fact.
3.  **Search, manufacturer-restricted first.** A ``site:`` query against the manufacturer's own
    domain, then an open query if that returns nothing. The open arm is what makes "all over the
    web" real, and it is also why :class:`~axiom.retrieve.policy.SourcePolicy` exists — every result
    is classified before it is fetched, and marketplaces and distributors are dropped here rather
    than being noticed later in the output.

**There is no default search provider, and that is deliberate.** Search is an external service with
a key and a bill; wiring one in silently would mean this module's behaviour depended on ambient
credentials. With no provider configured the resolver returns no search candidates and says so, in
the same words a report can print. A stubbed-out fake provider would be worse than none: it would
make an unconfigured install look like a working one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from axiom.retrieve.policy import Manufacturer, SourcePolicy, SourceTier, SourceVerdict

# Terms appended to a search query to bias it towards a specification rather than a shop page. Not
# a filter — the policy does the filtering — just a nudge at the one point where wording is free.
SPEC_TERMS = ("specifications", "datasheet")

NO_PROVIDER = (
    "no search provider is configured, so only supplied URLs and declared manufacturer patterns "
    "were tried. Pass a SearchProvider to reach the open web."
)
NO_MANUFACTURER = (
    "no manufacturer could be established for this row, so there is no domain to search first and "
    "nothing to prefer. Add the vendor to schema/sourcing.yaml, or supply the brand."
)
DISTRIBUTOR_ONLY = (
    "the only party named on this row is a distributor or buying co-op, which has no datasheet to "
    "fetch. The manufacturer has to come from the brand column, and this row's is a sentinel."
)


class SearchProvider(Protocol):
    """Whatever answers a web query with URLs.

    Deliberately the narrowest possible interface — a query in, URLs out. Anything richer would tie
    this module to one vendor's result shape, and the only thing the resolver needs is the link.
    """

    def __call__(self, query: str, *, limit: int) -> Sequence[str]: ...


@dataclass(frozen=True)
class Candidate:
    """One URL worth fetching, with where it came from and how it ranked."""

    url: str
    verdict: SourceVerdict
    origin: str
    """``supplied``, ``pattern``, ``site_search`` or ``open_search``."""

    rank: float
    query: str | None = None
    manufacturer_id: str | None = None

    @property
    def tier(self) -> SourceTier:
        return self.verdict.tier

    def summary(self) -> dict[str, object]:
        return {
            "url": self.url,
            "origin": self.origin,
            "rank": round(self.rank, 3),
            "query": self.query,
            "manufacturer_id": self.manufacturer_id,
            **{k: v for k, v in self.verdict.summary().items() if k != "url"},
        }


@dataclass(frozen=True)
class Resolution:
    """What resolution found for one part number, including when it found nothing.

    ``rejected`` is kept rather than discarded. "We looked and every result was a marketplace" and
    "we could not think of anywhere to look" are different problems with different fixes, and a
    resolver that reported both as an empty list would hide which one happened.
    """

    mpn: str
    manufacturer: Manufacturer | None = None
    candidates: tuple[Candidate, ...] = ()
    rejected: tuple[SourceVerdict, ...] = ()
    queries: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def resolved(self) -> bool:
        return bool(self.candidates)

    def summary(self) -> dict[str, object]:
        return {
            "mpn": self.mpn,
            "manufacturer": self.manufacturer.id if self.manufacturer else None,
            "candidates": [c.summary() for c in self.candidates],
            "rejected": [v.summary() for v in self.rejected],
            "queries": list(self.queries),
            "notes": list(self.notes),
        }


class Resolver:
    """Turns identity into candidate URLs, under a policy."""

    def __init__(
        self,
        policy: SourcePolicy,
        *,
        search: SearchProvider | None = None,
        per_query: int = 8,
    ) -> None:
        self._policy = policy
        self._search = search
        self._per_query = per_query

    @property
    def has_search(self) -> bool:
        return self._search is not None

    def resolve(
        self,
        mpn: str,
        *,
        brand: str | None = None,
        vendor_code: str | None = None,
        vendor_name: str | None = None,
        supplied: Sequence[str] = (),
        limit: int | None = None,
    ) -> Resolution:
        budget = self._policy.max_candidates_per_sku if limit is None else max(0, limit)
        maker = self._policy.manufacturer_for(
            vendor_code=vendor_code, vendor_name=vendor_name, brand=brand
        )

        candidates: list[Candidate] = []
        rejected: list[SourceVerdict] = []
        queries: list[str] = []
        notes: list[str] = []

        # --- arm 1: supplied -------------------------------------------------------
        for url in supplied:
            verdict = self._policy.classify(url)
            if not verdict.fetchable:
                rejected.append(verdict)
                continue
            candidates.append(
                Candidate(
                    url=url,
                    verdict=verdict,
                    origin="supplied",
                    # Above everything derived. Somebody knew the answer.
                    rank=100.0 + self._policy.spec_score(url),
                    manufacturer_id=verdict.manufacturer_id,
                )
            )

        # --- arm 2: declared patterns ---------------------------------------------
        if maker:
            for url in _pattern_urls(maker, mpn):
                verdict = self._policy.classify(url)
                if not verdict.fetchable:
                    rejected.append(verdict)
                    continue
                candidates.append(
                    Candidate(
                        url=url,
                        verdict=verdict,
                        origin="pattern",
                        rank=50.0 + self._policy.spec_score(url),
                        manufacturer_id=maker.id,
                    )
                )
        elif self._policy.is_known_distributor(
            vendor_code=vendor_code, vendor_name=vendor_name
        ):
            notes.append(DISTRIBUTOR_ONLY)
        else:
            notes.append(NO_MANUFACTURER)

        # --- arm 3: search --------------------------------------------------------
        if self._search is None:
            notes.append(NO_PROVIDER)
        else:
            for query, origin, base in self._query_plan(
                mpn, maker, brand, vendor_name=vendor_name, vendor_code=vendor_code
            ):
                queries.append(query)
                try:
                    urls = self._search(query, limit=self._per_query)
                except Exception as exc:  # noqa: BLE001 - one provider failure must not end a batch
                    notes.append(
                        f"search provider {type(self._search).__name__} failed for {query!r}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                for url in urls:
                    verdict = self._policy.classify(url)
                    if not verdict.fetchable:
                        rejected.append(verdict)
                        continue
                    candidates.append(
                        Candidate(
                            url=url,
                            verdict=verdict,
                            origin=origin,
                            rank=base
                            + self._policy.spec_score(url)
                            # A manufacturer-tier result outranks an unknown one even when the
                            # unknown one has a more spec-looking path.
                            + (5.0 if verdict.tier is SourceTier.MANUFACTURER else 0.0),
                            query=query,
                            manufacturer_id=verdict.manufacturer_id,
                        )
                    )

        # Python's sort is stable, so equal-scored results retain the search provider's relevance
        # order. Sorting ties by URL used to promote arbitrary locales (for example ``de-de`` before
        # ``en-gb``), making classification and extraction harder despite a better-ranked result.
        ranked = _dedupe(sorted(candidates, key=lambda c: -c.rank))
        selected = _select_candidates(ranked, budget)
        return Resolution(
            mpn=mpn,
            manufacturer=maker,
            candidates=tuple(selected),
            rejected=tuple(_dedupe_verdicts(rejected)),
            queries=tuple(queries),
            notes=tuple(notes),
        )

    # ------------------------------------------------------------------ internals

    def _query_plan(
        self,
        mpn: str,
        maker: Manufacturer | None,
        brand: str | None,
        *,
        vendor_name: str | None = None,
        vendor_code: str | None = None,
    ) -> list[tuple[str, str, float]]:
        """The queries to run, in order, with the rank floor each arm's results start from."""
        terms = " ".join(SPEC_TERMS)
        plan: list[tuple[str, str, float]] = []

        if maker:
            # Restricted to the manufacturer's own domain, which is the whole preference expressed
            # as a query rather than as a post-filter — cheaper, and it cannot be diluted by a
            # marketplace outranking the maker in the general index.
            plan.append((f"site:{maker.primary_domain} {mpn}", "site_search", 40.0))

        # Who to name in the open query, most trustworthy first.
        #
        # `vendor_name` is the late addition and it is the one that matters, because the open arm
        # exists precisely for the rows where no domain is declared — and those are exactly the rows
        # where `maker` is None. Without it the query degraded to a bare part number the moment it
        # was
        # needed most: "PDSH4816AF specifications" rather than "Frigidaire PDSH4816AF
        # specifications".
        #
        # Guarded against a *declared distributor*, though. On those rows the vendor field names a
        # buying co-op rather than a maker, and putting it in the query would bias the results
        # towards
        # the distributor's own listings — the results the policy then refuses. Better to search the
        # bare part number than to spend the query asking for the wrong thing.
        label = (brand or (maker.name if maker else "") or "").strip()
        if (
            not label
            and vendor_name
            and not self._policy.is_known_distributor(
                vendor_code=vendor_code, vendor_name=vendor_name
            )
        ):
            label = vendor_name.strip()

        opening = f"{label} {mpn}".strip()
        plan.append((f"{opening} {terms}".strip(), "open_search", 10.0))
        return plan


def _pattern_urls(maker: Manufacturer, mpn: str) -> list[str]:
    """Render a manufacturer's declared URL templates for one part number.

    ``{mpn}`` is the only substitution, in three forms, because part numbers appear in URLs with
    their separators intact, stripped, or lowercased and no single form is right everywhere.
    """
    if not maker.patterns:
        return []
    stripped = "".join(ch for ch in mpn if ch.isalnum())
    forms = {
        "mpn": mpn,
        "mpn_lower": mpn.lower(),
        "mpn_stripped": stripped,
        "mpn_stripped_lower": stripped.lower(),
    }
    urls: list[str] = []
    for template in maker.patterns:
        try:
            urls.append(template.format(**forms))
        except (KeyError, IndexError):
            # A malformed template is a data error in the YAML, not a reason to abandon the row.
            continue
    return urls


def _select_candidates(candidates: Sequence[Candidate], budget: int) -> list[Candidate]:
    """Keep the stable ranking while reserving safe capacity for open-web recovery.

    The normal ranked slice is authoritative. Only a lower-trust site-search candidate may be
    replaced to admit the first open-search result; supplied URLs and deterministic manufacturer
    patterns are never evicted. Starting from the full slice also prevents sparse preferred arms
    from under-filling a budget that open search can satisfy.
    """
    if budget <= 0:
        return []
    selected = list(candidates[:budget])
    if any(candidate.origin == "open_search" for candidate in selected):
        return selected

    open_candidate = next(
        (candidate for candidate in candidates if candidate.origin == "open_search"),
        None,
    )
    if open_candidate is None:
        return selected

    replace_at = next(
        (
            index
            for index in range(len(selected) - 1, -1, -1)
            if selected[index].origin == "site_search"
        ),
        None,
    )
    if replace_at is not None:
        selected[replace_at] = open_candidate
    return selected


def _dedupe(candidates: Sequence[Candidate]) -> list[Candidate]:
    """One entry per URL, keeping the highest-ranked. Arms overlap by design."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for candidate in candidates:
        key = candidate.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _dedupe_verdicts(verdicts: Sequence[SourceVerdict]) -> list[SourceVerdict]:
    seen: set[str] = set()
    out: list[SourceVerdict] = []
    for verdict in verdicts:
        if verdict.url in seen:
            continue
        seen.add(verdict.url)
        out.append(verdict)
    return out


__all__ = [
    "DISTRIBUTOR_ONLY",
    "NO_MANUFACTURER",
    "NO_PROVIDER",
    "SPEC_TERMS",
    "Candidate",
    "Resolution",
    "Resolver",
    "SearchProvider",
]
