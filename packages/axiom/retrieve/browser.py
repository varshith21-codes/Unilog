"""Bounded browser rendering for product pages whose useful content requires JavaScript.

The browser is an optional last-mile transport. It does not decide whether a document covers a SKU
and it does not extract product values. It returns bounded rendered HTML and status-bound PDF
downloads as :class:`~axiom.ingest.web.FetchedResource` objects so the normal policy, hashing,
parsing, coverage and citation path remains authoritative.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from axiom.ingest.web import USER_AGENT, FetchedResource

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_REQUESTS = 40
DEFAULT_MAX_PDFS = 3
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font", "stylesheet"})


class BrowserFetchError(RuntimeError):
    """Browser rendering was unavailable or failed safely."""

    def __init__(self, message: str, *, requests_made: int = 0) -> None:
        super().__init__(message)
        self.requests_made = requests_made


@dataclass(frozen=True)
class RenderedResult:
    resources: tuple[FetchedResource, ...] = ()
    requests_made: int = 0
    notes: tuple[str, ...] = ()


class RenderedFetcher(Protocol):
    def __call__(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
        authorize: Callable[[str], None],
    ) -> RenderedResult: ...


@dataclass
class PlaywrightRenderer:
    """Render one page in an isolated Chromium context and retain HTML/PDF evidence only."""

    max_requests: int = DEFAULT_MAX_REQUESTS
    max_pdfs: int = DEFAULT_MAX_PDFS
    headless: bool = True
    launch_args: Sequence[str] = field(default_factory=tuple)

    def __call__(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int,
        authorize: Callable[[str], None],
    ) -> RenderedResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserFetchError(
                "Playwright is not installed; install the axiom[browser] extra and Chromium"
            ) from exc

        resources: list[FetchedResource] = []
        notes: list[str] = []
        request_count = 0
        pdf_keys: set[tuple[str, str]] = set()
        pdf_responses: dict[str, tuple[int, str]] = {}
        observed_response_urls: set[str] = set()
        ambiguous_response_urls: set[str] = set()
        pending_downloads: list[object] = []
        retained_bytes = 0
        main_frame_status: int | None = None
        timeout_ms = max(1, int(timeout * 1000))

        def approve(request_url: str) -> bool:
            nonlocal request_count
            parsed = urlparse(request_url)
            if parsed.scheme not in {"http", "https"}:
                return False
            if request_count >= self.max_requests:
                return False
            request_count += 1
            try:
                authorize(request_url)
            except Exception as exc:  # noqa: BLE001 - blocked subresources are diagnostic only
                notes.append(f"blocked {request_url}: {exc}")
                return False
            return True

        def keep_pdf(
            data: bytes, response_url: str, content_type: str, *, status: int
        ) -> None:
            nonlocal retained_bytes
            if not 200 <= status < 300:
                notes.append(f"discarded PDF {response_url}: HTTP {status}")
                return
            if len(pdf_keys) >= self.max_pdfs or not data or len(data) > max_bytes:
                return
            # A download name or response header is caller-controlled. PDF magic is required before
            # bytes can be typed as a specification and become citable evidence.
            if not data.startswith(b"%PDF-"):
                notes.append(f"discarded non-PDF response from {response_url}")
                return
            if retained_bytes + len(data) > max_bytes * self.max_pdfs:
                notes.append("discarded PDF after reaching the rendered-artifact byte ceiling")
                return
            try:
                authorize(response_url)
            except Exception as exc:  # noqa: BLE001 - a final redirect can change policy
                notes.append(f"discarded PDF {response_url}: {exc}")
                return
            key = (response_url, hashlib.sha256(data).hexdigest())
            if key in pdf_keys:
                return
            pdf_keys.add(key)
            retained_bytes += len(data)
            resources.append(
                FetchedResource(
                    data=data,
                    url=response_url,
                    content_type=content_type or "application/pdf",
                    status=status,
                )
            )

        try:
            with (
                tempfile.TemporaryDirectory(prefix="axiom-browser-") as download_dir,
                sync_playwright() as playwright,
            ):
                browser = playwright.chromium.launch(
                    headless=self.headless,
                    downloads_path=download_dir,
                    args=list(self.launch_args),
                )
                context = browser.new_context(
                    accept_downloads=True,
                    service_workers="block",
                    user_agent=USER_AGENT,
                    locale="en-US",
                )

                def route_request(route) -> None:
                    request = route.request
                    # Count every routed browser request, including repeated polling and resource
                    # types that are intentionally aborted. Otherwise a page can evade the cap by
                    # repeatedly requesting the same URL or blocked assets.
                    if not approve(request.url):
                        route.abort()
                        return
                    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        route.abort()
                        return
                    route.continue_()

                def capture_response(response) -> None:
                    nonlocal main_frame_status
                    request = response.request
                    if (
                        request.is_navigation_request()
                        and request.frame == page.main_frame
                    ):
                        # Keep the latest main-frame response. A page can navigate again after
                        # page.goto() returns; pairing the final DOM with the first status would
                        # turn a later 404/login page into apparent 200 evidence.
                        main_frame_status = response.status

                    # Download objects expose only a URL, not their exact response. If Chromium
                    # reports that URL more than once, no response can be bound safely to the file;
                    # fail closed rather than reusing stale 2xx metadata from an earlier request.
                    if response.url in observed_response_urls:
                        ambiguous_response_urls.add(response.url)
                        pdf_responses.pop(response.url, None)
                    else:
                        observed_response_urls.add(response.url)

                    content_type = response.headers.get("content-type", "")
                    if (
                        response.url not in ambiguous_response_urls
                        and "application/pdf" in content_type.lower()
                    ):
                        pdf_responses[response.url] = (response.status, content_type)
                    if (
                        "application/pdf" in content_type.lower()
                        and not 200 <= response.status < 300
                    ):
                        notes.append(
                            f"discarded PDF {response.url}: HTTP {response.status}"
                        )

                def capture_download(download) -> None:
                    # Download objects do not expose their HTTP response. Defer reading the file
                    # until response events can bind this URL to an observed status.
                    pending_downloads.append(download)

                def retain_download(download) -> None:
                    download_url = download.url
                    if not download_url.startswith(("https://", "http://")):
                        notes.append(
                            "discarded browser-only download without an HTTP source URL"
                        )
                        return
                    if download_url in ambiguous_response_urls:
                        notes.append(
                            f"discarded download {download_url}: repeated responses were ambiguous"
                        )
                        return
                    response_metadata = pdf_responses.get(download_url)
                    if response_metadata is None:
                        notes.append(
                            f"discarded download {download_url} without an observed PDF response"
                        )
                        return
                    status, content_type = response_metadata
                    if not 200 <= status < 300:
                        notes.append(f"discarded PDF {download_url}: HTTP {status}")
                        return
                    try:
                        path = download.path()
                        # Playwright may dispatch response events while waiting for the download.
                        # Re-read the binding after that wait so stale 2xx metadata cannot survive
                        # a same-URL error response observed in the meantime.
                        if download_url in ambiguous_response_urls:
                            notes.append(
                                f"discarded download {download_url}: repeated responses were "
                                "ambiguous"
                            )
                            return
                        current_metadata = pdf_responses.get(download_url)
                        if current_metadata != response_metadata:
                            notes.append(
                                f"discarded download {download_url}: response metadata changed"
                            )
                            return
                        status, content_type = current_metadata
                        if not 200 <= status < 300:
                            notes.append(f"discarded PDF {download_url}: HTTP {status}")
                            return
                        if path:
                            saved = Path(path)
                            if saved.stat().st_size > max_bytes:
                                notes.append(f"discarded oversized download {download_url}")
                                return
                            with saved.open("rb") as downloaded:
                                data = downloaded.read(max_bytes + 1)
                            keep_pdf(
                                data,
                                download_url,
                                content_type,
                                status=status,
                            )
                    except (OSError, PlaywrightError) as exc:
                        notes.append(f"could not retain download {download_url}: {exc}")

                context.route("**/*", route_request)
                context.route_web_socket(
                    "**/*",
                    lambda websocket: websocket.close(
                        code=1008, reason="browser retrieval does not permit WebSockets"
                    ),
                )
                page = context.new_page()
                page.on("response", capture_response)
                page.on("download", capture_download)
                navigation = page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=min(timeout_ms, 5_000)
                    )
                except PlaywrightTimeout:
                    notes.append("page did not become network-idle; retained the rendered DOM")

                for download in pending_downloads:
                    retain_download(download)

                final_url = page.url
                authorize(final_url)
                status = main_frame_status
                if (
                    status is None
                    and navigation is not None
                    and navigation.url == final_url
                ):
                    status = navigation.status
                if status is None:
                    notes.append(
                        f"discarded rendered page {final_url}: no observed final HTTP status"
                    )
                elif not 200 <= status < 300:
                    notes.append(f"discarded rendered page {final_url}: HTTP {status}")
                else:
                    cdp = context.new_cdp_session(page)
                    try:
                        frame_tree = cdp.send("Page.getFrameTree")
                        frame_id = frame_tree["frameTree"]["frame"]["id"]
                        isolated_world = cdp.send(
                            "Page.createIsolatedWorld",
                            {
                                "frameId": frame_id,
                                "worldName": "axiom-bounded-markup",
                            },
                        )
                        evaluation = cdp.send(
                            "Runtime.callFunctionOn",
                            {
                                "executionContextId": isolated_world[
                                    "executionContextId"
                                ],
                                "functionDeclaration": """function(limit) {
                                    const markup =
                                        document.documentElement?.outerHTML ?? "";
                                    return new TextEncoder().encode(markup).length <= limit
                                        ? markup
                                        : null;
                                }""",
                                "arguments": [{"value": max_bytes}],
                                "returnByValue": True,
                            },
                        )
                    finally:
                        cdp.detach()

                    if "exceptionDetails" in evaluation:
                        raise BrowserFetchError(
                            "could not serialize rendered HTML in an isolated browser world"
                        )
                    remote_value = evaluation.get("result", {})
                    markup_text = (
                        None
                        if remote_value.get("subtype") == "null"
                        else remote_value.get("value")
                    )
                    if markup_text is None:
                        notes.append(
                            f"rendered HTML exceeded the {max_bytes:,}-byte ceiling and was "
                            "discarded"
                        )
                    elif not isinstance(markup_text, str):
                        raise BrowserFetchError(
                            "isolated browser serialization returned a non-text value"
                        )
                    else:
                        resources.insert(
                            0,
                            FetchedResource(
                                data=markup_text.encode("utf-8"),
                                url=final_url,
                                content_type="text/html; charset=utf-8",
                                status=status,
                            ),
                        )
                context.close()
                browser.close()
        except BrowserFetchError as exc:
            raise BrowserFetchError(
                str(exc), requests_made=max(request_count, exc.requests_made)
            ) from exc
        except Exception as exc:  # noqa: BLE001 - browser failure degrades to static retrieval
            raise BrowserFetchError(
                f"Playwright rendering failed for {url}: {exc}",
                requests_made=request_count,
            ) from exc

        return RenderedResult(
            resources=tuple(resources),
            requests_made=request_count,
            notes=tuple(notes),
        )


__all__ = [
    "DEFAULT_MAX_PDFS",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_TIMEOUT",
    "BrowserFetchError",
    "PlaywrightRenderer",
    "RenderedFetcher",
    "RenderedResult",
]
