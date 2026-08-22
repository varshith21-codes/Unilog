"""Where does every value in the console actually come from, and what does the item master supply?"""

import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "packages")
from axiom.delivery.source import INPUT_COLUMNS, SupplierRow
from axiom.ingest import profile_rows

rows = list(csv.DictReader(open(r"Unihack_ Sample Dataset - Input.csv", encoding="utf-8-sig")))

print("=" * 78)
print("WHAT THE ITEM MASTER ACTUALLY CARRIES")
print("=" * 78)
profiles = profile_rows(list(INPUT_COLUMNS), rows)
for header in INPUT_COLUMNS:
    p = profiles[header]
    flag = "   <- carries no data at all" if not p.carries_data else ""
    print(f"  {header:14s} real {p.populated:5d}  sentinel {p.placeholders:5d}  distinct {p.distinct:5d}{flag}")

brand_resolved = 0
manuf_publishable = 0
manuf_distributor = 0
neither = 0
for r in rows:
    s = SupplierRow.parse(dict(r))
    if s.brand.resolved:
        brand_resolved += 1
    if s.manufacturer.publishable_as_manufacturer:
        manuf_publishable += 1
    if s.manufacturer.looks_like_a_distributor:
        manuf_distributor += 1
    if not s.brand.resolved and not s.manufacturer.publishable_as_manufacturer:
        neither += 1

print(f"\n  rows with a resolvable brand                 {brand_resolved:5d} / {len(rows)}")
print(f"  rows whose Part_Manuf is publishable         {manuf_publishable:5d} / {len(rows)}")
print(f"  rows whose Part_Manuf is a distributor/co-op {manuf_distributor:5d} / {len(rows)}")
print(f"  rows with NEITHER a brand nor a manufacturer {neither:5d} / {len(rows)}")

print()
print("=" * 78)
print("WHERE EVERY VALUE IN data/console COMES FROM")
print("=" * 78)
by_method = collections.Counter()
by_doc = collections.Counter()
by_page = collections.Counter()
verified = collections.Counter()
examples: dict[str, tuple] = {}

for path in Path("data/console").glob("*.bundle.json"):
    payload = json.loads(path.read_text(encoding="utf-8"))
    bundle = payload["bundle"]
    doc = payload["document"]
    for value in bundle["values"]:
        method = value["method"]
        by_method[method] += 1
        by_doc[doc["doc_type"]] += 1
        span = value["evidence"][0] if value["evidence"] else None
        by_page[bool(span and span.get("page") is not None)] += 1
        verified[bool(span and span.get("quote_verified"))] += 1
        if method not in examples and span:
            examples[method] = (
                bundle["sku"],
                value["attribute_code"],
                value["value_display"],
                span["quote"],
                doc["uri"],
                value.get("model_tier"),
            )

print("  by derivation method:")
for method, n in by_method.most_common():
    print(f"    {method:24s} {n:5d}")
print(f"\n  quote verified            {verified[True]} of {sum(verified.values())}")
print(f"  carries a page + bbox     {by_page[True]} of {sum(by_page.values())}")
print("\n  by source document type:")
for doc_type, n in by_doc.most_common():
    print(f"    {doc_type:24s} {n:5d}")

print("\n  one example per method (sku, attribute, value, quote, source, model tier):")
for method, ex in examples.items():
    print(f"    {method}:")
    print(f"      sku={ex[0]!r} {ex[1]}={ex[2]!r}")
    print(f"      quote={ex[3]!r}")
    print(f"      source={ex[4]!r}  model_tier={ex[5]!r}")

print()
print("=" * 78)
print("THE ABBREVIATION TABLE: what a description CAN yield")
print("=" * 78)
from axiom.extract.description import AbbreviationTable

table = AbbreviationTable.load()
print(f"  attributes with abbreviation rules: {len(table.attributes())}")
for code in table.attributes():
    terms = table.for_attribute(code)
    sample = list(terms.items())[:6]
    print(f"    {code:22s} {len(terms):3d} terms  e.g. {sample}")
