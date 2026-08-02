"""Model client abstraction and the tiered cascade.

Two things this buys:

**Testability.** The extractor takes a :class:`ModelClient`, so the whole extraction path —
contract parsing, quote verification, gap creation — is testable with a stub and no AWS
call. Tests that need a network round-trip to check JSON parsing are tests nobody runs.

**A real cascade.** ``invoke_with_cascade`` starts on the cheapest tier that has passed the
golden-set gate for the task and escalates only on failure. Escalation is recorded per call
so the cost dashboard can show which attributes actually justify the expensive tier, rather
than everything running on the frontier model because that felt safer.

Token counts are always recorded. Costs are only computed when a price table is supplied,
because inventing per-token prices produces a confident number that is wrong.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

CASCADE_ORDER = ("micro", "volume", "mid", "frontier")
"""Cheapest first. 'vision' and 'embedding' sit outside the text cascade."""


class ModelError(Exception):
    """Raised when a model call fails in a way retrying will not fix."""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_id: str
    tier: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    stop_reason: str | None = None

    @property
    def was_truncated(self) -> bool:
        """A truncated response yields invalid JSON, which is worth distinguishing from a
        model that simply produced bad output."""
        return self.stop_reason in {"max_tokens", "length"}


class ModelClient(Protocol):
    def converse(
        self,
        *,
        model_id: str,
        tier: str,
        system: str,
        user: str,
        max_tokens: int = 4000,
        temperature: float = 0.0,
    ) -> ModelResponse: ...


@dataclass
class ModelCascade:
    """Pinned model IDs per tier, loaded from the generated config."""

    region: str
    tiers: dict[str, str]
    embedding_model: str | None = None

    @classmethod
    def load(cls, path: Path | str | None = None) -> ModelCascade:
        config_path = Path(path or DEFAULT_CONFIG_PATH)
        if not config_path.exists():
            raise ModelError(
                f"{config_path} not found. Run scripts/preflight_bedrock.py --write to pin "
                f"verified model IDs; do not hard-code them."
            )
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        tiers = {
            name: spec["model_id"]
            for name, spec in (payload.get("tiers") or {}).items()
            if spec and spec.get("model_id")
        }
        if not tiers:
            raise ModelError(f"{config_path} pins no usable model IDs")
        embedding = (payload.get("embedding") or {}).get("model_id")
        return cls(
            region=payload.get("region", "us-east-2"),
            tiers=tiers,
            embedding_model=embedding,
        )

    def model_for(self, tier: str) -> str:
        try:
            return self.tiers[tier]
        except KeyError as exc:
            raise ModelError(
                f"tier '{tier}' is not pinned; available: {sorted(self.tiers)}"
            ) from exc

    def escalation_path(self, start: str) -> list[str]:
        """Tiers to try, from ``start`` upward, skipping any that are not pinned."""
        if start not in CASCADE_ORDER:
            return [start] if start in self.tiers else []
        begin = CASCADE_ORDER.index(start)
        return [t for t in CASCADE_ORDER[begin:] if t in self.tiers]


@dataclass
class UsageLedger:
    """Running token and call totals. Feeds the cost-per-SKU meter."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    escalations: int = 0
    latency_ms: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)

    def record(self, response: ModelResponse, *, escalated: bool = False) -> None:
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.latency_ms += response.latency_ms
        self.by_tier[response.tier] = self.by_tier.get(response.tier, 0) + 1
        if escalated:
            self.escalations += 1

    def cost_usd(self, prices: dict[str, tuple[float, float]] | None) -> float | None:
        """Cost, given ``{tier: (usd_per_million_in, usd_per_million_out)}``.

        Returns None when no price table is supplied. Reporting a made-up cost would be
        worse than reporting none, because a plausible number invites decisions.
        """
        if not prices:
            return None
        total = 0.0
        for tier, calls in self.by_tier.items():
            if tier not in prices:
                return None
            share = calls / self.calls if self.calls else 0
            per_in, per_out = prices[tier]
            total += (self.input_tokens * share / 1e6) * per_in
            total += (self.output_tokens * share / 1e6) * per_out
        return round(total, 6)

    def merge(self, other: UsageLedger) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.escalations += other.escalations
        self.latency_ms += other.latency_ms
        for tier, count in other.by_tier.items():
            self.by_tier[tier] = self.by_tier.get(tier, 0) + count


class BedrockModelClient:
    """Bedrock Converse-backed client."""

    def __init__(self, *, region: str, profile: str | None = None, read_timeout: int = 180):
        import boto3
        from botocore.config import Config

        session = boto3.Session(profile_name=profile, region_name=region)
        self._client = session.client(
            "bedrock-runtime",
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                read_timeout=read_timeout,
            ),
        )

    def converse(
        self,
        *,
        model_id: str,
        tier: str,
        system: str,
        user: str,
        max_tokens: int = 4000,
        temperature: float = 0.0,
    ) -> ModelResponse:
        from botocore.exceptions import ClientError

        started = time.perf_counter()
        try:
            response = self._client.converse(
                modelId=model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            raise ModelError(f"{error.get('Code')}: {error.get('Message', '')[:200]}") from exc

        usage = response.get("usage", {})
        text = "".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content", [])
        )
        return ModelResponse(
            text=text,
            model_id=model_id,
            tier=tier,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            stop_reason=response.get("stopReason"),
        )


class StubModelClient:
    """Scripted client for tests. Returns queued payloads in order."""

    def __init__(self, responses: list[str | Exception], *, model_id: str = "stub-model"):
        self._responses = list(responses)
        self._model_id = model_id
        self.calls: list[dict] = []

    def converse(
        self,
        *,
        model_id: str,
        tier: str,
        system: str,
        user: str,
        max_tokens: int = 4000,
        temperature: float = 0.0,
    ) -> ModelResponse:
        self.calls.append({"tier": tier, "model_id": model_id, "system": system, "user": user})
        if not self._responses:
            raise ModelError("StubModelClient exhausted")
        payload = self._responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return ModelResponse(
            text=payload,
            model_id=model_id or self._model_id,
            tier=tier,
            input_tokens=len(user) // 4,
            output_tokens=len(payload) // 4,
            latency_ms=1,
            stop_reason="end_turn",
        )

    @staticmethod
    def json_payload(items: list[dict]) -> str:
        return json.dumps(items)


def invoke_with_cascade(
    client: ModelClient,
    cascade: ModelCascade,
    *,
    system: str,
    user: str,
    start_tier: str = "volume",
    validate,
    max_tokens: int = 4000,
    ledger: UsageLedger | None = None,
) -> tuple[ModelResponse, object]:
    """Invoke the cheapest viable tier, escalating only when the result is unusable.

    ``validate`` receives the raw response text and either returns a parsed result or raises.
    Escalation is therefore driven by *whether the output was usable*, not by a guess about
    difficulty made before the call.
    """
    path = cascade.escalation_path(start_tier)
    if not path:
        raise ModelError(f"no pinned tier at or above '{start_tier}'")

    failures: list[str] = []
    for index, tier in enumerate(path):
        model_id = cascade.model_for(tier)
        try:
            response = client.converse(
                model_id=model_id, tier=tier, system=system, user=user, max_tokens=max_tokens
            )
        except ModelError as exc:
            failures.append(f"{tier}: {exc}")
            continue

        if ledger is not None:
            ledger.record(response, escalated=index > 0)

        try:
            return response, validate(response.text)
        except Exception as exc:  # noqa: BLE001 - any parse failure is grounds to escalate
            reason = "truncated output" if response.was_truncated else str(exc)
            failures.append(f"{tier}: {reason}")

    raise ModelError("every tier failed: " + "; ".join(failures))
