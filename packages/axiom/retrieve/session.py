"""The polite, policy-gated fetch loop — the orchestration ``ingest_url`` deliberately left out.

``axiom.ingest.web`` says so in its own docstring: *"What this deliberately is not is a crawler.
One URL, one artifact, no link following, no robots negotiation, no rate limiting — those belong
with the batch orchestration this does not yet have."* This is that orchestration.

Four things happen here that do not happen anywhere else, and each exists because doing it per
call-site would mean forgetting it at one:

*   **The policy gate runs before the request, not after.** A marketplace URL is never fetched at
    all, so its bytes never enter the store and can never be cited by accident. Filtering the output
    instead would leave a laundered citation one bug away.
*   **robots.txt is honoured.** We are reading sites that did not ask to be read. Disclosing an
    honest User-Agent (``axiom-ingest/…``) and then ignoring the file that tells us what it may
    fetch would make the disclosure worse than useless.
*   **One request per domain at a time, spaced.** A thousand-row batch against one manufacturer is a
    thousand requests to one origin. The gap is not a limit anyone imposed on us; it is the minimum
    courtesy owed to a server we are using for free.
*   **The library is consulted first.** A URL several rows resolve to is fetched once, and a
    document already covering a part means no request happens at all.

Every clock and every network call is injectable, because a test that needs the internet to check
rate limiting is a test that gets skipped, and then this code rots.
"""

from __future__ import annotations

import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from axiom.ingest import IngestError, LocalArtifactStore, ingest_url
from axiom.ingest.web import DEFAULT_MAX_BYTES, USER_AGENT, Fetcher, fetch_url
from axiom.retrieve.library import DocumentEntry, DocumentLibrary
from axiom.retrieve.policy import SourcePolicy, SourceTier
from axiom.retrieve.resolver import Candidate


class FetchStatus(str, Enum):
    FETCHED = "fetched"
    """Bytes arrived and were stored."""

    REUSED = "reused"
    """Already in the library under this URL. No request was made."""

    REFUSED_POLICY = "refused_policy"
    REFUSED_ROBOTS = "refused_robots"
    FAILED = "failed"
    """The request was made and did not produce an ingestable document."""


@dataclass(frozen=True)
class TransientFetch:
    """Bytes fetched for **navigation**, deliberately not stored and not indexed.

    The distinction this type exists to enforce was a real bug, found on the first live run. A
    sitemap, a homepage and a search-results page are all *maps* — they tell you where a document
    is.
    None of them is a source document. But the first version put everything through
    :meth:`RetrievalSession.fetch`, so they landed in the library, and then
    :meth:`~axiom.retrieve.library.DocumentLibrary.coverage_for` searched them for part numbers and
    found plenty: a search-results page listing forty products was recorded as *covering* all forty.
    A citation could then point at a results page as the source of a specification.

    So navigation gets its own path. Same policy gate, same robots check, same per-origin spacing —
    the courtesy owed to a server does not depend on what we intend to do with the response — but
    the
    bytes are returned to the caller and forgotten. Nothing is hashed, nothing is archived, nothing
    can be cited.
    """

    url: str
    status: FetchStatus
    data: bytes | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.data is not None

    def text(self) -> str:
        """Decoded body, for reading structure. Empty when there was none."""
        if not self.data:
            return ""
        for encoding in ("utf-8", "cp1252"):
            try:
                return self.data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return self.data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class FetchOutcome:
    candidate: Candidate
    status: FetchStatus
    detail: str = ""
    entry: DocumentEntry | None = None

    @property
    def usable(self) -> bool:
        return self.entry is not None

    def summary(self) -> dict[str, object]:
        return {
            "url": self.candidate.url,
            "status": self.status.value,
            "tier": self.candidate.tier.value,
            "origin": self.candidate.origin,
            "detail": self.detail,
            "sha256": self.entry.sha256 if self.entry else None,
        }


class RobotsCache:
    """One ``robots.txt`` per host, fetched once and remembered.

    A host that does not serve one, or serves an error, is treated as **permitting** the fetch. That
    is the documented convention rather than a shortcut: absence of a policy is not a prohibition,
    and failing closed here would make an unrelated 500 look like a refusal to be read.
    """

    def __init__(self, reader: Callable[[str], str | None] | None = None) -> None:
        self._reader = reader or _read_robots
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allows(self, url: str, *, user_agent: str = USER_AGENT) -> tuple[bool, str]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)

        parser = self._parsers[origin]
        if parser is None:
            return True, "no robots.txt published"
        if parser.can_fetch(user_agent, url):
            return True, "permitted by robots.txt"
        return False, f"disallowed by {origin}/robots.txt"

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        text = self._reader(f"{origin}/robots.txt")
        if text is None:
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        return parser

    def crawl_delay(self, url: str, *, user_agent: str = USER_AGENT) -> float | None:
        """A delay the site asked for, if any. Honoured when longer than our own floor."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._parsers.get(origin)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(user_agent)
        except Exception:  # noqa: BLE001 - a malformed directive must not stop the fetch
            return None
        return float(delay) if delay else None


def _read_robots(url: str) -> str | None:
    """Fetch a robots.txt with the stdlib. Returns None when there isn't one to read."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            # 500 KB is far more than any real robots.txt and bounds a hostile one.
            return response.read(512_000).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - absence, error and timeout are all "no policy published"
        return None


class RetrievalSession:
    """Fetches candidates under a policy, politely, into a library."""

    def __init__(
        self,
        *,
        policy: SourcePolicy,
        store: LocalArtifactStore,
        library: DocumentLibrary,
        fetcher: Fetcher | None = None,
        robots: RobotsCache | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        timeout: float = 30.0,
        supplier_id: str | None = None,
    ) -> None:
        self._policy = policy
        self._store = store
        self._library = library
        self._fetcher = fetcher
        self._robots = robots if robots is not None else RobotsCache()
        self._timeout = timeout
        self._supplier_id = supplier_id

        import time

        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._last_request: dict[str, float] = {}

        self.requests_made = 0
        self.bytes_fetched = 0

    def fetch(self, candidate: Candidate) -> FetchOutcome:
        """Fetch one candidate, or explain why it was not fetched."""
        # 1. Policy, before anything touches the network.
        if not candidate.verdict.fetchable:
            return FetchOutcome(
                candidate, FetchStatus.REFUSED_POLICY, candidate.verdict.reason
            )

        # 2. Already have it. Checked before robots, because reuse involves no request and a site's
        #    robots file cannot retroactively forbid bytes we already hold and have cited.
        if existing := self._library.seen_url(candidate.url):
            return FetchOutcome(
                candidate,
                FetchStatus.REUSED,
                f"already stored as {existing.sha256[:12]}",
                entry=existing,
            )

        # 3. robots.txt.
        if self._policy.respect_robots_txt:
            allowed, why = self._robots.allows(candidate.url)
            if not allowed:
                return FetchOutcome(candidate, FetchStatus.REFUSED_ROBOTS, why)

        # 4. Politeness, per origin.
        self._wait_for(candidate.url)

        try:
            artifact = ingest_url(
                candidate.url,
                self._store,
                supplier_id=self._supplier_id,
                fetcher=self._fetcher,
                timeout=self._timeout,
                license_note=(
                    f"retrieved from {candidate.verdict.host} "
                    f"({candidate.verdict.tier.value} tier) for product data enrichment; "
                    f"review the site's terms before republishing any asset from it"
                ),
            )
        except IngestError as exc:
            return FetchOutcome(candidate, FetchStatus.FAILED, str(exc))
        finally:
            self.requests_made += 1

        self.bytes_fetched += artifact.size_bytes
        entry = self._library.register(
            artifact,
            host=candidate.verdict.host,
            tier=candidate.verdict.tier.value,
            manufacturer_id=candidate.manufacturer_id,
        )
        detail = (
            "identical bytes were already stored under a different URL"
            if artifact.was_already_stored
            else f"{artifact.size_bytes:,} bytes"
        )
        return FetchOutcome(candidate, FetchStatus.FETCHED, detail, entry=entry)

    def fetch_transient(
        self, url: str, *, max_bytes: int | None = None
    ) -> TransientFetch:
        """Fetch a navigation artifact — a sitemap, a homepage, a results page — without storing it.

        See :class:`TransientFetch` for why this is a separate path rather than a flag on
        :meth:`fetch`. Everything a server is owed still happens here; only the archiving does not.

        ``max_bytes`` is overridable because sitemaps are large. Milwaukee's is 12 MB of XML, which
        is
        a perfectly reasonable thing to read once and a ridiculous thing to keep.
        """
        verdict = self._policy.classify(url)
        if not verdict.fetchable:
            return TransientFetch(url, FetchStatus.REFUSED_POLICY, detail=verdict.reason)

        if self._policy.respect_robots_txt:
            allowed, why = self._robots.allows(url)
            if not allowed:
                return TransientFetch(url, FetchStatus.REFUSED_ROBOTS, detail=why)

        self._wait_for(url)
        fetch = self._fetcher or fetch_url
        try:
            resource = fetch(
                url,
                timeout=self._timeout,
                max_bytes=max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES,
            )
        except IngestError as exc:
            return TransientFetch(url, FetchStatus.FAILED, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - a navigation read must not end a batch
            return TransientFetch(url, FetchStatus.FAILED, detail=f"{type(exc).__name__}: {exc}")
        finally:
            self.requests_made += 1

        data = resource.data or b""
        if data[:2] == b"\x1f\x8b":
            # Sitemaps are routinely gzipped and served without a Content-Encoding that urllib
            # unwraps. Decompressed here because the caller wants XML, not a container.
            import gzip

            try:
                data = gzip.decompress(data)
            except OSError as exc:
                return TransientFetch(url, FetchStatus.FAILED, detail=f"bad gzip: {exc}")

        self.bytes_fetched += len(data)
        return TransientFetch(url, FetchStatus.FETCHED, data=data, detail=f"{len(data):,} bytes")

    def _wait_for(self, url: str) -> None:
        """Space requests to one origin, honouring a longer Crawl-delay if the site asks for one."""
        host = urlparse(url).hostname or ""
        gap = self._policy.min_seconds_between_requests
        if (asked := self._robots.crawl_delay(url)) and asked > gap:
            gap = asked

        previous = self._last_request.get(host)
        now = self._clock()
        if previous is not None and (waited := gap - (now - previous)) > 0:
            self._sleep(waited)
            now = self._clock()
        self._last_request[host] = now

    def stats(self) -> dict[str, object]:
        return {
            "requests_made": self.requests_made,
            "bytes_fetched": self.bytes_fetched,
            "origins_touched": len(self._last_request),
        }


__all__ = [
    "FetchOutcome",
    "FetchStatus",
    "RetrievalSession",
    "RobotsCache",
    "SourceTier",
    "TransientFetch",
]
