"""Does this document actually describe the part number we were asked about?

Found by running the adversarial harness (``scripts/run_adversarial.py``): handed a gate valve
datasheet and asked for a ball valve SKU, extraction returned twelve confident values — and
**every one carried a quote that verified**, because the words really were in the document. They
just described a different product.

That is worth being precise about, because it bounds the system's central claim. The evidence
contract guarantees *a published value can be traced to text in its source*. It does not
guarantee *that text is about the requested product*, and no amount of stricter quote checking
would catch the difference: the citation is genuine. Wrong-product attribution is a targeting
failure, not a citation failure.

The fix is deterministic and costs nothing, which is the right shape for a guard like this: if
the part number cannot be found anywhere in the document, the document is not describing that
product, so there is nothing to extract and no reason to spend a model call finding out.

Matching is separator-insensitive because catalogues are wildly inconsistent about punctuation
(``BA-100-075``, ``BA100075``, ``BA 100 075``), but it is not *loose* beyond that. A guard that
accepted near-misses would reintroduce the bug it exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from axiom.docintel.models import ParsedDocument

MIN_SKU_CHARS = 4
"""Below this a part number is too generic to search for safely. A two-character SKU would match
inside half the words on the page, and a guard that fires at random is worse than none."""


@dataclass(frozen=True)
class SkuPresence:
    """Whether, and where, a part number appears in a parsed document."""

    sku: str
    found: bool
    matched_text: str | None = None
    location: str | None = None
    method: str | None = None
    """``exact`` for a literal match, ``separator_insensitive`` for one found only after
    punctuation was stripped. Recorded so a reviewer can see how loose the match was."""

    checked: bool = True
    """False when the SKU was too short to search for, in which case ``found`` is meaningless
    and callers must not treat it as evidence of absence."""

    listed_in_table: bool = False
    """True when the match was a data cell of a table — an ordering row, in practice.

    This is the strongest available signal that a document *offers* a part rather than merely
    referring to it, and it is what keeps withdrawal detection from over-firing: a part with a
    priced row is being sold no matter what the notes elsewhere say.
    """

    withdrawal_quote: str | None = None
    """The text that retires the part, when one was found. Present for reporting even when the
    part is still offered, because a note nobody surfaced is a note nobody acted on."""

    withdrawn: bool = False
    """True when every mention of the part withdraws it and none of them is an ordering row."""

    @property
    def is_absent(self) -> bool:
        """True only when we genuinely looked and did not find it."""
        return self.checked and not self.found

    @property
    def is_not_offered(self) -> bool:
        """Present in the document, but only in order to be retired by it.

        Kept separate from ``is_absent`` because the remedy is different and not interchangeable.
        An absent part number means the wrong file was attached: find the right document. A
        withdrawn one means the file is correct and the *part* is dead: stop trying to enrich it
        and delist it. Collapsing the two would send someone hunting for a datasheet that does
        not exist.
        """
        return self.checked and self.found and self.withdrawn

    @property
    def is_extractable(self) -> bool:
        """True when there is a real product here to extract."""
        return not self.is_absent and not self.is_not_offered

    def summary(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "found": self.found,
            "checked": self.checked,
            "matched_text": self.matched_text,
            "location": self.location,
            "method": self.method,
            "listed_in_table": self.listed_in_table,
            "withdrawn": self.withdrawn,
            "withdrawal_quote": self.withdrawal_quote,
        }


def _strip_separators(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


@lru_cache(maxsize=1)
def _withdrawal_markers() -> tuple[str, ...]:
    """Withdrawal phrasing, loaded from the declarative constants.

    Imported lazily so that document parsing does not depend on the validation package at import
    time. An unreadable constants file degrades to no markers, which restores the previous
    behaviour — permissive — rather than refusing every part in the catalogue.
    """
    from axiom.validate.constants import RuleConstants

    try:
        constants = RuleConstants.load()
    except (OSError, yaml.YAMLError):
        return ()
    return tuple(m.casefold() for m in constants.sets.get("WITHDRAWAL_MARKERS", ()))


def _context_for(parsed: ParsedDocument, page, line) -> str:
    """The matched line plus the one after it.

    Withdrawal notes wrap: "77C-102 (DN10) is discontinued and superseded by" / "77C-103. Do not
    order." Reading one line at a time would miss half of them, and which half depends on where
    the PDF happened to break.
    """
    following = [
        candidate.text
        for candidate in page.lines
        if candidate.line_index in (line.line_index + 1, line.line_index + 2)
    ]
    return " ".join([line.text, *following])


def _is_withdrawal(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _withdrawal_markers())


def _withdrawal_context(parsed: ParsedDocument, line_hits: list) -> str | None:
    for page, line in line_hits:
        context = _context_for(parsed, page, line)
        if _is_withdrawal(context):
            return " ".join(context.split())
    return None


def find_sku(parsed: ParsedDocument, sku: str, *, variants: set[str] | None = None) -> SkuPresence:
    """Look for a part number in a parsed document.

    ``variants`` lets a caller pass brand-aware forms from ``axiom.normalize.mpn_variants``,
    which knows about leading-zero and prefix conventions this module deliberately does not.
    """
    candidate = (sku or "").strip()
    if len(candidate) < MIN_SKU_CHARS:
        return SkuPresence(sku=candidate, found=False, checked=False)

    forms = {candidate, *(variants or set())}
    forms = {form for form in forms if form and len(form) >= MIN_SKU_CHARS}

    def mentions(text: str) -> bool:
        lowered = text.casefold()
        return any(form.casefold() in lowered for form in forms)

    # Exact first. A literal hit is stronger evidence and gives a better location to report.
    #
    # Every mention is collected rather than returning on the first, because "is this part number
    # here" and "does this document sell this part" are different questions and the second one
    # cannot be answered from a single hit.
    data_cell: tuple[object, object] | None = None
    header_cell: tuple[object, object] | None = None
    line_hits: list[tuple[object, object]] = []

    for page in parsed.pages:
        for table in page.tables:
            for cell in table.cells:
                if not mentions(cell.text):
                    continue
                if cell.row > 0:
                    data_cell = data_cell or (table, cell)
                else:
                    header_cell = header_cell or (table, cell)
        for line in page.lines:
            if mentions(line.text):
                line_hits.append((page, line))

    withdrawal = _withdrawal_context(parsed, line_hits)

    # An ordering row outranks everything. A part with a row is being sold.
    for hit in (data_cell, header_cell):
        if hit is not None:
            table, cell = hit
            return SkuPresence(
                sku=candidate,
                found=True,
                matched_text=cell.text.strip(),
                location=table.cell_ref(cell.row, cell.col),
                method="exact",
                listed_in_table=cell.row > 0,
                withdrawal_quote=withdrawal,
            )

    if line_hits:
        page, line = line_hits[0]
        # Only refuse when *every* mention retires the part. One withdrawal note beside three
        # ordinary references is a cross-reference to some other product's replacement, not a
        # statement about this one.
        all_withdrawn = withdrawal is not None and all(
            _is_withdrawal(_context_for(parsed, p, ln)) for p, ln in line_hits
        )
        return SkuPresence(
            sku=candidate,
            found=True,
            matched_text=line.text.strip(),
            location=f"p.{page.number}:l.{line.line_index}",
            method="exact",
            withdrawal_quote=withdrawal,
            withdrawn=all_withdrawn,
        )

    # Then separator-insensitive, over the whole document, for catalogues that punctuate
    # part numbers differently from the ERP that asked about them.
    flattened = _strip_separators(parsed.full_text)
    for form in forms:
        stripped = _strip_separators(form)
        if stripped and stripped in flattened:
            return SkuPresence(
                sku=candidate,
                found=True,
                matched_text=form,
                location=None,
                method="separator_insensitive",
            )

    return SkuPresence(sku=candidate, found=False)
