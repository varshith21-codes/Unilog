"""Sentinel values that mean "empty" without being empty.

Item masters are full of strings that occupy a cell while asserting nothing. The Unilog file
writes them as ``-- Unbranded --``, ``-- No Unilog Brand --`` and ``-- No DIB Brand --``; other
sources use a bare dash. The client's guide is explicit that these are not data and must be
filtered before matching or prompting.

Getting this wrong is expensive in both directions, which is why it is a module rather than an
inline ``if``:

* Miss a placeholder and ``-- Unbranded --`` becomes a brand. It will be fuzzy-matched against the
  approved brand master, printed in a product title, and syndicated to a channel.
* Over-detect and real data is deleted. ``None`` is a legitimate value for a coating attribute and
  ``NA`` is a real trade designation, so a keen-eyed "obviously empty" list silently destroys
  values that a supplier took the trouble to supply.

So detection here is **deliberately narrow**: the bracketed sentinel form, and strings with no
alphanumeric content at all. Both are structurally recognisable rather than guessed from meaning.
``N/A``, ``NONE``, ``TBD`` and friends are *not* treated as placeholders — see
:data:`AMBIGUOUS_SENTINELS` and the note there for why that omission is a decision and not an
oversight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The bracketed sentinel: "-- Unbranded --", "--No DIB Brand--", "- - none - -".
#
# Anchored and requiring leading and trailing dashes, so a hyphenated real value such as
# "Nickel-Plated" or a dimension such as "50-1/4" can never match.
_BRACKETED = re.compile(r"^\s*-{1,}\s*[^-].*?\s*-{1,}\s*$", re.DOTALL)

# No alphanumeric content whatsoever: "-", "--", "...", "N/A" would NOT match (has letters).
_NO_CONTENT = re.compile(r"^[^0-9a-z]*$", re.IGNORECASE)

AMBIGUOUS_SENTINELS = frozenset(
    {"n/a", "na", "none", "null", "nil", "tbd", "unknown", "not applicable", "no"}
)
"""Strings that *often* mean empty but sometimes mean something.

Not filtered by :func:`is_placeholder`, on purpose. Every one of these is a real value somewhere
in an industrial catalogue: ``NONE`` is a valid coating, ``NA`` is a sodium designation and a
region code, ``NO`` is an answer to a boolean attribute. Deleting them costs real data to save a
little noise, and the noise is visible downstream while the deletion is not.

Exposed so a caller with category-specific knowledge can opt in — pass them to
:func:`is_placeholder` via ``extra`` when the column is known to be free of such values.
"""


def is_placeholder(value: str | None, *, extra: frozenset[str] | None = None) -> bool:
    """Whether a cell asserts nothing.

    ``extra`` adds caller-supplied sentinels, compared case-insensitively after stripping. Use it
    when you know the column cannot legitimately contain them.
    """
    if value is None:
        return True
    text = value.strip()
    if not text:
        return True
    if _NO_CONTENT.match(text):
        return True
    if _BRACKETED.match(text):
        return True
    return bool(extra) and text.casefold() in {s.casefold() for s in extra}


def clean(value: str | None, *, extra: frozenset[str] | None = None) -> str | None:
    """The value, or ``None`` if it is a placeholder. Whitespace is stripped either way."""
    if is_placeholder(value, extra=extra):
        return None
    assert value is not None  # guaranteed by is_placeholder
    return value.strip()


def clean_row(
    row: dict[str, str], *, extra: frozenset[str] | None = None
) -> dict[str, str]:
    """Drop placeholder cells from a row.

    Keys disappear rather than mapping to ``""`` so that ``in`` and ``.get()`` both read as
    "absent", which is what the value actually is. A key present with an empty string invites
    exactly the bug this module exists to prevent.
    """
    return {
        key: cleaned
        for key, value in row.items()
        if (cleaned := clean(value, extra=extra)) is not None
    }


@dataclass(frozen=True)
class ColumnProfile:
    """What one column of a delivered file actually contains."""

    header: str
    total: int
    populated: int
    """Cells with real content, after placeholder filtering."""

    placeholders: int
    """Cells occupied by a sentinel. Counted separately from blanks because a file full of
    sentinels looks complete to any tool measuring non-empty cells, and that false completeness
    is the thing worth reporting."""

    blanks: int
    distinct: int
    """Distinct real values. A column with one distinct value carries no discriminating
    information even when every cell is populated."""

    examples: tuple[str, ...] = ()

    @property
    def fill_rate(self) -> float:
        return self.populated / self.total if self.total else 0.0

    @property
    def placeholder_rate(self) -> float:
        return self.placeholders / self.total if self.total else 0.0

    @property
    def carries_data(self) -> bool:
        """Whether this column is worth reading at all."""
        return self.populated > 0

    @property
    def is_discriminating(self) -> bool:
        """Whether the column can distinguish one row from another."""
        return self.distinct > 1

    def summary(self) -> dict[str, object]:
        return {
            "header": self.header,
            "total": self.total,
            "populated": self.populated,
            "placeholders": self.placeholders,
            "blanks": self.blanks,
            "distinct": self.distinct,
            "fill_rate": round(self.fill_rate, 4),
            "placeholder_rate": round(self.placeholder_rate, 4),
            "carries_data": self.carries_data,
            "is_discriminating": self.is_discriminating,
        }


def profile_column(
    header: str,
    values: list[str],
    *,
    extra: frozenset[str] | None = None,
    example_limit: int = 3,
) -> ColumnProfile:
    """Measure one column's real content."""
    placeholders = blanks = 0
    real: list[str] = []
    for value in values:
        text = (value or "").strip()
        if not text:
            blanks += 1
        elif is_placeholder(text, extra=extra):
            placeholders += 1
        else:
            real.append(text)

    seen: list[str] = []
    for value in real:
        if value not in seen:
            seen.append(value)
        if len(seen) >= example_limit:
            break

    return ColumnProfile(
        header=header,
        total=len(values),
        populated=len(real),
        placeholders=placeholders,
        blanks=blanks,
        distinct=len(set(real)),
        examples=tuple(seen),
    )


def profile_rows(
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    extra: frozenset[str] | None = None,
) -> dict[str, ColumnProfile]:
    """Profile every column of a delivered file, keyed by header."""
    return {
        header: profile_column(header, [row.get(header, "") for row in rows], extra=extra)
        for header in headers
    }


def dead_columns(profiles: dict[str, ColumnProfile]) -> tuple[str, ...]:
    """Columns that carry no data at all, in declared order.

    Worth surfacing to an operator before anything else: a column that is 100% sentinel is a
    column the supplier believes they are sending and are not, which is a conversation to have
    with them rather than a modelling problem to solve.
    """
    return tuple(h for h, p in profiles.items() if not p.carries_data)


__all__ = [
    "AMBIGUOUS_SENTINELS",
    "ColumnProfile",
    "clean",
    "clean_row",
    "dead_columns",
    "is_placeholder",
    "profile_column",
    "profile_rows",
]
