import csv
import re

rows = list(csv.DictReader(open(r"Unihack_ Sample Dataset - Input.csv", encoding="utf-8-sig")))
pattern = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
bad = [r["Mfg_Part_Num"] for r in rows if pattern.search(r["Mfg_Part_Num"] or "")]
print("rows:", len(rows))
print("unsafe for filenames:", len(bad))
for b in bad:
    print("  ", repr(b))
