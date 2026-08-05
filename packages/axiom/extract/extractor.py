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
from axiom.docintel import ParsedDocument, SkuPresence, build_evidence_span, find_sku
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
from axiom.extract.entailment import Support, check_entailment
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
    """Claims discarded because their quote could not be located, or because the quote that was
    located does not support the value. Kept because a silent drop is untraceable, and a pattern
    of rejections is a signal about the source or the prompt rather than about one SKU."""

    corrections: list[str] = field(default_factory=list)
    """Values rewritten to agree with their own citation — an under-read enum, or a list with an
    unmentioned member pruned. Recorded rather than applied quietly: a correction means the
    model and its evidence disagreed, which is worth seeing even when the resolution is right."""

    usage: UsageLedger = field(default_factory=UsageLedger)
    response: ModelResponse | None = None
    prompt_version: str = ""
    schema_version: str = ""
    requested_codes: tuple[str, ...] = ()

    sku_presence: SkuPresence | None = None
    """Where the target part number was found in the document, or that it was not.

    Carried through because ``found=False`` is the difference between "this datasheet does not
    state the pressure rating" and "this datasheet is about a different product". Both produce
    gaps; only one of them means someone attached the wrong file.
    """

    @property
    def targeting_failed(self) -> bool:
        """True when extraction was refused because the document does not mention the SKU."""
        return self.sku_presence is not None and self.sku_presence.is_absent

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
            "corrected_to_match_evidence": len(self.corrections),
            "citation_coverage": round(self.citation_coverage, 4),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "escalations": self.usage.escalations,
            "latency_ms": self.usage.latency_ms,
            "sku_found_in_document": (
                self.sku_presence.found if self.sku_presence else None
            ),
            "targeting_failed": self.targeting_failed,
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
        enforce_evidence: bool = True,
        require_sku_in_document: bool = True,
    ) -> None:
        self._registry = registry
        self._client = client
        self._cascade = cascade
        self._start_tier = start_tier
        self._max_tokens = max_tokens
        self._quote_threshold = quote_threshold
        self._enforce_evidence = enforce_evidence
        self._require_sku_in_document = require_sku_in_document
        """Set False only for genuine series-level documents, where orderable part numbers are
        built from a grammar and never printed. That is a real case, and it is also the excuse
        a mismatched-document bug would hide behind, so it is opt-in."""

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
        sku_variants: set[str] | None = None,
    ) -> ExtractionResult:
        # Targeting gate, before any model call.
        #
        # Handed the wrong datasheet, the model will answer confidently from whatever product
        # the document *does* describe, and every one of those values carries a quote that
        # verifies — because the text really is there. The evidence contract cannot catch this:
        # the citation is genuine, only the subject is wrong. Measured at 39 fabrications out of
        # 64 requested attributes across three mismatched pairings (scripts/run_adversarial.py).
        #
        # So the part number is looked for first. If it is nowhere in the document, the document
        # is not about this product and there is nothing here to extract. Deterministic, free,
        # and it fails closed.
        presence = find_sku(parsed, target_sku, variants=sku_variants)
        if self._require_sku_in_document and presence.is_absent:
            return self._sku_absent_result(parsed, class_code, target_sku, presence, only_codes,
                                           include_recommended, include_optional, max_pages)

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
        result.sku_presence = presence
        self._materialise(result, items, parsed, class_code, prompt.attribute_codes)
        return result

    # ------------------------------------------------------------------ internals

    def _sku_absent_result(
        self,
        parsed: ParsedDocument,
        class_code: str,
        target_sku: str,
        presence,
        only_codes,
        include_recommended: bool,
        include_optional: bool,
        max_pages: int | None,
    ) -> ExtractionResult:
        """Every requested attribute becomes a gap, with no model call made.

        The gap reason is deliberately ``NO_SOURCE_AVAILABLE`` rather than
        ``NOT_PRESENT_IN_ANY_SOURCE``: the distinction is between "we read a document about this
        product and it does not state this value" and "we never had a document about this
        product at all". Those need different remedies — the first is a question for the
        supplier, the second means someone attached the wrong file.
        """
        prompt = build_extraction_prompt(
            self._registry,
            class_code,
            source_content="",
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
            sku_presence=presence,
        )
        for code in prompt.attribute_codes:
            result.gaps.append(
                Gap(
                    attribute_code=code,
                    reason=GapReason.NO_SOURCE_AVAILABLE,
                    sources_searched=[parsed.document.document_id],
                    detail=(
                        f"part number {target_sku!r} does not appear anywhere in "
                        f"{parsed.document.document_id!r}, so this document does not describe "
                        f"it; no value was requested from the model"
                    ),
                    recommended_action=RecommendedAction.RETRY_WITH_BETTER_SOURCE,
                    is_required=self._is_required(class_code, code),
                )
            )
        return result

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
                if self._enforce_evidence:
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

                # Ablation only: keep the bare claim so a baseline can be measured.
                result.values.append(
                    self._unevidenced_value(item, code, result, response)
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
                if self._enforce_evidence:
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

                # Ablation only: the span travels with quote_verified still False, so nothing
                # downstream can mistake it for checked evidence.
                result.rejected.append(item)

            # A located quote proves the text exists. It does not prove the text says this.
            value_raw = item.value_raw
            if self._enforce_evidence:
                verdict = check_entailment(
                    item.value_raw,
                    span.quote,
                    self._registry.attribute(code),
                    table_ref=span.table_ref,
                )
                if verdict.support is Support.UNSUPPORTED:
                    result.rejected.append(item)
                    result.gaps.append(
                        self._gap(
                            code,
                            class_code,
                            parsed,
                            GapReason.EXTRACTED_BUT_UNVERIFIABLE,
                            detail=(
                                f"citation does not support the value: {verdict.detail}"
                            ),
                            action=RecommendedAction.HUMAN_RESEARCH,
                        )
                    )
                    continue
                if verdict.support is Support.CORRECTED:
                    # The evidence outranks the model's reading of it.
                    value_raw = verdict.value_raw
                    result.corrections.append(
                        f"{code}: {item.value_raw!r} -> {value_raw!r} ({verdict.detail})"
                    )

            result.values.append(
                AttributeValue(
                    attribute_code=code,
                    value_raw=value_raw,
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

    def _unevidenced_value(self, item, code: str, result, response) -> AttributeValue:
        """A value with no evidence at all. Reachable only with ``enforce_evidence=False``.

        Used by the ablation harness to measure what the trust layer is worth: this is what a
        generic enrichment pipeline would publish. ``AttributeValue`` normally refuses to exist
        without evidence when the method requires it, so the method is recorded as an inference
        — which is the truth. Nothing was read from the document.
        """
        result.rejected.append(item)
        return AttributeValue(
            attribute_code=code,
            value_raw=item.value_raw,
            method=DerivationMethod.STATISTICAL_DEFAULT,
            confidence=item.certainty.provisional_confidence,
            evidence=[],
            model_id=response.model_id if response else None,
            model_tier=response.tier if response else None,
            prompt_version=result.prompt_version,
            schema_version=result.schema_version,
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
