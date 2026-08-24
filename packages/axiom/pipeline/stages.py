"""Stages 3 to 10: a parsed document in, a signed certificate out.

Lifted verbatim out of ``scripts/run_pipeline.py``, which held this sequence inline for as long as
the CLI was the only caller. The script keeps argument parsing, ingest, parse and every
``_report_*`` printer; everything that decides what a value is worth is here.

The ordering is load-bearing and is preserved exactly as the script had it. Three places where it
matters, all of which were commented in the original and are worth restating because they are the
kind of thing a well-meaning refactor breaks:

1.  **Classification runs before extraction**, because the class decides which attributes are asked
    for at all. When classification abstains, ``class_code_fallback`` is the explicit fallback
    rather than a guess.
2.  **Validation runs before scoring**, and its findings are attached to each value first, because
    validation is the strongest independent signal the confidence features have. Scoring first would
    throw it away.
3.  **The certificate is built last**, after the exports and the copy exist, because the Quality
    Index's richness dimension is observed from them. Building it first — as the script once did —
    left richness permanently unmeasured and dragged every composite down for a reason unrelated to
    the data.

Nothing here prints, and nothing here writes to disk. That is what makes the same function usable
from a CLI that reports to a terminal and from an HTTP handler that has to return JSON.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiom.classify import Classifier
from axiom.confidence import (
    DEFAULT_EPSILON,
    Calibrator,
    Priors,
    RiskPolicy,
    apply_policy,
    extract_features,
    select_threshold,
)
from axiom.console import serialise_copy
from axiom.core.certificate import build_certificate
from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue
from axiom.extract import Extractor, ModelCascade, PriceTable, UsageLedger
from axiom.extract.extractor import ExtractionResult
from axiom.generate import CopyGenerator, build_fact_sheet, load_policy
from axiom.normalize import BrandMaster, clean_mpn, normalize_all
from axiom.syndicate import export_all
from axiom.validate import ReasoningChecker, ReasoningConfig, Validator, guardrail_runtime


@dataclass
class PipelineRun:
    """Everything one run produced.

    Deliberately wide rather than nested. These are precisely the local variables the script
    threaded between its stages, and every consumer — the terminal report, the console bundle, the
    review session, the delivery row — needs a different subset. Grouping them into sub-objects
    would invent a hierarchy none of those consumers share.
    """

    record: ProductRecord
    classification: Any
    """The full :class:`~axiom.classify.ClassificationResult`, not just the code. The candidate list
    and the abstention reason are both reported, and neither survives reduction to a string."""

    class_code: str | None
    """The class actually used, which is the classifier's answer or the caller's fallback."""

    class_code_from_fallback: bool
    """True when classification abstained and the fallback was used. Reported rather than inferred,
    because "classified as X" and "told to assume X" are different claims about the same value."""

    extraction: Any
    normalized: list[AttributeValue]
    normalization_issues: list[Any]
    validation: Any
    scores: dict[str, float]
    features: dict[str, Any]
    decisions: list[Any]
    certificate: Any
    exports: dict[str, Any]
    policy: RiskPolicy
    calibrator: Calibrator
    priors: Priors
    usage: UsageLedger
    prices: PriceTable | None
    tier_prices: dict[str, Any] | None
    cost_usd: float | None
    classified_from: str = "document"
    """Which text the class was read from: ``supplied`` (a description, or a title block) or
    ``document`` (the whole thing). Worth reporting because the two are not equally trustworthy — a
    description states what the product is, while a full page also contains everything around it."""

    brand: Any | None = None
    """The :class:`BrandResolution` when a raw brand string was supplied, so a caller can report
    *how* it resolved rather than only what it resolved to."""

    copy: Any | None = None
    serialised_copy: dict[str, Any] | None = None
    seeded: list[AttributeValue] = field(default_factory=list)
    """Values added before the document's own, by a caller that had something to contribute — a
    product description, or a golden set. Superseded by any document value for the same attribute,
    and retained in the record's history either way."""

    notes: list[str] = field(default_factory=list)
    """Things a caller should surface that are not failures. Collected rather than printed, because
    this function has no idea whether its output is a terminal or an HTTP response."""

    def feature_explanations(self) -> dict[str, dict[str, float]]:
        """Per-attribute feature vectors, in the shape the console bundle wants."""
        return {code: features.explain() for code, features in self.features.items()}

    def wall_clock_seconds(self) -> float:
        return round(self.usage.latency_ms / 1000, 2)


def load_calibration(
    directory: Path | str, epsilon: float = DEFAULT_EPSILON
) -> tuple[Calibrator, Priors, RiskPolicy]:
    """Load a trained calibrator, learned priors and a validated risk policy if they exist.

    All three are optional. With none of them present the system cold-starts: scores come from a
    capped heuristic and the policy is unachievable, so every value queues for review. That is the
    correct opening state — automation coverage should be earned from review outcomes, not granted
    before any exist. It is also exactly the state a brand-new SKU arrives in.
    """
    import json

    directory = Path(directory)
    calibrator = Calibrator()
    priors = Priors()
    policy = select_threshold([], [], epsilon=epsilon)

    if (path := directory / "calibrator.json").exists():
        calibrator = Calibrator.load(path)
    if (path := directory / "priors.json").exists():
        priors = Priors.load(path)
    if (path := directory / "calibration_set.json").exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = select_threshold(payload["scores"], payload["labels"], epsilon=epsilon)
    return calibrator, priors, policy


def run_stages(
    parsed,
    artifact,
    *,
    registry,
    client,
    cascade: ModelCascade,
    sku: str,
    mpn: str | None = None,
    tenant_id: str = "demo",
    brand: str | None = None,
    supplier_id: str | None = None,
    class_code_fallback: str | None = None,
    calibration_dir: Path | str,
    risk_budget: float = DEFAULT_EPSILON,
    include_optional: bool = False,
    tier: str = "volume",
    generate_copy: bool = False,
    verify_claims: bool = False,
    profile: str | None = None,
    brands: BrandMaster | None = None,
    seed_values: Callable[[ProductRecord], Sequence[AttributeValue]] | None = None,
    classify_text: str | None = None,
    manufacturer_source_verified: bool = False,
) -> PipelineRun:
    """Run the pipeline over one parsed document.

    ``client`` is injected rather than constructed here, which is what makes the whole path
    testable: a test passes :class:`~axiom.extract.StubModelClient` and exercises the same code the
    Bedrock path runs, instead of a parallel implementation that agrees with it by inspection.

    ``seed_values`` is the hook for a caller that has evidenced values of its own — a product
    description read deterministically, or a golden set. It is called once with the freshly built
    record, after classification has settled the class and before any document value is added, so
    the document supersedes it. That ordering is not a detail: a datasheet *states* a fact where a
    description only implies it, and :meth:`ProductRecord.add_value` supersedes rather than appends,
    so the weaker reading stays in history instead of colliding with the stronger one.
    """
    notes: list[str] = []

    # --- stage 3: classify -----------------------------------------------------
    # Runs before extraction because the class decides which attributes to ask for. When
    # classification abstains, the caller's fallback is the explicit answer rather than a guess.
    # `classify_text` overrides what the classifier reads, and defaults to the whole document, which
    # is right when the document describes one product — a single datasheet, which is the CLI's
    # case.
    #
    # It is *not* right when the document is a series catalogue covering fifty parts: its full text
    # votes for whatever the catalogue mostly contains rather than for this part. So a caller
    # holding
    # a more specific statement of what this product is should pass it. `axiom.delivery.batch`
    # classifies on the item-master description for exactly this reason, and
    # `tests/test_minimal_input.py` uses the document's title block when there is no description.
    classifier = Classifier(registry, client=client, cascade=cascade, tier=tier)
    classified_from = "document"
    if classify_text:
        classification = classifier.classify(classify_text, sku=sku)
        classified_from = "supplied"
        if classification.abstained or classification.class_code is None:
            # Fall back to the whole document, and this fallback is load-bearing rather than
            # defensive. A retrieved *web page* has navigation chrome where a datasheet has a title
            # block — "Menu / Ellipsis / Chevron / Grid" scores against nothing in the schema — so
            # the more specific signal is sometimes the emptier one. Observed on a real retrieved
            # Kichler product page: the title block abstained with `no_viable_candidate` while the
            # full text classified decisively.
            #
            # Free in exactly the case it fires: an abstention on vocabulary grounds happens before
            # any model call, so this costs a second deterministic retrieval pass and nothing else.
            fallback = classifier.classify(parsed.full_text, sku=sku)
            if fallback.class_code is not None:
                classification = fallback
                classified_from = "document"
                notes.append(
                    "the supplied classification text matched no class, so the whole document was "
                    "read instead. A scraped product page opens with navigation, not a title."
                )
    else:
        classification = classifier.classify(parsed.full_text, sku=sku)

    class_code = classification.class_code or class_code_fallback

    # --- stage 4: extract ------------------------------------------------------
    # Typed extraction remains class-bound. When classification abstains, the independent
    # source-native channel still has a useful answer: exact manufacturer label/value pairs. An
    # untrusted web source cannot produce those claims, so that narrow case still skips the call.
    if class_code is None and manufacturer_source_verified is False:
        result = ExtractionResult()
        notes.append(
            "classification abstained and the source was not verified as manufacturer-owned, so "
            "there was neither a typed attribute list nor an authorized manufacturer "
            "specification pass to run"
        )
    else:
        extractor = Extractor(registry, client, cascade, start_tier=tier)
        result = extractor.extract(
            parsed,
            class_code=class_code,
            target_sku=sku,
            include_optional=include_optional,
            manufacturer_source_verified=manufacturer_source_verified,
        )
        if class_code is None:
            notes.append(
                "classification abstained, so no typed attributes were requested; verified "
                "source-native manufacturer specifications were retained instead"
            )

    # --- stage 5: normalize ----------------------------------------------------
    normalized, norm_issues = normalize_all(result.values, registry)

    # --- stage 6: validate -----------------------------------------------------
    brands = brands if brands is not None else BrandMaster.load()
    resolution = brands.resolve(brand) if brand else None
    record = ProductRecord(
        tenant_id=tenant_id,
        sku=sku,
        mpn=mpn or sku,
        mpn_normalized=clean_mpn(
            mpn or sku, brand=resolution.brand if resolution else None
        ),
        brand=resolution.brand.name if resolution and resolution.resolved else brand,
        brand_id=resolution.brand.brand_id if resolution and resolution.resolved else None,
        supplier_id=supplier_id,
        class_code=class_code,
        schema_version=result.schema_version,
        source_document_ids=[artifact.document.document_id],
    )
    record.classifications.extend(classification.classifications)

    # The seeded arm, before the document. See the docstring: this is the ordering that lets a
    # datasheet supersede a description reading rather than fight it.
    seeded: list[AttributeValue] = []
    if seed_values is not None:
        seeded = list(seed_values(record))
        for value in seeded:
            record.add_value(value)

    for value in normalized:
        record.add_value(value)
    for specification in result.manufacturer_specifications:
        record.add_manufacturer_specification(specification)
    for gap in result.gaps:
        record.add_gap(gap)

    report = Validator(registry).validate(record)

    # --- stage 7: score and decide ---------------------------------------------
    # Validation results are attached to each value first, so the confidence features can see
    # them. Scoring before validating would ignore the strongest independent signal available.
    for value in record.current_values():
        findings = report.per_attribute.get(value.attribute_code, [])
        if findings:
            value.validations = [*value.validations, *findings]

    calibrator, priors, policy = load_calibration(calibration_dir, risk_budget)
    scores: dict[str, float] = {}
    feature_map: dict[str, Any] = {}
    for value in record.current_values():
        features = extract_features(value, priors=priors, supplier_id=supplier_id)
        feature_map[value.attribute_code] = features
        scores[value.attribute_code] = calibrator.predict(features)

    decisions = apply_policy(record.current_values(), scores, policy)

    # --- stage 8: certificate and channel exports ------------------------------
    # Cost covers classification *and* extraction. Classification is a real model call against
    # a real prompt, and reporting only extraction would understate the true cost per SKU by
    # whatever the cheapest stage happens to cost — flattering, and wrong.
    usage = UsageLedger()
    usage.merge(classification.usage)
    usage.merge(result.usage)

    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None
    cost_usd = usage.cost_usd(tier_prices)

    exports = export_all(record, registry)

    # --- stage 9: constrained copy generation ----------------------------------
    # Runs last, and only from values that already survived every earlier gate. Generating
    # before the acceptance decision would let a queued value into a product description.
    generated = None
    if generate_copy:
        sheet = build_fact_sheet(record, registry)
        generator = CopyGenerator(client, cascade, load_policy(), tier="mid")
        generated = generator.generate(sheet)
        usage.merge(generated.usage)
        cost_usd = usage.cost_usd(tier_prices)

        # --- stage 9b: formal verification of the prose (L6) --------------------
        # Runs after the claim check rather than instead of it. The claim check proves each
        # statement came *from* a verified attribute; this proves the statement is not
        # self-contradictory given everything else the record establishes. Copy assembled
        # entirely from real attributes can still assert something impossible.
        #
        # Deliberately no regeneration on an L6 failure, unlike an unsupported claim. A
        # contradiction here is almost always a property of the source data — a leaded alloy
        # carrying a potable-water approval — so asking the model to rewrite would spend another
        # call to re-derive the same contradiction from the same facts. The finding belongs in
        # front of a human, not in a retry loop.
        if verify_claims and generated.headline:
            reasoning_config = ReasoningConfig.load()
            if reasoning_config is None:
                notes.append(
                    "L6 skipped: no reasoning policy is deployed. Deploy one with "
                    "scripts/deploy_reasoning_policy.py"
                )
            else:
                checker = ReasoningChecker(
                    guardrail_runtime(reasoning_config, profile=profile),
                    reasoning_config,
                )
                generated.formal = checker.verify_copy(record, generated.fields())

    # --- stage 10: certificate -------------------------------------------------
    # Built last, after the exports and the copy exist, because the Quality Index's richness
    # dimension is observed from them: channel readiness from the pre-flight results, copy depth
    # from the generated prose. Building the certificate first — as the script used to — left
    # richness permanently unmeasured, which dragged every composite down by a tenth for a reason
    # unrelated to the data.
    serialised = serialise_copy(generated)
    certificate = build_certificate(
        record,
        # An empty set rather than a raise when nothing classified: `required_codes` needs a class,
        # and there is none.
        #
        # Be aware of what that produces, because it is a trap. `fill_rate([])` returns **1.0**, not
        # zero and not "unmeasured" — vacuously, since no required attribute is missing when none is
        # required. So an unclassified record certifies at `completeness 1.0` and a composite of 0.6
        # while holding no values at all.
        #
        # Left as-is deliberately rather than special-cased here, on two grounds. It is what
        # `export_console_catalogue.py` already produces for the 20 unclassified SKUs in the demo
        # corpus, so diverging would make two writers disagree about the same state. And the fix
        # belongs in `QualityIndex`, which already models exactly this for `richness` —
        # `float | None`, excluded from `measured`, composite renormalised — rather than in
        # each of its callers.
        #
        # Consumers are expected to read `class_code is None` and decline to report a figure,
        # which is what `app/review/page.tsx` does: "Unmeasured classification is not zero
        # completeness."
        required_attribute_codes=(
            registry.required_codes(class_code) if class_code else frozenset()
        ),
        pipeline_version=f"axiom-{__import__('axiom').__version__}",
        cost_usd=cost_usd,
        wall_clock_seconds=round(usage.latency_ms / 1000, 2),
        exports=exports,
        copy=serialised,
    )

    return PipelineRun(
        record=record,
        classification=classification,
        class_code=class_code,
        class_code_from_fallback=classification.class_code is None and class_code is not None,
        classified_from=classified_from,
        extraction=result,
        normalized=list(normalized),
        normalization_issues=list(norm_issues),
        validation=report,
        scores=scores,
        features=feature_map,
        decisions=decisions,
        certificate=certificate,
        exports=exports,
        policy=policy,
        calibrator=calibrator,
        priors=priors,
        usage=usage,
        prices=prices,
        tier_prices=tier_prices,
        cost_usd=cost_usd,
        brand=resolution,
        copy=generated,
        serialised_copy=serialised,
        seeded=seeded,
        notes=notes,
    )


__all__ = ["PipelineRun", "load_calibration", "run_stages"]
