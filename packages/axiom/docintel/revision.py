"""Revision awareness: which of two documents is newer?

Module M2's quietest sub-capability, and validation layer L4 does not work without it. When two
sources disagree about a pressure rating, the question is almost never "which is wrong" — it is
"which is current". An older catalogue printing last year's specification is behaving correctly,
and treating that as a data-quality failure would bury real conflicts under a pile of stale-but-
honest ones.

The marker is already printed on the page. Every datasheet in the sample corpus carries one in its
header block:

    Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08
    Bronze Gate Valve, Rising Stem, Solid Wedge                     Rev F  2025-03

**Read from the document, not from the filesystem.** A file's modification time and the moment it
was crawled both describe when *this copy* was obtained, which says nothing about when the
specification was written — a datasheet downloaded today may be a decade old. Ordering two
documents by retrieval time would let the order an operator happened to collect them in decide
which specification wins.

Scanning is confined to the first page, and to a short window at that. A revision block lives in a
header or a footer; a match found deep in body prose is far more likely to be a reference to some
*other* document's revision ("supersedes Rev B") than this one's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered by how much they pin down. A marker carrying both a letter and a date is worth more than
# either alone, so the combined patterns are tried first.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "letter_and_date",
        re.compile(
            r"\brev(?:ision)?\.?\s*([A-Z0-9]{1,3})\b[\s,:()]*"
            r"((?:19|20)\d{2}(?:[-/](?:0[1-9]|1[0-2]))?(?:[-/](?:0[1-9]|[12]\d|3[01]))?)",
            re.IGNORECASE,
        ),
    ),
    (
        "date_and_letter",
        re.compile(
            r"((?:19|20)\d{2}(?:[-/](?:0[1-9]|1[0-2]))?(?:[-/](?:0[1-9]|[12]\d|3[01]))?)"
            r"[\s,:()]*\brev(?:ision)?\.?\s*([A-Z0-9]{1,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "effective_date",
        re.compile(
            r"\b(?:effective|issued|revised|updated|published)\b\s*(?:date)?[\s,:]*"
            r"((?:19|20)\d{2}(?:[-/](?:0[1-9]|1[0-2]))?(?:[-/](?:0[1-9]|[12]\d|3[01]))?)",
            re.IGNORECASE,
        ),
    ),
    (
        "letter_only",
        re.compile(r"\brev(?:ision)?\.?\s*([A-Z0-9]{1,3})\b", re.IGNORECASE),
    ),
)

# A revision block sits in a header or a footer. Beyond this the odds that a "Rev" mention refers
# to a different document than this one outweigh the odds of finding this one's marker late.
HEADER_LINES = 8
FOOTER_LINES = 4

# "supersedes Rev B", "replaces Rev A" — a reference to another document's revision, not this
# one's. Matching it would reliably pick the *older* label off the page.
_FOREIGN = re.compile(
    r"\b(?:supersedes?|superseding|replaces?|replacing|obsoletes?|formerly|was)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RevisionMarker:
    """A revision marker found in a document."""

    label: str
    """The marker as printed, e.g. ``'Rev C 2024-08'``. Stored verbatim so a citation can quote
    it; ordering is done on the parsed parts."""

    letter: str | None = None
    date: str | None = None
    method: str = ""
    line: str = ""
    """The line it was found on, for showing a reviewer where it came from."""

    @property
    def is_orderable(self) -> bool:
        return self.date is not None or self.letter is not None


def find_revision(text: str) -> RevisionMarker | None:
    """Extract a revision marker from a document's header or footer.

    Returns None when nothing is found, which is the honest outcome: L4 then reports an
    unresolvable conflict rather than ordering two documents on a guess.
    """
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    candidates = [line for line in lines[:HEADER_LINES] if line]

    # Footers are checked too, but only after headers, so a header marker always wins.
    tail = [line for line in lines[-FOOTER_LINES:] if line] if len(lines) > FOOTER_LINES else []
    candidates.extend(line for line in tail if line not in candidates)

    for name, pattern in _PATTERNS:
        for line in candidates:
            if _FOREIGN.search(line):
                # The line is talking about a different document's revision.
                continue
            match = pattern.search(line)
            if match is None:
                continue
            return _marker(name, match, line)
    return None


def _marker(name: str, match: re.Match[str], line: str) -> RevisionMarker:
    if name == "letter_and_date":
        letter, date = match.group(1), match.group(2)
    elif name == "date_and_letter":
        date, letter = match.group(1), match.group(2)
    elif name == "effective_date":
        letter, date = None, match.group(1)
    else:
        letter, date = match.group(1), None

    label = " ".join(match.group(0).split())
    return RevisionMarker(
        label=label,
        letter=letter.upper() if letter else None,
        date=date,
        method=name,
        line=line,
    )
