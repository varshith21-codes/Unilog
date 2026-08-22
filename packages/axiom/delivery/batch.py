"""Running a supplier row file through to delivery rows.

Extracted from ``scripts/export_delivery.py``, which held this loop inline for as long as the batch
driver was the only caller. It is not any more: the upload endpoint runs the same projection over
bytes arriving from a browser, and a second implementation of it would be the worst kind of
duplicate — one that scores differently from the one CI gates, for reasons nobody would find
quickly.

So the CLI and the API now share this module and differ only in where the bytes come from and how
the result is presented. The file-path handling, argument parsing and console reporting stay in the
script; everything that decides what a cell contains lives here.

**Entirely offline, and no model calls.** Classification is the deterministic retrieval step
(``CandidateIndex`` scoring plus the dominance test) and abstains rather than guessing when
retrieval is not decisive. Extraction reads the description string and any attached documents by
layout. That is what makes the whole path runnable in CI, and runnable on an upload without
credentials.

The ordering inside :func:`run_batch` is load-bearing and is preserved from the original:

1. the description pass, which cites the substring it read an abbreviation from;
2. the document pass, which supersedes it — both are evidenced, but a datasheet *states* a fact
   where a description only implies it;
3. golden seeding, last, so a supplied value supersedes a weaker reading rather than colliding
   with it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from axiom.classify import Classifier
from axiom.core.evidence import EvidenceSpan, SourceDocument
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, DerivationMethod, ValueStatus
from axiom.delivery.builder import DeliveryRow, DeliveryRowBuilder
from axiom.delivery.format import DeliveryFormat
from axiom.delivery.source import INPUT_COLUMNS, SupplierRow
from axiom.docintel import ParsedDocument, parse_artifact
from axiom.extract.description import AbbreviationTable, extract_from_description
from axiom.extract.description import to_attribute_values as description_values
from axiom.extract.structured import (
    extract_structured,
    reject_unresolved,
    to_attribute_values,
)
from axiom.ingest import detect_document_type, sha256_bytes
from axiom.normalize import normalize_all
from axiom.schema import SchemaRegistry

NO_PART_NUMBER = "no_part_number"
UNCLASSIFIED = "unclassified"


class InputColumnsError(ValueError):
    """The uploaded file is not a Unilog item master.

    Carries the two lists rather than only a message, because the caller that can act on this is a
    UI telling someone which header to fix, and re-parsing a sentence to recover them would be
    silly.
    """

    def __init__(self, missing: Sequence[str], found: Sequence[str]) -> None:
        self.missing = tuple(missing)
        self.found = tuple(found)
        super().__init__(
            f"input is missing expected columns: {', '.join(self.missing)}\n"
            f"found: {', '.join(self.found)}"
        )


@dataclass(frozen=True)
class BatchOptions:
    """How to process the rows. Mirrors the script's flags, one field per flag."""

    class_code: str | None = None
    """Force this class on every row, bypassing classification."""

    classified_only: bool = False
    """Skip rows that could not be classified rather than emitting an identity-only row."""

    read_descriptions: bool = True
    """Read attributes out of ``Part_Desc``. The script's ``--no-description-extraction``
    inverted, because the affirmative reads better as a default of True."""


@dataclass
class RowOutcome:
    """What happened to one input row.

    Returned per row so a caller can report progress without this module knowing whether the
    destination is a terminal, a log or a JSON response.
    """

    mpn: str | None
    method: str
    class_code: str | None = None
    row: DeliveryRow | None = None
    skipped: str | None = None
    from_description: int = 0
    from_documents: int = 0
    from_golden: int = 0

    @property
    def populated(self) -> int:
        return self.row.populated_count if self.row is not None else 0

    def summary(self) -> dict[str, object]:
        return {
            "mpn": self.mpn,
            "class_code": self.class_code,
            "method": self.method,
            "skipped": self.skipped,
            "populated": self.populated,
            "from_description": self.from_description,
            "from_documents": self.from_documents,
            "from_golden": self.from_golden,
        }


@dataclass
class BatchResult:
    """Every delivery row the batch produced, and the account of how it got there."""

    rows: list[DeliveryRow] = field(default_factory=list)
    outcomes: list[RowOutcome] = field(default_factory=list)
    methods: Counter[str] = field(default_factory=Counter)
    skipped: int = 0
    extracted_total: int = 0
    refused_total: int = 0
    from_documents_total: int = 0
    document_refused_total: int = 0
    seeded_total: int = 0

    def __len__(self) -> int:
        return len(self.rows)

    def summary(self) -> dict[str, object]:
        return {
            "rows": len(self.rows),
            "skipped": self.skipped,
            "classification": dict(self.methods),
            "from_description": {
                "extracted": self.extracted_total,
                "refused": self.refused_total,
            },
            "from_documents": {
                "extracted": self.from_documents_total,
                "refused": self.document_refused_total,
            },
            "from_golden": self.seeded_total,
        }


def validate_input_columns(headers: Iterable[str]) -> None:
    """Raise :class:`InputColumnsError` unless every expected input column is present.

    Checked before any row is processed. A file missing ``Mfg_Part_Num`` would otherwise produce a
    batch of unidentified rows and a confusing "everything was skipped" at the end, when the real
    answer is that the wrong file was uploaded.
    """
    present = set(headers)
    missing = [column for column in INPUT_COLUMNS if column not in present]
    if missing:
        raise InputColumnsError(missing, list(headers))


def select_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    mpns: Iterable[str] = (),
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Filter by part number, then truncate. Order matters: ``--mpn`` with ``--limit`` should mean
    "the first N of the ones I named", not "the named ones among the first N"."""
    selected = [dict(row) for row in rows]
    wanted = {m.strip() for m in mpns if m and m.strip()}
    if wanted:
        selected = [r for r in selected if (r.get("Mfg_Part_Num") or "").strip() in wanted]
    if limit:
        selected = selected[:limit]
    return selected


@dataclass(frozen=True)
class DocumentSource:
    """A manufacturer document to extract from, as bytes plus how to name it.

    ``uri`` and ``stem`` are overridable because the same document reaches this module two ways and
    the two should not claim the same origin. A file on disk gets a ``file://`` URI, which is
    resolvable by whoever reads the sidecar; an upload has no path worth recording, so it gets
    ``upload:`` and is honest about that.
    """

    data: bytes
    name: str
    uri: str | None = None
    stem: str | None = None

    def resolved_stem(self) -> str:
        if self.stem:
            return self.stem
        tail = self.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return tail.rsplit(".", 1)[0] if "." in tail else tail

    def resolved_uri(self) -> str:
        return self.uri or f"upload:{self.name}"


def parse_documents(sources: Iterable[DocumentSource]) -> list[ParsedDocument]:
    """Parse each manufacturer document once, content-hashed.

    Parsed up front rather than per row: a thousand-row batch against one datasheet would otherwise
    re-parse the PDF a thousand times.

    The document id embeds the hash (``ba100@1f3c9d2e``) because a citation that named the file
    without pinning its content would not survive the supplier issuing a revision. Type detection
    is on content, not on the filename, because both the extension and a declared Content-Type are
    wrong often enough to matter.
    """
    parsed: list[ParsedDocument] = []
    for source in sources:
        sha = sha256_bytes(source.data)
        document = SourceDocument(
            document_id=f"{source.resolved_stem()}@{sha[:8]}",
            uri=source.resolved_uri(),
            sha256=sha,
            doc_type=detect_document_type(source.data, source.name),
            fetched_at=datetime.now(UTC),
        )
        parsed.append(parse_artifact(source.data, document))
    return parsed


def run_batch(
    rows: Sequence[Mapping[str, str]],
    *,
    fmt: DeliveryFormat,
    registry: SchemaRegistry,
    document_id: str,
    document_sha256: str,
    options: BatchOptions | None = None,
    documents: Sequence[ParsedDocument] = (),
    golden: Mapping[str, Mapping] | None = None,
    builder: DeliveryRowBuilder | None = None,
    classifier: Classifier | None = None,
    on_row: Callable[[RowOutcome], None] | None = None,
) -> BatchResult:
    """Project supplier rows onto delivery rows.

    ``document_id`` and ``document_sha256`` describe the *item master itself*, which is the source
    document any description-derived value is cited against. It is content-hashed like any other
    arrival, for the same reason: a citation naming a file without pinning its bytes would not
    survive the supplier sending a corrected version.

    ``builder`` and ``classifier`` are injectable so a caller processing several files in one
    process is not reloading the recipe books and the candidate index per file.
    """
    options = options or BatchOptions()
    classifier = classifier or Classifier(registry)
    builder = builder or DeliveryRowBuilder(fmt, registry)
    golden = golden or {}
    # Loaded once for the batch, not per row.
    abbreviations = AbbreviationTable.load() if options.read_descriptions else None

    result = BatchResult()

    for raw in rows:
        source = SupplierRow.parse(dict(raw))
        if not source.identified:
            result.skipped += 1
            outcome = RowOutcome(mpn=None, method=NO_PART_NUMBER, skipped=NO_PART_NUMBER)
            result.outcomes.append(outcome)
            if on_row:
                on_row(outcome)
            continue

        class_code, method = classify_row(classifier, source, options.class_code)
        result.methods[method] += 1

        if class_code is None and options.classified_only:
            result.skipped += 1
            outcome = RowOutcome(mpn=source.mpn, method=method, skipped=UNCLASSIFIED)
            result.outcomes.append(outcome)
            if on_row:
                on_row(outcome)
            continue

        record = ProductRecord(
            tenant_id="unilog",
            sku=source.mpn or "",
            mpn=source.mpn,
            class_code=class_code,
            source_document_ids=[document_id],
        )
        if class_code:
            record.classifications.extend(_classifications(classifier, source))

        # 1. What the description itself evidences. Deterministic, class-scoped, and every value
        #    cites the substring it came from — which is why these are allowed through the publish
        #    gate while a legacy item-master value is not.
        from_description = 0
        if abbreviations is not None and class_code and source.description:
            extraction = extract_from_description(
                source.description,
                registry=registry,
                class_code=class_code,
                abbreviations=abbreviations,
            )
            values = description_values(
                extraction,
                document_id=document_id,
                document_sha256=document_sha256,
                schema_version=registry.product_class(class_code).schema_version,
            )
            for value in values:
                record.add_value(value)
            from_description = len(values)
            result.extracted_total += from_description
            result.refused_total += len(extraction.refused)

        # 2. Real extraction from a manufacturer document, deterministically.
        from_documents = 0
        if class_code and documents:
            from_documents, document_refused = extract_from_documents(
                record, documents, registry, class_code, source.mpn or ""
            )
            result.from_documents_total += from_documents
            result.document_refused_total += document_refused

        # 3. The supplied arm, seeded after everything real.
        from_golden = 0
        entry = golden.get(source.mpn or "")
        if entry:
            if entry.get("class_code") and not record.class_code:
                record.class_code = entry["class_code"]
            from_golden = seed_from_golden(record, entry, document_sha256, registry)
            result.seeded_total += from_golden

        row = builder.build(
            record,
            source=source,
            reference_urls=list(entry.get("reference_urls", [])) if entry else None,
            # Brand and manufacturer cannot be resolved from a six-column input, so the arm
            # supplies them. Retrieval will supply them the same way.
            brand=entry.get("brand") if entry else None,
            manufacturer=entry.get("manufacturer") if entry else None,
        )
        result.rows.append(row)

        outcome = RowOutcome(
            mpn=source.mpn,
            method=method,
            class_code=class_code,
            row=row,
            from_description=from_description,
            from_documents=from_documents,
            from_golden=from_golden,
        )
        result.outcomes.append(outcome)
        if on_row:
            on_row(outcome)

    return result


def classify_row(
    classifier: Classifier, source: SupplierRow, forced: str | None = None
) -> tuple[str | None, str]:
    """The class for one row, and how it was arrived at."""
    if forced:
        return forced, "forced"
    if not source.description:
        return None, "no_description"
    result = classifier.classify(source.description, sku=source.mpn)
    return result.class_code, result.method


def _classifications(classifier: Classifier, source: SupplierRow):
    """Re-run classification to capture the Classification objects, not just the code.

    Called only for rows that classified, so the cost is one extra deterministic retrieval pass
    over a 35-character string. Restructuring ``classify_row`` to return both would be tidier and
    is worth doing if this ever runs over a million rows; at a thousand it is not measurable.
    """
    if not source.description:
        return []
    return classifier.classify(source.description, sku=source.mpn).classifications


def extract_from_documents(
    record: ProductRecord,
    documents: Sequence[ParsedDocument],
    registry: SchemaRegistry,
    class_code: str,
    target_sku: str,
) -> tuple[int, int]:
    """Read every attached document deterministically. Returns (values added, values refused).

    Values are normalised and then filtered through ``reject_unresolved``, because an enum value
    that normalisation could not snap keeps its accepted status and its verified citation — so it
    would publish as a null with a perfect quote attached.
    """
    added = refused = 0
    for parsed in documents:
        result = extract_structured(
            parsed, registry, class_code=class_code, target_sku=target_sku or None
        )
        refused += len(result.refused)
        if not result.matches:
            continue

        values = to_attribute_values(
            result,
            parsed.document.sha256,
            schema_version=registry.product_class(class_code).schema_version,
        )
        normalized, _issues = normalize_all(values, registry, class_code=class_code)
        kept, dropped = reject_unresolved(normalized, registry)
        refused += len(dropped)

        for value in kept:
            record.add_value(value)
            added += 1
        if parsed.document.document_id not in record.source_document_ids:
            record.source_document_ids.append(parsed.document.document_id)
    return added, refused


def seed_from_golden(
    record: ProductRecord,
    entry: Mapping,
    document_sha256: str,
    registry: SchemaRegistry,
) -> int:
    """Attach golden attribute values to a record, citing the golden set as their source.

    The citation names the golden file rather than pretending to a datasheet page. A span that
    claimed a manufacturer document we never opened would be the exact dishonesty this system is
    built to prevent, and it would also make the arm impossible to tell apart from a real run.

    Values are written in SOURCE FORM ("120 V", "50-1/4 in") and pushed through the same
    ``normalize_all`` the extractor's output goes through — the convention
    ``data/golden/pvf_valves.yaml`` already establishes. That matters for a concrete reason: the
    grid keeps magnitude and unit in separate columns, and only normalisation turns "120 V" into a
    Quantity the exporter can split. Seeding pre-normalised values would leave the unit welded to
    the magnitude and score every quantity cell wrong.
    """
    attributes = entry.get("attributes") or {}
    if not attributes:
        return 0

    values = [
        AttributeValue(
            attribute_code=code,
            value_raw=str(value),
            method=DerivationMethod.SUPPLIER_FEED,
            confidence=1.0,
            status=ValueStatus.AUTO_ACCEPTED,
            evidence=[
                EvidenceSpan(
                    span_id=f"golden-{entry['sku']}-{code}",
                    document_id="golden:unilog_dishwashers_v1",
                    document_sha256=document_sha256,
                    quote=str(value),
                    quote_verified=True,
                    match_score=1.0,
                )
            ],
            prompt_version="golden@v1",
        )
        for code, value in attributes.items()
    ]

    normalized, _ = normalize_all(values, registry, class_code=entry.get("class_code"))
    for value in normalized:
        record.add_value(value)
    return len(normalized)


__all__ = [
    "NO_PART_NUMBER",
    "UNCLASSIFIED",
    "BatchOptions",
    "BatchResult",
    "DocumentSource",
    "InputColumnsError",
    "RowOutcome",
    "classify_row",
    "extract_from_documents",
    "parse_documents",
    "run_batch",
    "seed_from_golden",
    "select_rows",
    "validate_input_columns",
]
