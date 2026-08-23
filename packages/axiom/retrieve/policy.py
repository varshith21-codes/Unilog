"""Where product data may be read from — the gate every fetch passes through.

Loaded from ``schema/sourcing.yaml``, so which sites are marketplaces and which domain belongs to
which manufacturer stay where a merchandiser can correct them.

Three tiers, and the middle one is the interesting one:

``manufacturer``
    A host declared as the manufacturer's own. The only tier whose URL may be written to the
    delivery format's ``MFR URL`` column, because that column means the manufacturer's page.

``unknown``
    An unrecognised host. **Fetchable, cited, and never promoted.** A trade association's
    specification, a standards body, a manufacturer whose domain nobody has declared yet — refusing
    all of those would make the open web unreachable, and promoting them would make the citation a
    lie. So the tier travels with the value and the certificate records it.

``excluded``
    Never fetched. Marketplaces, mass retail, distributors, datasheet aggregators, user-generated
    content. The reasoning is in the YAML beside each category; the short version is that a
    retailer's spec table is an unsourced transcription by a party with an incentive to look
    complete, and citing it produces an evidence chain that terminates in someone else's guess
    while looking exactly like one that terminates in an engineering drawing.

**Matching is on the registrable domain, never a substring.** That distinction is the whole
correctness argument of this module:

*   ``amazon.com`` must match ``www.amazon.com`` and ``smile.amazon.com`` — a rule that only matched
    the exact host would be trivially defeated by the ``www``.
*   ``amazon.com`` must **not** match ``notamazon.com`` or ``amazon.com.evil.example``, which is
    exactly what ``"amazon.com" in host`` does. A substring check here is a policy that can be
    bypassed by registering a domain, and worse, one that silently misclassifies an innocent host.

There is no public-suffix list in the standard library, so "registrable domain" is approximated by
suffix-matching on label boundaries: a host matches a rule when it equals the rule or ends with
``"." + rule``. That is exact for the rules this file carries, all of which are already registrable
domains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml


def default_sourcing_path() -> Path:
    return Path(__file__).resolve().parents[3] / "schema" / "sourcing.yaml"


class SourceTier(str, Enum):
    """How much a host's word is worth."""

    MANUFACTURER = "manufacturer"
    UNKNOWN = "unknown"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class SourceVerdict:
    """The decision about one URL, with the reason that produced it.

    The reason is not decoration. A refusal a reviewer cannot explain to a supplier is a refusal
    they will override, and "excluded: industrial_distributor" is the difference between a rule and
    an opinion.
    """

    url: str
    host: str
    tier: SourceTier
    reason: str
    category: str | None = None
    """The excluded category, or the manufacturer id, depending on the tier."""

    manufacturer_id: str | None = None
    matched_domain: str | None = None
    """The rule that matched, so a verdict can be traced back to a line in the YAML."""

    @property
    def fetchable(self) -> bool:
        return self.tier is not SourceTier.EXCLUDED

    @property
    def citable_as_manufacturer(self) -> bool:
        return self.tier is SourceTier.MANUFACTURER

    def summary(self) -> dict[str, object]:
        return {
            "url": self.url,
            "host": self.host,
            "tier": self.tier.value,
            "reason": self.reason,
            "category": self.category,
            "manufacturer_id": self.manufacturer_id,
            "matched_domain": self.matched_domain,
        }


@dataclass(frozen=True)
class Manufacturer:
    """One manufacturer, its domains, and every string the item master might name it by."""

    id: str
    name: str
    domains: tuple[str, ...]
    vendor_codes: frozenset[str] = frozenset()
    """Unilog's own code from ``Part_Manuf``, e.g. ``5831``. Matched exactly and case-folded.

    The most reliable key available: it is stable and unaffected by how the name is spelled that
    day, and it is what keeps `Milwaukee Accessory` (code 4031, a tool maker) apart from Milwaukee
    Valve, whose alias list in ``brands.yaml`` also contains "milwaukee".
    """

    vendors: frozenset[str] = frozenset()
    """``Part_Manuf`` names, folded. Carries the file's own spelling, misspellings included."""

    brands: frozenset[str] = frozenset()
    """Brand-column values, folded. The only key that works for the distributor rows."""

    patterns: tuple[str, ...] = ()
    """URL templates taking ``{mpn}``, for sites whose product URL shape is known.

    Empty for every manufacturer here, and that is the honest default rather than an oversight.
    Establishing a site's URL shape means checking it; a template that was guessed produces a
    confident 404 on every row, and the resulting failure looks like a retrieval bug rather than
    the missing fact it is. Fill one in once it has been verified against a real part.
    """

    @property
    def primary_domain(self) -> str:
        return self.domains[0]


@dataclass(frozen=True)
class ExcludedGroup:
    category: str
    reason: str
    domains: tuple[str, ...]


@dataclass(frozen=True)
class SpecHints:
    """Path fragments that suggest a URL is a specification rather than a shop front."""

    paths: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    negative_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcePolicy:
    """The loaded sourcing rules, and the questions retrieval asks of them."""

    manufacturers: tuple[Manufacturer, ...] = ()
    excluded: tuple[ExcludedGroup, ...] = ()
    hints: SpecHints = field(default_factory=SpecHints)
    min_seconds_between_requests: float = 2.0
    respect_robots_txt: bool = True
    max_candidates_per_sku: int = 4
    unresolved_distributors: tuple[tuple[str, str], ...] = ()
    """(code, name) for vendors that are distributors. No domain will ever be right for them."""

    unresolved_needs_research: tuple[tuple[str, str], ...] = ()

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: Path | str | None = None) -> SourcePolicy:
        """Read the policy, or return an empty one.

        An empty policy is **not** permissive: with no excluded groups nothing is refused, but with
        no manufacturers nothing resolves either, so the orchestrator has no candidates to fetch and
        reports that. Failing closed on a missing file would be worse in a different way — it would
        make an unreadable YAML indistinguishable from a policy that forbids everything.
        """
        path = Path(path) if path else default_sourcing_path()
        if not path.is_file():
            return cls()
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        policy = payload.get("policy") or {}
        hints_raw = payload.get("spec_hints") or {}
        unresolved = payload.get("unresolved") or {}

        return cls(
            manufacturers=tuple(
                _manufacturer(entry) for entry in payload.get("manufacturers") or []
            ),
            excluded=tuple(_excluded(entry) for entry in payload.get("excluded") or []),
            hints=SpecHints(
                paths=tuple(str(p).lower() for p in hints_raw.get("paths") or []),
                suffixes=tuple(str(s).lower() for s in hints_raw.get("suffixes") or []),
                negative_paths=tuple(
                    str(p).lower() for p in hints_raw.get("negative_paths") or []
                ),
            ),
            min_seconds_between_requests=float(
                policy.get("min_seconds_between_requests", 2.0)
            ),
            respect_robots_txt=bool(policy.get("respect_robots_txt", True)),
            max_candidates_per_sku=int(policy.get("max_candidates_per_sku", 4)),
            unresolved_distributors=tuple(
                (str(e.get("code") or ""), str(e.get("name") or ""))
                for e in unresolved.get("distributors") or []
            ),
            unresolved_needs_research=tuple(
                (str(e.get("code") or ""), str(e.get("name") or ""))
                for e in unresolved.get("needs_research") or []
            ),
        )

    # ------------------------------------------------------------------ classification

    def classify(self, url: str) -> SourceVerdict:
        """The tier a URL belongs to, and why.

        Excluded is checked **first**. If a host somehow appeared in both lists the refusal has to
        win, because the cost of wrongly fetching a marketplace is a laundered citation and the cost
        of wrongly refusing a manufacturer is a log line.

        Anything that is not a well-formed ``http(s)`` URL is ``excluded``, not ``unknown``. That
        matters because ``unknown`` means *fetchable*, and a search result that arrives as a
        fragment or a ``javascript:`` link must not be handed to the fetcher on the grounds that
        nobody recognised its host. ``ingest_url`` would refuse it too, but a policy that classifies
        garbage as fetchable is a policy that has to be double-checked by every caller.
        """
        if not _is_web_url(url):
            return SourceVerdict(
                url=url,
                host="",
                tier=SourceTier.EXCLUDED,
                reason=(
                    "not a fetchable web URL: expected an absolute http:// or https:// address "
                    "with a hostname"
                ),
                category="malformed",
            )

        host = host_of(url)

        for group in self.excluded:
            if matched := _match(host, group.domains):
                return SourceVerdict(
                    url=url,
                    host=host,
                    tier=SourceTier.EXCLUDED,
                    reason=f"{group.category}: {group.reason}",
                    category=group.category,
                    matched_domain=matched,
                )

        for maker in self.manufacturers:
            if matched := _match(host, maker.domains):
                return SourceVerdict(
                    url=url,
                    host=host,
                    tier=SourceTier.MANUFACTURER,
                    reason=f"declared domain of {maker.name}",
                    category=maker.id,
                    manufacturer_id=maker.id,
                    matched_domain=matched,
                )

        return SourceVerdict(
            url=url,
            host=host,
            tier=SourceTier.UNKNOWN,
            reason=(
                "host is not a declared manufacturer domain and not excluded; usable as evidence "
                "but not citable as the manufacturer's own page"
            ),
        )

    def allows(self, url: str) -> bool:
        return self.classify(url).fetchable

    # ------------------------------------------------------------------ manufacturer lookup

    def manufacturer_for(
        self,
        *,
        vendor_code: str | None = None,
        vendor_name: str | None = None,
        brand: str | None = None,
    ) -> Manufacturer | None:
        """Resolve a manufacturer from whatever the row happens to carry.

        Order is by reliability, not by convenience. The vendor code is exact; the brand is next,
        because on a distributor row it is the *only* thing naming the manufacturer; the vendor name
        is last, because it is free text that is frequently the distributor rather than the maker.
        """
        if vendor_code:
            folded = vendor_code.strip().casefold()
            for maker in self.manufacturers:
                if folded in maker.vendor_codes:
                    return maker

        if brand:
            folded = _fold(brand)
            for maker in self.manufacturers:
                if folded in maker.brands:
                    return maker

        if vendor_name:
            folded = _fold(vendor_name)
            for maker in self.manufacturers:
                if folded in maker.vendors:
                    return maker

        return None

    def is_known_distributor(self, *, vendor_code: str | None, vendor_name: str | None) -> bool:
        """Whether this vendor is declared a distributor, so no domain will ever be right for it."""
        code = (vendor_code or "").strip().casefold()
        name = _fold(vendor_name or "")
        for declared_code, declared_name in self.unresolved_distributors:
            if code and code == declared_code.strip().casefold():
                return True
            if name and name == _fold(declared_name):
                return True
        return False

    # ------------------------------------------------------------------ ranking

    def spec_score(self, url: str) -> float:
        """How much a URL looks like a specification rather than a shop front.

        Ranking only. It never decides fetchability — a manufacturer's landing page is a legitimate
        source, it is just a worse first choice than the datasheet PDF two clicks in.
        """
        path = (urlparse(url).path or "").lower()
        score = 0.0
        if any(path.endswith(suffix) for suffix in self.hints.suffixes):
            score += 2.0
        score += sum(1.0 for fragment in self.hints.paths if fragment in path)
        score -= sum(2.0 for fragment in self.hints.negative_paths if fragment in path)
        return score


# ----------------------------------------------------------------------------- helpers


WEB_SCHEMES = frozenset({"http", "https"})
"""Matches ``axiom.ingest.web.ALLOWED_SCHEMES``. The fetcher enforces this too; classifying on it
here means the policy never labels something fetchable that the fetcher would refuse."""


def _is_web_url(url: str) -> bool:
    """Whether this is an absolute http(s) URL with a hostname."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in WEB_SCHEMES:
        return False
    host = (parsed.hostname or "").strip()
    # A space in a hostname means the string was never a URL. `urlparse` is permissive about it.
    return bool(host) and " " not in host


def host_of(url: str) -> str:
    """The lowercased hostname, with a trailing dot and any port removed.

    A trailing dot is a legal fully-qualified form — ``amazon.com.`` resolves identically — so
    leaving it on would let a rule be bypassed by typing one extra character.

    Accepts a bare host as well as a full URL, so a caller holding ``www.example.com`` can ask about
    it without synthesising a scheme.
    """
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
    except ValueError:
        return ""
    return (parsed.hostname or "").strip().lower().rstrip(".")


def _match(host: str, domains: tuple[str, ...] | frozenset[str]) -> str | None:
    """The rule that covers ``host``, matched on label boundaries. See the module docstring."""
    for domain in domains:
        rule = domain.strip().lower().rstrip(".")
        if not rule:
            continue
        if host == rule or host.endswith(f".{rule}"):
            return rule
    return None


def _fold(text: str) -> str:
    """Case- and punctuation-insensitive form, for matching messy vendor and brand strings.

    ``Black & Decker/dewlt`` and ``black and decker dewlt`` have to reach the same key, and
    ``3 M Co`` has to survive the space inside the name.
    """
    lowered = (text or "").casefold().replace("&", " and ")
    kept = [char if char.isalnum() else " " for char in lowered]
    return " ".join("".join(kept).split())


def _manufacturer(entry: dict) -> Manufacturer:
    return Manufacturer(
        id=str(entry.get("id") or ""),
        name=str(entry.get("name") or entry.get("id") or ""),
        domains=tuple(str(d).strip().lower() for d in entry.get("domains") or []),
        vendor_codes=frozenset(
            str(c).strip().casefold() for c in entry.get("vendor_codes") or []
        ),
        vendors=frozenset(_fold(str(v)) for v in entry.get("vendors") or []),
        brands=frozenset(_fold(str(b)) for b in entry.get("brands") or []),
        patterns=tuple(str(p) for p in entry.get("patterns") or []),
    )


def _excluded(entry: dict) -> ExcludedGroup:
    return ExcludedGroup(
        category=str(entry.get("category") or "excluded"),
        reason=" ".join(str(entry.get("reason") or "").split()),
        domains=tuple(str(d).strip().lower() for d in entry.get("domains") or []),
    )


@lru_cache(maxsize=1)
def load_default() -> SourcePolicy:
    """The policy, loaded once per process.

    Cached because the resolver asks it a question per row and a thousand-row batch would otherwise
    re-read and re-parse the same YAML a thousand times.
    """
    return SourcePolicy.load()


__all__ = [
    "ExcludedGroup",
    "Manufacturer",
    "SourcePolicy",
    "SourceTier",
    "SourceVerdict",
    "SpecHints",
    "host_of",
    "load_default",
]
