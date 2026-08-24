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

import re
from dataclasses import dataclass, field

from axiom.core.gaps import Gap, GapReason, RecommendedAction
from axiom.core.specifications import (
    ManufacturerSpecification,
    quote_supports_specification,
    specification_id,
)
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
    ExtractionContract,
    ManufacturerSpecificationContractItem,
    classify_abstention,
    parse_extraction_contract,
)
from axiom.extract.entailment import Support, check_entailment
from axiom.schema import (
    Requirement,
    SchemaRegistry,
    build_extraction_prompt,
    build_specification_extraction_prompt,
)

_ABSTENTION_REASONS = {
    "deferred": (GapReason.REFERRED_ELSEWHERE, RecommendedAction.REQUEST_FROM_SUPPLIER),
    "applicability": (GapReason.NOT_PRESENT_IN_ANY_SOURCE, RecommendedAction.HUMAN_RESEARCH),
    "absent": (GapReason.NOT_PRESENT_IN_ANY_SOURCE, RecommendedAction.REQUEST_FROM_SUPPLIER),
}
_LABEL_FOLD = re.compile(r"[^a-z0-9]+")


def _fold_label(value: str) -> str:
    return _LABEL_FOLD.sub("", value.casefold())


@dataclass
class ExtractionResult:
    """Everything one extraction pass produced."""

    values: list[AttributeValue] = field(default_factory=list)
    manufacturer_specifications: list[ManufacturerSpecification] = field(default_factory=list)
    """Every verified source-native label/value pair, whether or not the class schema knows it."""
    gaps: list[Gap] = field(default_factory=list)
    rejected: list[ContractItem] = field(default_factory=list)
    """Claims discarded because their quote could not be located, or because the quote that was
    located does not support the value. Kept because a silent drop is untraceable, and a pattern
    of rejections is a signal about the source or the prompt rather than about one SKU."""
    rejected_specifications: list[ManufacturerSpecificationContractItem] = field(
        default_factory=list
    )
    """Open-ended rows discarded because their quote, label or value could not be verified."""
    suppressed_specifications: list[ManufacturerSpecification] = field(default_factory=list)
    """Verified source-native rows withheld from delivery because publisher authority was absent."""

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
        """True when extraction was refused because the document is not about this product.

        Covers both shapes of that: the part number is absent, or it is present only in a note
        withdrawing it. Either way no model call was made and every attribute is a gap.
        """
        return self.sku_presence is not None and not self.sku_presence.is_extractable

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
            "manufacturer_specifications": len(self.manufacturer_specifications),
            "manufacturer_specifications_unmapped": sum(
                1
                for specification in self.manufacturer_specifications
                if specification.mapped_attribute_code is None
            ),
            "gaps": len(self.gaps),
            "rejected_unverifiable": len(self.rejected),
            "rejected_specifications": len(self.rejected_specifications),
            "suppressed_untrusted_specifications": len(self.suppressed_specifications),
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
        max_tokens: int = 8000,
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
        class_code: str | None,
        target_sku: str,
        only_codes: tuple[str, ...] | None = None,
        include_recommended: bool = True,
        include_optional: bool = False,
        max_pages: int | None = None,
        sku_variants: set[str] | None = None,
        manufacturer_source_verified: bool = False,
    ) -> ExtractionResult:
        # Publisher authority is never inferred from URI shape. Local files are useful evidence,
        # but they become manufacturer-citable only when their caller has established provenance.

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
            return self._no_extraction_result(
                parsed, class_code, target_sku, presence, only_codes,
                include_recommended, include_optional, max_pages,
                detail=(
                    f"part number {target_sku!r} does not appear anywhere in "
                    f"{parsed.document.document_id!r}, so this document does not describe "
                    f"it; no value was requested from the model"
                ),
                action=RecommendedAction.RETRY_WITH_BETTER_SOURCE,
            )

        # A part number the document mentions only in order to retire it. The presence check
        # above passes — the number really is there — so this is the one targeting failure that
        # gate cannot see. Left unhandled it is the worst case in the adversarial harness: a
        # complete, confident, fully-cited record for a part that cannot be bought, because
        # every shared specification in the surrounding prose reads as though it applies.
        if self._require_sku_in_document and presence.is_not_offered:
            return self._no_extraction_result(
                parsed, class_code, target_sku, presence, only_codes,
                include_recommended, include_optional, max_pages,
                detail=(
                    f"{parsed.document.document_id!r} mentions {target_sku!r} only to withdraw "
                    f"it: {presence.withdrawal_quote!r}. The surrounding specifications describe "
                    f"the range, not this part; no value was requested from the model"
                ),
                action=RecommendedAction.DELIST_PRODUCT,
            )

        prompt = (
            build_extraction_prompt(
                self._registry,
                class_code,
                source_content=parsed.to_prompt_content(max_pages=max_pages),
                target_sku=target_sku,
                source_name=parsed.document.document_id,
                include_recommended=include_recommended,
                include_optional=include_optional,
                only_codes=only_codes,
            )
            if class_code is not None
            else build_specification_extraction_prompt(
                source_content=parsed.to_prompt_content(max_pages=max_pages),
                target_sku=target_sku,
                source_name=parsed.document.document_id,
            )
        )

        result = ExtractionResult(
            prompt_version=prompt.prompt_version,
            schema_version=prompt.schema_version,
            requested_codes=prompt.attribute_codes,
        )

        def validate(text: str) -> ExtractionContract:
            return parse_extraction_contract(text, expected_codes=prompt.attribute_codes)

        try:
            response, contract = invoke_with_cascade(
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
        self._materialise(
            result, list(contract.attributes), parsed, class_code, prompt.attribute_codes
        )
        # Source-native pairs are manufacturer claims, not merely statements found on a page.
        # Unknown web sources remain valid evidence for typed extraction, but source-native rows
        # from them are retained only as non-citable provenance and never enter delivery cells.
        specification_start = len(result.manufacturer_specifications)
        self._materialise_specifications(
            result,
            list(contract.manufacturer_specifications),
            parsed,
            class_code,
            start_index=len(prompt.attribute_codes),
            citable_as_manufacturer=manufacturer_source_verified,
        )
        if not manufacturer_source_verified:
            result.suppressed_specifications.extend(
                result.manufacturer_specifications[specification_start:]
            )
        return result

    # ------------------------------------------------------------------ internals

    def _no_extraction_result(
        self,
        parsed: ParsedDocument,
        class_code: str | None,
        target_sku: str,
        presence,
        only_codes,
        include_recommended: bool,
        include_optional: bool,
        max_pages: int | None,
        *,
        detail: str,
        action: RecommendedAction,
    ) -> ExtractionResult:
        """Every requested attribute becomes a gap, with no model call made.

        The gap reason is deliberately ``NO_SOURCE_AVAILABLE`` rather than
        ``NOT_PRESENT_IN_ANY_SOURCE``: the distinction is between "we read a document about this
        product and it does not state this value" and "we never had a usable document about this
        product at all". Those need different remedies — the first is a question for the
        supplier, the second means someone attached the wrong file or the part is withdrawn.

        ``action`` carries which of those it was, because the remedies are not interchangeable.
        """
        prompt = (
            build_extraction_prompt(
                self._registry,
                class_code,
                source_content="",
                target_sku=target_sku,
                source_name=parsed.document.document_id,
                include_recommended=include_recommended,
                include_optional=include_optional,
                only_codes=only_codes,
            )
            if class_code is not None
            else build_specification_extraction_prompt(
                source_content="",
                target_sku=target_sku,
                source_name=parsed.document.document_id,
            )
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
                    detail=detail,
                    recommended_action=action,
                    is_required=self._is_required(class_code, code),
                )
            )
        return result

    def _materialise(
        self,
        result: ExtractionResult,
        items: list[ContractItem],
        parsed: ParsedDocument,
        class_code: str | None,
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

    def _materialise_specifications(
        self,
        result: ExtractionResult,
        items: list[ManufacturerSpecificationContractItem],
        parsed: ParsedDocument,
        class_code: str | None,
        *,
        start_index: int,
        citable_as_manufacturer: bool,
    ) -> None:
        """Verify and retain arbitrary manufacturer label/value pairs.

        Quote location alone is insufficient here: a genuine paragraph may be cited while the model
        invents the label or value. Both raw strings must therefore be reproducible inside the
        located quote before the observation enters the canonical record.
        """
        response = result.response
        seen: set[tuple[str, str, str]] = set()
        for offset, item in enumerate(items):
            if not item.is_well_formed:
                result.rejected_specifications.append(item)
                continue

            # Narrowed by is_well_formed; local names keep the verifier and constructor explicit.
            label = item.label_raw or ""
            value = item.value_raw or ""
            quote = item.evidence_quote or ""
            span = build_evidence_span(
                quote,
                parsed,
                span_id=f"ms_{parsed.document.sha256[:8]}_{start_index + offset}",
                page_hint=item.evidence_page,
                threshold=self._quote_threshold,
            )
            supported = span.quote_verified and quote_supports_specification(
                label, value, span.quote
            )
            if not supported:
                result.rejected_specifications.append(item)
                continue

            specification = ManufacturerSpecification(
                specification_id=specification_id(parsed.document.sha256, label, value),
                label_raw=label,
                value_raw=value,
                evidence=[span],
                confidence=item.certainty.provisional_confidence,
                method=(
                    DerivationMethod.TABLE_EXTRACTION
                    if span.table_ref
                    else DerivationMethod.DOCUMENT_EXTRACTION
                ),
                mapped_attribute_code=self._mapped_attribute_code(class_code, label),
                citable_as_manufacturer=citable_as_manufacturer,
                model_id=response.model_id if response else None,
                model_tier=response.tier if response else None,
                prompt_version=result.prompt_version,
                schema_version=result.schema_version,
            )
            if specification.deduplication_key in seen:
                continue
            seen.add(specification.deduplication_key)
            result.manufacturer_specifications.append(specification)

    def _mapped_attribute_code(self, class_code: str | None, label: str) -> str | None:
        if class_code is None:
            return None
        folded = _fold_label(label)
        if not folded:
            return None
        for attribute in self._registry.attributes_for(class_code):
            labels = (attribute.name, *attribute.spec_labels, *attribute.table_headers)
            if any(_fold_label(candidate) == folded for candidate in labels):
                return attribute.code
        return None

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
        class_code: str | None,
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

    def _is_required(self, class_code: str | None, code: str) -> bool:
        if class_code is None:
            return False
        try:
            binding = self._registry.product_class(class_code).binding(code)
        except KeyError:
            return False
        return binding is not None and binding.requirement is Requirement.REQUIRED


__all__ = ["ContractError", "ExtractionResult", "Extractor"]
