"""Export real pipeline output as JSON fixtures for the Next.js console.

The console has no HTTP API to talk to yet (see blueprint Part 6 — ``apps/api`` is not
built). Rather than hand-writing mock data and inventing shapes the backend does not
produce, this script drives the **actual pipeline** — ingest, parse, classify, extract,
normalize, validate, score, certify — and serialises the result.

Every field the console renders therefore originates from a real Pydantic model. When
``apps/api`` lands, the console swaps its data source and the shapes do not move.

Two things here are deliberately synthetic, and both are labelled in the output:

*   **The model response.** ``StubModelClient`` is seeded with an evidence contract whose
    quotes are copied verbatim from ``data/samples/ba100.txt``. Quote verification, span
    location and bbox resolution all run for real against the parsed document, so an
    unverifiable quote would still be rejected here.
*   **The calibration set.** No reviewer outcomes exist yet, so the risk-controlled
    threshold has nothing to learn from. A synthetic calibration set is supplied purely so
    the acceptance policy produces a realistic mix of states for the UI to render. The
    output records ``policy_source: synthetic-dev-calibration`` so this is never mistaken
    for a trained model.

Usage::

    python scripts/export_console_fixture.py
    python scripts/export_console_fixture.py --out apps/console/src/data
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

import axiom  # noqa: E402
from axiom.classify import Classifier  # noqa: E402
from axiom.confidence import (  # noqa: E402
    DEFAULT_EPSILON,
    Calibrator,
    Priors,
    apply_policy,
    extract_features,
    select_threshold,
)
from axiom.console import (  # noqa: E402
    build_bundle,
    build_dataset,
    dataset_stats,
    jsonable,
    serialise_class,
    serialise_document,
    serialise_pages,
)
from axiom.core.certificate import build_certificate  # noqa: E402
from axiom.core.product import ProductRecord  # noqa: E402
from axiom.docintel import parse_artifact  # noqa: E402
from axiom.extract import Extractor, ModelCascade, StubModelClient  # noqa: E402
from axiom.ingest import LocalArtifactStore, ingest_file  # noqa: E402
from axiom.normalize import BrandMaster, clean_mpn, normalize_all  # noqa: E402
from axiom.schema import load_default  # noqa: E402
from axiom.syndicate import export_all  # noqa: E402
from axiom.validate import Validator  # noqa: E402

CLASS_CODE = "PLB.VLV.BALL.2PC"
TENANT = "demo"
SUPPLIER = "milwaukee"
BRAND_RAW = "Milwaukee Valve"
SAMPLE = REPO_ROOT / "data" / "samples" / "ba100.txt"
DEFAULT_OUT = REPO_ROOT / "apps" / "console" / "src" / "data"
ARTIFACT_STORE = REPO_ROOT / "data" / "cache" / "artifacts"

# --------------------------------------------------------------------------------------
# Verbatim source lines. Copied character-for-character out of data/samples/ba100.txt so
# that quote verification genuinely resolves them to a page and a bounding box. Changing
# the sample datasheet without changing these will (correctly) produce unverified spans.
# --------------------------------------------------------------------------------------
Q_TITLE = "MILWAUKEE VALVE - BA-100 SERIES"
Q_SUBTITLE = "Two-Piece Full Port Bronze Ball Valve                          Rev C  2024-08"
Q_BODY = "Body Material .................. Bronze C84400"
Q_BALL_STEM = "Ball / Stem .................... Chrome-plated brass / Brass"
Q_SEAT = "Seat Material .................. RPTFE"
Q_PRESSURE = "Pressure Rating ................ 600 PSI WOG @ 73 degF"
Q_STEAM = "Steam Rating ................... 150 PSI WSP"
Q_TEMP = "Temperature Range .............. -20 degF to 366 degF"
Q_END = "End Connection ................. NPT threaded, female both ends"
Q_APPROVALS = "Approvals ...................... UL listed, CSA certified, NSF/ANSI 61"
Q_TORQUE = 'Operating Torque ............... 18-22 ft-lb (1/2" size)'
Q_CV = "Flow Coefficient (Cv) .......... Consult factory"

# One row per orderable SKU, quoted verbatim from the ORDERING INFORMATION table.
ROWS: dict[str, dict[str, str]] = {
    "BA-100-025": {"quote": 'BA-100-025       1/4"        Lever        24',
                   "size": '1/4"', "handle": "Lever", "carton": "24"},
    "BA-100-050": {"quote": 'BA-100-050       1/2"        Lever        24',
                   "size": '1/2"', "handle": "Lever", "carton": "24"},
    "BA-100-075": {"quote": 'BA-100-075       3/4"        Lever        12',
                   "size": '3/4"', "handle": "Lever", "carton": "12"},
    "BA-100-100": {"quote": 'BA-100-100       1"          Lever        12',
                   "size": '1"', "handle": "Lever", "carton": "12"},
    "BA-100-125": {"quote": 'BA-100-125       1-1/4"      Tee          6',
                   "size": '1-1/4"', "handle": "Tee", "carton": "6"},
}


def contract_for(sku: str) -> list[dict[str, Any]]:
    """The evidence contract a competent extraction of this datasheet would return.

    Abstentions are as important as values here: they exercise the three distinct gap
    reasons the console has to render differently (deferred, applicability, absent).
    """
    row = ROWS[sku]
    torque_applies = row["size"] == '1/2"'

    items: list[dict[str, Any]] = [
        {"attribute_code": "product_series", "found": True, "value_raw": "BA-100",
         "evidence_quote": Q_TITLE, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "number_of_pieces", "found": True, "value_raw": "2",
         "evidence_quote": Q_SUBTITLE, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "nominal_size", "found": True, "value_raw": row["size"],
         "evidence_quote": row["quote"], "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "pressure_rating_wog", "found": True, "value_raw": "600 PSI WOG",
         "evidence_quote": Q_PRESSURE, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "body_material", "found": True, "value_raw": "Bronze C84400",
         "evidence_quote": Q_BODY, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "end_connection", "found": True, "value_raw": "NPT threaded",
         "evidence_quote": Q_END, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "port_type", "found": True, "value_raw": "Full Port",
         "evidence_quote": Q_SUBTITLE, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "seat_material", "found": True, "value_raw": "RPTFE",
         "evidence_quote": Q_SEAT, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "stem_material", "found": True, "value_raw": "Brass",
         "evidence_quote": Q_BALL_STEM, "evidence_page": 1, "certainty": "medium"},
        {"attribute_code": "handle_type", "found": True, "value_raw": row["handle"],
         "evidence_quote": row["quote"], "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "temperature_range", "found": True,
         "value_raw": "-20 degF to 366 degF",
         "evidence_quote": Q_TEMP, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "steam_pressure_rating", "found": True, "value_raw": "150 PSI WSP",
         "evidence_quote": Q_STEAM, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "approvals", "found": True,
         "value_raw": "UL listed, CSA certified, NSF/ANSI 61",
         "evidence_quote": Q_APPROVALS, "evidence_page": 1, "certainty": "high"},
        {"attribute_code": "potable_water_approved", "found": True, "value_raw": "true",
         "evidence_quote": Q_APPROVALS, "evidence_page": 1, "certainty": "medium"},
        {"attribute_code": "case_quantity", "found": True, "value_raw": row["carton"],
         "evidence_quote": row["quote"], "evidence_page": 1, "certainty": "high"},

        # --- abstentions, one per distinct reason class -------------------------------
        {"attribute_code": "cv_flow_coefficient", "found": False,
         "reason": "The datasheet states 'Consult factory' rather than a Cv value.",
         "certainty": "low"},
        {"attribute_code": "operating_torque", "found": torque_applies,
         "value_raw": "18-22 ft-lb" if torque_applies else None,
         "evidence_quote": Q_TORQUE if torque_applies else None,
         "evidence_page": 1 if torque_applies else None,
         "reason": None if torque_applies else
         'Torque is stated only for the 1/2" size and does not apply to this part number.',
         "certainty": "medium"},
        {"attribute_code": "lead_free_compliant", "found": False,
         "reason": "No lead-free certification or declaration appears in this document.",
         "certainty": "low"},
        {"attribute_code": "country_of_origin", "found": False,
         "reason": "Country of origin is not stated anywhere in this document.",
         "certainty": "low"},
        {"attribute_code": "gtin", "found": False,
         "reason": "No GTIN, UPC or EAN appears in this document.", "certainty": "low"},
        {"attribute_code": "each_weight", "found": False,
         "reason": "Unit weight is not stated; only carton quantity is given.",
         "certainty": "low"},
        {"attribute_code": "case_weight", "found": False,
         "reason": "Carton weight is not stated.", "certainty": "low"},
        {"attribute_code": "selling_uom", "found": False,
         "reason": "Selling unit of measure is not stated on the datasheet.",
         "certainty": "low"},
    ]
    return items


def synthetic_calibration(seed: int = 7) -> tuple[list[float], list[int]]:
    """A stand-in calibration set so the acceptance policy has a validated threshold.

    Not trained on anything. Its only job is to let ``select_threshold`` return an
    achievable policy so the console renders the full range of decision states instead of
    the cold-start case where everything queues. Labelled as synthetic in the output.
    """
    rng = random.Random(seed)
    scores: list[float] = []
    labels: list[int] = []
    for _ in range(2000):
        score = rng.betavariate(5, 2)
        # Accuracy rises sharply with score, so a high-precision region exists at the top of
        # the range. Without one, no threshold can satisfy a 2% error budget and the policy
        # correctly refuses to authorise any automation.
        error_rate = 0.40 * (1.0 - score) ** 2.2
        correct = rng.random() > error_rate
        scores.append(round(score, 4))
        labels.append(int(correct))
    return scores, labels


def run_sku(sku: str, *, registry, parsed, artifact, policy, calibrator, priors) -> dict[str, Any]:
    """Drive the real pipeline for one SKU and project the result for the console."""
    cascade = ModelCascade.load()
    payload = StubModelClient.json_payload(contract_for(sku))
    # First response is consumed by the classifier; the rest cover extraction plus any
    # tier escalation the cascade decides to perform.
    client = StubModelClient(["[]", payload, payload, payload])

    classifier = Classifier(registry, client=client, cascade=cascade, tier="volume")
    classification = classifier.classify(parsed.full_text, sku=sku)
    class_code = classification.class_code or CLASS_CODE

    extractor = Extractor(registry, client, cascade, start_tier="volume")
    result = extractor.extract(
        parsed, class_code=class_code, target_sku=sku, include_optional=True
    )

    normalized, norm_issues = normalize_all(result.values, registry)

    brands = BrandMaster.load()
    brand = brands.resolve(BRAND_RAW)
    record = ProductRecord(
        tenant_id=TENANT,
        sku=sku,
        mpn=sku,
        mpn_normalized=clean_mpn(sku, brand=brand.brand if brand.resolved else None),
        brand=brand.brand.name if brand.resolved else BRAND_RAW,
        brand_id=brand.brand.brand_id if brand.resolved else None,
        supplier_id=SUPPLIER,
        class_code=class_code,
        schema_version=result.schema_version,
        source_document_ids=[artifact.document.document_id],
    )
    record.classifications.extend(classification.classifications)
    for value in normalized:
        record.add_value(value)
    for gap in result.gaps:
        record.add_gap(gap)

    report = Validator(registry).validate(record)
    for value in record.current_values():
        findings = report.per_attribute.get(value.attribute_code, [])
        if findings:
            value.validations = [*value.validations, *findings]

    scores: dict[str, float] = {}
    features_by_code: dict[str, dict[str, float]] = {}
    for value in record.current_values():
        features = extract_features(value, priors=priors, supplier_id=SUPPLIER)
        features_by_code[value.attribute_code] = features.explain()
        scores[value.attribute_code] = calibrator.predict(features)

    decisions = apply_policy(record.current_values(), scores, policy)

    # No cost is reported here, deliberately. The token counts come from StubModelClient, so
    # pricing them would produce a real-looking dollar figure derived from fabricated usage —
    # precisely the kind of confident-but-meaningless number the rest of this system refuses to
    # emit. The console renders "no cost recorded" instead, and the live API path carries the
    # real figure. See scripts/fetch_bedrock_prices.py.
    certificate = build_certificate(
        record,
        required_attribute_codes=registry.required_codes(class_code),
        pipeline_version=f"axiom-{axiom.__version__}",
        cost_usd=None,
        wall_clock_seconds=round(result.usage.latency_ms / 1000, 2),
    )

    return build_bundle(
        registry=registry,
        record=record,
        artifact=artifact,
        classification=classification,
        extraction=result,
        normalization_issues=norm_issues,
        validation=report,
        scores=scores,
        features=features_by_code,
        decisions=decisions,
        certificate=certificate,
        exports=export_all(record, registry),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sample", type=Path, default=SAMPLE)
    args = parser.parse_args()

    if not args.sample.is_file():
        print(f"sample not found: {args.sample}", file=sys.stderr)
        return 2

    registry = load_default()

    store = LocalArtifactStore(ARTIFACT_STORE)
    artifact = ingest_file(args.sample, store, supplier_id=SUPPLIER)
    parsed = parse_artifact(store.get(artifact.storage_uri), artifact.document)

    scores, labels = synthetic_calibration()
    policy = select_threshold(scores, labels, epsilon=DEFAULT_EPSILON)
    calibrator = Calibrator()
    priors = Priors()

    skus = [
        run_sku(
            sku,
            registry=registry,
            parsed=parsed,
            artifact=artifact,
            policy=policy,
            calibrator=calibrator,
            priors=priors,
        )
        for sku in ROWS
    ]

    document_id = artifact.document.document_id
    fixture = build_dataset(
        skus,
        documents={
            document_id: {
                "document": serialise_document(artifact, parsed),
                "pages": serialise_pages(parsed),
            }
        },
        class_definitions={CLASS_CODE: serialise_class(registry, CLASS_CODE)},
        policy=jsonable(policy.summary()),
        meta={
            "generator": "scripts/export_console_fixture.py",
            "pipeline_version": f"axiom-{axiom.__version__}",
            "source_sample": str(args.sample.relative_to(REPO_ROOT)).replace("\\", "/"),
            "calibrator": "untrained-heuristic",
            "policy_source": "synthetic-dev-calibration",
            "live": False,
            "notes": (
                "Values, evidence spans, bounding boxes, validation verdicts and gaps are "
                "produced by the real pipeline. The model response is a seeded contract "
                "quoting the sample datasheet verbatim, and the acceptance threshold comes "
                "from a synthetic calibration set because no reviewer outcomes exist yet. "
                "For live data from real model calls, run the API instead: this fixture is "
                "the offline fallback."
            ),
        },
    )

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "fixture.json"
    target.write_text(json.dumps(fixture, indent=2, default=str), encoding="utf-8")

    stats = dataset_stats(fixture)
    print(f"wrote {target.relative_to(REPO_ROOT)}")
    for key, value in stats.items():
        print(f"  {key:<15} {value}")
    print(f"  {'policy':<15} threshold={policy.summary().get('threshold')} "
          f"achievable={policy.achievable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
