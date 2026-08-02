"""Bedrock preflight: prove access, measure latency and cost, then pin the model IDs.

Listing foundation models proves nothing — `list-foundation-models` returns the catalogue
regardless of whether your account has been granted access. The only reliable check is an
actual invocation, so that is what this does.

For each candidate in each tier it attempts a tiny `Converse` call, records whether it
succeeded, how long it took, and how many tokens it consumed. Then it writes the winners to
``packages/axiom/config/models.yaml`` so the pipeline reads pinned, verified IDs rather than
names someone remembered.

Newer models are frequently only reachable through a cross-region inference profile, in
which case the bare model ID fails and the geo-prefixed form (``us.<model-id>``) works.
This script tries both and records whichever form actually resolved.

Usage:
    python scripts/preflight_bedrock.py
    python scripts/preflight_bedrock.py --region us-east-1 --write
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "packages" / "axiom" / "config" / "models.yaml"

PROBE_PROMPT = "Reply with exactly one word: ready"

# Candidates per tier, best-first. The cascade in blueprint Part 6.4 expects one working
# model per tier; the first that responds wins.
#
# Ordering note: the open-weight Chinese-lab models (GLM, MiniMax, Qwen, DeepSeek, Kimi)
# are listed first because Anthropic models on this account fail with
# INVALID_PAYMENT_INSTRUMENT — they are billed through AWS Marketplace subscriptions, which
# require a valid payment method on the account. The open-weight families are served
# directly by Bedrock and do not need a Marketplace subscription, so they are the pragmatic
# primary path. Anthropic and Amazon entries are retained as fallbacks so that re-running
# this script after billing is fixed automatically picks up the better models.
CANDIDATES: dict[str, list[str]] = {
    "micro": [
        "zai.glm-4.7-flash",
        "qwen.qwen3-32b-v1:0",
        "qwen.qwen3-coder-30b-a3b-v1:0",
        "amazon.nova-micro-v1:0",
        "amazon.nova-lite-v1:0",
    ],
    "volume": [
        "zai.glm-4.7-flash",
        "qwen.qwen3-next-80b-a3b",
        "qwen.qwen3-32b-v1:0",
        "minimax.minimax-m2",
        "amazon.nova-lite-v1:0",
    ],
    "mid": [
        "zai.glm-4.7",
        "deepseek.v3.2",
        "minimax.minimax-m2.1",
        "minimax.minimax-m2",
        "amazon.nova-pro-v1:0",
        "anthropic.claude-haiku-4-5-20251001-v1:0",
    ],
    "frontier": [
        "zai.glm-5",
        "minimax.minimax-m2.5",
        "moonshotai.kimi-k2.5",
        "moonshot.kimi-k2-thinking",
        "deepseek.r1-v1:0",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
    ],
    "vision": [
        "qwen.qwen3-vl-235b-a22b",
        "amazon.nova-pro-v1:0",
        "amazon.nova-lite-v1:0",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
    ],
}

EMBEDDING_CANDIDATES = [
    "amazon.titan-embed-text-v2:0",
    "cohere.embed-v4:0",
    "amazon.titan-embed-text-v1",
]

GEO_PREFIXES = ("", "us.")


@dataclass
class ProbeResult:
    tier: str
    model_id: str
    resolved_id: str
    ok: bool
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reply: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def probe_converse(client, tier: str, model_id: str) -> ProbeResult:
    """Try the bare ID, then the geo-prefixed inference-profile form."""
    last: ProbeResult | None = None
    for prefix in GEO_PREFIXES:
        resolved = f"{prefix}{model_id}"
        started = time.perf_counter()
        try:
            response = client.converse(
                modelId=resolved,
                messages=[{"role": "user", "content": [{"text": PROBE_PROMPT}]}],
                inferenceConfig={"maxTokens": 16, "temperature": 0.0},
            )
        except ClientError as exc:
            last = ProbeResult(
                tier=tier,
                model_id=model_id,
                resolved_id=resolved,
                ok=False,
                error_code=exc.response.get("Error", {}).get("Code"),
                error_message=exc.response.get("Error", {}).get("Message", "")[:180],
            )
            continue
        except Exception as exc:  # noqa: BLE001 - preflight should never crash the run
            last = ProbeResult(
                tier=tier,
                model_id=model_id,
                resolved_id=resolved,
                ok=False,
                error_code=type(exc).__name__,
                error_message=str(exc)[:180],
            )
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.get("usage", {})
        text = ""
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                text += block["text"]
        return ProbeResult(
            tier=tier,
            model_id=model_id,
            resolved_id=resolved,
            ok=True,
            latency_ms=latency_ms,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            reply=text.strip()[:60],
        )
    assert last is not None
    return last


def probe_embedding(client, model_id: str) -> ProbeResult:
    """Embeddings use InvokeModel, and the request shape differs by provider."""
    if model_id.startswith("cohere."):
        body = {"texts": ["ball valve"], "input_type": "search_document"}
    else:
        body = {"inputText": "ball valve"}
    started = time.perf_counter()
    try:
        response = client.invoke_model(modelId=model_id, body=json.dumps(body))
    except ClientError as exc:
        return ProbeResult(
            tier="embedding",
            model_id=model_id,
            resolved_id=model_id,
            ok=False,
            error_code=exc.response.get("Error", {}).get("Code"),
            error_message=exc.response.get("Error", {}).get("Message", "")[:180],
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    payload = json.loads(response["body"].read())
    vector = payload.get("embedding") or (payload.get("embeddings") or [[]])[0]
    return ProbeResult(
        tier="embedding",
        model_id=model_id,
        resolved_id=model_id,
        ok=True,
        latency_ms=latency_ms,
        reply=f"dim={len(vector)}",
    )


def render_table(results: list[ProbeResult]) -> str:
    rows = [f"{'tier':<10} {'ok':<4} {'ms':>6}  {'in/out':<9} model"]
    rows.append("-" * 88)
    for r in results:
        tokens = (
            f"{r.input_tokens}/{r.output_tokens}"
            if r.input_tokens is not None
            else (r.reply or "")
            if r.ok
            else ""
        )
        mark = "yes" if r.ok else "NO"
        latency = f"{r.latency_ms}" if r.latency_ms is not None else "-"
        rows.append(f"{r.tier:<10} {mark:<4} {latency:>6}  {tokens:<9} {r.resolved_id}")
        if not r.ok:
            rows.append(f"{'':<10} {'':<4} {'':>6}  -> {r.error_code}: {r.error_message}")
    return "\n".join(rows)


def write_config(winners: dict[str, ProbeResult], region: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Pinned Bedrock model IDs — GENERATED by scripts/preflight_bedrock.py.",
        "# Do not hand-edit; re-run the script instead. Every ID here was verified with a",
        "# live Converse/InvokeModel call, not merely found in list-foundation-models.",
        f"region: {region}",
        "tiers:",
    ]
    for tier in ("micro", "volume", "mid", "frontier", "vision"):
        result = winners.get(tier)
        if result is None:
            lines.append(f"  {tier}: null   # no candidate was reachable")
            continue
        lines.append(f"  {tier}:")
        lines.append(f"    model_id: {result.resolved_id}")
        lines.append(f"    probe_latency_ms: {result.latency_ms}")
    embed = winners.get("embedding")
    lines.append("embedding:")
    if embed is None:
        lines.append("  model_id: null   # no candidate was reachable")
    else:
        lines.append(f"  model_id: {embed.resolved_id}")
        lines.append(f"  probe_result: {embed.reply}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--write", action="store_true", help="write packages/axiom/config/models.yaml"
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    print(f"account {identity['Account']} as {identity['Arn'].split('/')[-1]} in {args.region}\n")

    cfg = Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=45)
    runtime = session.client("bedrock-runtime", config=cfg)

    results: list[ProbeResult] = []
    winners: dict[str, ProbeResult] = {}

    for tier, candidates in CANDIDATES.items():
        for model_id in candidates:
            result = probe_converse(runtime, tier, model_id)
            results.append(result)
            if result.ok:
                winners[tier] = result
                break  # first working candidate wins the tier

    for model_id in EMBEDDING_CANDIDATES:
        result = probe_embedding(runtime, model_id)
        results.append(result)
        if result.ok:
            winners["embedding"] = result
            break

    print(render_table(results))

    missing = [t for t in (*CANDIDATES, "embedding") if t not in winners]
    print()
    if missing:
        print(f"UNRESOLVED TIERS: {', '.join(missing)}")
        print("Enable model access: Bedrock console -> Model access -> Modify model access.")
    else:
        print("All tiers resolved.")

    if args.write:
        # Guard: never clobber a good pinned config with an all-null one. A run where
        # nothing resolved is almost always an account/billing problem, not a signal that
        # the previously-working models have gone away.
        if not winners:
            print(
                f"NOT writing {CONFIG_PATH.name}: no tier resolved, so the existing config "
                f"(if any) is more useful than an empty one."
            )
        else:
            write_config(winners, args.region)
            print(f"wrote {CONFIG_PATH.relative_to(REPO_ROOT)}")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
