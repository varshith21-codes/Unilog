"""Negative control: ask for a product the document does not describe.

The ablation in ``run_ablation.py`` came back null — on clean, text-extractable datasheets the
evidence contract never fires, because the model does not invent quotes. That is a real result
and a weak test. It says nothing about the failure that actually costs a distributor money.

This is the harder question. Supply the **wrong document**: ask for the ball valve BA-100-075
while handing over the gate valve datasheet. Nothing about that part number appears in the
source, so the only correct behaviour is to abstain on every attribute.

What makes this worth running separately is that **quote verification cannot catch this
failure**. A model that answers "Bronze" for body material can cite the gate valve datasheet
verbatim, and the quote will verify, because the word really is there. The value is still wrong,
because it describes a different product. So this measures something the evidence contract does
not protect against — target-SKU discipline in the prompt — and knowing where the guarantee ends
is more useful than another run that confirms where it holds.

Scoring is deliberately blunt. Every value returned is a defect:

*   **abstained** — a gap was recorded. Correct.
*   **fabricated** — a value was returned. Wrong product attribution, whether or not it carries
    a verifiable quote.

    $env:AWS_PROFILE = "axiom"
    python scripts/run_adversarial.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

from axiom.docintel import parse_artifact  # noqa: E402
from axiom.extract import BedrockModelClient, Extractor, ModelCascade, PriceTable  # noqa: E402
from axiom.ingest import LocalArtifactStore, ingest_file  # noqa: E402
from axiom.schema import load_default  # noqa: E402

STORE = REPO_ROOT / "data" / "cache" / "artifacts"
SAMPLES = REPO_ROOT / "data" / "samples"

# (label, document, target sku, class code, why this pairing is adversarial)
CASES = [
    (
        "ball valve SKU against the gate valve datasheet",
        SAMPLES / "gv200.txt",
        "BA-100-075",
        "PLB.VLV.BALL.2PC",
        "gv200 describes NIBCO bronze gate valves; BA-100-075 is a Milwaukee ball valve and "
        "appears nowhere in it",
    ),
    (
        "gate valve SKU against the ball valve datasheet",
        SAMPLES / "ba100.txt",
        "T-113-100",
        "PLB.VLV.GATE.BRZ",
        "ba100 describes Milwaukee ball valves; T-113-100 is a NIBCO gate valve and appears "
        "nowhere in it",
    ),
    (
        "nonexistent part number in the right family",
        SAMPLES / "ba100.txt",
        "BA-100-999",
        "PLB.VLV.BALL.2PC",
        "the BA-100 series is described but there is no -999 size; shared family specs are the "
        "tempting wrong answer",
    ),
    # The hardest case in this file, and the only one the targeting gate cannot answer.
    #
    # 77C-102 *is* in the document. It appears exactly once, in a note declaring it discontinued
    # and superseded. So the part number is found, the gate opens, and a model call is made — the
    # protection that handles the three cases above does not apply here. Every shared
    # specification in the prose (alloy, pressure, seats, temperature, approvals) is sitting
    # right there and reads as though it applies. Answering from it produces a full, confident,
    # perfectly-cited record for a part that cannot be bought.
    (
        "discontinued part that the document does mention",
        SAMPLES / "ap77c.pdf",
        "77C-102",
        "PLB.VLV.BALL.2PC",
        "77C-102 appears only in NOTE 2, which withdraws it. The targeting gate finds the part "
        "number and opens, so abstention has to come from reading what the note says",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="volume")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = load_default()
    cascade = ModelCascade.load()
    client = BedrockModelClient(region=cascade.region, profile=args.profile)
    store = LocalArtifactStore(STORE)
    extractor = Extractor(registry, client, cascade, start_tier=args.tier)

    prices = PriceTable.load()
    tier_prices = prices.tier_prices(cascade) if prices else None

    print(f"{len(CASES)} adversarial case(s). Every returned value is a defect.\n")

    results = []
    for label, document, sku, class_code, rationale in CASES:
        if not document.is_file():
            print(f"  SKIP {label}: {document} not found", file=sys.stderr)
            continue

        artifact = ingest_file(document, store)
        parsed = parse_artifact(store.get(artifact.storage_uri), artifact.document)

        extraction = extractor.extract(
            parsed, class_code=class_code, target_sku=sku, include_optional=True
        )

        fabricated = [
            {
                "attribute_code": value.attribute_code,
                "value_raw": value.value_raw,
                "quote": value.evidence[0].quote if value.evidence else None,
                # The damning combination: a wrong value whose citation checks out.
                "quote_verified": bool(
                    value.evidence and value.evidence[0].quote_verified
                ),
            }
            for value in extraction.values
        ]

        presence = extraction.sku_presence
        case = {
            "case": label,
            "document": document.name,
            "target_sku": sku,
            "class_code": class_code,
            "rationale": rationale,
            "requested": len(extraction.requested_codes),
            "abstained": len(extraction.gaps),
            # Which gate refused, so a clean run says *why* it was clean rather than asserting a
            # single mechanism for cases that are stopped by different ones.
            "refusal": (
                "sku_absent"
                if presence is not None and presence.is_absent
                else "sku_withdrawn"
                if presence is not None and presence.is_not_offered
                else None
            ),
            "withdrawal_quote": presence.withdrawal_quote if presence else None,
            "fabricated": len(fabricated),
            "fabricated_with_verified_quote": sum(
                1 for f in fabricated if f["quote_verified"]
            ),
            "values": fabricated,
            "cost_usd": extraction.usage.cost_usd(tier_prices),
        }
        results.append(case)
        _report_case(case)

    _report_totals(results)

    if args.json:
        print("\n" + json.dumps(results, indent=2))
    if args.out:
        target = args.out if args.out.is_absolute() else Path.cwd() / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {target}")

    # Non-zero when anything was fabricated, so this can gate a build.
    return 1 if any(case["fabricated"] for case in results) else 0


def _report_case(case: dict) -> None:
    print("=" * 78)
    print(f"{case['case']}")
    print("=" * 78)
    print(f"  document       {case['document']}")
    print(f"  asked for      {case['target_sku']} ({case['class_code']})")
    print(f"  why            {case['rationale']}")
    print(f"  requested      {case['requested']} attributes")
    print(f"  abstained      {case['abstained']}")

    if case["fabricated"] == 0:
        if case["refusal"] == "sku_withdrawn":
            print("  fabricated     0  <- correct: the source withdraws this part")
            print(f"    withdrawal note: {(case['withdrawal_quote'] or '')[:100]!r}")
        elif case["refusal"] == "sku_absent":
            print("  fabricated     0  <- correct: the document describes a different product")
        else:
            print("  fabricated     0  <- correct: nothing was returned")
    else:
        print(f"  fabricated     {case['fabricated']}  <- WRONG PRODUCT ATTRIBUTION")
        print(
            f"    of which with a verifiable quote: "
            f"{case['fabricated_with_verified_quote']}"
        )
        for value in case["values"][:8]:
            mark = "verified" if value["quote_verified"] else "unverified"
            print(f"      {value['attribute_code']:<26} {value['value_raw']!r}  [{mark}]")
            if value["quote"]:
                print(f"        quote: {value['quote'][:90]!r}")
    print()


def _report_totals(results: list[dict]) -> None:
    if not results:
        return

    fabricated = sum(case["fabricated"] for case in results)
    verified_fabrications = sum(case["fabricated_with_verified_quote"] for case in results)
    requested = sum(case["requested"] for case in results)

    print("=" * 78)
    print("TOTAL")
    print("=" * 78)
    print(f"  attributes requested across all cases   {requested}")
    print(f"  fabricated                              {fabricated}")
    print(f"  fabricated with a verifiable quote      {verified_fabrications}")

    if fabricated == 0:
        withdrawn = sum(1 for case in results if case["refusal"] == "sku_withdrawn")
        print(
            "\n  The extractor abstained on every attribute of every case, and made no model"
            "\n  call in any of them."
            "\n"
            "\n  Two different gates produce that, and it is worth keeping them apart."
            "\n"
            "\n  Absent part number: the SKU is looked for in the parsed document first, and"
            "\n  when it is nowhere present the document is not about this product. Quote"
            "\n  verification could never have caught this — a value copied from the wrong"
            "\n  product's datasheet cites that datasheet perfectly well. Before this gate"
            "\n  existed the same harness reported 39 fabrications out of 64, every one with a"
            "\n  verifiable quote."
        )
        if withdrawn:
            print(
                "\n  Withdrawn part number: the SKU *is* present, so the check above passes and"
                "\n  a model call would ordinarily be made. It appears only in a note retiring"
                "\n  it, while every shared specification in the surrounding prose reads as"
                "\n  though it applies. That case produced 12 values with 12 verifiable quotes"
                "\n  for a part that cannot be bought, which is the most convincing wrong answer"
                "\n  this system has produced. Withdrawal phrasing is declared in"
                "\n  schema/constants.yaml (WITHDRAWAL_MARKERS); an ordering-table row overrides"
                "\n  it, so the replacement part named inside the note stays extractable."
            )
    else:
        print(
            f"\n  {fabricated} value(s) were attributed to a product the source does not"
            "\n  describe."
        )
        if verified_fabrications:
            print(
                f"  {verified_fabrications} of them carry a *verifiable* quote, which is the"
                "\n  important part: the evidence contract passed them. Wrong-product"
                "\n  attribution is not a citation problem and cannot be fixed by checking"
                "\n  quotes harder. Check that the targeting gate is enabled"
                "\n  (Extractor(require_sku_in_document=True), the default)."
            )

    costs = [case["cost_usd"] for case in results if case["cost_usd"] is not None]
    if costs:
        print(f"\n  cost of this run  ${sum(costs):.6f}")


if __name__ == "__main__":
    raise SystemExit(main())
