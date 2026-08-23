"""Structure, not prose: the links out of a page and the search form on it.

:mod:`axiom.docintel.html_parser` renders a page into text and deliberately throws attributes away —
its whole design is *render, do not re-implement*, and an ``href`` is not extractable content. But
finding a datasheet needs exactly the thing it discards. Going from ``milwaukeetool.com`` plus
``49-94-0013`` to a specification PDF means reading the site's own navigation: what it links to, and
how it lets you search it.

Kept separate rather than bolted onto the renderer because the two answer different questions and
mixing them would blur the one that is load-bearing. The renderer's output is *citable text*; this
module's output is *where to go next*, and nothing here is ever cited.

The search-form reader is the part worth explaining. Guessing a site's search endpoint —
``/search?q=``, ``/?s=``, ``/catalogsearch/result/?q=`` — is the same mistake as guessing a product
URL pattern: it works on some sites, 404s on others, and the failure looks like a bug rather than a
wrong guess. Instead this reads the ``<form>`` the site itself publishes and takes its ``action``
and
its query field name from the markup. That is not a heuristic about how sites are built; it is the
site telling us how to search it.

Nothing here fetches, resolves or executes anything. ``html.parser`` is non-validating with no DTD
handling, and only ``http(s)`` links survive — a ``javascript:`` or ``data:`` href is dropped here
so
no caller has to remember to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

# Query field names a site search uses, best first. Consulted only when a form declares no
# `type="search"` input, which is the majority of real markup.
_QUERY_NAMES = ("q", "s", "query", "search", "keyword", "keywords", "term", "text", "searchterm")

# Never followed. `mailto:` and `tel:` are not documents; `javascript:` and `data:` are not
# addresses. Dropped here so that no caller has to remember to.
_UNFETCHABLE_SCHEMES = frozenset(
    {"javascript", "mailto", "tel", "data", "about", "file", "ftp", "sms"}
)


@dataclass(frozen=True)
class Link:
    """One outbound link, absolute and de-fragmented."""

    url: str
    text: str = ""
    """The anchor text, collapsed. Frequently carries the part number when the ``href`` does not,
    so it is the other half of deciding whether a link is worth following."""

    rel: str = ""
    title: str = ""

    @property
    def is_pdf(self) -> bool:
        return urlparse(self.url).path.lower().endswith(".pdf")

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").lower()


@dataclass(frozen=True)
class SearchForm:
    """A site's own search form, as it published it."""

    action: str
    """Absolute URL the form submits to."""

    field: str
    """Name of the query parameter."""

    method: str = "get"
    hidden: tuple[tuple[str, str], ...] = ()
    """Hidden inputs the form carries. Some catalogue searches need a scope or a category id, and
    dropping them yields an empty result page rather than an error."""

    @property
    def usable(self) -> bool:
        """Only GET forms are usable for discovery.

        A POST search cannot be expressed as a URL, so it cannot be a citation source and cannot be
        re-run from an index. Reported rather than coerced: submitting a POST we inferred would be
        acting on the site in a way a plain fetch does not.
        """
        return self.method.lower() == "get" and bool(self.action) and bool(self.field)

    def url_for(self, query: str) -> str:
        """The search URL for a query. Empty when the form is not usable."""
        if not self.usable:
            return ""
        from urllib.parse import urlencode

        params = [*self.hidden, (self.field, query)]
        separator = "&" if urlparse(self.action).query else "?"
        return f"{self.action}{separator}{urlencode(params)}"


@dataclass
class _FormState:
    action: str = ""
    method: str = "get"
    fields: list[tuple[str, str]] = field(default_factory=list)
    """(name, type) for every input, in document order."""

    hidden: list[tuple[str, str]] = field(default_factory=list)


class _Navigator(HTMLParser):
    """Collects links and forms. Ignores text except as anchor labels."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base_url
        self.links: list[Link] = []
        self.forms: list[_FormState] = []

        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []
        self._form: _FormState | None = None

    # ------------------------------------------------------------------ tags

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}

        if tag == "base" and values.get("href"):
            # A `<base href>` reassigns what every relative link on the page means. Honouring it is
            # not optional: ignoring one silently produces a page full of wrong URLs.
            self._base = urljoin(self._base, values["href"])
            return

        if tag == "a":
            self._anchor = values
            self._anchor_text = []
            return

        if tag == "form":
            self._form = _FormState(
                action=values.get("action", ""),
                method=values.get("method", "get") or "get",
            )
            self.forms.append(self._form)
            return

        if tag in {"input", "textarea"} and self._form is not None:
            name = values.get("name", "")
            kind = values.get("type", "text").lower()
            if name:
                self._form.fields.append((name, kind))
                if kind == "hidden":
                    self._form.hidden.append((name, values.get("value", "")))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"a", "form"}:
            return
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self._close_anchor()
        elif tag == "form":
            self._form = None

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)

    # ------------------------------------------------------------------ assembly

    def _close_anchor(self) -> None:
        attrs, self._anchor = self._anchor, None
        text = " ".join("".join(self._anchor_text).split())
        self._anchor_text = []

        href = (attrs.get("href") or "").strip()
        if not href:
            return
        # Fragment-only: an in-page anchor. It resolves to the page we are already on, so keeping it
        # would put every page's own table of contents into its own candidate list.
        if href.startswith("#"):
            return
        scheme = urlparse(href).scheme.lower()
        if scheme in _UNFETCHABLE_SCHEMES:
            return

        absolute, _fragment = urldefrag(urljoin(self._base, href))
        if urlparse(absolute).scheme.lower() not in {"http", "https"}:
            return
        self.links.append(
            Link(
                url=absolute,
                text=text,
                rel=(attrs.get("rel") or "").strip().lower(),
                title=" ".join((attrs.get("title") or "").split()),
            )
        )

    def search_forms(self) -> list[SearchForm]:
        found: list[SearchForm] = []
        for state in self.forms:
            if not (name := _query_field(state.fields)):
                continue
            found.append(
                SearchForm(
                    action=urljoin(self._base, state.action) if state.action else self._base,
                    field=name,
                    method=state.method,
                    hidden=tuple(state.hidden),
                )
            )
        return found


def _query_field(fields: list[tuple[str, str]]) -> str:
    """The field a query goes in, or empty when this form is not a search.

    ``type="search"`` wins outright — it is the site declaring the field's purpose. Otherwise the
    conventional names are tried in order, and a form matching none of them is not treated as a
    search at all. A login form has a ``name`` field and a newsletter box has an ``email`` field;
    guessing "first text input" would submit a part number to both.
    """
    for name, kind in fields:
        if kind == "search":
            return name
    lowered = {name.lower(): name for name, kind in fields if kind in {"text", "search", ""}}
    for candidate in _QUERY_NAMES:
        if candidate in lowered:
            return lowered[candidate]
    return ""


def extract_links(html: str, base_url: str) -> list[Link]:
    """Every fetchable link on a page, absolute, de-fragmented and de-duplicated.

    Duplicates are collapsed keeping the entry with the longest anchor text, because the same
    product is routinely linked twice — once from an image with no text and once from a caption —
    and the caption is the half that tells you what it is.
    """
    navigator = _Navigator(base_url)
    navigator.feed(html)
    navigator.close()

    best: dict[str, Link] = {}
    for link in navigator.links:
        existing = best.get(link.url)
        if existing is None or len(link.text) > len(existing.text):
            best[link.url] = link
    return list(best.values())


def find_search_form(html: str, base_url: str) -> SearchForm | None:
    """The page's own search form, or None.

    Returns the first *usable* one. Pages carry several forms — newsletter, login, locale — and the
    ordering here is document order, which on a real site puts the header search ahead of a footer
    signup.
    """
    navigator = _Navigator(base_url)
    navigator.feed(html)
    navigator.close()
    for form in navigator.search_forms():
        if form.usable:
            return form
    return None


__all__ = ["Link", "SearchForm", "extract_links", "find_search_form"]
