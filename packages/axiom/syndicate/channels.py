"""Channel pre-flight validation and title rendering.

A PIM is judged by what comes out of it, not by its authoring screens. So the discipline here
is: **validate against the destination's rules before publishing, not after.** A rejection
surfaced in our own UI costs a reviewer thirty seconds; the same rejection surfaced in a
marketplace error report a day later costs an investigation and a missed launch window.

The other rule this module enforces is that **only publishable values leave the system**. A
value sitting in the review queue must never reach a channel feed, even if it is populated and
looks fine. Leaking queued values would quietly defeat the entire risk-control mechanism.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from axiom.core.product import ProductRecord
from axiom.core.values import AttributeValue
from axiom.schema import ChannelProfile, SchemaRegistry

# Tokens a title template may use that come from the product record rather than an attribute.
_RECORD_TOKENS = frozenset({"brand", "mpn", "sku", "gtin", "supplier", "class_name"})

# Derived tokens, computed from an attribute rather than read straight off it.
_DERIVED_TOKENS = frozenset({"body_material_short"})


@dataclass
class ChannelReadiness:
    """Whether a record may be published to a channel, and what is blocking it."""

    channel: str
    ready: bool
    missing: list[str] = field(default_factory=list)
    """Required attributes with no value at all."""

    not_publishable: list[str] = field(default_factory=list)
    """Required attributes that have a value which is not publishable — queued for review,
    unverified, or failing validation. Distinguished from `missing` because the remedy is
    different: this needs a reviewer, not a supplier."""

    warnings: list[str] = field(default_factory=list)

    @property
    def blocking_count(self) -> int:
        return len(self.missing) + len(self.not_publishable)

    def summary(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "ready": self.ready,
            "missing": self.missing,
            "not_publishable": self.not_publishable,
            "warnings": self.warnings,
        }


def preflight(
    record: ProductRecord, registry: SchemaRegistry, channel: str
) -> ChannelReadiness:
    """Check a record against a channel's requirements before attempting to publish."""
    if record.class_code is None:
        return ChannelReadiness(
            channel=channel,
            ready=False,
            warnings=["record has no product class, so no channel profile applies"],
        )

    definition = registry.product_class(record.class_code)
    profile = definition.channel(channel)
    if profile is None:
        return ChannelReadiness(
            channel=channel,
            ready=False,
            warnings=[f"class {record.class_code} declares no '{channel}' channel profile"],
        )

    readiness = ChannelReadiness(channel=channel, ready=True)

    for code in profile.required:
        if code in _RECORD_TOKENS:
            if not _record_token(record, code, registry):
                readiness.missing.append(code)
            continue

        value = record.get(code)
        if value is None:
            readiness.missing.append(code)
        elif not value.is_publishable:
            readiness.not_publishable.append(code)

    title = render_title(record, registry, profile)
    if profile.title_template and not title:
        readiness.warnings.append("title template could not be rendered from available values")
    elif title and profile.max_title_chars and len(title) > profile.max_title_chars:
        # A warning rather than a blocker: truncation is applied on export, and losing the
        # tail of a title is far less damaging than withholding the product.
        readiness.warnings.append(
            f"title is {len(title)} characters, over the {profile.max_title_chars} limit for "
            f"this channel; it will be truncated on export"
        )

    readiness.ready = readiness.blocking_count == 0
    return readiness


def render_title(
    record: ProductRecord, registry: SchemaRegistry, profile: ChannelProfile
) -> str | None:
    """Render a channel title from its template.

    Templates are used rather than free-form generation on purpose: consistent titles across a
    category are what make on-site search and feed quality work, and a model asked to write
    titles will vary the word order between two products that ought to read identically.

    Missing tokens are dropped and the result tidied, rather than left as a literal
    ``{placeholder}`` in a published title.
    """
    if not profile.title_template:
        return None

    template = " ".join(profile.title_template.split())
    rendered = template
    for token in sorted(profile.template_tokens(), key=len, reverse=True):
        replacement = _resolve_token(record, registry, token) or ""
        rendered = rendered.replace(f"{{{token}}}", replacement)

    # Tidy the damage left by dropped tokens: doubled spaces, and orphaned separators such as
    # a comma with nothing after it.
    rendered = re.sub(r"\s{2,}", " ", rendered)
    rendered = re.sub(r"\s+([,;])", r"\1", rendered)
    rendered = re.sub(r"([,;])\s*(?=[,;])", "", rendered)
    rendered = rendered.strip(" ,;-")
    return rendered or None


def _resolve_token(
    record: ProductRecord, registry: SchemaRegistry, token: str
) -> str | None:
    if token in _RECORD_TOKENS:
        return _record_token(record, token, registry)
    if token in _DERIVED_TOKENS:
        return _derived_token(record, token)

    value = record.get(token)
    if value is None or not value.is_publishable:
        return None
    return value.value_display or (
        str(value.value_canonical) if value.value_canonical is not None else None
    )


def _record_token(record: ProductRecord, token: str, registry: SchemaRegistry) -> str | None:
    if token == "class_name" and record.class_code:
        return registry.product_class(record.class_code).name
    if token == "supplier":
        return record.supplier_id
    if token == "gtin":
        # GTIN lives on the record but may also have been extracted as an attribute.
        if record.gtin:
            return record.gtin
        value = record.get("gtin")
        return value.value_display if value and value.is_publishable else None
    return getattr(record, token, None)


def _derived_token(record: ProductRecord, token: str) -> str | None:
    if token != "body_material_short":
        return None
    value = record.get("body_material")
    if value is None or not value.is_publishable or not value.value_canonical:
        return None
    # "Bronze C84400" -> "Bronze". Alloy designations belong in the spec table, not the title.
    return str(value.value_canonical).split()[0]


def publishable_values(record: ProductRecord) -> list[AttributeValue]:
    """Values cleared for export. The single gate every exporter must pass through."""
    return [v for v in record.current_values() if v.is_publishable]


def truncate_title(title: str, limit: int | None) -> str:
    """Truncate on a word boundary so a channel title never ends mid-word."""
    if limit is None or len(title) <= limit:
        return title
    cut = title[:limit]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;-")
