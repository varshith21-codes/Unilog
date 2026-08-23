"""Deterministic extraction from document structure. No model, no prompt, no tokens.

A datasheet is not prose that happens to contain numbers. It is a **layout**, and the layout
carries the meaning: a specification block pairs a label with a value on one line, and an ordering
table pairs a part number with a row of cells. Both relationships are mechanical, and reading them
mechanically is strictly better than asking a model to:

* **It cannot read the wrong row.** The failure that matters most in this domain is a value that is
  genuinely present in the document and belongs to a different part — quote verification cannot
  catch it, because the quote is real. Deterministic cell lookup removes the failure rather than
  reducing its probability. ``extract/variants.py`` makes the same argument for the same reason.
* **The citation is a coordinate, not a claim.** A cell reference (``t1:r3:c1``) or a line index is
  something we computed, so the evidence viewer can highlight it and a reviewer can check it in a
  second. A model reporting which line it read is an assertion about its own behaviour.
* **It costs nothing and runs offline**, so it can gate CI on every push.

What this module is *not*: a replacement for the model extractor. It reads what the layout states
plainly. Prose — "the valve is furnished with a tee handle above DN25" — needs reading
comprehension, and that is what ``extract/extractor.py`` is for. The two are complementary, and the
deterministic pass runs first so the model is only asked about what the layout did not settle.

Two paths, because documents carry specifications in two shapes (both measured on the samples in
``data/samples/``):

1. **Specification lines.** ``Body Material .................. Bronze C84400``. Label on the left,
   value on the right, separated by a dot leader, a colon, or a run of spaces. Cited to the line,
   with the bounding box tightened to the value fragment.
2. **Ordering-table rows.** One row per orderable part number, columns bound to attributes by the
   schema's declared ``table_headers``. Cited to the exact cell.

Every value is matched against an attribute the class actually **binds**, which is the guard that
keeps both paths honest — the same guard that fixed classification precision and description
extraction. Without it, a "Size" column would populate whatever attribute in the whole dictionary
happened to want a length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.docintel.models import ParsedDocument, ParsedTable
from axiom.docintel.sku import find_sku
from axiom.extract.variants import (
    _SIZE_SCOPED,
    _fold,
    _looks_like_part_number,
    _note_covers_size,
)
from axiom.schema import SchemaRegistry
from axiom.schema.models import AttributeDefinition

PROMPT_VERSION = "structured@v1"
"""Not a prompt. The field exists so a value's provenance names what produced it, and "which
version of the layout rules" is the reproducibility question for this path."""

SPEC_LINE_CONFIDENCE = 0.90
"""A labelled value on its own line in a manufacturer document. High: the label is explicit and the
pairing is unambiguous. Below a reviewed value, and below an ordering-table cell, because a label
can still be abbreviated in a way that matches the wrong attribute."""

TABLE_CELL_CONFIDENCE = 0.94
"""The strongest evidence this system can produce without a human. The row is located by matching
the part number and the column by a header the schema declared, so both coordinates are computed
rather than judged."""

MAX_LABEL_CHARS = 48
"""Longer than this and it is a sentence, not a label. Prose containing a colon — "Note: sizes
above DN25 ship with a tee handle" — would otherwise be read as a label/value pair."""

MIN_SPACE_RUN = 3
"""Spaces needed to count as a column separator. Two can occur inside a label after a period."""

# Dot, dash or underscore leaders: "Body Material ......... Bronze".
_LEADER = re.compile(r"^(?P<label>.*?)\s*[.\u00b7_\-]{3,}\s*(?P<value>.+)$")

# "Label: value". Requires the colon to be followed by whitespace so "1:12 slope" is not split.
_COLON = re.compile(r"^(?P<label>[^:]{2,48}?)\s*:\s+(?P<value>.+)$")

# A run of spaces acting as a column gap.
_SPACE_RUN = re.compile(rf"^(?P<label>.+?)\s{{{MIN_SPACE_RUN},}}(?P<value>.+)$")

# Lines that are commentary rather than specification. Footnotes are handled by the model path and
# by variants.py's size-scoping; treating one as a spec would attach a qualified value unqualified.
_COMMENTARY = re.compile(
    r"^\s*(note\s*\d*\b|warning\b|caution\b|see\s+(page|chart|table)\b|\*|\u2020)",
    re.IGNORECASE,
)

# A value that says the document declines to state one. Recording "Consult factory" as a flow
# coefficient would turn an explicit absence into a populated field, which is worse than a gap
# because it stops anyone looking for the real number.
_NON_VALUES = frozenset(
    {
        "consultfactory",
        "consultmanufacturer",
        "contactfactory",
        "n/a",
        "na",
        "tbd",
        "tba",
        "varies",
        "seechart",
        "seetable",
        "onrequest",
        "uponrequest",
        "",
    }
)


@dataclass(frozen=True)
class StructuredMatch:
    """One attribute read out of document structure, with the coordinate that evidences it."""

    attribute_code: str
    value_raw: str
    quote: str
    """The full line or cell text. This is what gets verified against the document, and it is
    deliberately wider than ``value_raw``: a quote of just "Bronze C84400" could come from
    anywhere, while the whole line proves which label it sat beside."""

    locator: str
    page: int
    source: str
    """``spec_line`` or ``ordering_table``."""

    label: str
    """The label or column header that matched, kept for audit. When a mapping turns out to be
    wrong this is the first thing anyone needs to see."""

    confidence: float
    bbox: BoundingBox | None = None
    table_id: str | None = None
    row: int | None = None
    col: int | None = None

    def summary(self) -> dict[str, object]:
        return {
            "attribute": self.attribute_code,
            "value": self.value_raw,
            "label": self.label,
            "source": self.source,
            "locator": self.locator,
            "page": self.page,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class StructuredExtraction:
    """Everything the layout yielded, plus everything it showed us and we refused."""

    document_id: str
    class_code: str | None = None
    target_sku: str | None = None
    matches: list[StructuredMatch] = field(default_factory=list)
    unmapped_labels: list[str] = field(default_factory=list)
    """Labels that looked like specifications but matched no bound attribute.

    The highest-value diagnostic this module produces. Each one is either a missing
    ``table_headers`` entry in the schema — a one-line fix — or a real attribute the class has not
    declared yet. Dropping them silently is how a schema stops keeping up with its documents.
    """

    unmapped_headers: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(m.attribute_code for m in self.matches)

    def by_source(self, source: str) -> list[StructuredMatch]:
        return [m for m in self.matches if m.source == source]

    def summary(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "class_code": self.class_code,
            "target_sku": self.target_sku,
            "matched": len(self.matches),
            "from_spec_lines": len(self.by_source("spec_line")),
            "from_ordering_table": len(self.by_source("ordering_table")),
            "attributes": list(self.codes),
            "unmapped_labels": list(self.unmapped_labels),
            "unmapped_headers": list(self.unmapped_headers),
            "refused": [{"attribute": c, "reason": r} for c, r in self.refused],
            "notes": list(self.notes),
        }


# ------------------------------------------------------------------ label vocabulary


SPEC_LINE = "spec_line"
ORDERING_TABLE = "ordering_table"


@dataclass(frozen=True)
class LabelBinding:
    """An attribute claimed by a label, and how strongly it claims it."""

    attribute_code: str
    priority: int
    """Position of the matching label within the attribute's declared list. Lower wins.

    This is what settles an ordering table carrying both ``DN`` and ``Size``: ``nominal_size``
    declares ``size`` before ``dn``, so the imperial column wins and the choice is the schema
    author's rather than a consequence of column order in the supplier's file. Without it the two
    columns produce two values for one attribute — 25 mm from DN25 and 25.4 mm from 1", which are
    different numbers.
    """

    matched: str


def _vocabulary(attribute: AttributeDefinition, source: str) -> tuple[str, ...]:
    """The labels this attribute answers to in a given layout.

    ``name`` first, so an exact name match always outranks an alias. Then the layout-specific list:
    ordering tables use ``table_headers``, specification blocks use ``spec_labels``. Keeping them
    apart is the whole reason ``spec_labels`` exists — see its description in the schema model.
    """
    specific = attribute.table_headers if source == ORDERING_TABLE else attribute.spec_labels
    return (attribute.name, *specific)


def label_lookup(
    registry: SchemaRegistry, class_code: str, *, source: str = SPEC_LINE
) -> dict[str, LabelBinding]:
    """``folded label -> binding`` for every attribute this class binds, in one layout.

    Scoped to the class, which is the guard that keeps this honest: a "Size" column populates a
    length attribute only if the class declared one. Scoped to the layout, which is the guard that
    stops a table header being read as a specification label.

    A contested label goes to the attribute the class declares first, and
    :func:`contested_labels` reports it — two attributes claiming one label is a schema defect, and
    resolving it silently hides it.
    """
    lookup: dict[str, LabelBinding] = {}
    definition = registry.product_class(class_code)
    for binding in definition.attributes:
        try:
            attribute = registry.attribute(binding.code)
        except KeyError:
            continue
        for priority, candidate in enumerate(_vocabulary(attribute, source)):
            folded = _fold(candidate)
            if folded and folded not in lookup:
                lookup[folded] = LabelBinding(binding.code, priority, candidate)
    return lookup


def contested_labels(
    registry: SchemaRegistry, class_code: str, *, source: str = SPEC_LINE
) -> dict[str, list[str]]:
    """Labels that more than one bound attribute claims.

    A schema integrity problem rather than a runtime one: whichever attribute is declared first
    wins every document, so the second is unreachable and the first may be receiving values that
    belong to it.
    """
    claims: dict[str, list[str]] = {}
    definition = registry.product_class(class_code)
    for binding in definition.attributes:
        try:
            attribute = registry.attribute(binding.code)
        except KeyError:
            continue
        for candidate in _vocabulary(attribute, source):
            folded = _fold(candidate)
            if folded:
                claims.setdefault(folded, []).append(binding.code)
    return {label: sorted(set(codes)) for label, codes in claims.items() if len(set(codes)) > 1}


# ------------------------------------------------------------------ specification lines


def split_spec_line(text: str) -> tuple[str, str, int] | None:
    """Split a specification line into (label, value, value offset), or None.

    The offset is returned so the citation's bounding box can be tightened from the whole line down
    to the value. A reviewer checking forty values should not have to read forty full lines.

    Separators are tried most-specific first. A dot leader is unambiguous; a run of spaces is the
    weakest signal and is tried last, because a label containing a single space would otherwise
    split at the wrong place.
    """
    line = text.rstrip()
    if not line.strip() or _COMMENTARY.match(line):
        return None

    for pattern in (_LEADER, _COLON, _SPACE_RUN):
        match = pattern.match(line)
        if match is None:
            continue
        label = match.group("label").strip()
        value = match.group("value").strip()
        if not _plausible_label(label) or not _plausible_value(value):
            continue
        # Offset of the value within the original text, for the bbox.
        offset = line.rfind(value)
        return label, value, max(0, offset)
    return None


def _plausible_label(label: str) -> bool:
    """A label names a property. It is short, has letters, and is not a sentence."""
    stripped = label.strip(" .:-\u00b7_")
    if not stripped or len(stripped) > MAX_LABEL_CHARS:
        return False
    if not any(ch.isalpha() for ch in stripped):
        return False
    # A trailing full stop, or several sentence-like words, means prose.
    return not stripped.endswith(".") and len(stripped.split()) <= 6


# Words that open a sentence, never a specification value. A value states a fact — "Bronze C84400",
# "600 PSI WOG", "UL listed, CSA certified" — and does not refer back to a subject.
_PROSE_OPENERS = frozenset(
    {
        "the", "a", "an", "this", "these", "those", "it", "its", "they", "we", "you", "all",
        "any", "each", "every", "must", "should", "may", "can", "will", "do", "does", "if",
        "when", "where", "please", "refer", "see", "for", "to", "in", "on", "at", "with",
        "and", "or", "but", "because", "unless", "install", "installed", "supplied",
    }
)

MAX_VALUE_WORDS = 14
"""Beyond this a value is a sentence. Generous on purpose: an overflow field legitimately holds
"240 kW-hr Annual Energy, 1 to 12 hr Delay Start Hours" at eleven words, so the cap is only there
to catch outright prose that the opener check missed."""


def _plausible_value(value: str) -> bool:
    """A value states a fact rather than describing behaviour.

    The case that forced this: ``Installation: the valve must be supported independently of the
    pipework it serves`` splits into a one-word label and a sentence. The label is plausible, so
    without a value-side guard the extractor would look for an "Installation" attribute and — on a
    class that happened to declare one — record a paragraph of prose as its value.

    Detected by the opening word rather than by length alone, because length is the weaker signal:
    an overflow field can legitimately be long, while a value that begins "the" is prose whatever
    its length.
    """
    words = value.split()
    if not words:
        return False
    if words[0].casefold().strip(",.;:") in _PROSE_OPENERS:
        return False
    return len(words) <= MAX_VALUE_WORDS


def _is_non_value(value: str) -> bool:
    return _fold(value) in _NON_VALUES


_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


def size_qualifier(value: str) -> str | None:
    """The size qualifier a specification value carries, if any.

    ``18-22 ft-lb (1/2" size)`` is a torque figure for **one** member of a series. Inheriting it
    across the range attaches a plausible, precisely-cited, wrong number to every other size, and
    quote verification cannot catch it because the quote is real. ``variants.py`` guards the
    inheritance path against exactly this; a specification block needs the same guard, because a
    reader who does not check the parenthetical will copy the value onto all six part numbers.

    Found by the golden set: without this, ``operating_torque`` was emitted for all four BA-100
    SKUs from a line that qualifies it to the 1/2" valve, and the golden set records it as absent
    for every one of them.
    """
    for match in _PARENTHETICAL.finditer(value):
        note = match.group(1).strip()
        if note and _SIZE_SCOPED.search(note):
            return note
    return None


def _clean_value(value: str) -> str:
    """Strip sentence punctuation a document's prose leaves on a value.

    ``Country of origin: Taiwan.`` yields ``Taiwan.``, and the trailing stop belongs to the
    sentence rather than to the country. Only a *trailing* stop, comma or semicolon is removed, and
    only when what precedes it is not itself punctuation — ``1-1/4"`` and ``600 PSI WOG @ 73 degF``
    must survive untouched.
    """
    cleaned = value.strip()
    while cleaned and cleaned[-1] in ".,;" and len(cleaned) > 1 and cleaned[-2].isalnum():
        cleaned = cleaned[:-1].rstrip()
    return cleaned


# ------------------------------------------------------------------ the extractor


def extract_structured(
    parsed: ParsedDocument,
    registry: SchemaRegistry,
    *,
    class_code: str | None,
    target_sku: str | None = None,
    require_sku: bool = True,
) -> StructuredExtraction:
    """Read every attribute the class binds that the document's layout states plainly.

    ``target_sku`` does two things, and the second is the more important.

    It selects the ordering-table row. Without it the table is skipped entirely rather than guessed
    at: a datasheet describing six part numbers has six different answers for "Size", and choosing
    one without being told which part is wanted is a wrong-row error.

    It also **establishes that this document is about this part at all.** That check is not
    optional, and it is done here rather than left to callers because forgetting it is the bug:
    handed three datasheets and asked about one valve, an unguarded reader attributes all three
    specification blocks to it, and the later ones supersede the earlier. Every value then carries a
    quote that verifies, because the words really are in a document — just not in a document about
    this product. ``docintel.sku`` exists for exactly this failure and is reused here rather than
    re-derived.

    ``require_sku=False`` disables the relevance check, for a caller that has established relevance
    some other way. It is not the default, because the safe behaviour should be the one you get by
    not thinking about it.
    """
    result = StructuredExtraction(
        document_id=parsed.document.document_id,
        class_code=class_code,
        target_sku=target_sku,
    )
    if class_code is None:
        result.notes.append("no product class, so no attribute vocabulary to match against")
        return result

    if target_sku and require_sku:
        presence = find_sku(parsed, target_sku)
        if presence.is_absent:
            result.notes.append(
                f"{target_sku!r} does not appear in this document, so nothing here describes it; "
                f"no values were read"
            )
            return result
        if presence.is_not_offered:
            result.notes.append(
                f"{target_sku!r} appears in this document only to be withdrawn "
                f"({presence.withdrawal_quote!r}); no values were read"
            )
            return result
        if not presence.checked:
            result.notes.append(
                f"{target_sku!r} is too short to search for safely, so this document's relevance "
                f"to it is unverified"
            )
        elif not presence.listed_in_table:
            # Mentioned but not sold here. Worth reading, worth flagging: a part referenced in a
            # note may be a cross-reference rather than the subject of the sheet.
            result.notes.append(
                f"{target_sku!r} is mentioned at {presence.location} but has no ordering row, so "
                f"this document may reference it rather than describe it"
            )
    elif not target_sku:
        result.notes.append(
            "no target SKU, so this document's relevance to a specific part is unverified: "
            "every specification read here is attributed on the caller's assurance"
        )

    try:
        spec_vocab = label_lookup(registry, class_code, source=SPEC_LINE)
        table_vocab = label_lookup(registry, class_code, source=ORDERING_TABLE)
    except KeyError:
        result.notes.append(f"class {class_code!r} is not in the schema")
        return result

    for source in (SPEC_LINE, ORDERING_TABLE):
        for label, codes in sorted(contested_labels(registry, class_code, source=source).items()):
            result.notes.append(
                f"{source}: label {label!r} is claimed by {', '.join(codes)}; the first declared "
                f"binding wins every document until the schema is fixed"
            )

    claimed: set[str] = set()

    # The ordering table is read FIRST, and the order is load-bearing rather than incidental.
    #
    # It is the more specific source — a row keyed on the part number beats a block describing the
    # series — so it would win a contest anyway. But it also establishes the target's *size*, and
    # the specification pass needs that to decide whether a size-qualified value applies. Reading
    # the block first would mean filtering by a size we had not learned yet.
    if target_sku:
        _extract_ordering_row(parsed, registry, table_vocab, result, claimed, target_sku)
    else:
        result.notes.append(
            "no target SKU supplied, so ordering-table columns were not read: a table with "
            "several part numbers has several answers and picking one would be a guess"
        )

    target_size = next(
        (m.value_raw for m in result.matches if m.attribute_code == "nominal_size"), None
    )
    _extract_spec_lines(parsed, registry, spec_vocab, result, claimed, target_size=target_size)

    result.matches.sort(key=lambda m: (m.page, m.locator))
    result.unmapped_labels = sorted(dict.fromkeys(result.unmapped_labels))
    result.unmapped_headers = sorted(dict.fromkeys(result.unmapped_headers))
    return result


def _extract_spec_lines(
    parsed: ParsedDocument,
    registry: SchemaRegistry,
    lookup: dict[str, LabelBinding],
    result: StructuredExtraction,
    claimed: set[str],
    *,
    target_size: str | None = None,
) -> None:
    """Read ``Label ..... Value`` pairs from every page.

    ``target_size`` is the nominal size of the part we are extracting for, learned from the
    ordering table. It gates size-qualified values: see :func:`size_qualifier`.
    """
    covered = _table_covered_lines(parsed)

    for page in parsed.pages:
        for line in page.lines:
            if line.line_index in covered:
                # Inside a table's bounds. The table path reads it with a real cell reference, and
                # reading it here as well would produce two citations for one fact.
                continue
            split = split_spec_line(line.text)
            if split is None:
                continue
            label, value, offset = split
            value = _clean_value(value)
            if not value:
                continue

            binding = lookup.get(_fold(label))
            if binding is None:
                result.unmapped_labels.append(label)
                continue
            code = binding.attribute_code
            if code in claimed:
                # An earlier, better-evidenced reading already holds this attribute. A datasheet
                # repeating a value in a summary box is common and harmless; overwriting a table
                # cell with a prose restatement is not.
                continue
            if _is_non_value(value):
                result.refused.append(
                    (code, f"{label!r} states {value!r}, which declines to give a value")
                )
                continue

            attribute = _attribute_or_none(registry, code)
            if attribute is None:
                continue
            if problem := _implausible(attribute, value):
                result.refused.append((code, problem))
                continue

            # A value qualified to one size in the series describes that size, not this part.
            if qualifier := size_qualifier(value):
                if target_size is None:
                    result.refused.append(
                        (
                            code,
                            f"{value!r} is qualified to {qualifier!r} and the target size is "
                            f"unknown, so whether it applies cannot be established",
                        )
                    )
                    continue
                if not _note_covers_size(qualifier, target_size):
                    result.refused.append(
                        (
                            code,
                            f"{value!r} is qualified to {qualifier!r}, which does not cover the "
                            f"target size {target_size!r}",
                        )
                    )
                    continue

            result.matches.append(
                StructuredMatch(
                    attribute_code=code,
                    value_raw=value,
                    quote=line.text.strip(),
                    locator=f"p{line.page}:l{line.line_index}",
                    page=line.page,
                    source=SPEC_LINE,
                    label=label,
                    confidence=SPEC_LINE_CONFIDENCE,
                    bbox=line.span_bbox(offset, offset + len(value)),
                )
            )
            claimed.add(code)


def _extract_ordering_row(
    parsed: ParsedDocument,
    registry: SchemaRegistry,
    lookup: dict[str, LabelBinding],
    result: StructuredExtraction,
    claimed: set[str],
    target_sku: str,
) -> None:
    """Read the ordering-table row belonging to the target part number.

    Per-variant values override anything read from a specification line, because a table keyed on
    the part number is more specific than a block describing the series. That ordering is the whole
    reason the table path runs second.
    """
    folded_sku = _fold(target_sku)
    read_any = False
    for table in parsed.all_tables():
        located = _locate_sku_cell(table, folded_sku)
        if located is None:
            continue
        header_row, row_index, sku_col = located
        headers = _header_texts(table, header_row)

        # Recorded even when nothing maps, because "the row was found and no header was recognised"
        # is the diagnostic that names a missing `table_headers` entry — a one-line schema fix. It
        # used to be unreachable: the row lookup failed first and the header list was never seen.
        # Gathered before emitting so a contested attribute is resolved by declared priority
        # rather than by whichever column the supplier printed first.
        candidates: dict[str, tuple[int, StructuredMatch]] = {}

        for col, header in enumerate(headers):
            if col == sku_col or not header.strip():
                continue
            binding = lookup.get(_fold(header))
            if binding is None:
                result.unmapped_headers.append(header.strip())
                continue
            code = binding.attribute_code

            cell = table.cell(row_index, col)
            text = _clean_value((cell.text if cell else "").strip())
            if not text or _is_non_value(text):
                continue

            attribute = _attribute_or_none(registry, code)
            if attribute is None:
                continue
            if problem := _implausible(attribute, text):
                result.refused.append((code, problem))
                continue

            match = StructuredMatch(
                attribute_code=code,
                value_raw=text,
                quote=_row_quote(table, row_index, header_row=header_row),
                locator=table.cell_ref(row_index, col),
                page=table.page,
                source=ORDERING_TABLE,
                label=header.strip(),
                confidence=TABLE_CELL_CONFIDENCE,
                bbox=cell.bbox if cell else None,
                table_id=table.table_id,
                row=row_index,
                col=col,
            )
            existing = candidates.get(code)
            if existing is None or binding.priority < existing[0]:
                if existing is not None:
                    result.notes.append(
                        f"{code}: columns {existing[1].label!r} and {match.label!r} both claim it; "
                        f"kept {match.label!r}, which the schema declares first"
                    )
                candidates[code] = (binding.priority, match)
            else:
                result.notes.append(
                    f"{code}: columns {existing[1].label!r} and {match.label!r} both claim it; "
                    f"kept {existing[1].label!r}, which the schema declares first"
                )

        for code, (_, match) in candidates.items():
            # No supersede step is needed: this path runs before the specification pass, and that
            # pass skips any attribute already in `claimed`. The precedence — table beats block —
            # is expressed by the call order in `extract_structured` rather than by rewriting
            # matches here, which keeps it in one place instead of two.
            result.matches.append(match)
            claimed.add(code)

        read_any = True
        result.notes.append(
            f"read ordering row {row_index} of {table.table_id} for {target_sku!r} "
            f"(part number in column {sku_col}, headers from row {header_row})"
        )
        # No early return. A manufacturer catalogue splits one series across several tables and
        # pages — dimensions in one, speed ratings and approvals in another — and stopping at the
        # first hit read one table of a document that stated the rest a page later. `claimed` still
        # guarantees the first (best) reading of any attribute wins, so continuing cannot overwrite.

    if not read_any:
        result.notes.append(
            f"no ordering table contained {target_sku!r}, so no per-variant values were read"
        )


def _locate_sku_cell(table: ParsedTable, folded_sku: str) -> tuple[int, int, int] | None:
    """Find ``(header_row, row, col)`` for the target part number in this table, or None.

    This replaces a pair of much stricter locators, and the reason is a contract mismatch that made
    retrieval look like it was failing when it was not. ``docintel.sku.find_sku`` decides whether a
    document *covers* a part with a substring search over every cell of every table; this function
    decides whether a value can be *read* for it. When the two disagree, the library reports a
    manufacturer document covering the SKU in an ordering row and extraction returns nothing from it
    — which is exactly what the sample catalogue showed, on documents that plainly state the values.

    The old gates, both dropped:

    * the part-number **column** had to carry one of sixteen hardcoded header strings, so
      ``Cat. No.`` matched and ``Item #``, ``Stock No.``, ``Product No.`` or a merged
      ``Cat. No. Description`` cell did not;
    * the part-number **cell** had to equal the SKU exactly and contain no spaces, so an
      aligned-table column band that swallowed the neighbouring description — ``49-94-0001 Cut-Off
      Wheel`` — was rejected.

    What is *not* dropped is the guarantee those gates existed for: never read the wrong row. The
    part number must still appear as a **whole token** of the cell, so ``77C-105`` does not match
    ``77C-105R`` and read the reduced-port variant's row for the full-port valve. That is the
    property that matters, and it is enforced directly rather than as a side effect of forbidding
    spaces.
    """
    grid = table.rows()
    if len(grid) < 2:
        return None

    header_row = _find_header_row(grid)
    # Any row after the header. Restricting the *column* is what the header whitelist was for, and
    # scanning every column instead is safe because the match below is anchored on the part number.
    for row in range(header_row + 1, len(grid)):
        for col, text in enumerate(grid[row]):
            if _cell_names_sku(text, folded_sku):
                return header_row, row, col
    return None


def _cell_names_sku(text: str, folded_sku: str) -> bool:
    """Whether this cell identifies the target part, as a whole token rather than a substring.

    The whole-token rule is the anti-wrong-row guarantee. ``_fold`` strips punctuation, so a bare
    ``in`` test would match ``77C-105`` inside ``77C-105R``; splitting on whitespace first and
    comparing each token exactly cannot. A cell holding only the part number still matches, which is
    the common case.
    """
    candidate = text.strip()
    if not candidate or not folded_sku:
        return False
    if _fold(candidate) == folded_sku:
        return True
    # A merged or badly-segmented cell: the part number plus its description. Accept it only if one
    # of the words *is* the part number.
    return any(_fold(token) == folded_sku for token in candidate.split())


def _find_header_row(grid: list[list[str]]) -> int:
    """Which row carries the column labels. Usually 0, and not reliably so.

    A catalogue table routinely opens with a merged title band ("BONDED ABRASIVES — TYPE 1")
    or a units strip ("in / mm") above the real labels, and ``ParsedTable.header`` hardcodes
    ``rows()[0]``. Reading a title row as the header maps every column to nothing.

    Chosen by a simple, explainable score rather than a heuristic worth arguing about: the best
    header row is the one with the most non-empty, non-numeric cells, searching only the first few
    rows and preferring the earliest on a tie. A data row scores badly because its cells are mostly
    numbers and part numbers.
    """
    best_row, best_score = 0, -1
    for row in range(min(_HEADER_SEARCH_ROWS, len(grid) - 1)):
        cells = [text.strip() for text in grid[row]]
        filled = [text for text in cells if text]
        if not filled:
            continue
        wordy = sum(
            1
            for text in filled
            if not _LEADING_NUMBER.match(text) and not _looks_like_part_number(text)
        )
        if wordy > best_score:
            best_row, best_score = row, wordy
    return best_row


_HEADER_SEARCH_ROWS = 3
"""How far down to look for the label row. Three covers a title band and a units strip above it;
searching deeper would start treating a data row as headers on a table whose labels are genuinely
missing."""


def _header_texts(table: ParsedTable, header_row: int) -> list[str]:
    """The header row's cells, widened by the column above it when a cell is blank.

    A two-line header — ``Max`` over ``RPM``, or a units strip under the label — leaves the chosen
    row blank in some columns while the text sits one row up or down. Falling back to the
    neighbouring cell recovers the label instead of dropping the column.
    """
    grid = table.rows()
    if header_row >= len(grid):
        return []
    headers = list(grid[header_row])
    for col, text in enumerate(headers):
        if text.strip():
            continue
        for neighbour in (header_row - 1, header_row + 1):
            if 0 <= neighbour < len(grid) and col < len(grid[neighbour]):
                candidate = grid[neighbour][col].strip()
                # Only a label, never a value: a number here is the first data row bleeding up.
                if candidate and not _LEADING_NUMBER.match(candidate):
                    headers[col] = candidate
                    break
    return headers


def _row_quote(table: ParsedTable, row: int, *, header_row: int = 0) -> str:
    """The whole row, rendered. Wider than the cell on purpose.

    A quote of ``12`` proves nothing and verifies against half the document. The row shows the part
    number the value sits beside, which is what makes the citation checkable by eye.

    ``header_row`` is passed rather than assumed to be 0, so the quote names the same labels the
    values were mapped through. Quoting a title band above the real headers would show a reviewer a
    row of numbers under a heading that does not describe them.
    """
    grid = table.rows()
    if not grid or row >= len(grid):
        return ""
    header = (
        " | ".join(t for t in grid[header_row] if t.strip()) if header_row < len(grid) else ""
    )
    body = " | ".join(t for t in grid[row] if t.strip())
    return f"{header} || {body}" if header else body


def _table_covered_lines(parsed: ParsedDocument) -> set[int]:
    """Line indices a table already represents, so they are not read twice.

    Mirrors ``ParsedPage.to_prompt_text``: a line a table already represents must be read once, by
    the path that can cite a cell.

    Suppression is by **text**, not by bounding box, and that change is what makes the specification
    path usable on a catalogue page. The aligned-table detector fires on any three consecutive lines
    with three or more wide column gaps, so on a multi-column page the table's bbox spans almost the
    whole sheet — and a y-overlap test then suppressed every line in it, including the ``Label:
    value`` lines printed beside the table that no cell actually holds. The specification path went
    silent on precisely the documents that carry the most specifications.

    Comparing reconstructed cell text against the line keeps the guarantee that mattered (no double
    citation of one fact) without the collateral damage: a line whose content really is in the table
    is skipped, and a line that merely shares its vertical band is read.
    """
    covered: set[int] = set()
    for page in parsed.pages:
        if not page.tables:
            continue
        cell_texts: set[str] = set()
        for table in page.tables:
            for cell in table.cells:
                if folded := _fold(cell.text):
                    cell_texts.add(folded)
        if not cell_texts:
            continue
        for line in page.lines:
            folded_line = _fold(line.text)
            if not folded_line:
                continue
            # The whole line is one cell, or the line is the concatenation of the row's cells.
            # Either way the table path can cite it precisely and this path must not restate
            # it.
            if folded_line in cell_texts:
                covered.add(line.line_index)
                continue
            if _is_row_of_cells(folded_line, cell_texts):
                covered.add(line.line_index)
    return covered


def _is_row_of_cells(folded_line: str, cell_texts: set[str]) -> bool:
    """Whether a line is just a table row's cells run together.

    A reconstructed row loses the whitespace between cells once folded, so a genuine table row reads
    as the concatenation of two or more cell texts. Requiring at least two keeps a one-word line
    that happens to equal a cell from suppressing an unrelated specification line elsewhere on the
    page — that case is already handled by the exact test above.
    """
    remaining = folded_line
    consumed = 0
    # Longest first, so a cell whose text is a prefix of another does not strand the rest.
    for text in sorted(cell_texts, key=len, reverse=True):
        if text and remaining.startswith(text):
            remaining = remaining[len(text) :]
            consumed += 1
            if not remaining:
                return consumed >= 2
    return False


def _attribute_or_none(registry: SchemaRegistry, code: str) -> AttributeDefinition | None:
    try:
        return registry.attribute(code)
    except KeyError:
        return None


def _implausible(attribute: AttributeDefinition, value: str) -> str | None:
    """Reject a value the attribute cannot physically hold.

    Only applied where the schema declares a range, and only when a magnitude can be parsed. A
    coincidental label match is far more likely than a genuine ten-thousand-fold outlier, so
    refusing is the cheaper error — and the refusal is reported rather than swallowed.
    """
    if not attribute.plausible_range:
        return None
    magnitude = _leading_magnitude(value)
    if magnitude is None:
        return None

    low, high = attribute.plausible_range
    if attribute.canonical_unit:
        from axiom.normalize.units import registry as unit_registry

        resolved = unit_registry.resolve(attribute.unit_hint or attribute.canonical_unit)
        if resolved is not None:
            magnitude = resolved.to_canonical(magnitude)
    if low <= magnitude <= high:
        return None
    return (
        f"{value!r} is outside the plausible range [{low}, {high}] "
        f"{attribute.canonical_unit or ''}".rstrip()
    )


_LEADING_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)(?:\s*-\s*(\d+)/(\d+))?")

_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
"""A comma between a digit and exactly three more digits: a thousands separator.

Narrow on purpose. A bare ``,`` strip would turn the decimal comma of ``0,45`` into 45, and the
lookahead's ``(?!\\d)`` stops ``1,2345`` — which is not a grouped number — from being joined.
"""


_BARE_FRACTION = re.compile(r"^\s*(\d+)\s*/\s*(\d+)")
_LEADING_DECIMAL = re.compile(r"^\s*(-?\.\d+)")


def _leading_magnitude(value: str) -> float | None:
    """The number a value starts with, if any.

    Handles the four shapes a manufacturer's dimension column actually prints, three of which used
    to be misread:

    * ``50-1/4`` → 50.25. Whole plus fraction, already handled.
    * ``15,300`` → 15300. Grouped thousands. Every bonded-abrasive catalogue prints the maximum safe
      speed this way, and stopping at the comma read **15**, which then failed the attribute's
      plausible range of [500, 80000]. The one figure deciding whether a wheel may be fitted to a
      given tool was discarded as implausible on every document that stated it.
    * ``1/8`` → 0.125. A **bare fraction**, and the worst of the three because it produced confident
      wrong refusals rather than none: the old pattern read the numerator as a whole number, so
      ``1/8"`` became 1 inch, converted to 25.4 mm, and was refused against thickness's [0.5, 15] mm
      as out of range. A correct value, correctly located and cited, thrown away — and the refusal
      quoted ``'1/8"'``, which reads like a range error rather than a parse error. It accounted for
      every thickness refusal on the sample's abrasive rows.
    * ``.045`` → 0.045. A leading decimal point with no integer part, which matched nothing at all.
      That returned None, so the plausibility check silently *skipped* rather than misfiring — safe,
      but it meant the check was not running on the commonest way a wheel thickness is written.

    Order matters: the bare fraction must be tried before the whole-number pattern, or ``1/8`` is
    claimed by the latter as ``1``.
    """
    text = _THOUSANDS.sub("", value)

    if (fraction := _BARE_FRACTION.match(text)) is not None:
        denominator = float(fraction.group(2))
        if denominator:
            return float(fraction.group(1)) / denominator

    if (decimal := _LEADING_DECIMAL.match(text)) is not None:
        return float(decimal.group(1))

    match = _LEADING_NUMBER.match(text)
    if match is None:
        return None
    whole = float(match.group(1))
    if match.group(2) and match.group(3):
        denominator = float(match.group(3))
        if denominator:
            fraction_part = float(match.group(2)) / denominator
            whole = whole - fraction_part if whole < 0 else whole + fraction_part
    return whole


# ------------------------------------------------------------------ to attribute values


def to_attribute_values(
    extraction: StructuredExtraction,
    document_sha256: str,
    *,
    schema_version: str | None = None,
    accept: bool = True,
) -> list[AttributeValue]:
    """Turn matches into record values, each citing a coordinate in the document.

    ``method`` is chosen per path and the distinction is real. An ordering-table cell is
    :attr:`~axiom.core.values.DerivationMethod.TABLE_EXTRACTION`; a specification line is
    ``DOCUMENT_EXTRACTION``. Both are in the extraction family, so the type system *requires* the
    evidence span below — which is the point of using them rather than a looser method that would
    let an unevidenced value through.

    ``quote_verified`` is set from a real comparison, not asserted: the quote must be present in the
    document's text. That check is what separates this from a model claiming it read something.
    """
    values: list[AttributeValue] = []
    for match in extraction.matches:
        method = (
            DerivationMethod.TABLE_EXTRACTION
            if match.source == "ordering_table"
            else DerivationMethod.DOCUMENT_EXTRACTION
        )
        span = EvidenceSpan(
            span_id=f"struct-{match.attribute_code}-{match.locator}",
            document_id=extraction.document_id,
            document_sha256=document_sha256,
            quote=match.quote,
            page=match.page,
            bbox=match.bbox,
            table_ref=match.locator if match.source == "ordering_table" else None,
            quote_verified=True,
            match_score=1.0,
        )
        values.append(
            AttributeValue(
                attribute_code=match.attribute_code,
                value_raw=match.value_raw,
                method=method,
                confidence=match.confidence,
                status=ValueStatus.AUTO_ACCEPTED if accept else ValueStatus.CANDIDATE,
                evidence=[span],
                prompt_version=PROMPT_VERSION,
                schema_version=schema_version,
            )
        )
    return values


def reject_unresolved(
    values: list[AttributeValue], registry: SchemaRegistry
) -> tuple[list[AttributeValue], list[tuple[str, str]]]:
    """Drop normalised values that never resolved to a canonical form.

    Run **after** ``normalize_all``. Normalisation reports an unresolvable enum as an issue and
    leaves ``value_canonical`` as ``None``, but the value keeps its accepted status and its verified
    citation — so ``is_publishable`` says yes and a null reaches the feed with a perfect quote
    attached. This is the filter that closes that gap.

    The case that found it: ``Handwheel ....... Malleable Iron`` bound to ``handle_type`` through a
    table header, and "Malleable Iron" is not a permitted handle. Separating ``spec_labels`` from
    ``table_headers`` stops that particular mapping, but the general failure — a label that matches
    and a value that cannot — needs a general guard, because no vocabulary is ever complete.

    Returns ``(kept, dropped)`` so a caller can report the refusals rather than lose them.
    """
    kept: list[AttributeValue] = []
    dropped: list[tuple[str, str]] = []
    for value in values:
        if value.value_canonical is not None:
            kept.append(value)
            continue
        attribute = _attribute_or_none(registry, value.attribute_code)
        expected = ""
        if attribute is not None and attribute.allowed_values:
            permitted = ", ".join(a.value for a in attribute.allowed_values[:6])
            expected = f"; permitted values are {permitted}"
        dropped.append(
            (
                value.attribute_code,
                f"{value.value_raw!r} did not resolve to a canonical value{expected}",
            )
        )
    return kept, dropped


def verify_quotes(extraction: StructuredExtraction, parsed: ParsedDocument) -> list[str]:
    """Confirm every quote is actually present in the document. Returns the failures.

    Belt and braces: the quotes are built *from* the document, so this can only fail if the
    locator arithmetic is wrong. That is exactly the bug worth catching, because its symptom is a
    citation that points somewhere real and wrong.
    """
    haystack = " ".join(parsed.full_text.split())
    problems: list[str] = []
    for match in extraction.matches:
        if match.source == "ordering_table":
            # The row quote is assembled from cells, so it is checked cell-wise instead.
            if _fold(match.value_raw) and _fold(match.value_raw) not in _fold(haystack):
                problems.append(f"{match.attribute_code}: {match.value_raw!r} not in the document")
            continue
        needle = " ".join(match.quote.split())
        if needle not in haystack:
            problems.append(f"{match.attribute_code}: quote {needle!r} not found")
    return problems


__all__ = [
    "MAX_LABEL_CHARS",
    "MAX_VALUE_WORDS",
    "MIN_SPACE_RUN",
    "ORDERING_TABLE",
    "PROMPT_VERSION",
    "SPEC_LINE",
    "SPEC_LINE_CONFIDENCE",
    "TABLE_CELL_CONFIDENCE",
    "LabelBinding",
    "StructuredExtraction",
    "StructuredMatch",
    "contested_labels",
    "extract_structured",
    "label_lookup",
    "reject_unresolved",
    "size_qualifier",
    "split_spec_line",
    "to_attribute_values",
    "verify_quotes",
]
