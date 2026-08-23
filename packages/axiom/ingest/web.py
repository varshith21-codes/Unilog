"""URL ingestion: a manufacturer page or a linked datasheet, hashed like anything else.

The third arrival channel, alongside flat files and local documents. It matters more than it
looks: for a newly released part the manufacturer's web page is frequently the *only* source, and
its ordering table is often ahead of the printed catalogue.

Everything here funnels into :func:`axiom.ingest.ingest_bytes`, so a crawled page gets the same
treatment as a supplier PDF — content-addressed, immutable, and citable by hash. The one addition
is that ``uri`` records the URL the bytes came from rather than the local store path, because a
citation that cannot say *where on the internet* a claim came from is not provenance.

**Fetched bytes are untrusted input.** Concretely, that means:

*   Only ``http`` and ``https`` are fetchable. ``file://``, ``ftp://`` and ``data:`` are refused,
    so a URL from a spreadsheet cell cannot be used to read the local disk.
*   ``https`` is required by default. Hashing bytes that arrived over a channel with no integrity
    guarantee and calling the result provenance would be self-defeating, so plain ``http`` has to
    be asked for explicitly and is recorded in the document when used.
*   Responses are read against a byte ceiling and abandoned past it, so a hostile or misconfigured
    endpoint cannot exhaust memory.
*   The document is never executed, and nothing inside it is followed. Scripts, styles and frames
    are discarded by the HTML renderer at parse time.

What this deliberately is **not** is a crawler. One URL, one artifact, no link following, no
robots negotiation, no rate limiting — those belong with the batch orchestration this does not yet
have, and pretending otherwise would invite pointing it at a site.

The loopback and private-address guard below stops the obvious mistakes. It is **not** a substitute
for egress control: it resolves nothing, so a hostname that points at an internal address defeats
it. Before this runs anywhere but an operator's laptop it needs to sit behind a network policy that
enforces the same rule where it cannot be raced.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from axiom.core.evidence import DocumentType
from axiom.ingest.fabric import IngestedArtifact, IngestError, ingest_bytes
from axiom.ingest.store import ArtifactStore

USER_AGENT = "axiom-ingest/0.1 (+product data enrichment; contact your AXIOM operator)"
"""Identifies the fetcher honestly. A crawler that disguises itself as a browser is making a
claim about who it is, and this system's whole argument is that its claims are checkable."""

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Content types worth ingesting, mapped to how the artifact should be typed when magic-byte
# sniffing cannot decide. Anything else is refused rather than stored: a login page or an error
# document stored as a "datasheet" is worse than a failed fetch, because it will be cited.
_CONTENT_TYPES: dict[str, tuple[DocumentType, str]] = {
    "application/pdf": (DocumentType.SPEC_SHEET, ".pdf"),
    "text/html": (DocumentType.WEB_PAGE, ".html"),
    "application/xhtml+xml": (DocumentType.WEB_PAGE, ".html"),
    "text/plain": (DocumentType.SPEC_SHEET, ".txt"),
    "text/csv": (DocumentType.SUPPLIER_FEED, ".csv"),
    "text/tab-separated-values": (DocumentType.SUPPLIER_FEED, ".tsv"),
    "application/csv": (DocumentType.SUPPLIER_FEED, ".csv"),
    "application/vnd.ms-excel": (DocumentType.SUPPLIER_FEED, ".xls"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        DocumentType.SUPPLIER_FEED,
        ".xlsx",
    ),
    "application/xml": (DocumentType.SUPPLIER_FEED, ".xml"),
    "text/xml": (DocumentType.SUPPLIER_FEED, ".xml"),
    "image/png": (DocumentType.PRODUCT_IMAGE, ".png"),
    "image/jpeg": (DocumentType.PRODUCT_IMAGE, ".jpg"),
    # Portals serve real documents under this constantly. Allowed, but the type is then decided
    # by magic bytes rather than by the header.
    "application/octet-stream": (DocumentType.UNKNOWN, ""),
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UrlFetchError(IngestError):
    """Raised when a URL cannot be fetched, or returns something not worth storing."""


@dataclass(frozen=True)
class FetchedResource:
    """The bytes a URL returned, plus what the server said about them."""

    data: bytes
    url: str
    """The URL actually fetched, after redirects. Provenance must record where the bytes came
    from, not where the operator was pointed."""

    content_type: str = ""
    status: int = 200
    last_modified: str | None = None

    @property
    def media_type(self) -> str:
        """Content type without parameters, lowercased."""
        return self.content_type.split(";", 1)[0].strip().lower()


class Fetcher(Protocol):
    def __call__(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> FetchedResource: ...


def _refuse_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address, shown: str) -> None:
    if address.is_loopback or address.is_private or address.is_link_local or address.is_reserved:
        raise UrlFetchError(
            f"refusing to fetch a non-public address: {shown}. A product source lives on the "
            f"public internet; an internal address here is a mistake or an attempt at one."
        )


def _check_resolved(host: str) -> None:
    """Resolve ``host`` and refuse if *any* address it answers with is not public.

    This is the guard the literal check cannot be. ``http://169.254.169.254/`` is caught by
    inspecting the URL; ``http://metadata.attacker.example/`` resolving to the same address is not,
    and that is the shape a real SSRF attempt takes against an endpoint that fetches caller-supplied
    URLs.

    Every resolved address is checked rather than the first, because a hostname answering with one
    public address and one private one would otherwise pass and then connect to whichever the
    resolver handed the socket.

    This does **not** close the race: DNS is re-resolved when the connection is actually made, so a
    record with a one-second TTL can answer publicly here and privately there. Closing that requires
    connecting to a pinned address, which the stdlib opener does not expose. Treat this as raising
    the cost of the attack, not as egress control — the module docstring's point about needing a
    network policy stands, and it stands harder for the HTTP endpoint than for a CLI operator.

    Off by default. Turning it on unconditionally would put a DNS lookup on the path of every
    unit test that ingests a URL through an injected fetcher, which is most of them, and a test
    suite that needs a resolver is a test suite that gets skipped.
    """
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise UrlFetchError(f"could not resolve {host!r}: {exc}") from exc

    if not infos:
        raise UrlFetchError(f"{host!r} resolved to no addresses")

    for info in infos:
        literal = info[4][0]
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:  # pragma: no cover - getaddrinfo does not return non-literals
            continue
        _refuse_private(address, f"{host!r} resolves to {literal}")


def _check_url(
    url: str, *, allow_insecure_http: bool, verify_public_address: bool = False
) -> str:
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlFetchError(
            f"refusing to fetch scheme {parsed.scheme!r}: only "
            f"{', '.join(sorted(ALLOWED_SCHEMES))} are allowed, so a URL cannot be used to read "
            f"the local filesystem"
        )
    if parsed.scheme.lower() == "http" and not allow_insecure_http:
        raise UrlFetchError(
            "refusing to fetch over plain http: the bytes would be hashed as provenance without "
            "any integrity guarantee. Pass allow_insecure_http=True to accept that trade-off "
            "explicitly."
        )
    if not parsed.hostname:
        raise UrlFetchError(f"no host in URL: {url!r}")

    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise UrlFetchError(f"refusing to fetch a loopback host: {host!r}")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if verify_public_address:
            _check_resolved(host)
        return url
    _refuse_private(address, repr(host))
    return url


def _guarded_opener(verify_public_address: bool):
    """An opener that re-checks every redirect target before following it.

    Checking only the URL the caller gave us and only the URL we ended up at leaves the hops in
    between unguarded, and a redirect chain is the ordinary way past a front-door check: the caller
    supplies a public URL whose only job is to 302 at an internal address. urllib follows redirects
    inside ``urlopen``, so by the time the final URL is available the request has already been made.
    """
    import urllib.request

    class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - stdlib name
            # allow_insecure_http is True here to match the pre-existing behaviour: a redirect that
            # downgrades to http is followed and then *recorded* in the document's licence note,
            # rather than refused. The scheme allowlist and the address guard still apply.
            _check_url(
                newurl,
                allow_insecure_http=True,
                verify_public_address=verify_public_address,
            )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_CheckedRedirect)


def check_url(
    url: str, *, allow_insecure_http: bool = False, verify_public_address: bool = False
) -> str:
    """Apply the fetch guard without fetching. Raises :class:`UrlFetchError` on a refusal.

    Exists for a caller that wants to refuse a URL *early* — before it does other work, or
    before it reports a different problem that would mask this one. The single-SKU
    enrichment endpoint uses it so an internal address is refused ahead of its
    already-enriched check, because a security refusal should not be hidden behind a
    bookkeeping one.

    Public so that caller does not need a second copy of the scheme and address rules. A guard that
    disagrees with the thing it guards is worse than no guard.

    ``verify_public_address`` defaults to False here, which makes this the free, no-DNS half
    of the check. Leave it that way in a pre-flight: resolving would put a lookup in front of
    every request and would report an unresolvable host as a validation error, when the
    honest answer for that is a failed fetch.
    """
    return _check_url(
        url,
        allow_insecure_http=allow_insecure_http,
        verify_public_address=verify_public_address,
    )


def fetch_url(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    verify_public_address: bool = False,
) -> FetchedResource:
    """Fetch a URL with the standard library, capped and timed out.

    Stdlib rather than ``requests`` on purpose: this is the only place in the codebase that makes
    an outbound HTTP call that is not AWS, and it is not worth a dependency.

    ``verify_public_address`` resolves the hostname and refuses any non-public answer, at every hop.
    See :func:`_check_resolved` for what it does and does not buy.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # noqa: S310 - scheme is validated by _check_url
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    opener = _guarded_opener(verify_public_address)
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            # One byte past the ceiling is enough to know it was exceeded, and stops here rather
            # than reading the rest of an arbitrarily large body.
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise UrlFetchError(
                    f"{url} exceeds the {max_bytes:,}-byte ceiling; refusing to buffer it"
                )
            final_url = response.geturl()
            headers = response.headers
            status = getattr(response, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        raise UrlFetchError(f"{url} returned HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UrlFetchError(f"could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UrlFetchError(f"{url} timed out after {timeout:g}s") from exc

    # A redirect can leave the allowed-scheme set. The opener above checks each hop before following
    # it; re-checking the destination costs nothing and this is the value that becomes the citation.
    _check_url(
        final_url, allow_insecure_http=True, verify_public_address=verify_public_address
    )

    return FetchedResource(
        data=payload,
        url=final_url,
        content_type=headers.get("Content-Type", "") or "",
        status=int(status),
        last_modified=headers.get("Last-Modified"),
    )


def filename_for(url: str, media_type: str = "") -> str:
    """A filename for a URL, so the suffix routes the parser correctly.

    The suffix is load-bearing downstream: ``read_flat_file`` and ``parse_artifact`` both consult
    it. A URL ending in a directory or a query string yields nothing usable, so the host stands in
    and the extension comes from the declared media type.
    """
    path = unquote(urlparse(url).path or "")
    stem = _SAFE_NAME.sub("-", Path(path).name).strip("-.")

    suffix = Path(stem).suffix.lower()
    declared = _CONTENT_TYPES.get(media_type, (DocumentType.UNKNOWN, ""))[1]

    if not stem:
        host = _SAFE_NAME.sub("-", urlparse(url).hostname or "download").strip("-.")
        return f"{host}{declared or '.html'}"
    if not suffix and declared:
        return f"{stem}{declared}"
    return stem


def ingest_url(
    url: str,
    store: ArtifactStore,
    *,
    supplier_id: str | None = None,
    fetcher: Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_insecure_http: bool = False,
    allow_any_content_type: bool = False,
    license_note: str | None = None,
    revision_label: str | None = None,
    verify_public_address: bool = False,
) -> IngestedArtifact:
    """Fetch a URL and store it as a content-addressed artifact.

    ``fetcher`` is injectable so the whole path is testable without a network. That is not a
    convenience: a test that needs the internet to check filename derivation is a test that will
    be skipped, and then this code rots.

    ``license_note`` is worth filling in for web sources specifically. Crawled manufacturer
    content carries terms that a supplier-supplied PDF does not, and the certificate is the place
    an auditor will look for it.

    ``verify_public_address`` should be set by any caller whose URL came from someone else. It
    resolves the hostname and refuses a non-public answer at every hop, which is the difference
    between a guard that stops a typo and one that stops an attempt. It is off by default because
    the literal check needs no resolver and most callers here are tests with an injected fetcher;
    see :func:`_check_resolved`. When a ``fetcher`` is injected the flag applies to the front-door
    check only — the injected function decides its own network behaviour.
    """
    _check_url(
        url,
        allow_insecure_http=allow_insecure_http,
        verify_public_address=verify_public_address,
    )
    if fetcher is not None:
        fetch: Fetcher = fetcher
    elif verify_public_address:
        from functools import partial

        fetch = partial(fetch_url, verify_public_address=True)
    else:
        fetch = fetch_url
    resource = fetch(url, timeout=timeout, max_bytes=max_bytes)

    if not resource.data:
        raise UrlFetchError(f"{url} returned an empty body")

    media = resource.media_type
    if media and media not in _CONTENT_TYPES and not allow_any_content_type:
        raise UrlFetchError(
            f"{url} returned {media!r}, which is not an ingestable document type. This is usually "
            f"a login wall or an error page; storing it would put an unrelated document behind a "
            f"citation. Pass allow_any_content_type=True to store it anyway."
        )

    filename = filename_for(resource.url, media)
    declared_type = _CONTENT_TYPES.get(media, (DocumentType.UNKNOWN, ""))[0]

    note = license_note
    if urlparse(resource.url).scheme.lower() == "http":
        # Recorded rather than merely warned about. Six months later the only way to know a
        # citation's bytes arrived unauthenticated is if the document says so.
        insecure = "fetched over plain http; bytes are not integrity-protected in transit"
        note = f"{note}; {insecure}" if note else insecure

    return ingest_bytes(
        resource.data,
        store,
        filename=filename,
        # The URL, not the store path. This is the field that makes a web citation resolvable.
        source_uri=resource.url,
        supplier_id=supplier_id,
        # None lets ingest_bytes sniff magic bytes, which beats a declared header.
        doc_type=declared_type if declared_type is not DocumentType.UNKNOWN else None,
        revision_label=revision_label or resource.last_modified,
        license_note=note,
        fetched_at=datetime.now(UTC),
    )


def is_url(candidate: str) -> bool:
    """Whether a CLI argument should be treated as a URL rather than a path.

    Only the fetchable schemes count. A Windows path like ``C:\\feeds\\x.csv`` parses with a
    one-letter scheme, so testing for "has a scheme" would misroute it.
    """
    return urlparse(candidate).scheme.lower() in ALLOWED_SCHEMES
