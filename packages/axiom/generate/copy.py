"""Constrained copy generation, with the claim check as a hard gate.

The order here is the whole design. Generate, then **check deterministically**, then publish only
if the check passed. The prompt asks the model to stay inside the fact sheet; the checker enforces
it. Asking nicely is not a control.

When copy fails the check it is not silently patched or quietly published with a warning. It is
returned as failed, with every offending claim named, and the caller decides. One regeneration
attempt is allowed — with the failures fed back — because a single unsupported number is usually
a slip the model will fix when told. Repeated failure is a signal about the fact sheet or the
prompt, not something to retry into submission.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from axiom.extract.client import (
    ModelCascade,
    ModelClient,
    ModelError,
    UsageLedger,
    invoke_with_cascade,
)
from axiom.generate.claims import ClaimReport, check_copy
from axiom.generate.facts import FactSheet
from axiom.generate.policy import CopyPolicy

PROMPT_VERSION = "copy@v1"

FIELDS = ("headline", "short_description", "long_description", "bullets")

SYSTEM = """\
You write product copy for an industrial distributor's catalogue.

You will be given a SKU and a list of VERIFIED ATTRIBUTES. Those attributes are the only facts \
you know about this product. They came from the manufacturer's datasheet and each one has been \
checked against it.

Absolute rules:

1. State no fact that is not in the verified attributes. No pressure, temperature, size, \
material, standard, certification or approval that is not listed.
2. Do not restate a number imprecisely. If the attribute says 600 psi, write 600 psi, not \
"around 600" or "over 500".
3. Do not mention what is missing, and do not speculate about typical or usual values for this \
kind of product.
4. No comparative or superlative claims: nothing is best, superior, industry-leading or \
unmatched. You have no information about other products.
5. No guarantees, warranties, or claims of being maintenance-free or permanent.
6. Do not claim lead-free, potable-water or regulatory compliance unless an attribute \
explicitly establishes it.
7. Write plainly, for a professional buyer who wants to know whether this part fits their job. \
Specifications sell industrial products; adjectives do not.

Return strict JSON only, with exactly these keys:

{
  "headline": "<= 80 characters, identifies the product",
  "short_description": "one or two sentences, <= 300 characters",
  "long_description": "2-3 short paragraphs",
  "bullets": ["3 to 6 short specification bullets"]
}
"""


@dataclass
class GeneratedCopy:
    """Generated copy plus the verdict on it."""

    sku: str
    headline: str = ""
    short_description: str = ""
    long_description: str = ""
    bullets: list[str] = field(default_factory=list)

    report: ClaimReport = field(default_factory=ClaimReport)
    attempts: int = 0
    usage: UsageLedger = field(default_factory=UsageLedger)
    prompt_version: str = PROMPT_VERSION
    model_id: str | None = None
    model_tier: str | None = None
    error: str | None = None

    @property
    def published(self) -> bool:
        """Copy is publishable only if it generated *and* passed the claim check."""
        return self.error is None and bool(self.headline) and self.report.passed

    def fields(self) -> dict[str, str]:
        return {
            "headline": self.headline,
            "short_description": self.short_description,
            "long_description": self.long_description,
            "bullets": "\n".join(self.bullets),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "published": self.published,
            "headline": self.headline,
            "short_description": self.short_description,
            "long_description": self.long_description,
            "bullets": list(self.bullets),
            "attempts": self.attempts,
            "prompt_version": self.prompt_version,
            "model_id": self.model_id,
            "model_tier": self.model_tier,
            "error": self.error,
            "claim_check": self.report.summary(),
            "claims": [claim.to_dict() for claim in self.report.claims],
        }


class CopyGenerator:
    """Generates copy from a fact sheet and gates it on the claim check."""

    def __init__(
        self,
        client: ModelClient,
        cascade: ModelCascade,
        policy: CopyPolicy,
        *,
        tier: str = "mid",
        max_tokens: int = 1200,
        max_attempts: int = 2,
    ) -> None:
        self._client = client
        self._cascade = cascade
        self._policy = policy
        self._tier = tier
        """Copy defaults to the mid tier rather than the cheapest. Prose quality is the one place
        where a better model shows, and the volume is one call per SKU rather than per
        attribute."""

        self._max_tokens = max_tokens
        self._max_attempts = max(1, max_attempts)

    def generate(self, sheet: FactSheet) -> GeneratedCopy:
        result = GeneratedCopy(sku=sheet.sku)

        if not sheet.facts:
            # Nothing verified means nothing to say. Generating from an empty fact sheet would
            # produce pure invention, which is the one outcome that must be impossible.
            result.error = (
                "no publishable attributes: copy would have no verified facts to draw on"
            )
            return result

        feedback: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            result.attempts = attempt
            try:
                payload, response = self._invoke(sheet, feedback, result.usage)
            except (ModelError, ValueError) as exc:
                result.error = f"generation failed: {exc}"
                return result

            result.model_id = response.model_id if response else None
            result.model_tier = response.tier if response else None
            _apply(result, payload)

            result.report = check_copy(result.fields(), sheet, self._policy)
            if result.report.passed:
                return result

            if attempt < self._max_attempts:
                feedback = _feedback(result.report)

        return result

    def _invoke(self, sheet: FactSheet, feedback: str | None, ledger: UsageLedger):
        user = sheet.to_prompt()
        if feedback:
            user += (
                "\n\nYour previous attempt was rejected by an automated claim check. "
                "Every statement below could not be traced to a verified attribute. "
                "Rewrite the copy without them.\n\n" + feedback
            )

        def validate(text: str) -> dict:
            payload = json.loads(_strip_fences(text))
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
            if not payload.get("headline"):
                raise ValueError("headline is required")
            return payload

        payload, response = None, None
        response, payload = invoke_with_cascade(
            self._client,
            self._cascade,
            system=SYSTEM,
            user=user,
            start_tier=self._tier,
            validate=validate,
            max_tokens=self._max_tokens,
            ledger=ledger,
        )
        return payload, response


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
    return stripped.strip()


def _apply(result: GeneratedCopy, payload: dict) -> None:
    result.headline = str(payload.get("headline") or "").strip()
    result.short_description = str(payload.get("short_description") or "").strip()
    result.long_description = str(payload.get("long_description") or "").strip()

    bullets = payload.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    result.bullets = [str(b).strip() for b in bullets if str(b).strip()]


def _feedback(report: ClaimReport) -> str:
    lines = []
    for claim in report.banned + report.unsupported:
        where = f" (in {claim.field_name})" if claim.field_name else ""
        lines.append(f"- {claim.text!r}{where}: {claim.reason}")
    return "\n".join(lines)
