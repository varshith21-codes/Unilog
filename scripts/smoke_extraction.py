"""Smoke test: does the schema-generated prompt actually work against a real model?

Connectivity is not capability. The preflight proves a model responds; this proves the two
things the architecture depends on:

1. **The prompt is generated from the schema.** This script contains no hand-written
   attribute descriptions. It loads ``schema/`` and calls ``build_extraction_prompt``, which
   is the same code path the pipeline uses. If the YAML changes, this test changes with it.
2. **The evidence contract holds.** Strict JSON, verbatim quotes that can be mechanically
   located in the source, and — hardest of all — correct abstention.

The fixture is built around traps that a naive extractor fails:

* ``operating_torque`` is stated for the 1/2" size only, and the target SKU is 3/4". The
  schema tells the model not to assume it transfers. Correct answer: abstain.
* ``lead_free_compliant`` must not be inferred from the NSF/ANSI 61 listing. 61 is potable
  water; 372 is lead content. Different standards. Correct answer: abstain.
* ``cv_flow_coefficient`` is deferred with "Consult factory". Correct answer: abstain.
* ``handle_type`` is "Lever" in the ordering row, and a footnote substitutes a locking lever
  for 1" and larger — which does not apply to 3/4".
* Six further attributes are absent entirely.

Run:
    python scripts/smoke_extraction.py                 # mid tier
    python scripts/smoke_extraction.py --all-tiers     # compare across the cascade
    python scripts/smoke_extraction.py --show-prompt   # inspect what the schema generated
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import yaml
from axiom.schema import build_extraction_prompt, load_default
from botocore.config import Config
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "packages" / "axiom" / "config" / "models.yaml"

CLASS_CODE = "PLB.VLV.BALL.2PC"
TARGET_SKU = "BA-100-075"
SOURCE_NAME = "milwaukee-ba100-series.pdf"

SOURCE_TEXT = """\
MILWAUKEE VALVE - BA-100 SERIES
Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08

SPECIFICATIONS
  Body Material .................. Bronze C84400
  Ball / Stem .................... Chrome-plated brass / Brass
  Seat Material .................. RPTFE
  Pressure Rating ................ 600 PSI WOG @ 73 degF
  Steam Rating ................... 150 PSI WSP
  Temperature Range .............. -20 degF to 366 degF
  End Connection ................. NPT threaded, female both ends
  Approvals ...................... UL listed, CSA certified, NSF/ANSI 61
  Operating Torque ............... 18-22 ft-lb (1/2" size)
  Flow Coefficient (Cv) .......... Consult factory

ORDERING INFORMATION
  Part Number      Size        Handle       Carton Qty
  BA-100-025       1/4"        Lever        24
  BA-100-050       1/2"        Lever        24
  BA-100-075       3/4"        Lever        12
  BA-100-100       1"          Lever        12
  BA-100-125       1-1/4"      Tee          6

  NOTE 1: Sizes 1" and larger are supplied with a locking lever handle.
  NOTE 2: Pressure rating derates above 100 degF. See derating chart, page 7.
"""

# Ground truth. None means "the correct answer is to abstain".
# Keyed by the real schema attribute codes — if a code is renamed in YAML, this test breaks,
# which is the desired coupling.
EXPECTED: dict[str, str | None] = {
    # plainly present in the specification block
    "body_material": "Bronze C84400",
    "pressure_rating_wog": "600 PSI",
    "steam_pressure_rating": "150 PSI",
    "seat_material": "RPTFE",
    "end_connection": "NPT",
    "temperature_range": "-20",
    "approvals": "UL",
    # present, but only in the ordering-table row for the target SKU
    "nominal_size": '3/4"',
    "case_quantity": "12",
    "handle_type": "Lever",
    # present in the document title
    "product_series": "BA-100",
    "number_of_pieces": "Two-Piece",
    "port_type": "Full Port",
    # --- the traps: correct answer is abstention ---
    "cv_flow_coefficient": None,   # "Consult factory"
    "operating_torque": None,      # stated for 1/2" only; target is 3/4"
    "lead_free_compliant": None,   # NSF 61 is not NSF 372; must not be conflated
    "country_of_origin": None,
    "gtin": None,
    "each_weight": None,
    "case_weight": None,
    "selling_uom": None,
    # genuinely ambiguous: "Ball / Stem ... Chrome-plated brass / Brass" is a paired value.
    # Scored informationally rather than pass/fail, because reasonable humans disagree.
    "stem_material": "Brass",
    "potable_water_approved": "true",
}

INFORMATIONAL = frozenset({"stem_material"})
"""Scored and displayed, but excluded from the pass/fail verdict.

``stem_material`` stays here because "Ball / Stem ... Chrome-plated brass / Brass" is a
paired value where reasonable humans disagree about which half is the stem.
``potable_water_approved`` was previously here too, because the model returned the citation
text instead of a boolean — that turned out to be a missing instruction in the schema
rather than a model limitation, so it is now graded.
"""

# (informational, correct) -> display marker
_MARKS = {(False, True): "yes", (False, False): "NO", (True, True): "i", (True, False): "i!"}


@dataclass
class AttributeOutcome:
    code: str
    found: bool
    value_raw: str | None
    quote: str | None
    quote_verified: bool
    match_score: float
    expected: str | None
    correct: bool
    informational: bool
    failure: str | None = None


def load_models() -> tuple[str, dict[str, str]]:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"{CONFIG_PATH.relative_to(REPO_ROOT)} not found. "
            f"Run scripts/preflight_bedrock.py --write first."
        )
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    tiers = {
        name: spec["model_id"]
        for name, spec in (cfg.get("tiers") or {}).items()
        if spec and spec.get("model_id")
    }
    return cfg.get("region", "us-east-2"), tiers


def extract_json_array(text: str) -> list[dict]:
    """Parse a JSON array, tolerating fences and leading prose.

    Being liberal is deliberate: a fence around valid JSON is a formatting nuisance, not a
    correctness problem. Genuinely invalid JSON still raises, because that is a real finding.
    """
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*]", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if isinstance(parsed, dict):
        for key in ("attributes", "results", "data"):
            if key in parsed:
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
    return parsed


def verify_quote(quote: str | None, source: str) -> tuple[bool, float]:
    """Locate a quote in the source: exact substring, then fuzzy against a single line.

    Whitespace is normalised first because PDF text extraction routinely collapses runs of
    spaces, and penalising a model for that is noise rather than signal.
    """
    if not quote:
        return False, 0.0
    needle, haystack = _squash(quote), _squash(source)
    if needle and needle in haystack:
        return True, 1.0
    best = 0.0
    for line in source.splitlines():
        line_norm = _squash(line)
        if line_norm:
            best = max(best, difflib.SequenceMatcher(None, needle, line_norm).ratio())
    return best >= 0.90, round(best, 3)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def value_matches(actual: str | None, expected: str) -> bool:
    """Lenient: this scores extraction fidelity, not normalization."""
    if actual is None:
        return False
    strip = lambda s: re.sub(r"[\s\"'/,]+", "", s).lower()  # noqa: E731
    a, e = strip(actual), strip(expected)
    return a == e or e in a or a in e


def score(raw_items: list[dict], requested: tuple[str, ...]) -> list[AttributeOutcome]:
    by_code = {i.get("attribute_code"): i for i in raw_items if isinstance(i, dict)}
    outcomes: list[AttributeOutcome] = []
    for code in requested:
        if code not in EXPECTED:
            continue  # schema grew but this fixture has no ground truth for it yet
        expected = EXPECTED[code]
        informational = code in INFORMATIONAL
        item = by_code.get(code)

        if item is None:
            outcomes.append(
                AttributeOutcome(
                    code, False, None, None, False, 0.0, expected,
                    correct=expected is None,
                    informational=informational,
                    failure=None if expected is None else "omitted from response",
                )
            )
            continue

        found = bool(item.get("found"))
        value_raw = item.get("value_raw")
        if isinstance(value_raw, list):
            value_raw = ", ".join(str(v) for v in value_raw)
        elif value_raw is not None and not isinstance(value_raw, str):
            value_raw = str(value_raw)
        quote = item.get("evidence_quote")
        verified, match_score = verify_quote(quote, SOURCE_TEXT) if found else (False, 0.0)

        failure: str | None = None
        if expected is None:
            correct = not found
            if found:
                failure = f"WRONGLY REPORTED {value_raw!r} - source does not support this"
        elif not found:
            correct = False
            failure = "abstained on a value that IS present"
        elif not value_matches(value_raw, expected):
            correct = False
            failure = f"wrong value: got {value_raw!r}, expected ~{expected!r}"
        elif not verified:
            correct = False
            failure = f"quote not locatable in source (best match {match_score})"
        else:
            correct = True

        outcomes.append(
            AttributeOutcome(
                code, found, value_raw, quote, verified, match_score, expected,
                correct, informational, failure,
            )
        )
    return outcomes


def run_tier(client, tier: str, model_id: str, prompt) -> dict:
    started = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": prompt.system}],
            messages=[{"role": "user", "content": [{"text": prompt.user_message}]}],
            inferenceConfig={"maxTokens": 4000, "temperature": 0.0},
        )
    except ClientError as exc:
        err = exc.response.get("Error", {})
        return {
            "tier": tier,
            "model_id": model_id,
            "error": f"{err.get('Code')}: {err.get('Message', '')[:140]}",
        }

    usage = response.get("usage", {})
    text = "".join(
        b.get("text", "")
        for b in response.get("output", {}).get("message", {}).get("content", [])
    )
    result: dict = {
        "tier": tier,
        "model_id": model_id,
        "latency_s": round(time.perf_counter() - started, 2),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "raw_text": text,
    }
    try:
        items = extract_json_array(text)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"unparseable JSON: {exc}"
        return result
    result["clean_json"] = text.strip().startswith("[")
    result["outcomes"] = score(items, prompt.attribute_codes)
    return result


def report(result: dict) -> bool:
    print(f"\n{'=' * 80}\n{result['tier'].upper():<10} {result['model_id']}\n{'=' * 80}")
    if "error" in result:
        print(f"  FAILED: {result['error']}")
        if result.get("raw_text"):
            print(f"  raw (first 300): {result['raw_text'][:300]!r}")
        return False

    outcomes: list[AttributeOutcome] = result["outcomes"]
    graded = [o for o in outcomes if not o.informational]
    present = [o for o in graded if o.expected is not None]
    absent = [o for o in graded if o.expected is None]

    print(
        f"  latency {result['latency_s']}s | tokens {result['input_tokens']}"
        f"/{result['output_tokens']} | JSON: {'clean' if result['clean_json'] else 'fenced'}"
    )
    print(f"\n  {'attribute':<24} {'ok':<4} {'quote':<7} value")
    print(f"  {'-' * 74}")
    for o in outcomes:
        mark = _MARKS[(o.informational, o.correct)]
        q = "ok" if o.quote_verified else ("BAD" if o.found else "-")
        shown = (o.value_raw if o.found else "(abstained)") or ""
        print(f"  {o.code:<24} {mark:<4} {q:<7} {shown[:36]}")
        if o.failure and not o.informational:
            print(f"  {'':<24} -> {o.failure}")

    hallucinations = [o for o in absent if o.found]
    quote_fail = [o for o in graded if o.found and not o.quote_verified]
    n_present_ok = sum(1 for o in present if o.correct)

    print(f"\n  present values correct : {n_present_ok}/{len(present)}")
    print(f"  correct abstentions    : {sum(1 for o in absent if o.correct)}/{len(absent)}")
    print(f"  hallucinations         : {len(hallucinations)}")
    print(f"  unverifiable quotes    : {len(quote_fail)}")
    print(f"  (informational, ungraded: {sum(1 for o in outcomes if o.informational)})")

    passed = not hallucinations and not quote_fail and n_present_ok == len(present)
    print(f"\n  VERDICT: {'PASS' if passed else 'FAIL'}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="mid")
    parser.add_argument("--all-tiers", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--show-raw", action="store_true")
    args = parser.parse_args()

    # The prompt comes from the schema. No attribute text is written in this file.
    registry = load_default()
    prompt = build_extraction_prompt(
        registry,
        CLASS_CODE,
        source_content=SOURCE_TEXT,
        target_sku=TARGET_SKU,
        source_name=SOURCE_NAME,
        include_optional=True,  # so the operating_torque and case_weight traps are exercised
    )

    if args.show_prompt:
        print(f"--- system ---\n{prompt.system}")
        print(f"--- cacheable prefix ({len(prompt.cacheable_prefix)} chars) ---")
        print(prompt.cacheable_prefix)
        print(f"--- volatile suffix ---\n{prompt.volatile_suffix}\n")

    region, tiers = load_models()
    session = boto3.Session(profile_name=args.profile, region_name=region)
    client = session.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=180),
    )

    selected = ["volume", "mid", "frontier"] if args.all_tiers else [args.tier]
    selected = [t for t in selected if t in tiers]
    if not selected:
        raise SystemExit(f"no usable tier; available: {sorted(tiers)}")

    covered = [c for c in prompt.attribute_codes if c in EXPECTED]
    print(
        f"region {region} | class {CLASS_CODE} | SKU {TARGET_SKU}\n"
        f"prompt generated from schema: {len(prompt.attribute_codes)} attributes requested, "
        f"{len(covered)} with ground truth | cache key {prompt.cache_key()}"
    )

    all_passed = True
    for tier in selected:
        result = run_tier(client, tier, tiers[tier], prompt)
        all_passed &= report(result)
        if args.show_raw and result.get("raw_text"):
            print(f"\n--- raw ---\n{result['raw_text']}\n")

    print(f"\n{'=' * 80}\nOVERALL: {'PASS' if all_passed else 'FAIL'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
