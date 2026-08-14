"""The delivery-format contract: which columns we emit, in what order, and what each one claims.

Loaded from ``schema/delivery/*.yaml`` rather than declared in Python, on the same principle as
the attribute dictionary: the column set belongs to the client, and their next revision should be
a YAML edit.

Two things this module exists to guarantee.

**The header matches exactly.** A delivery file whose columns do not line up with the client's
cannot be ingested at all, so the expanded header is treated as a hard contract and
``tests/test_delivery_format.py`` diffs it against the client's real CSV. Everything else in the
pipeline can degrade gracefully; this cannot.

**Every column declares its provenance class.** That is what lets a 252-column row coexist with
the evidence rule instead of quietly breaking it — see :class:`Provenance` and blueprint 17.9.
Without it, "we filled 79 columns from a 35-character description" is indistinguishable from
fabrication, both to a judge and to us.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

_SLOT_TOKEN = "{n}"


class Provenance(str, Enum):
    """How a cell came to hold what it holds.

    The delivery format asks for ~79 populated columns per row from an input of six columns and
    no attached document. Treating every cell as an extraction would be false; treating every
    cell as free is worse. These five classes are the distinction that keeps the row honest.
    """

    PASSTHROUGH = "passthrough"
    """Copied from the client's own input unchanged. Needs no evidence: the only claim being
    made is "you sent us this"."""

    DERIVED = "derived"
    """A deterministic function of passthrough data or of an already-accepted value. Inherits
    its input's provenance, exactly as a unit conversion does in ``ProductRecord.verifiability``."""

    EXTRACTED = "extracted"
    """Read off a manufacturer source. The full gate applies: evidence span required, validation
    and confidence policy enforced. These are the cells that can be wrong expensively."""

    GENERATED = "generated"
    """Composed from cells that are already established. May introduce wording; may not
    introduce facts."""

    EVIDENCE = "evidence"
    """The citation itself — the source URL the enrichment came from."""

    UNAVAILABLE = "unavailable"
    """Cannot be established from this input. Always emitted empty."""

    @property
    def requires_evidence_span(self) -> bool:
        """Whether a populated cell of this class must carry a citation to be legitimate."""
        return self is Provenance.EXTRACTED

    @property
    def may_be_populated(self) -> bool:
        return self is not Provenance.UNAVAILABLE

    @property
    def is_established(self) -> bool:
        """Whether a `generated` cell is allowed to draw on this class.

        Generation may reason from data we hold or have proven, never from data we also invented.
        Excluding ``GENERATED`` itself is what stops a chain of inventions compounding.
        """
        return self in {Provenance.PASSTHROUGH, Provenance.DERIVED, Provenance.EXTRACTED}


class Casing(str, Enum):
    UPPER = "upper"
    LOWER = "lower"

    def applies_to(self, text: str) -> bool:
        return text == (text.upper() if self is Casing.UPPER else text.lower())


@dataclass(frozen=True)
class DeliveryColumn:
    """One column of the delivery format."""

    name: str
    index: int
    group: str
    provenance: Provenance

    min_chars: int | None = None
    max_chars: int | None = None
    casing: Casing | None = None
    constraint_source: str | None = None
    """Where the constraint came from. ``guide`` means the client stated it, so a violation is a
    real defect. Observed-only lengths are deliberately left undeclared rather than guessed at,
    because scoring ourselves against a limit we invented proves nothing."""

    delimiter: str | None = None
    slot: int | None = None
    """1-based position within a repeating block, e.g. 12 for ``ATTRIBUTE_VALUE 12``."""

    role: str | None = None
    """Which part of a repeating group this is: ``label``, ``value``, ``uom``, ``feature``."""

    note: str | None = None

    @property
    def is_constrained(self) -> bool:
        return self.max_chars is not None or self.min_chars is not None or self.casing is not None

    def violations(self, value: str) -> list[str]:
        """Constraint violations for a candidate value. Empty means compliant.

        An empty value never violates anything: the format is a superset envelope and a blank
        cell is a legitimate, frequently correct answer. A minimum length that rejected "" would
        force us to invent copy to satisfy our own validator.
        """
        if not value:
            return []
        problems = []
        if self.max_chars is not None and len(value) > self.max_chars:
            problems.append(f"{len(value)} chars exceeds the {self.max_chars}-char limit")
        if self.min_chars is not None and len(value) < self.min_chars:
            problems.append(f"{len(value)} chars is under the {self.min_chars}-char minimum")
        if self.casing is not None and not self.casing.applies_to(value):
            problems.append(f"must be {self.casing.value}case")
        return problems


@dataclass(frozen=True)
class DeliverySection:
    """A contiguous run of columns sharing a purpose."""

    group: str
    provenance: Provenance
    columns: tuple[DeliveryColumn, ...]
    note: str | None = None


class DeliveryFormatError(Exception):
    """Raised when the declared contract is internally inconsistent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        bullets = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{len(problems)} delivery-format problem(s):\n{bullets}")


class DeliveryFormat:
    """The ordered column contract, with per-column provenance and constraints."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        sections: tuple[DeliverySection, ...],
        join_key: str,
        description: str | None = None,
        populated_in_ground_truth: int | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.description = description
        self.join_key = join_key
        self.populated_in_ground_truth = populated_in_ground_truth
        self.sections = sections
        self._columns = tuple(c for s in sections for c in s.columns)
        self._by_name = {c.name: c for c in self._columns}

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: Path | str) -> DeliveryFormat:
        path = Path(path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = payload.get("format", payload)

        problems: list[str] = []
        sections: list[DeliverySection] = []
        index = 0

        for raw_section in raw.get("sections", []):
            group = raw_section.get("group") or "<unnamed>"
            try:
                default = Provenance(raw_section["provenance"])
            except (KeyError, ValueError):
                problems.append(
                    f"section '{group}' declares provenance "
                    f"{raw_section.get('provenance')!r}, which is not one of "
                    f"{[p.value for p in Provenance]}"
                )
                continue

            columns: list[DeliveryColumn] = []
            if "repeat" in raw_section and "columns" in raw_section:
                problems.append(
                    f"section '{group}' declares both 'columns' and 'repeat'; a section is "
                    f"either a literal run or a repeating block, not both"
                )
                continue

            if "repeat" in raw_section:
                built, errs = _expand_repeat(raw_section["repeat"], group, default, index)
                problems.extend(errs)
            elif "columns" in raw_section:
                built, errs = _expand_literal(raw_section["columns"], group, default, index)
                problems.extend(errs)
            else:
                problems.append(f"section '{group}' declares neither 'columns' nor 'repeat'")
                continue

            columns = built
            index += len(columns)
            sections.append(
                DeliverySection(
                    group=group,
                    provenance=default,
                    columns=tuple(columns),
                    note=_clean(raw_section.get("note")),
                )
            )

        problems.extend(_check_unique(sections))
        if problems:
            raise DeliveryFormatError(problems)

        join_key = raw.get("join_key") or ""
        fmt = cls(
            name=raw.get("name") or path.stem,
            version=str(raw.get("version") or "v1"),
            sections=tuple(sections),
            join_key=join_key,
            description=_clean(raw.get("description")),
            populated_in_ground_truth=raw.get("populated_in_ground_truth"),
        )
        if join_key and join_key not in fmt._by_name:
            raise DeliveryFormatError(
                [f"join_key '{join_key}' is not one of the declared columns"]
            )
        return fmt

    # ------------------------------------------------------------------ accessors

    @property
    def header(self) -> tuple[str, ...]:
        """The exact ordered header. This is the contract."""
        return tuple(c.name for c in self._columns)

    @property
    def columns(self) -> tuple[DeliveryColumn, ...]:
        return self._columns

    def __len__(self) -> int:
        return len(self._columns)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def column(self, name: str) -> DeliveryColumn:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"'{name}' is not a delivery-format column") from exc

    def group(self, group: str) -> tuple[DeliveryColumn, ...]:
        return tuple(c for c in self._columns if c.group == group)

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(s.group for s in self.sections)

    def constrained_columns(self) -> tuple[DeliveryColumn, ...]:
        """Columns carrying a declared character or casing limit."""
        return tuple(c for c in self._columns if c.is_constrained)

    def provenance_of(self, name: str) -> Provenance:
        return self.column(name).provenance

    def slots(self, group: str, role: str) -> int:
        """How many slots a repeating group offers for a role, e.g. 50 attribute values."""
        return sum(1 for c in self.group(group) if c.role == role)

    def slot_column(self, group: str, role: str, slot: int) -> DeliveryColumn:
        """The column holding slot ``slot`` of ``role`` within a repeating group."""
        for c in self.group(group):
            if c.role == role and c.slot == slot:
                return c
        raise KeyError(f"no {role} slot {slot} in group '{group}'")

    def blank_row(self) -> dict[str, str]:
        """Every column mapped to the empty string.

        The starting point for building a row, and the reason a missing value can never shift
        neighbouring columns: the row is keyed by name and serialised in declared order.
        """
        return dict.fromkeys(self.header, "")

    def unavailable_columns(self) -> tuple[str, ...]:
        """Columns we will never populate, with the honesty that implies."""
        return tuple(c.name for c in self._columns if c.provenance is Provenance.UNAVAILABLE)


def _expand_literal(
    raw_columns: list, group: str, default: Provenance, start: int
) -> tuple[list[DeliveryColumn], list[str]]:
    columns: list[DeliveryColumn] = []
    problems: list[str] = []
    for offset, entry in enumerate(raw_columns):
        spec = {"name": entry} if isinstance(entry, str) else dict(entry or {})
        name = spec.get("name")
        if not name:
            problems.append(f"section '{group}' has a column with no name")
            continue
        try:
            columns.append(
                _build(spec, group=group, default=default, index=start + offset)
            )
        except ValueError as exc:
            problems.append(f"section '{group}' column '{name}': {exc}")
    return columns, problems


def _expand_repeat(
    raw_repeat: dict, group: str, default: Provenance, start: int
) -> tuple[list[DeliveryColumn], list[str]]:
    """Expand a repeating block.

    Slot-major rather than role-major: the client interleaves
    ``ATTRIBUTE_LABEL 1, ATTRIBUTE_VALUE 1, ATTRIBUTE_UOM 1, ATTRIBUTE_LABEL 2, …`` and emitting
    all fifty labels followed by all fifty values would produce a file that looks plausible and
    is entirely misaligned.
    """
    problems: list[str] = []
    count = raw_repeat.get("count")
    if not isinstance(count, int) or count < 1:
        return [], [f"section '{group}' repeat declares count={count!r}, which must be a "
                    f"positive integer"]

    specs = raw_repeat.get("columns") or []
    if not specs:
        return [], [f"section '{group}' repeat declares no columns"]

    for spec in specs:
        template = (spec or {}).get("template", "")
        if _SLOT_TOKEN not in template:
            problems.append(
                f"section '{group}' repeat template {template!r} does not contain "
                f"'{_SLOT_TOKEN}', so all {count} slots would collide on one name"
            )
    if problems:
        return [], problems

    columns: list[DeliveryColumn] = []
    index = start
    for slot in range(1, count + 1):
        for spec in specs:
            entry = dict(spec)
            template = entry.pop("template")
            entry["name"] = template.replace(_SLOT_TOKEN, str(slot))
            try:
                columns.append(
                    _build(entry, group=group, default=default, index=index, slot=slot)
                )
            except ValueError as exc:
                problems.append(f"section '{group}' column '{entry['name']}': {exc}")
            index += 1
    return columns, problems


def _build(
    spec: dict, *, group: str, default: Provenance, index: int, slot: int | None = None
) -> DeliveryColumn:
    provenance = default
    if "provenance" in spec:
        try:
            provenance = Provenance(spec["provenance"])
        except ValueError as exc:
            raise ValueError(f"unknown provenance {spec['provenance']!r}") from exc

    casing = None
    if spec.get("casing") is not None:
        try:
            casing = Casing(spec["casing"])
        except ValueError as exc:
            raise ValueError(f"unknown casing {spec['casing']!r}") from exc

    minimum, maximum = spec.get("min_chars"), spec.get("max_chars")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"min_chars {minimum} exceeds max_chars {maximum}")
    if provenance is Provenance.UNAVAILABLE and (minimum or maximum or casing):
        raise ValueError(
            "declares a value constraint but is provenance: unavailable, so it is always "
            "emitted empty and the constraint is dead configuration"
        )

    return DeliveryColumn(
        name=spec["name"],
        index=index,
        group=group,
        provenance=provenance,
        min_chars=minimum,
        max_chars=maximum,
        casing=casing,
        constraint_source=spec.get("constraint_source"),
        delimiter=spec.get("delimiter"),
        slot=slot,
        role=spec.get("role"),
        note=_clean(spec.get("note")),
    )


def _check_unique(sections: list[DeliverySection]) -> list[str]:
    seen: dict[str, str] = {}
    problems: list[str] = []
    for section in sections:
        for column in section.columns:
            if column.name in seen:
                problems.append(
                    f"column '{column.name}' is declared twice (in '{seen[column.name]}' and "
                    f"'{section.group}'); a duplicate header silently drops one of them on export"
                )
                continue
            seen[column.name] = section.group
    return problems


def _clean(text: str | None) -> str | None:
    """Collapse YAML folded-block whitespace into a single line."""
    if text is None:
        return None
    collapsed = " ".join(str(text).split())
    return collapsed or None


def default_format_path() -> Path:
    """Repository path to the Unilog delivery contract."""
    return (
        Path(__file__).resolve().parents[3]
        / "schema"
        / "delivery"
        / "unilog_delivery_v1.yaml"
    )


_CACHE: dict[Path, DeliveryFormat] = {}


def load_default() -> DeliveryFormat:
    """Load the Unilog delivery contract, cached.

    Cached because the scorer, the exporter and the batch driver all want it and parsing 252
    columns per row would be a silly cost. Keyed on the resolved path so a test loading a
    fixture format does not poison the default.
    """
    path = default_format_path()
    if path not in _CACHE:
        _CACHE[path] = DeliveryFormat.load(path)
    return _CACHE[path]


__all__ = [
    "Casing",
    "DeliveryColumn",
    "DeliveryFormat",
    "DeliveryFormatError",
    "DeliverySection",
    "Provenance",
    "default_format_path",
    "load_default",
]
