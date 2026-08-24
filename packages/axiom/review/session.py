"""Review sessions: the data a human needs in order to adjudicate a value in seconds.

The design constraint that drives everything here is that **the evidence must be on screen
next to the proposed value**. A reviewer who has to open a PDF and hunt for a number is back to
doing the original job by hand, and most of the claimed speed gain evaporates. So a session
carries the source text alongside the values, with each citation resolved to a page and a line,
ready to highlight.

The second constraint is that a decision is not just an edit. Every accept, reject and
correction updates the per-attribute and per-supplier priors, which feed confidence estimation,
which moves the auto-accept threshold. That is the flywheel: reviewing makes the next batch
need less review.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axiom.confidence import AcceptanceDecision, Priors, RiskPolicy
from axiom.core.product import ClassificationScheme, ProductRecord
from axiom.core.validation import Verdict
from axiom.core.values import Quantity, ValueRange, ValueStatus
from axiom.docintel import ParsedDocument, locate_quote
from axiom.schema import SchemaRegistry


def _render_canonical(value: Any) -> Any:
    if isinstance(value, Quantity):
        return {"magnitude": value.magnitude, "unit": value.unit}
    if isinstance(value, ValueRange):
        return {"minimum": value.minimum, "maximum": value.maximum, "unit": value.unit}
    return value


@dataclass
class EvidenceView:
    """A citation resolved to something the UI can highlight."""

    document_id: str
    quote: str
    verified: bool
    match_score: float | None = None
    page: int | None = None
    line_index: int | None = None
    table_ref: str | None = None
    method: str | None = None

    @property
    def locator(self) -> str:
        parts = [f"p.{self.page}"] if self.page else []
        if self.table_ref:
            parts.append(self.table_ref)
        return " ".join(parts) or "unlocated"


@dataclass
class ManufacturerSpecificationView:
    """A source-native fact shown beside, but not mixed into, the typed review queue."""

    specification_id: str
    label_raw: str
    value_raw: str
    confidence: float
    method: str
    mapped_attribute_code: str | None = None
    citable_as_manufacturer: bool = False
    evidence: list[EvidenceView] = field(default_factory=list)


@dataclass
class ValidationView:
    layer: str
    rule_id: str
    verdict: str
    reason: str
    counterexample: str | None = None
    suggested_fix: str | None = None
    blocking: bool = False


@dataclass
class ReviewItem:
    """One value awaiting a decision, with everything needed to make it."""

    attribute_code: str
    attribute_name: str
    datatype: str
    value_raw: str | None
    value_canonical: Any
    value_display: str | None
    score: float
    status: str
    reason_code: str
    detail: str
    method: str
    model_tier: str | None
    is_required: bool
    accepted: bool
    evidence: list[EvidenceView] = field(default_factory=list)
    validations: list[ValidationView] = field(default_factory=list)
    allowed_values: list[str] = field(default_factory=list)
    unit: str | None = None

    @property
    def needs_attention(self) -> bool:
        return not self.accepted


@dataclass
class SourcePage:
    number: int
    lines: list[str]


@dataclass
class ReviewSession:
    """One SKU's worth of review work, self-contained and serialisable."""

    sku: str
    tenant_id: str
    brand: str | None
    class_code: str | None
    class_name: str | None
    category_path: list[str]
    document_id: str
    document_sha256: str
    pages: list[SourcePage]
    items: list[ReviewItem]
    gaps: list[dict]
    policy: dict
    quality: dict
    created_at: str
    decisions: list[dict] = field(default_factory=list)
    manufacturer_specifications: list[ManufacturerSpecificationView] = field(default_factory=list)

    @property
    def queue(self) -> list[ReviewItem]:
        """Items needing attention, ordered by reason code then by ascending confidence.

        Grouping by reason code first is deliberate. Working one failure mode at a time is
        much faster than context-switching per SKU, because the reviewer keeps the same
        question in mind across a run of items.
        """
        pending = [item for item in self.items if item.needs_attention]
        return sorted(pending, key=lambda i: (i.reason_code, i.score))

    @property
    def accepted(self) -> list[ReviewItem]:
        return [item for item in self.items if item.accepted]

    def item(self, attribute_code: str) -> ReviewItem | None:
        for candidate in self.items:
            if candidate.attribute_code == attribute_code:
                return candidate
        return None

    def to_dict(self) -> dict:
        return {
            **{
                k: v
                for k, v in asdict(self).items()
                if k not in {"items", "pages"}
            },
            "pages": [asdict(p) for p in self.pages],
            "items": [asdict(i) for i in self.items],
            "queue": [i.attribute_code for i in self.queue],
        }

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | str) -> ReviewSession:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.pop("queue", None)
        pages = [SourcePage(**p) for p in payload.pop("pages", [])]
        items = []
        for raw in payload.pop("items", []):
            evidence = [EvidenceView(**e) for e in raw.pop("evidence", [])]
            validations = [ValidationView(**v) for v in raw.pop("validations", [])]
            items.append(ReviewItem(**raw, evidence=evidence, validations=validations))
        manufacturer_specifications = []
        for raw in payload.pop("manufacturer_specifications", []):
            evidence = [EvidenceView(**e) for e in raw.pop("evidence", [])]
            manufacturer_specifications.append(
                ManufacturerSpecificationView(**raw, evidence=evidence)
            )
        return cls(
            **payload,
            pages=pages,
            items=items,
            manufacturer_specifications=manufacturer_specifications,
        )


def build_session(
    record: ProductRecord,
    parsed: ParsedDocument,
    registry: SchemaRegistry,
    decisions: list[AcceptanceDecision],
    scores: dict[str, float],
    policy: RiskPolicy,
    *,
    quality: dict | None = None,
) -> ReviewSession:
    """Assemble a review session from the artifacts of one pipeline run."""
    by_code = {d.attribute_code: d for d in decisions}
    internal = record.classification(ClassificationScheme.INTERNAL)

    items: list[ReviewItem] = []
    for value in record.current_values():
        definition = _definition(registry, value.attribute_code)
        decision = by_code.get(value.attribute_code)
        items.append(
            ReviewItem(
                attribute_code=value.attribute_code,
                attribute_name=definition.name if definition else value.attribute_code,
                datatype=definition.datatype.value if definition else "string",
                value_raw=value.value_raw,
                value_canonical=_render_canonical(value.value_canonical),
                value_display=value.value_display,
                score=round(scores.get(value.attribute_code, 0.0), 4),
                status=value.status.value,
                reason_code=decision.reason_code if decision else "unreviewed",
                detail=decision.detail if decision else "",
                method=value.method.value,
                model_tier=value.model_tier,
                is_required=_is_required(registry, record.class_code, value.attribute_code),
                accepted=bool(decision and decision.accepted),
                evidence=[_evidence_view(span, parsed) for span in value.evidence],
                validations=[_validation_view(v) for v in value.validations],
                allowed_values=(
                    [a.value for a in definition.allowed_values] if definition else []
                ),
                unit=definition.canonical_unit if definition else None,
            )
        )

    return ReviewSession(
        sku=record.sku,
        tenant_id=record.tenant_id,
        brand=record.brand,
        class_code=record.class_code,
        class_name=(
            registry.product_class(record.class_code).name if record.class_code else None
        ),
        category_path=list(internal.path) if internal else [],
        document_id=parsed.document.document_id,
        document_sha256=parsed.document.sha256,
        pages=[
            SourcePage(number=page.number, lines=[line.text for line in page.lines])
            for page in parsed.pages
        ],
        items=items,
        gaps=[g.to_certificate_entry() for g in record.gaps],
        policy=policy.summary(),
        quality=quality or {},
        created_at=datetime.now(UTC).isoformat(),
        manufacturer_specifications=[
            ManufacturerSpecificationView(
                specification_id=specification.specification_id,
                label_raw=specification.label_raw,
                value_raw=specification.value_raw,
                confidence=specification.confidence,
                method=specification.method.value,
                mapped_attribute_code=specification.mapped_attribute_code,
                citable_as_manufacturer=specification.citable_as_manufacturer,
                evidence=[_evidence_view(span, parsed) for span in specification.evidence],
            )
            for specification in record.manufacturer_specifications
        ],
    )


def _definition(registry: SchemaRegistry, code: str):
    try:
        return registry.attribute(code)
    except KeyError:
        return None


def _is_required(registry: SchemaRegistry, class_code: str | None, code: str) -> bool:
    if not class_code:
        return False
    try:
        return code in registry.required_codes(class_code)
    except KeyError:
        return False


def _evidence_view(span, parsed: ParsedDocument) -> EvidenceView:
    """Resolve a citation to a page and line so the UI can highlight it.

    The line index is recovered by re-locating the quote rather than stored on the span,
    because a span holds geometry (a bounding box) and the UI needs a text position. Doing it
    here keeps the core model free of presentation concerns.
    """
    location = locate_quote(span.quote, parsed, page_hint=span.page)
    return EvidenceView(
        document_id=span.document_id,
        quote=span.quote,
        verified=span.quote_verified,
        match_score=span.match_score,
        page=location.page if location else span.page,
        line_index=location.line_index if location else None,
        table_ref=span.table_ref or (location.table_ref if location else None),
        method=location.method if location else None,
    )


def _validation_view(result) -> ValidationView:
    return ValidationView(
        layer=result.layer.value,
        rule_id=result.rule_id,
        verdict=result.verdict.value,
        reason=result.reason,
        counterexample=result.counterexample,
        suggested_fix=result.suggested_fix,
        blocking=result.is_blocking,
    )


# --------------------------------------------------------------------------- decisions

ACCEPT = "accept"
REJECT = "reject"
CORRECT = "correct"

_STATUS_FOR = {
    ACCEPT: ValueStatus.HUMAN_APPROVED,
    REJECT: ValueStatus.REJECTED,
    CORRECT: ValueStatus.HUMAN_APPROVED,
}


@dataclass
class ReviewOutcome:
    """The result of one human decision, including its effect on future automation."""

    sku: str
    attribute_code: str
    action: str
    reviewer: str
    before: Any
    after: Any
    status: str
    recorded_at: str
    prior_before: float
    prior_after: float
    sibling_impact: int = 0
    """How many other values share this attribute-and-supplier prior, and are therefore
    affected by what this decision taught the system."""

    def to_dict(self) -> dict:
        return asdict(self)


def record_decision(
    session: ReviewSession,
    attribute_code: str,
    action: str,
    *,
    reviewer: str,
    priors: Priors,
    supplier_id: str | None = None,
    corrected_value: str | None = None,
) -> ReviewOutcome:
    """Apply a human decision and fold it into the priors.

    An accept is evidence the extractor was right; a reject or a correction is evidence it was
    wrong. Both update the per-attribute prior, which is why coverage improves as review
    happens rather than staying fixed.
    """
    if action not in _STATUS_FOR:
        raise ValueError(f"unknown review action '{action}'")

    item = session.item(attribute_code)
    if item is None:
        raise KeyError(f"session for {session.sku} has no attribute '{attribute_code}'")

    before = item.value_display or item.value_raw
    prior_before = priors.attribute_prior(attribute_code)

    was_correct = action == ACCEPT
    priors.observe(attribute_code, "attribute", correct=was_correct)
    if supplier_id:
        priors.observe(f"{supplier_id}:{attribute_code}", "source", correct=was_correct)

    if action == CORRECT:
        if not corrected_value:
            raise ValueError("a correction requires the corrected value")
        item.value_raw = corrected_value
        item.value_display = corrected_value
        item.value_canonical = corrected_value
        item.method = "human_correction"

    item.status = _STATUS_FOR[action].value
    item.accepted = action in {ACCEPT, CORRECT}
    item.reason_code = f"human_{action}"
    item.detail = f"reviewed by {reviewer}"

    outcome = ReviewOutcome(
        sku=session.sku,
        attribute_code=attribute_code,
        action=action,
        reviewer=reviewer,
        before=before,
        after=item.value_display,
        status=item.status,
        recorded_at=datetime.now(UTC).isoformat(),
        prior_before=round(prior_before, 4),
        prior_after=round(priors.attribute_prior(attribute_code), 4),
        sibling_impact=priors.sample_counts.get(attribute_code, 0),
    )
    session.decisions.append(outcome.to_dict())
    return outcome


def queue_summary(session: ReviewSession) -> dict[str, object]:
    """Counts by reason code, for the queue header."""
    grouped: dict[str, int] = {}
    for item in session.queue:
        grouped[item.reason_code] = grouped.get(item.reason_code, 0) + 1
    blocking = sum(
        1 for item in session.items if any(v.blocking for v in item.validations)
    )
    return {
        "pending": len(session.queue),
        "accepted": len(session.accepted),
        "total": len(session.items),
        "by_reason": grouped,
        "blocking_failures": blocking,
        "warnings": sum(
            1
            for item in session.items
            for v in item.validations
            if v.verdict == Verdict.WARN.value
        ),
    }
