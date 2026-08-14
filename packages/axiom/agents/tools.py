"""Catalogue tools for an AI agent, and the provenance contract they enforce.

Blueprint Tier 3, item 21, and persona five from Part 4.2: *an AI shopping agent needs
machine-readable, unambiguous product facts — typed values and units, not prose.*

### The one rule this module exists to enforce

**No tool may return a value without its provenance.**

That is not a nicety. An agent is a relay: whatever it receives, it restates to a buyer in
confident natural language, and it has no way to recover a distinction the tool layer discarded. If
``get_product`` returns ``{"lead_free_compliant": true}``, an agent will tell a plumbing contractor
the valve is lead-free — and it will say it identically whether that came from an NSF/ANSI 372
certificate or from a part-number grammar's guess.

So every value crosses this boundary wrapped in :class:`ValueView`, which carries the derivation
method, whether a citation was verified, and an explicit ``usable_for_compliance`` flag. There is
exactly one serializer and every tool goes through it, for the same reason
:mod:`axiom.core.compare` is the only place two values are compared: a second path is a path where
the guarantee silently does not hold.

### Filtering on a compliance attribute is restricted, deliberately

:func:`search_products` will not match an unverified value when the filtered attribute is a
compliance claim. An agent asking for lead-free valves is asking a legal question, and returning a
part whose lead-free status was inferred would be the single most damaging thing this catalogue
could do — it converts an internal "we are not sure" into an external assertion of fact.

Specification attributes are matched on any publishable value, with the provenance attached so the
agent can qualify its answer. The asymmetry is the point: a wrong pressure rating is a
disappointment, a wrong compliance flag is a liability.

### What is not built

No supervisor/specialist agent topology, no tool-calling loop, no model in this package at all.
Those are the blueprint's ``agents/`` module and they are a different piece of work; what is here is
the **server side** — the typed, provenance-preserving surface an agent calls. That boundary is
deliberate: these tools are pure functions over records on disk, so they are testable, free, and
reproducible bit-for-bit, and none of that would survive putting a model inside them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue, Quantity, ValueRange
from axiom.normalize import normalize_value
from axiom.resolve import Catalogue, cross_reference, equivalence
from axiom.schema.models import AttributeDefinition
from axiom.schema.registry import SchemaRegistry


class ToolError(Exception):
    """A tool was called with arguments it cannot satisfy.

    Raised rather than returned as a partial result. An agent handed
    ``{"products": []}`` for a misspelled class code will conclude the catalogue is empty and say
    so; an error tells it the query was wrong, which is a different and recoverable situation.
    """


# --------------------------------------------------------------------------- value views


@dataclass(frozen=True)
class ValueView:
    """One attribute value as an agent may see it. The only way a value leaves this package."""

    attribute_code: str
    name: str
    value: Any
    unit: str | None
    display: str
    datatype: str
    method: str
    verified: bool
    """Whether at least one citation was matched back to its source document."""

    compliance_claim: bool
    usable_for_compliance: bool
    """False whenever a compliance claim lacks verified evidence, whatever its confidence.

    Stated as its own field rather than left for a caller to derive from `verified` and
    `compliance_claim`, because a derivation is a step a caller can skip.
    """

    confidence: float
    citations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_code": self.attribute_code,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "display": self.display,
            "datatype": self.datatype,
            "provenance": {
                "method": self.method,
                "verified": self.verified,
                "confidence": round(self.confidence, 4),
                "citations": list(self.citations),
            },
            "compliance_claim": self.compliance_claim,
            "usable_for_compliance": self.usable_for_compliance,
        }


def value_view(value: AttributeValue, definition: AttributeDefinition) -> ValueView:
    """Wrap a value with everything an agent needs in order not to overstate it."""
    canonical = value.value_canonical
    plain, unit = _plain(canonical)
    verified = value.has_verified_evidence or value.method.is_human

    return ValueView(
        attribute_code=value.attribute_code,
        name=definition.name,
        value=plain,
        unit=unit or definition.canonical_unit,
        display=value.value_display or value.value_raw or str(canonical),
        datatype=definition.datatype.value,
        method=value.method.value,
        verified=verified,
        compliance_claim=definition.compliance_claim,
        # Verified *and* not inferred, both required. The second clause is not redundant: an
        # inference is permitted to carry an evidence span (a grammar rule can cite the catalogue
        # row it generalised from), so `verified` alone would let a rule-derived value present
        # itself as sourced. An inference is never an observation, whatever it points at.
        usable_for_compliance=verified and not value.method.is_inference,
        confidence=value.confidence,
        citations=tuple(value.citation_summary()),
    )


def _plain(canonical: object) -> tuple[Any, str | None]:
    """Reduce a canonical value to JSON with its unit pulled out alongside.

    An agent comparing two pressures needs the magnitude as a number, not ``"600 psi"`` as a
    string. Keeping the unit in a sibling field is what makes the value arithmetic-ready without
    losing what it is denominated in.
    """
    if isinstance(canonical, Quantity):
        return canonical.magnitude, canonical.unit
    if isinstance(canonical, ValueRange):
        return {"minimum": canonical.minimum, "maximum": canonical.maximum}, canonical.unit
    if isinstance(canonical, list):
        return [str(v) for v in canonical], None
    return canonical, None


# --------------------------------------------------------------------------- filters


@dataclass(frozen=True)
class Filter:
    """One constraint on an attribute, expressed in source form.

    ``value`` is a string in the form a human would write on a datasheet, and it is normalised
    through the same pipeline extracted values go through. That is what lets an agent ask for
    ``600 PSI`` or ``3/4"`` without knowing this system's canonical units — and it means a filter
    and a value can never disagree about what a unit means, because one normaliser resolved both.
    """

    attribute_code: str
    operator: str = "eq"
    value: str = ""

    OPERATORS = ("eq", "gte", "lte", "contains")

    def __post_init__(self) -> None:
        if self.operator not in self.OPERATORS:
            raise ToolError(
                f"unknown operator '{self.operator}'; expected one of "
                f"{', '.join(self.OPERATORS)}"
            )


def _normalised_filter_value(
    spec: Filter, definition: AttributeDefinition
) -> object:
    from axiom.core.values import DerivationMethod, ValueStatus

    placeholder = AttributeValue(
        attribute_code=spec.attribute_code,
        value_raw=spec.value,
        method=DerivationMethod.HUMAN_ENTRY,
        confidence=1.0,
        status=ValueStatus.HUMAN_APPROVED,
    )
    outcome = normalize_value(placeholder, definition)
    if not outcome.normalized or outcome.value.value_canonical is None:
        raise ToolError(
            f"could not interpret '{spec.value}' as a value for "
            f"'{spec.attribute_code}' ({definition.datatype.value}, canonical unit "
            f"{definition.canonical_unit or 'none'})"
        )
    return outcome.value.value_canonical


def _matches(
    value: AttributeValue, spec: Filter, definition: AttributeDefinition
) -> bool:
    from axiom.core import compare

    target = _normalised_filter_value(spec, definition)
    actual = value.value_canonical

    if spec.operator == "eq":
        return compare.agree(target, actual, tolerance=definition.tolerance or 0.0)

    if spec.operator == "contains":
        if isinstance(actual, list):
            wanted = target if isinstance(target, list) else [target]
            return {str(w) for w in wanted} <= {str(a) for a in actual}
        return str(target).casefold() in str(actual).casefold()

    left, right = _magnitude(actual), _magnitude(target)
    if left is None or right is None:
        raise ToolError(
            f"operator '{spec.operator}' needs a single magnitude, and "
            f"'{spec.attribute_code}' is {definition.datatype.value}"
        )
    return left >= right if spec.operator == "gte" else left <= right


def _magnitude(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, Quantity):
        return float(value.magnitude)
    if isinstance(value, int | float):
        return float(value)
    return None


# --------------------------------------------------------------------------- the tools


def list_classes(registry: SchemaRegistry) -> dict[str, Any]:
    """The schema, so an agent can form a valid query instead of guessing attribute names."""
    classes = []
    for code in registry.class_codes:
        definition = registry.product_class(code)
        classes.append(
            {
                "class_code": code,
                "name": definition.name,
                "version": definition.version,
                "browse_path": list(definition.browse_path),
                "external_codes": dict(definition.mappings),
                "attributes": [
                    {
                        "attribute_code": attribute.code,
                        "name": attribute.name,
                        "datatype": attribute.datatype.value,
                        "canonical_unit": attribute.canonical_unit,
                        "compliance_claim": attribute.compliance_claim,
                        "allowed_values": [a.value for a in attribute.allowed_values],
                        "required": bool(
                            (binding := definition.binding(attribute.code))
                            and binding.requirement.value == "required"
                        ),
                    }
                    for attribute in registry.attributes_for(code)
                ],
            }
        )
    return {"classes": classes, "filter_operators": list(Filter.OPERATORS)}


def get_product(
    sku: str, catalogue: Catalogue, registry: SchemaRegistry
) -> dict[str, Any]:
    """One record's publishable values, each with its provenance."""
    record = catalogue.get(sku)
    if record is None:
        raise ToolError(
            f"'{sku}' is not in this catalogue. Known SKUs: "
            f"{', '.join(catalogue.skus()) or 'none'}"
        )

    views, unknown = _views(record, registry)
    unverified = [v for v in views if not v.verified]
    compliance_blocked = [v for v in views if v.compliance_claim and not v.usable_for_compliance]

    return {
        "sku": record.sku,
        "brand": record.brand,
        "mpn": record.mpn,
        "class_code": record.class_code,
        "lifecycle_status": record.lifecycle_status.value,
        "parent_sku": record.parent_sku,
        "classifications": [
            {
                "scheme": c.scheme.value,
                "code": c.code,
                "confidence": round(c.confidence, 4),
                "publishable": c.is_publishable,
            }
            for c in record.classifications
        ],
        "attributes": [v.to_dict() for v in views],
        "unknown_attributes": unknown,
        "advisory": {
            "values_without_verified_evidence": len(unverified),
            "compliance_claims_not_usable": [v.attribute_code for v in compliance_blocked],
            "note": (
                "Every value carries its own provenance. Do not restate a value whose "
                "usable_for_compliance is false as an established fact."
            ),
        },
        "catalogue": catalogue.summary(),
    }


def search_products(
    catalogue: Catalogue,
    registry: SchemaRegistry,
    *,
    class_code: str | None = None,
    filters: Sequence[Filter] = (),
    limit: int = 20,
) -> dict[str, Any]:
    """Find records matching typed constraints.

    A compliance attribute is matched only on a value that is verified and not inferred. An agent
    asking for lead-free parts is asking a legal question, and a probable answer is the wrong kind
    of answer to it.
    """
    if class_code is not None and class_code not in set(registry.class_codes):
        raise ToolError(
            f"unknown class '{class_code}'. Known classes: "
            f"{', '.join(registry.class_codes)}"
        )

    resolved: list[tuple[Filter, AttributeDefinition]] = []
    for spec in filters:
        try:
            resolved.append((spec, registry.attribute(spec.attribute_code)))
        except KeyError as exc:
            raise ToolError(
                f"unknown attribute '{spec.attribute_code}'. Call list_classes to see what "
                f"this catalogue can be filtered on."
            ) from exc

    matches: list[dict[str, Any]] = []
    excluded_for_provenance: list[str] = []

    for record in catalogue.records:
        if class_code is not None and record.class_code != class_code:
            continue

        matched: list[ValueView] = []
        for spec, definition in resolved:
            value = record.get(spec.attribute_code)
            if value is None or not value.is_publishable:
                break
            if not _matches(value, spec, definition):
                break

            view = value_view(value, definition)
            if definition.compliance_claim and not view.usable_for_compliance:
                # Matched on the value, refused on the provenance. Recorded rather than dropped
                # silently: "we hold this claim but cannot stand behind it" is information an
                # agent's user may want, and hiding it looks identical to having no such part.
                excluded_for_provenance.append(record.sku)
                break
            matched.append(view)
        else:
            matches.append(
                {
                    "sku": record.sku,
                    "brand": record.brand,
                    "class_code": record.class_code,
                    "matched_on": [v.to_dict() for v in matched],
                }
            )

    return {
        "query": {
            "class_code": class_code,
            "filters": [
                {"attribute_code": f.attribute_code, "operator": f.operator, "value": f.value}
                for f in filters
            ],
        },
        "count": len(matches),
        "products": matches[:limit],
        "truncated": len(matches) > limit,
        "excluded_for_unverified_compliance": sorted(set(excluded_for_provenance)),
        "catalogue": catalogue.summary(),
    }


def find_substitutes(
    sku: str,
    catalogue: Catalogue,
    registry: SchemaRegistry,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Ranked alternatives for a part, with the verdict ladder intact.

    Delegates to :mod:`axiom.resolve` rather than reimplementing compatibility. An agent must not
    be able to obtain a *different* substitution answer than the console shows, and two
    implementations would guarantee it eventually could.
    """
    if catalogue.get(sku) is None:
        raise ToolError(
            f"'{sku}' is not in this catalogue. Known SKUs: {', '.join(catalogue.skus())}"
        )
    if len(catalogue) < 2:
        raise ToolError(
            f"a cross-reference needs at least two records; this catalogue holds "
            f"{len(catalogue)}"
        )

    result = cross_reference(sku, catalogue, registry, limit=limit)
    return {
        "reference_sku": sku,
        **result.summary(),
        "candidates": [
            {
                "sku": report.candidate_sku,
                "verdict": report.verdict.value,
                "substitutable": report.verdict.is_substitutable,
                "cross_brand": report.cross_brand,
                "blocking_differences": [
                    {
                        "attribute_code": c.attribute_code,
                        "reference": c.reference_display,
                        "candidate": c.candidate_display,
                        "interchange": c.interchange.value if c.interchange else None,
                    }
                    for c in report.blocking()
                ],
            }
            for report in result.reports[:limit]
        ],
    }


def check_substitution(
    reference_sku: str,
    candidate_sku: str,
    catalogue: Catalogue,
    registry: SchemaRegistry,
) -> dict[str, Any]:
    """Can ``candidate_sku`` replace ``reference_sku``?

    Directional, and the direction is not symmetric: a 600 psi valve substitutes for a 400 psi one
    and the reverse could fail in service. The argument order is therefore part of the question,
    not a detail of the call.
    """
    for sku in (reference_sku, candidate_sku):
        if catalogue.get(sku) is None:
            raise ToolError(
                f"'{sku}' is not in this catalogue. Known SKUs: {', '.join(catalogue.skus())}"
            )

    report = equivalence(
        catalogue.get(reference_sku), catalogue.get(candidate_sku), registry
    )
    return {
        "question": f"can {candidate_sku} replace {reference_sku}?",
        "direction_note": (
            "This verdict is directional. Asking the reverse is a different question and "
            "routinely has a different answer."
        ),
        **report.to_dict(),
    }


def explain_value(
    sku: str, attribute_code: str, catalogue: Catalogue, registry: SchemaRegistry
) -> dict[str, Any]:
    """Where one value came from: method, citations, validations.

    The tool that makes an agent auditable. Without it, an agent can relay a figure but cannot
    answer the only follow-up question that matters, which is "how do you know".
    """
    record = catalogue.get(sku)
    if record is None:
        raise ToolError(f"'{sku}' is not in this catalogue")

    try:
        definition = registry.attribute(attribute_code)
    except KeyError as exc:
        raise ToolError(f"unknown attribute '{attribute_code}'") from exc

    value = record.get(attribute_code)
    if value is None:
        gap = next((g for g in record.gaps if g.attribute_code == attribute_code), None)
        return {
            "sku": sku,
            "attribute_code": attribute_code,
            "known": False,
            "reason": (
                gap.reason.value
                if gap is not None and hasattr(gap.reason, "value")
                else "no value was established for this attribute"
            ),
            "advisory": (
                "This attribute is not known for this product. It is not zero, false or empty."
            ),
        }

    view = value_view(value, definition)
    return {
        "sku": sku,
        "known": True,
        **view.to_dict(),
        "evidence": [
            {
                "document_id": span.document_id,
                "sha256": span.document_sha256,
                "page": span.page,
                "quote": span.quote,
                "verified": span.quote_verified,
                "locator": span.locator(),
            }
            for span in value.evidence
        ],
        "validations": [
            {
                "layer": result.layer.value,
                "rule_id": result.rule_id,
                "verdict": result.verdict.value,
                "reason": result.reason,
            }
            for result in value.validations
        ],
    }


def _views(
    record: ProductRecord, registry: SchemaRegistry
) -> tuple[list[ValueView], list[str]]:
    views: list[ValueView] = []
    unknown: list[str] = []
    for value in record.publishable_values():
        try:
            definition = registry.attribute(value.attribute_code)
        except KeyError:
            # Surfaced rather than dropped, on the same principle the equivalence engine reports
            # an unclassified interchange level: an attribute that vanishes without trace is worse
            # than one an agent is told it cannot interpret.
            unknown.append(value.attribute_code)
            continue
        views.append(value_view(value, definition))
    return views, unknown
