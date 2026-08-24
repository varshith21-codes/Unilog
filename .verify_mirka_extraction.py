import logging
import tempfile
from pathlib import Path

from apps.api import main
from axiom.ingest import LocalArtifactStore
from axiom.pipeline import EnrichmentRequest, enrich_one

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

TDS_URL = (
    "https://cdn.brandfolder.io/FSMPOV8D/at/"
    "mgphcs6nxx5hgtbgkmpf7rpv/TDS_Abranet_Max_Flap_Disc.pdf"
)

with tempfile.TemporaryDirectory(prefix="axiom-mirka-extraction-") as temp_dir:
    result = enrich_one(
        EnrichmentRequest(
            mpn="8896700140",
            manufacturer="Mirka",
            description="Abranet Max Flap Disc T29 125mm ALOX P40",
            source_url=TDS_URL,
            brand="Mirka",
            class_code="ABR.COATED.GEN",
            retrieve=False,
            include_optional=True,
            generate_copy=False,
        ),
        registry=main.load_schema(),
        client=main.model_client(),
        store=LocalArtifactStore(Path(temp_dir) / "artifacts"),
        calibration_dir=main.CALIBRATION_DIR,
    )

print(f"source_uri={result.source.artifact.document.uri}")
print(f"source_pages={result.source.parsed.page_count}")
print(f"source_contains_sku={str('8896700140' in result.source.parsed.full_text).lower()}")
print(f"class_code={result.run.class_code}")
print(f"model_calls={result.run.usage.calls}")
print(f"cost_usd={result.run.cost_usd}")
values = sorted(result.record.current_values(), key=lambda item: item.attribute_code)
print(f"value_count={len(values)}")
print(f"publishable_count={len(result.record.publishable_values())}")
for value in values:
    evidence = value.evidence[0] if value.evidence else None
    status = value.status.value if hasattr(value.status, "value") else str(value.status)
    print(
        "value="
        + "|".join(
            (
                value.attribute_code,
                value.value_display or value.value_raw or "",
                status,
                "verified" if evidence and evidence.quote_verified else "unverified",
                evidence.quote.replace("\n", " ") if evidence else "",
            )
        )
    )
print(f"gap_count={len(result.record.gaps)}")
for gap in result.record.gaps:
    print(f"gap={gap.attribute_code}|{gap.reason.value}|{gap.detail or ''}")
