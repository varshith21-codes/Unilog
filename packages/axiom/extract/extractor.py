"""The extraction orchestrator: parsed document plus schema, out come cited values.

This is where the architecture's central rule is actually enforced. A model claim becomes an
:class:`AttributeValue` only if its quote can be located in the source. Everything else
becomes a :class:`Gap` — with the failed claim recorded, so a reviewer can see what was
asserted and that it could not be substantiated.

Note what this deliberately does *not* do: it does not normalise values and it does not
validate them. ``value_raw`` is stored exactly as the source stated it, and canonicalisation
happens in the normalize package, validation in the validate package. Keeping extraction
free of interpretation is what makes each stage independently testable and what keeps the
raw source text available for audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.core.gaps import Gap, GapReason, RecommendedAction
from axiom.core.values import AttributeValue, DerivationMethod
from axiom.docintel import ParsedDocument, build_evidence_span
from axiom.extract.client import (
    ModelCascade,
    ModelClient,
    ModelError,
    ModelResponse,
    UsageLedger,
    invoke_with_cascade,
)
from axiom.extract.contract import (
    ContractError,
    ContractItem,
    classify_abstention,
    parse_contract,
)
from axiom.schema import Requirement, SchemaRegistry, build_extraction_prompt

_ABSTENTION_REASONS = {
    "deferred": (GapReason.REFERRED_ELSEWHERE, RecommendedAction.REQUEST_FROM_SUPPLIER),
    "applicability": (GapReason.NOT_PRESENT_IN_ANY_SOURCE, RecommendedAction.HUMAN_RESEARCH),
    "absent": (GapReason.NOT_PRESENT_IN_ANY_SOURCE, RecommendedAction.REQUEST_FROM_SUPPLIER),
}


@dataclass
class ExtractionResult:
    """Everything one extraction pass produced."""

    values: list[AttributeValue] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    rejected: list[ContractItem] = field(default_factory=list)
    """Claims discarded because their quote could not be located. Kept because a silent
    drop is untraceable, and a pattern of rejections is a signal about the source or the
    prompt rather than about one SKU."""

    usage: UsageLedger = field(default_factory=UsageLedger)
    response: ModelResponse | None = None
    prompt_version: str = ""
    schema_version: str = ""
    requested_codes: tuple[str, ...] = ()

    @property
    def verified_count(self) -> int:
        return sum(1 for v in self.values if v.has_verified_evidence)

    @property
    def citation_coverage(self) -> float:
        """Share of emitted values carrying a verified evidence span.

        By construction this should be 1.0 — anything less means a bug, because unverified
        claims are supposed to become gaps rather than values.
        """
        if not self.values:
            return 0.0
        return self.verified_count / len(self.values)

    def summary(self) -> dict[str, object]:
        return {
            "requested": len(self.requested_codes),
            "values": len(self.values),
            "gaps": len(self.gaps),
            "rejected_unverifiable": len(self.rejected),
            "citation_coverage": round(self.citation_coverage, 4),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "escalations": self.usage.escalations,
            "latency_ms": self.usage.latency_ms,
        }


class Extractor:
    """Runs schema-driven extraction against a parsed document."""

    def __init__(
        self,
        registry: SchemaRegistry,
        client: ModelClient,
        cascade: ModelCascade,
        *,
        start_tier: str = "volume",
        max_tokens: int = 4000,
        quote_threshold: float = 0.90,
    ) -> None:
        self._registry = registry
        self._client = client
        self._cascade = cascade
        self._start_tier = start_tier
        self._max_tokens = max_tokens
        self._quote_threshold = quote_threshold

    def extract(
        self,
        parsed: ParsedDocument,
        *,
        class_code: str,
        target_sku: str,
        only_codes: tuple[str, ...] | None = None,
        include_recommended: bool = True,
        include_optional: bool = False,
        max_pages: int | None = None,
    ) -> ExtractionResult:
        prompt = build_extraction_prompt(
            self._registry,
            class_code,
            source_content=parsed.to_prompt_content(max_pages=max_pages),
            target_sku=target_sku,
            source_name=parsed.document.document_id,
            include_recommended=include_recommended,
            include_optional=include_optional,
            only_codes=only_codes,
        )

        result = ExtractionResult(
            prompt_version=prompt.prompt_version,
            schema_version=prompt.schema_version,
            requested_codes=prompt.attribute_codes,
        )

        def validate(text: str) -> list[ContractItem]:
            return parse_contract(text, expected_codes=prompt.attribute_codes)

        try:
            response, items = invoke_with_cascade(
                self._client,
                self._cascade,
                system=prompt.system,
                user=prompt.user_message,
                start_tier=self._start_tier,
                validate=validate,
                max_tokens=self._max_tokens,
                ledger=result.usage,
            )
        except ModelError as exc:
            # A total model failure is not a catalogue of empty attributes. Every requested
            # attribute becomes an explicit gap saying the source was never successfully
            # read, which is a materially different claim from "the value is not stated".
            for code in prompt.attribute_codes:
                result.gaps.append(
                    Gap(
                        attribute_code=code,
                        reason=GapReason.NO_SOURCE_AVAILABLE,
                        sources_searched=[parsed.document.document_id],
                        detail=f"extraction failed before any value was read: {exc}",
                        recommended_action=RecommendedAction.RETRY_WITH_BETTER_SOURCE,
                        is_required=self._is_required(class_code, code),
                    )
                )
            return result

        result.response = response
        self._materialise(result, items, parsed, class_code, prompt.attribute_codes)
        return result

    # ------------------------------------------------------------------ internals

    def _materialise(
        self,
        result: ExtractionResult,
        items: list[ContractItem],
        parsed: ParsedDocument,
        class_code: str,
        requested: tuple[str, ...],
    ) -> None:
        response = result.response
        by_code = {item.attribute_code: item for item in items}

        for index, code in enumerate(requested):
            item = by_code.get(code)

            if item is None:
                result.gaps.append(
                    self._gap(
                        code,
                        class_code,
                        parsed,
                        GapReason.NOT_PRESENT_IN_ANY_SOURCE,
                        detail="attribute was requested but absent from the model response",
                        action=RecommendedAction.HUMAN_REVIEW,
                    )
                )
                continue

            if not item.found:
                bucket = classify_abstention(item.reason)
                reason, action = _ABSTENTION_REASONS[bucket]
                result.gaps.append(
                    self._gap(
                        code, class_code, parsed, reason, detail=item.reason, action=action
                    )
                )
                continue

            if not item.is_well_formed:
                # Claimed a value without a quote. Structurally unpublishable, so it never
                # becomes an AttributeValue.
                result.rejected.append(item)
                result.gaps.append(
                    self._gap(
                        code,
                        class_code,
                        parsed,
                        GapReason.EXTRACTED_BUT_UNVERIFIABLE,
                        detail=(
                            f"model reported {item.value_raw!r} with no supporting quote; "
                            f"discarded"
                        ),
                        action=RecommendedAction.HUMAN_RESEARCH,
                    )
                )
                continue

            span = build_evidence_span(
                item.evidence_quote,
                parsed,
                span_id=f"sp_{parsed.document.sha256[:8]}_{index}",
                page_hint=item.evidence_page,
                threshold=self._quote_threshold,
            )

            if not span.quote_verified:
                result.rejected.append(item)
                result.gaps.append(
                    self._gap(
                        code,
                        class_code,
                        parsed,
                        GapReason.EXTRACTED_BUT_UNVERIFIABLE,
                        detail=(
                            f"quote {item.evidence_quote!r} could not be located in the "
                            f"source; value {item.value_raw!r} discarded"
                        ),
                        action=RecommendedAction.HUMAN_RESEARCH,
                    )
                )
                continue

            result.values.append(
                AttributeValue(
                    attribute_code=code,
                    value_raw=item.value_raw,
                    method=(
                        DerivationMethod.TABLE_EXTRACTION
                        if span.table_ref
                        else DerivationMethod.DOCUMENT_EXTRACTION
                    ),
                    confidence=item.certainty.provisional_confidence,
                    evidence=[span],
                    model_id=response.model_id if response else None,
                    model_tier=response.tier if response else None,
                    prompt_version=result.prompt_version,
                    schema_version=result.schema_version,
                )
            )

    def _gap(
        self,
        code: str,
        class_code: str,
        parsed: ParsedDocument,
        reason: GapReason,
        *,
        detail: str | None,
        action: RecommendedAction,
    ) -> Gap:
        return Gap(
            attribute_code=code,
            reason=reason,
            sources_searched=[parsed.document.document_id],
            detail=detail,
            recommended_action=action,
            is_required=self._is_required(class_code, code),
        )

    def _is_required(self, class_code: str, code: str) -> bool:
        try:
            binding = self._registry.product_class(class_code).binding(code)
        except KeyError:
            return False
        return binding is not None and binding.requirement is Requirement.REQUIRED


__all__ = ["ContractError", "ExtractionResult", "Extractor"]
