"""Render every console route against the 1,001-SKU catalogue and report size and timing."""

import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3011"

PATHS = [
    "/",
    "/review",
    "/review?page=2",
    "/review?page=21",
    "/review?page=9999",
    "/certificates",
    "/certificates?page=5",
    "/quality",
    "/pipeline",
    "/delivery",
    # A part number that only works if the slug scheme holds end to end.
    "/review/52C3-5~2F8-UPC",
    "/review/MAG~3A2044-230-1",
    "/review/2~2F2~2F4~20UD~20ALUM",
    "/certificates/52C3-5~2F8-UPC",
    # An ordinary one, and a classified one with real values.
    "/review/BA-100-075",
    "/review/KDFM404KPS",
    "/certificates/KDFM404KPS",
    # Should 404: not a slug in the catalogue.
    "/review/NOT-A-REAL-SKU",
]

for path in PATHS:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE + path, timeout=180) as response:
            body = response.read()
            code = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        code = exc.code
    elapsed = time.perf_counter() - start
    print(f"{code}  {elapsed:6.2f}s  {len(body) / 1024:9.1f} KB  {path}")
