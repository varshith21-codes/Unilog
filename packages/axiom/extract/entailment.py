"""Does the cited quote actually *state* the value?

Quote verification and entailment are different questions, and conflating them is the hole
this module closes. Verification asks whether the quoted text really appears in the source
document. Entailment asks whether that text supports the value attached to it. A quote can
pass the first check perfectly and fail the second completely.

The failure that motivated this: an extractor returned ``selling_uom = "Each"`` citing the
quote ``"Ctn Qty"``. The quote verified at ``match_score 1.0`` — those words genuinely are on
the page, as the header of the carton-quantity column. The citation resolved to a real
highlight. And the value was invented. A reviewer clicking through to the evidence would see
a column header and no unit of measure anywhere near it.

That is worse than an ordinary hallucination, because it arrives wearing the uniform of a
checked fact. Evidence-or-null is only a guarantee if "evidence" means evidence *for this
value*.

Three mechanical checks, no model involved:

**Header cells are not values.** A value citing row 0 of a table is rejected outright. Header
text names what a column *means*; the value has to come from a data row. This is what catches
the ``Ctn Qty`` case, and it catches it structurally rather than by guessing at wording.

**Enum values must be spoken by the quote.** The quote is scanned for every allowed value and
every alias, and the *longest* surface match wins. This both rejects invented values and
corrects under-read ones: a quote reading "Seats are reinforced PTFE" contains ``ptfe`` (4
characters, mapping to PTFE) and ``reinforced ptfe`` (15 characters, mapping to RPTFE). The
longer match is the more specific reading and it is the one the document supports, so a model
that answered "PTFE" is corrected rather than trusted. The evidence outranks the model's
paraphrase of it, which is the only ordering consistent with the rest of this system.

**Numbers must appear.** Every numeric literal in the value has to be present in the quote.
Extraction is contractually forbidden from normalising — it returns ``value_raw`` verbatim —
so a magnitude that is absent from its own citation was not read from the document.

Booleans are deliberately exempt. ``lead_free_compliant: true`` is a conclusion drawn from
"lead-free bronze alloy C89833"; the literal token "true" will never appear in the quote, and
demanding it would reject every correct answer. Those attributes carry a strict evidence
requirement and are governed by the cross-field rules instead.

Corrections are applied only when the contract is being enforced. Under ablation the raw model
claim travels through untouched, because the ablation exists to measure what this layer is
worth and a control group that quietly benefits from the treatment measures nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from axiom.schema import AttributeDefinition, Datatype

# Datatypes whose value should be recoverable from the quote by looking for its numbers.
_NUMERIC_DATATYPES = frozenset(
    {
        Datatype.INTEGER,
        Datatype.NUMBER,
        Datatype.QUANTITY,
        Datatype.RANGE,
        Datatype.DIMENSION,
        Datatype.DIMENSION_SET,
    }
)

_ENUM_DATATYPES = frozenset({Datatype.ENUM, Datatype.MULTI_ENUM})

# Matches a table cell reference's row index, as minted by ``ParsedTable.cell_ref``.
_ROW_REF = re.compile(r":r(\d+):")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Splits a multi-enum value_raw into its listed members. Newlines appear when a model echoes a
# bulleted list; slashes do not, because "NSF/ANSI 61" contains one.
_LIST_SEPARATOR = re.compile(r"[,;\n]+")


class Support(Enum):
    """Whether the quote backs the value, and if not, what to do about it."""

    SUPPORTED = "supported"
    CORRECTED = "corrected"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class SurfaceMatch:
    """One allowed value, the wording that named it, and where that wording sits in the quote.

    Positions are what let a refinement be told apart from a contradiction: two matches over the
    same characters are competing readings of one phrase, while two matches over different
    characters are two separate statements.
    """

    surface: str
    value: str
    start: int
    end: int

    def overlaps(self, other: SurfaceMatch) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Entailment:
    """The verdict, plus the value the evidence actually supports."""

    support: Support
    value_raw: str
    detail: str | None = None

    @property
    def publishable(self) -> bool:
        return self.support is not Support.UNSUPPORTED


def check_entailment(
    value_raw: str,
    quote: str,
    definition: AttributeDefinition,
    *,
    table_ref: str | None = None,
) -> Entailment:
    """Decide whether ``quote`` supports ``value_raw`` for this attribute."""
    if not value_raw or not value_raw.strip():
        return Entailment(Support.UNSUPPORTED, value_raw, "value is empty")

    if _is_header_reference(table_ref):
        return Entailment(
            Support.UNSUPPORTED,
            value_raw,
            f"quote {quote!r} is a table header cell ({table_ref}); a header names a column "
            f"and does not state a value for this part",
        )

    if not quote or not quote.strip():
        return Entailment(Support.UNSUPPORTED, value_raw, "no quote to check against")

    if definition.datatype in _ENUM_DATATYPES:
        return _check_enum(value_raw, quote, definition)
    if definition.datatype in _NUMERIC_DATATYPES:
        return _check_numeric(value_raw, quote)
    if definition.datatype is Datatype.STRING:
        return _check_string(value_raw, quote)

    # Booleans and anything added later without a defined check pass through untouched. Silently
    # rejecting a datatype nobody has thought about would be worse than not checking it.
    return Entailment(Support.NOT_APPLICABLE, value_raw)


# --------------------------------------------------------------------------- enums


def _check_enum(
    value_raw: str, quote: str, definition: AttributeDefinition
) -> Entailment:
    matches = _allowed_values_spoken_by(quote, definition)

    if definition.datatype is Datatype.MULTI_ENUM:
        return _check_multi_enum(value_raw, quote, definition, matches)

    claimed = definition.resolve_allowed(value_raw)
    spoken_as = [m for m in matches if claimed is not None and m.value == claimed]

    if not spoken_as:
        # The quote may well name some other allowed value, but the model did not read it from
        # here. Correcting across disjoint text would manufacture a value out of a citation
        # that was chosen for the wrong reason, so this is a drop rather than a rewrite.
        return Entailment(
            Support.UNSUPPORTED,
            value_raw,
            f"quote {quote!r} does not state {value_raw!r}"
            + (
                f" (it names {sorted({m.value for m in matches})} instead)"
                if matches
                else " and names none of this attribute's allowed values"
            ),
        )

    # A longer match *overlapping* the same text is a more specific reading of that text, so it
    # wins. A longer match elsewhere in the quote is a different statement — possibly a
    # contradiction in the document — and adjudicating that is the rules layer's job, not this
    # one's. "FNPT x FNPT solder ends" names two end connections; silently picking the longer
    # surface would resolve a genuine conflict by string length.
    for match in matches:
        if match.value == claimed:
            continue
        if any(match.overlaps(hit) and len(match.surface) > len(hit.surface) for hit in spoken_as):
            return Entailment(
                Support.CORRECTED,
                match.surface,
                f"quote {quote!r} reads {match.surface!r} -> {match.value!r}, which is more "
                f"specific than the reported {value_raw!r} over the same text",
            )

    return Entailment(Support.SUPPORTED, value_raw)


def _check_multi_enum(
    value_raw: str,
    quote: str,
    definition: AttributeDefinition,
    matches: list[SurfaceMatch],
) -> Entailment:
    """Prune list members the quote does not mention.

    Fabricated certifications are the highest-consequence claim in this catalogue, and they
    are usually additive: a model lists the two standards the sheet states and appends a third
    it expects to see. Dropping the unsupported members keeps the real ones rather than
    discarding a mostly-correct list.
    """
    spoken = {match.value for match in matches}
    claimed_parts = [p.strip() for p in _LIST_SEPARATOR.split(value_raw) if p.strip()]

    kept: list[str] = []
    dropped: list[str] = []
    for part in claimed_parts:
        resolved = definition.resolve_allowed(part)
        if resolved is not None and resolved in spoken:
            kept.append(part)
        else:
            dropped.append(part)

    if not dropped:
        return Entailment(Support.SUPPORTED, value_raw)
    if not kept:
        return Entailment(
            Support.UNSUPPORTED,
            value_raw,
            f"quote {quote!r} mentions none of {value_raw!r}",
        )
    return Entailment(
        Support.CORRECTED,
        ", ".join(kept),
        f"quote {quote!r} does not mention {', '.join(dropped)}; dropped from the list",
    )


def _allowed_values_spoken_by(
    quote: str, definition: AttributeDefinition
) -> list[SurfaceMatch]:
    """Every allowed value the quote names, with where in the quote it says it."""
    haystack = _normalise(quote)
    found: list[SurfaceMatch] = []
    for allowed in definition.allowed_values or ():
        for surface in (allowed.value, *(allowed.aliases or ())):
            for start in _token_positions(haystack, _normalise(surface)):
                found.append(
                    SurfaceMatch(
                        surface=surface,
                        value=allowed.value,
                        start=start,
                        end=start + len(_normalise(surface)),
                    )
                )
    return found


# ------------------------------------------------------------------------ numerics


def _check_numeric(value_raw: str, quote: str) -> Entailment:
    literals = _NUMBER.findall(value_raw)
    if not literals:
        # A numeric attribute whose value carries no digits — "Consult factory" and friends.
        # Normalisation will reject it; entailment has nothing to say.
        return Entailment(Support.NOT_APPLICABLE, value_raw)

    quote_literals = _NUMBER.findall(quote)
    missing = [n for n in literals if not _magnitude_present(n, quote_literals)]
    if missing:
        return Entailment(
            Support.UNSUPPORTED,
            value_raw,
            f"quote {quote!r} does not contain {', '.join(missing)} from {value_raw!r}; "
            f"extraction returns values verbatim, so a magnitude absent from its own citation "
            f"was not read from the document",
        )
    return Entailment(Support.SUPPORTED, value_raw)


def _magnitude_present(literal: str, quote_literals: list[str]) -> bool:
    """Compare numerically so 15 matches 15.0 and 400 matches 400.00."""
    try:
        target = float(literal)
    except ValueError:
        return False
    for candidate in quote_literals:
        try:
            if float(candidate) == target:
                return True
        except ValueError:
            continue
    return False


# ------------------------------------------------------------------------- strings


def _check_string(value_raw: str, quote: str) -> Entailment:
    if _contains_token(_normalise(quote), _normalise(value_raw)):
        return Entailment(Support.SUPPORTED, value_raw)
    return Entailment(
        Support.UNSUPPORTED,
        value_raw,
        f"quote {quote!r} does not contain the literal {value_raw!r}",
    )


# --------------------------------------------------------------------------- shared


def _is_header_reference(table_ref: str | None) -> bool:
    if not table_ref:
        return False
    match = _ROW_REF.search(table_ref)
    return match is not None and match.group(1) == "0"


def _normalise(text: str) -> str:
    """Casefold and collapse whitespace, leaving punctuation alone.

    Punctuation is load-bearing in this vocabulary — ``NSF/ANSI 61``, ``U.L.``, ``C x C`` — so
    stripping it would merge aliases that must stay distinct.
    """
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _token_positions(haystack: str, needle: str) -> list[int]:
    """Every start index where ``needle`` appears, ignoring matches inside a longer word.

    Without the boundary check the alias ``fp`` matches ``fpm`` and ``ul`` matches every
    ``full`` on the page, which would let almost any quote "support" almost any value.
    """
    if not needle:
        return []
    positions: list[int] = []
    start = 0
    while (index := haystack.find(needle, start)) != -1:
        before = haystack[index - 1] if index > 0 else " "
        after_index = index + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            positions.append(index)
        start = index + 1
    return positions


def _contains_token(haystack: str, needle: str) -> bool:
    return bool(_token_positions(haystack, needle))
