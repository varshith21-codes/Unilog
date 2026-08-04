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

    @property
    def is_absent(self) -> bool:
        """True only when we genuinely looked and did not find it."""
        return self.checked and not self.found

    def summary(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "found": self.found,
            "checked": self.checked,
            "matched_text": self.matched_text,
            "location": self.location,
            "method": self.method,
        }


def _strip_separators(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


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

    # Exact first. A literal hit is stronger evidence and gives a better location to report.
    for page in parsed.pages:
        for table in page.tables:
            for cell in table.cells:
                for form in forms:
                    if form.casefold() in cell.text.casefold():
                        return SkuPresence(
                            sku=candidate,
                            found=True,
                            matched_text=cell.text.strip(),
                            location=table.cell_ref(cell.row, cell.col),
                            method="exact",
                        )
        for line in page.lines:
            for form in forms:
                if form.casefold() in line.text.casefold():
                    return SkuPresence(
                        sku=candidate,
                        found=True,
                        matched_text=line.text.strip(),
                        location=f"p.{page.number}:l.{line.line_index}",
                        method="exact",
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
