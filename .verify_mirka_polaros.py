"""End-to-end check for Mirka MRP6002100, the SKU whose specification table was being lost.

Runs the real deterministic chain — retrieval, parse, SKU coverage, classification, prompt
assembly — against a throwaway copy of the library index so the committed one is untouched. The
model call is the only stage not exercised; the prompt it would receive is printed instead, which is
the thing that actually decides whether the values in the product page's "Technical details" table
are reachable.

Run from the repo root: `python .verify_mirka_polaros.py`
"""

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "packages")

from axiom.classify import CandidateIndex  # noqa: E402
from axiom.docintel.sku import find_sku  # noqa: E402
from axiom.docintel.title import title_block  # noqa: E402
from axiom.ingest import LocalArtifactStore  # noqa: E402
from axiom.pipeline.retrieval import retrieve_documents  # noqa: E402
from axiom.retrieve import SerperSearch  # noqa: E402
from axiom.schema import build_extraction_prompt, load_default  # noqa: E402

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

MPN = "MRP6002100"
ROOT = Path(__file__).resolve().parent

# The 13 rows the product page publishes under "Technical details". Every one of them was absent
# from the enriched record before the html_parser fix, because the whole `<template
# #technical-details>` slot was discarded as inert markup.
EXPECTED = {
    "Connectivity": "Bluetooth",
    "Dust System": "Non vacuum",
    "Max Speed (rpm)": "2500 rpm",
    "Min Speed (rpm)": "700 rpm",
    "Noise Level, LpA (dB)": "58.0 dB",
    "Orbit Type": "Rotary",
    "Pad Diameter": "150 mm",
    "Pad Included": "8292557031",
    "Plug Type": "EU",
    "Power Input (W)": "750 W",
    "Vibration Level (m/s\u00b2)": "1.1 m/s\u00b2",
    "Voltage Supply (VAC)": "220-240 VAC",
    "Weight": "1.8 kg",
}

env = (ROOT / ".env.deploy").read_text(encoding="utf-8")
for line in env.splitlines():
    if line.startswith("AXIOM_SERPER_API_KEY="):
        os.environ["AXIOM_SERPER_API_KEY"] = line.split("=", 1)[1].strip()

failures: list[str] = []

with tempfile.TemporaryDirectory(prefix="axiom-polaros-") as temp_dir:
    index = Path(temp_dir) / "index.json"
    shutil.copy2(ROOT / "data" / "library" / "index.json", index)

    attempt = retrieve_documents(
        MPN,
        store=LocalArtifactStore(ROOT / "data" / "cache" / "artifacts"),
        library_path=index,
        manufacturer="Mirka",
        search=SerperSearch.from_env() if os.environ.get("AXIOM_SERPER_API_KEY") else None,
        renderer=None,
    )

print("=" * 78)
print("RETRIEVAL")
print("=" * 78)
print(f"found              = {attempt.found}")
print(f"from_library       = {attempt.from_library}")
print(f"requests_made      = {attempt.requests_made}")
print(f"documents          = {len(attempt.documents)}")
print(f"supplementary      = {len(attempt.supplementary)}")
for note in attempt.notes:
    print(f"  note: {note}")

for position, document in enumerate(attempt.documents, start=1):
    print(
        f"  doc{position}: pages={document.page_count:<4} "
        f"type={document.document.doc_type.value:<11} {document.document.uri[:88]}"
    )

primary = attempt.primary
if primary is None:
    print("\nFAIL: retrieval returned no primary document")
    raise SystemExit(1)

print(f"\nprimary            = {primary.document.uri}")
print(f"primary pages      = {primary.page_count}")

if primary.page_count > 40:
    failures.append(
        f"primary is a {primary.page_count}-page catalogue, not a focused source"
    )

# ---------------------------------------------------------------- SKU coverage
presence = find_sku(primary, MPN)
print("\n" + "=" * 78)
print("SKU COVERAGE  (the gate that refused this page before the identity fix)")
print("=" * 78)
print(f"absent             = {presence.is_absent}")
print(f"listed_in_table    = {presence.listed_in_table}")
print(f"location           = {presence.location}")
if presence.is_absent:
    failures.append(f"{MPN} not found in the primary document; extraction would refuse it")

# ---------------------------------------------------------------- classification
registry = load_default()
ranked = CandidateIndex.build(registry).search(title_block(primary))
print("\n" + "=" * 78)
print("CLASSIFICATION")
print("=" * 78)
for candidate in ranked[:4]:
    print(f"  {candidate.code:<20} {candidate.score:.6f}")
top = ranked[0].code if ranked else None
print(f"top                = {top}")
if top != "TOL.PWR.GEN":
    failures.append(f"classified {top}, expected TOL.PWR.GEN for a rotary polisher")

# ---------------------------------------------------------------- the spec table
print("\n" + "=" * 78)
print("TECHNICAL DETAILS TABLE, as reconstructed from the page")
print("=" * 78)
recovered: dict[str, str] = {}
for table in primary.all_tables():
    for row in table.rows():
        cells = [cell.strip() for cell in row if cell.strip()]
        if len(cells) == 2 and cells[0] in EXPECTED:
            recovered[cells[0]] = cells[1]

for label, expected_value in EXPECTED.items():
    got = recovered.get(label)
    if got is None:
        print(f"  MISSING  {label:<24}")
        failures.append(f"spec row not recovered: {label}")
    elif got != expected_value:
        print(f"  DIFFERS  {label:<24} {got!r} != {expected_value!r}")
        failures.append(f"spec row {label}: got {got!r}, expected {expected_value!r}")
    else:
        print(f"  ok       {label:<24} {got}")

# ---------------------------------------------------------------- linked PDFs
print("\n" + "=" * 78)
print("LINKED DATASHEET PDFS")
print("=" * 78)
pdf_uris = [
    document.document.uri
    for document in (*attempt.documents, *attempt.supplementary)
    if document.document.uri.lower().endswith(".pdf")
]
for uri in pdf_uris:
    print(f"  {uri[:96]}")
if not pdf_uris:
    print("  (none retrieved this run)")

# ---------------------------------------------------------------- source authority
#
# The gate that decides whether the "Technical details" rows may be recorded at all. None of the 13
# labels is bound as a typed attribute on TOL.PWR.GEN, so every one of them travels through the
# source-native `manufacturer_specifications` channel — and `Extractor` moves that whole channel
# into `suppressed_specifications` unless the source is verified as manufacturer-owned.
print("\n" + "=" * 78)
print("SOURCE AUTHORITY  (gates the manufacturer_specifications channel)")
print("=" * 78)
primary_entry = next(
    (e for e in attempt.entries if e.sha256 == primary.document.sha256), None
)
maker_id = attempt.manufacturer.id if attempt.manufacturer else None
entry_maker = primary_entry.manufacturer_id if primary_entry else None
entry_tier = primary_entry.tier if primary_entry else None
citable = bool(
    primary_entry
    and entry_tier == "manufacturer"
    and entry_maker
    and maker_id
    and entry_maker == maker_id
)
print(f"entry tier         = {entry_tier}")
print(f"entry manufacturer = {entry_maker}")
print(f"expected           = {maker_id}")
print(f"citable_as_mfr     = {citable}")
if not citable:
    failures.append(
        "source is not citable as manufacturer-owned, so the 13 source-native "
        "specification rows would be withheld"
    )

# ---------------------------------------------------------------- the prompt
print("\n" + "=" * 78)
print("EXTRACTION PROMPT")
print("=" * 78)
prompt = build_extraction_prompt(
    registry,
    top or "TOL.PWR.GEN",
    source_content=primary.to_prompt_content(),
    target_sku=MPN,
    source_name=primary.document.document_id,
    include_optional=True,
)
content = prompt.user_message
print(f"prompt chars       = {len(content):,}")
print(f"attributes asked   = {len(prompt.attribute_codes)}")
present = [label for label in EXPECTED if label in content]
print(f"spec labels in prompt = {len(present)}/{len(EXPECTED)}")
missing = [label for label in EXPECTED if label not in content]
if missing:
    failures.append(f"labels absent from the prompt the model receives: {missing}")

print("\n" + "=" * 78)
if failures:
    print(f"FAILED ({len(failures)})")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("PASSED: the specification table, the part number and the class all survive to the prompt.")
print("=" * 78)
