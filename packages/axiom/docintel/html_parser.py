"""HTML to citable text.

Manufacturer product pages are a first-class source — often the *only* source for a
newly-released part, and frequently carrying an ordering table the PDF catalogue has not caught
up with. But feeding raw markup to an extractor is worse than useless: the model spends tokens on
``<div class="spec-row">`` and every quote it returns fails verification because the quote
contains tags the human-readable text does not.

**The design decision here is to render, not to re-implement.** This converts HTML into the same
space-aligned text the plain-text parser already handles, then hands off to it. So HTML tables are
reconstructed by the *existing* column-boundary detector, evidence spans get coordinates from the
*existing* monospace grid, and quote location works unchanged. A parallel table builder for HTML
would have been a second implementation of the hardest part of the parser, drifting from the first.

Two consequences worth stating:

*   Coordinates are synthetic, exactly as they are for text. They are internally consistent, which
    is all the evidence viewer needs to box the right characters. They are not the positions a
    browser would have laid out.
*   The stored artifact remains the original bytes. Rendering happens at parse time, never at
    ingest, so the hash in a citation still resolves to what the server actually sent.

Nothing in here executes, resolves or fetches anything from the document. ``html.parser`` is a
non-validating pure-Python parser with no DTD handling, so there is no external-entity or
entity-expansion exposure; scripts, styles and embedded frames are discarded rather than followed.
"""

from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser

from axiom.core.evidence import SourceDocument
from axiom.docintel.models import ParsedDocument

# Content that is markup machinery rather than product information. Dropped wholesale, including
# any text inside, because a stylesheet or an inline script contains nothing citable and its
# contents would otherwise land in the extractable text.
#
# `template` is deliberately NOT here, and the reason is a whole product page.
#
# The HTML5 spec calls `<template>` inert, which is why it was originally discarded along with
# `<script>`. But every Vue/Nuxt SSR page uses *named slot* templates to carry its rendered,
# visible content: Mirka's product pages put the description in `<template #product-information>`,
# the specification table in `<template #technical-details>` and every datasheet link in
# `<template #downloads>`. Discarding the element wholesale threw away the description, the
# complete specification table and all three datasheet PDFs from a 575 KB page that plainly
# contained them, leaving 296 lines of navigation and footer. The observed symptom was the worst
# kind: retrieval reported success on the correct manufacturer URL, the SKU was found, and
# extraction returned nothing but values re-derived from the typed description.
#
# Keeping the contents is the safer error. A genuinely inert HTML5 template contributes a row
# prototype's placeholder text, which is inert-looking prose that maps to no attribute label and
# is discarded downstream anyway. Dropping it loses the page.
_DISCARD = frozenset(
    {"script", "style", "head", "noscript", "svg", "iframe", "object", "select"}
)

# Wrapper elements that carry no text of their own. Treated as blocks so a slot template's
# children still break lines, without the wrapper itself emitting anything.
_TRANSPARENT = frozenset({"template", "slot"})

# Class-name fragments that mark the two halves of a specification row.
#
# Real product pages do not use `<table>` for specifications any more; they use a flex wrapper
# holding two divs, and they name the halves in the class attribute. Mirka writes
# `technical-table-row__key` / `technical-table-row__value`; the same shape appears as
# `spec-label`/`spec-value`, `attribute-name`/`attribute-value`, `property-term`/`property-data`.
# Matched as substrings because the prefix is site-specific and the suffix is not.
#
# Without this the two halves are separate `<div>`s, so `_BLOCK` puts them on separate lines and
# `axiom.extract.structured.split_spec_line` — which pairs a label with a value *within one line* —
# can never see them as a pair. Neither can a model reading the rendered text: it gets an
# alternating list of labels and values with no stated association, which is precisely the input
# that produces a confident mis-pairing.
_KEY_HINTS = ("__key", "-key", "_key", "label", "__term", "-term", "spec-name", "attribute-name",
              "property-name", "prop-name", "param-name", "feature-name", "__name", "-caption")
_VALUE_HINTS = ("__value", "-value", "_value", "__data", "-data", "spec-val", "attribute-val",
                "property-val", "prop-val", "param-val", "feature-val", "__val")

# Tags that may carry a key/value half. Restricted on purpose: a matching class on a heading or a
# list item is far more likely to be layout than a specification row.
_PAIR_TAGS = frozenset({"div", "span", "p", "dt", "dd", "strong", "em", "li"})

_MAX_PAIR_KEY_CHARS = 80
"""A specification label is short. Beyond this the class name matched a content block."""

_MAX_PAIR_VALUE_CHARS = 300
"""A specification value can be a list of approvals, but not a paragraph of marketing prose."""

# Tags after which a line must break, so a specification list does not collapse into one
# unreadable run. `parse_text` works line by line, and a single 4,000-character line would give
# every value on the page the same coordinates.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
        "ul",
    }
)

_TABLE_CELLS = frozenset({"td", "th"})

# Inline elements whose close emits a soft space.
#
# Real product pages build specification rows out of adjacent spans with no whitespace between the
# tags: `<span>Body Material</span><span>Bronze C84400</span>`. A browser renders those
# concatenated, and reproducing that faithfully yields "Body MaterialBronze C84400" — which makes
# the *value* unlocatable as a quote and so unextractable.
#
# The trade is deliberate and one-sided. Quote matching squashes whitespace (see
# `axiom.docintel.spans.squash`), so an extra space costs nothing, while a missing one loses the
# value entirely. Erring toward separation is therefore strictly safer than erring toward fidelity.
_INLINE_BOUNDARY = frozenset(
    {"span", "a", "b", "strong", "em", "i", "u", "small", "label", "abbr", "code", "dfn"}
)

# Two spaces is what the text parser reads as a column gap, so cells are padded to a shared width
# and joined with exactly that. Aligning the whole table rather than each row is what makes the
# gap columns unanimous, which is the condition the boundary detector actually tests.
_COLUMN_GAP = "  "

_WHITESPACE = re.compile(r"[^\S\n]+")
_BLANK_RUN = re.compile(r"\n{3,}")

# ------------------------------------------------------------------ document identity
#
# WHY THE HEAD IS READ AT ALL, when `head` is in `_DISCARD`.
#
# A manufacturer product page routinely never prints its own part number in visible text. Mirka's
# does not: `MRP6002100` occurs 334 times in the markup — canonical link, og:url, og:image, and the
# JSON-LD `sku` — and **zero** times in the rendered body, which says only "Mirka® POLAROS RP 600
# EU Ø 150 mm". Every gate downstream searches rendered text, so the consequences compounded:
#
#   * `docintel.sku.find_sku` reported the part absent, so `Extractor.extract` refused the page
#     before any model call — the correct page, on the manufacturer's own domain, with the complete
#     specification table in it;
#   * `DocumentLibrary.coverage_for` therefore did not record the page as covering the SKU, so
#     retrieval kept going and settled on a 154-page general catalogue that *did* name it in an
#     ordering row. That is how a focused product page loses to a 31 MB catalogue.
#
# So identity metadata is rendered as ordinary text rather than parsed into a side channel. That
# keeps every existing guarantee intact: the part number is found by the same substring search, a
# quote citing it verifies because the text really is in the document, and the citation gets
# coordinates from the same monospace grid. The alternative — teaching `find_sku`, the library and
# the extractor each to consult a metadata dictionary — is three contracts instead of none.
#
# Only identity is taken. Keywords, robots directives, analytics ids and social handles are not
# product data and would be citable text if emitted.

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_LDJSON = re.compile(
    r"""<script\b[^>]*type\s*=\s*["']?application/ld\+json["']?[^>]*>(.*?)</script>""",
    re.S | re.I,
)
_ATTR = re.compile(r"""([-\w:]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")

# `meta` name/property values that state what the product *is* or what it is *called*.
_META_IDENTITY = {
    "og:title": "Product",
    "twitter:title": "Product",
    "description": "Description",
    "og:description": "Description",
    "twitter:description": "Description",
    "product:retailer_item_id": "Part Number",
    "product:mfr_part_no": "Part Number",
    "og:url": "Source URL",
}

# JSON-LD keys worth rendering, and the label to render them under.
_LD_IDENTITY = {
    "sku": "Part Number",
    "mpn": "Manufacturer Part Number",
    "productID": "Product ID",
    "gtin": "GTIN",
    "gtin8": "GTIN",
    "gtin12": "GTIN",
    "gtin13": "GTIN",
    "gtin14": "GTIN",
    "name": "Product",
}

_MAX_IDENTITY_VALUE_CHARS = 500
"""An identity field is a name, a code or a one-sentence summary, never a page of copy."""

_MAX_IDENTITY_ROWS = 40
"""A ProductGroup legitimately declares a handful of variant SKUs. Beyond this the page is a
category listing and its identity block would be a catalogue of unrelated parts."""


def _tag_attrs(tag: str) -> dict[str, str]:
    """Attribute map for one start tag, lowercased keys, entity-decoded values."""
    attrs: dict[str, str] = {}
    # Skip the tag name itself so `<meta ...>` does not yield a "meta" key.
    body = tag[1:-1] if tag.endswith(">") else tag[1:]
    body = body.lstrip("/")
    if " " in body:
        body = body.split(" ", 1)[1]
    else:
        return attrs
    for match in _ATTR.finditer(body):
        name = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        attrs[name] = unescape(value)
    return attrs


# JSON-LD `@type` values whose `name` is a product name. A code (`sku`, `mpn`, a `gtin`) is
# unambiguous wherever it appears, but `name` is not: the same key holds the organisation on an
# `Organization` node, the site on a `WebSite` node, and the string "variant" on the
# `PropertyValue` that describes how a `ProductGroup` varies. Emitting those as "Product" put
# `Product  Mirka` and `Product  variant` in the identity block — noise that reads like a product
# name and would be citable as one.
_LD_PRODUCT_TYPES = frozenset(
    {"product", "productgroup", "productmodel", "individualproduct", "vehicle", "book",
     "softwareapplication", "creativework"}
)

# Subtrees whose contents describe somebody or something other than this product.
_LD_SKIP_KEYS = frozenset(
    {"brand", "offers", "seller", "manufacturer", "publisher", "author", "provider",
     "breadcrumb", "itemlistelement", "potentialaction", "aggregaterating", "review",
     "isrelatedto", "issimilarto"}
)


def _walk_ld(node, into: list[tuple[str, str]]) -> None:
    """Collect identity pairs from a JSON-LD tree, depth-first, in document order."""
    if isinstance(node, dict):
        raw_type = node.get("@type") or ""
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        is_product = any(str(t).strip().casefold() in _LD_PRODUCT_TYPES for t in types)
        for key, value in node.items():
            if isinstance(value, str | int | float) and key in _LD_IDENTITY:
                if key == "name" and not is_product:
                    continue
                into.append((_LD_IDENTITY[key], str(value)))
            elif isinstance(value, dict | list):
                if key.casefold() in _LD_SKIP_KEYS:
                    continue
                _walk_ld(value, into)
    elif isinstance(node, list):
        for item in node:
            _walk_ld(item, into)


def identity_rows(html: str) -> list[tuple[str, str]]:
    """``(label, value)`` identity pairs stated in the document head, deduplicated.

    Ordered so the strongest statement of identity comes first: the page title, then the codes a
    machine-readable block declares, then the URL the page claims as canonical.
    """
    rows: list[tuple[str, str]] = []

    if title := _TITLE.search(html):
        text = _WHITESPACE.sub(" ", unescape(title.group(1))).strip()
        if text:
            rows.append(("Product", text))

    for tag in _META_TAG.findall(html):
        attrs = _tag_attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        label = _META_IDENTITY.get(key)
        content = _WHITESPACE.sub(" ", attrs.get("content", "")).strip()
        if label and content:
            rows.append((label, content))

    for tag in _LINK_TAG.findall(html):
        attrs = _tag_attrs(tag)
        if (attrs.get("rel") or "").strip().lower() == "canonical" and attrs.get("href"):
            rows.append(("Source URL", attrs["href"].strip()))

    for block in _LDJSON.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            # A malformed block is not an error worth failing a page for. Real sites ship trailing
            # commas and unescaped newlines in JSON-LD, and the visible body is unaffected.
            continue
        _walk_ld(data, rows)

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for label, value in rows:
        value = value.strip()
        if not value or len(value) > _MAX_IDENTITY_VALUE_CHARS:
            continue
        key = (label, value.casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, value))
        if len(unique) >= _MAX_IDENTITY_ROWS:
            break
    return unique


class _Renderer(HTMLParser):
    """Accumulates visible text, rendering tables as aligned columns."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._line: list[str] = []

        # A stack rather than a flag. Discard tags nest in real pages — an `<svg>` inside an
        # `<svg>`, a `<select>` inside a discarded subtree — and a single flag was cleared by the
        # inner close tag, leaking the remainder of the outer element's contents into the text.
        self._discard: list[str] = []

        # Tables nest in real pages, usually as a layout hangover. A stack keeps an inner table
        # from terminating its parent and losing the outer rows.
        self._tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None

        # A run of class-marked specification rows, accumulated so it can be emitted as one
        # aligned block. Aligning the run rather than each row is what lets the text parser's
        # column detector rebuild it as a real table: it looks for character positions that are
        # whitespace on every line of the block.
        self._pairs: list[list[str]] = []
        self._pair_buf: list[str] | None = None
        self._pair_slot = ""
        self._pair_tag = ""
        self._pair_depth = 0

    # ---------------------------------------------------------------- line assembly

    def _break_line(self) -> None:
        """Flush the current line, if it has content.

        Emits nothing for an empty buffer. Every block element breaks on both its open and its
        close tag, so inserting a separator here would put a blank line between every pair of
        paragraphs and double it between nested blocks.
        """
        text = _WHITESPACE.sub(" ", "".join(self._line)).strip()
        self._line = []
        if text:
            # Ordinary prose ends a run of specification rows. Flushed before the text so the
            # block keeps its position in the document rather than migrating to the next boundary.
            self._flush_pairs()
            self.lines.append(text)

    def _emit(self, text: str) -> None:
        self.lines.append(text)

    # ---------------------------------------------------------------- specification pairs

    def _flush_pairs(self) -> None:
        """Emit the accumulated specification rows as one aligned two-column block."""
        if not self._pairs:
            return
        rows, self._pairs = self._pairs, []
        for line in _align(rows):
            self._emit(line)
        self._emit("")

    @staticmethod
    def _pair_slot_for(attrs) -> str:
        """Whether this element is the key half of a specification row, the value half, or neither.

        Read from `class`, which is where every framework states it. The value test runs first
        because `technical-table-row__value` contains no key fragment but a hyphenated
        `data-label` convention can match both, and mislabelling a value as a key would silently
        drop the value.
        """
        classes = ""
        for name, raw in attrs:
            if name == "class" and raw:
                classes = raw.casefold()
                break
        if not classes:
            return ""
        if any(hint in classes for hint in _VALUE_HINTS):
            return "value"
        if any(hint in classes for hint in _KEY_HINTS):
            return "key"
        return ""

    def _start_pair(self, tag: str, slot: str) -> None:
        self._pair_buf = []
        self._pair_slot = slot
        self._pair_tag = tag
        self._pair_depth = 1

    def _finish_pair(self) -> None:
        """Attach the captured half to the current row.

        A key opens a row; a value completes the row the key opened. A value with no open row —
        a page that marks only its values — still records, against an empty label, because the
        aligned block then keeps the value on the document's own line order rather than losing it.
        """
        text = _WHITESPACE.sub(" ", "".join(self._pair_buf or [])).strip()
        slot = self._pair_slot
        self._pair_buf = None
        self._pair_slot = ""
        self._pair_tag = ""
        self._pair_depth = 0

        if not text:
            return
        if slot == "key":
            if len(text) > _MAX_PAIR_KEY_CHARS:
                # The class name matched a content wrapper, not a label. Emitted as ordinary text
                # so nothing is lost.
                self._flush_pairs()
                self._emit(text)
                return
            self._pairs.append([text, ""])
            return

        if len(text) > _MAX_PAIR_VALUE_CHARS:
            self._flush_pairs()
            self._emit(text)
            return
        if self._pairs and not self._pairs[-1][1]:
            self._pairs[-1][1] = text
        else:
            self._pairs.append(["", text])

    # ---------------------------------------------------------------- tag handling

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._discard:
            # Nested discard tags are tracked so the inner close does not resume the outer's text.
            if tag in _DISCARD:
                self._discard.append(tag)
            return
        if tag in _DISCARD:
            self._discard.append(tag)
            return

        # Inside a captured half, nested markup contributes text only. The depth counter is keyed
        # on the tag name so a `<div>` inside a `<div class="...__value">` cannot close it early.
        if self._pair_buf is not None:
            if tag == self._pair_tag:
                self._pair_depth += 1
            return

        if tag == "table":
            self._break_line()
            self._flush_pairs()
            self._tables.append([])
            return

        if tag == "tr" and self._tables:
            self._tables[-1].append([])
            return

        if tag in _TABLE_CELLS and self._tables:
            if not self._tables[-1]:
                # A cell outside any row: malformed, but real. Synthesise the row rather than
                # dropping the content.
                self._tables[-1].append([])
            self._cell = []
            return

        # A specification row's halves, but never inside a real table cell: there the cell is
        # already the unit of structure and capturing would empty it.
        if self._cell is None and tag in _PAIR_TAGS:
            if tag == "dt":
                self._break_line()
                self._start_pair(tag, "key")
                return
            if tag == "dd":
                self._break_line()
                self._start_pair(tag, "value")
                return
            if slot := self._pair_slot_for(attrs):
                self._break_line()
                self._start_pair(tag, slot)
                return

        if tag in _BLOCK or tag in _TRANSPARENT:
            self._break_line()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self._discard and tag == "br":
            if self._pair_buf is not None:
                self._pair_buf.append(" ")
                return
            self._break_line()

    def handle_endtag(self, tag: str) -> None:
        if self._discard:
            if tag in _DISCARD:
                # Pop the matching opener when there is one, so an unbalanced close cannot clear
                # an unrelated outer discard.
                if tag in self._discard:
                    while self._discard and self._discard.pop() != tag:
                        pass
                else:
                    self._discard.pop()
            return

        if self._pair_buf is not None:
            if tag == self._pair_tag:
                self._pair_depth -= 1
                if self._pair_depth <= 0:
                    self._finish_pair()
            elif tag in _INLINE_BOUNDARY and self._pair_buf:
                if not str(self._pair_buf[-1]).endswith(" "):
                    self._pair_buf.append(" ")
            return

        if tag in _TABLE_CELLS and self._cell is not None:
            text = _WHITESPACE.sub(" ", "".join(self._cell)).strip()
            self._cell = None
            if self._tables and self._tables[-1]:
                self._tables[-1][-1].append(text)
            return

        if tag == "table" and self._tables:
            rows = self._tables.pop()
            rendered = _align(rows)
            # An inner table's rendering belongs to the cell that contains it, flattened onto one
            # line. Emitting it at document level would interleave its rows with the outer
            # table's and destroy both grids.
            if self._tables and self._cell is not None:
                self._cell.append(" ".join(r.strip() for r in rendered))
                return
            for line in rendered:
                self._emit(line)
            if rendered:
                self._emit("")
            return

        if tag in _BLOCK or tag in _TRANSPARENT:
            self._break_line()
            return

        if tag in _INLINE_BOUNDARY:
            target = self._cell if self._cell is not None else self._line
            if target and not str(target[-1]).endswith(" "):
                target.append(" ")

    def handle_data(self, data: str) -> None:
        if self._discard:
            # Not just the tags — the contents. An inline script's "600 PSI" string is not a
            # specification, and a model handed it would cite text no human can read.
            return

        if self._pair_buf is not None:
            target: list = self._pair_buf
        elif self._cell is not None:
            target = self._cell
        else:
            target = self._line
        if not data.strip():
            # Whitespace between two inline elements is a genuine word separator: without it
            # `<span>Body Material</span> <span>Bronze</span>` collapses into one token. At the
            # start of a block it is layout, so it is dropped.
            if target:
                target.append(" ")
            return
        target.append(data)

    def finish(self) -> str:
        # An unclosed key/value half still holds its text, for the same reason an unclosed table
        # still holds its rows: the document being malformed is not a reason to lose the values.
        if self._pair_buf is not None:
            self._finish_pair()
        self._break_line()
        self._flush_pairs()
        # Any unclosed table still holds rows. Dropping them because the document was malformed
        # would lose exactly the ordering table this parser exists to recover.
        while self._tables:
            for line in _align(self._tables.pop()):
                self._emit(line)
        return _BLANK_RUN.sub("\n\n", "\n".join(self.lines)).strip("\n")


def _align(rows: list[list[str]]) -> list[str]:
    """Render table rows as space-aligned columns.

    Padding is computed across the whole table, not per row, because the text parser looks for
    character positions that are whitespace on *every* line of a block. Row-local padding would
    leave the gap columns misaligned and the table would not be detected at all.
    """
    populated = [row for row in rows if any(cell for cell in row)]
    if not populated:
        return []

    columns = max(len(row) for row in populated)
    if columns == 1:
        # A single-column table is a layout wrapper, not data. Rendering it as a "table" would
        # invite the column detector to find structure that is not there.
        return [row[0] for row in populated if row and row[0]]

    widths = [
        max(len(row[index]) if index < len(row) else 0 for row in populated)
        for index in range(columns)
    ]

    lines: list[str] = []
    for row in populated:
        cells = [
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(columns)
        ]
        lines.append(_COLUMN_GAP.join(cells).rstrip())
    return lines


def html_to_text(html: str) -> str:
    """Render HTML as the aligned plain text the text parser consumes.

    The document's own identity statement leads, because it is the part of a product page that
    decides whether the page is evidence about this part at all. See the note above
    :func:`identity_rows`.
    """
    renderer = _Renderer()
    renderer.feed(html)
    renderer.close()
    body = renderer.finish()

    rows = identity_rows(html)
    if not rows:
        return body

    # Rendered through `_align` like any other two-column block, so the text parser reconstructs it
    # as a table and a value read from it gets a real cell reference rather than a line offset.
    header = _align([[label, value] for label, value in rows])
    return "\n".join([*header, "", body]).strip("\n") if body else "\n".join(header)


def looks_like_html(data: bytes) -> bool:
    """Whether these bytes should be rendered before parsing.

    Sniffed from content rather than trusted from a Content-Type header or a file extension,
    because both are wrong often enough to matter: supplier portals serve HTML as
    ``application/octet-stream``, and a saved page keeps a ``.txt`` name.
    """
    head = data[:2048].lstrip()[:512].lower()
    if not head:
        return False
    if head.startswith((b"<!doctype html", b"<html", b"<?xml-stylesheet")):
        return True
    return b"<html" in head or (b"<head" in head and b"<meta" in head) or b"<body" in head


def parse_html(
    html: str, document: SourceDocument, **kwargs
) -> ParsedDocument:
    """Render HTML to text and parse it, reporting ``html`` as the parser."""
    from axiom.docintel.text_parser import parse_text

    return parse_text(html_to_text(html), document, parser="html", **kwargs)
