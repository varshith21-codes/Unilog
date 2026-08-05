"""Generate a deliberately difficult PDF datasheet for the golden set.

The ablation in ``run_ablation.py`` came back null: with the evidence contract disabled, scores
were identical. That is not a result about the trust layer, it is a result about the corpus. Two
clean, machine-generated text datasheets with tidy dot-leader blocks and well-formed ordering
tables give a competent model nothing to get wrong, so the gate never fires and the measurement
has no tail to measure.

This builds the tail. Every awkward feature here is one that actually appears on real supplier
literature and that the existing fixtures happen not to have:

*   **A real PDF**, not text. The entire pdfplumber path — coordinate extraction, word grouping,
    table reconstruction — is exercised by unit tests but by nothing in the backtest, so its
    accuracy contribution to the published numbers is currently unmeasured.
*   **Specifications in prose.** No dot leaders, no key-value block. The pressure rating sits in
    the middle of a sentence, which is where a table-oriented extractor stops working.
*   **Metric designations.** DN sizes with millimetre figures, so a correct answer requires the
    unit registry rather than a copied string.
*   **Two port styles in one ordering table.** Full-port and reduced-port rows for the same
    nominal size, which is precisely the trap that makes a Cv figure look plausible and be
    wrong — and it cannot be caught by verifying the quote, because both rows are really there.
*   **A footnote that overrides a column** for part of the range, contradicting the table it
    sits under.
*   **A value stated only as a tolerance band**, never as a single figure.
*   **A superseded part number** listed alongside its replacement, so the document mentions a
    SKU that is not orderable.

Run it, then re-run the backtest and the ablation:

    python scripts/make_hard_sample.py --write
    python scripts/run_backtest.py --detail
    python scripts/run_ablation.py
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "data" / "samples"

# Courier at 9pt, positioned by hand. Column x-offsets are chosen so pdfplumber's word grouping
# has to infer the table structure from spacing, which is what it faces on real documents —
# drawing ruled lines would make it easy in a way real datasheets are not.
TITLE = [
    "APOLLO VALVE - 77C SERIES",
    "Bronze Ball Valve, Full Port and Reduced Port      Rev D  2025-11",
]

PROSE = [
    "",
    "DESCRIPTION",
    "The 77C is a two-piece bronze ball valve intended for general service in",
    "commercial plumbing and light industrial systems. The body is cast from",
    "lead-free bronze alloy C89833 and the valve carries a maximum working",
    "pressure of 400 PSI WOG at ambient temperature, falling to 125 PSI WSP in",
    "saturated steam service. Seats are reinforced PTFE. The stem is silicon",
    "bronze with a blowout-proof design. All sizes are supplied with a chrome-",
    "plated ball and a vinyl-covered lever handle unless noted otherwise below.",
    "Valves are certified to NSF/ANSI 61 and NSF/ANSI 372 for potable water and",
    "are marked accordingly. Operating temperature is rated from -20 degF to",
    "365 degF continuous.",
    "",
    "Torque to open at rated pressure falls between 14 and 19 ft-lb depending on",
    "size and seat wear; no single figure is published.",
]

TABLE = [
    "",
    "ORDERING INFORMATION",
    "",
    "Catalog No       DN        Size        Port          Cv      Ctn Qty",
    "77C-103          DN15      1/2\"        Full          15.0    24",
    "77C-104          DN20      3/4\"        Full          28.0    20",
    "77C-105          DN25      1\"          Full          49.0    12",
    "77C-105R         DN25      1\"          Reduced       21.0    12",
    "77C-106          DN32      1-1/4\"      Full          88.0    6",
    "77C-106R         DN32      1-1/4\"      Reduced       37.0    6",
    "",
    "NOTE 1: Sizes DN25 and larger are furnished with a tee handle in place of",
    "        the lever handle described above.",
    "NOTE 2: Catalog No 77C-102 (DN10) is discontinued and superseded by",
    "        77C-103. Do not order.",
    "NOTE 3: Reduced port valves carry the same pressure rating but a lower Cv.",
    "        Confirm flow requirements before substituting.",
]

FOOTER = [
    "",
    "Country of origin: Taiwan.",
    "Weights and packaging data available on request.",
]


def build_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Courier-Bold", 10)

    y = 740
    for line in TITLE:
        pdf.drawString(54, y, line)
        y -= 13

    pdf.setFont("Courier", 9)
    for block in (PROSE, TABLE, FOOTER):
        for line in block:
            pdf.drawString(54, y, line)
            y -= 11.5

    pdf.save()
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SAMPLES / "ap77c.pdf")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true", help="parse it back and report")
    args = parser.parse_args()

    data = build_pdf()
    print(f"built {len(data)} bytes of PDF")

    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(data)
        print(f"wrote {args.out.relative_to(REPO_ROOT)}")

    if args.verify or args.write:
        _verify(data)
    return 0


def _verify(data: bytes) -> None:
    """Parse the PDF back and report what the extractor will actually see.

    Worth doing every time: a fixture whose difficulty comes from the *renderer* rather than the
    document would be measuring the wrong thing. If pdfplumber cannot find the ordering table at
    all, the fixture is testing parsing rather than extraction and needs redrawing.
    """
    if str(REPO_ROOT / "packages") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "packages"))

    from datetime import UTC, datetime

    from axiom.core.evidence import DocumentType, SourceDocument
    from axiom.docintel import parse_pdf

    document = SourceDocument(
        document_id="ap77c",
        uri="local://ap77c.pdf",
        sha256="0" * 64,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime.now(UTC),
    )
    parsed = parse_pdf(data, document)

    print("\n" + "=" * 78)
    print("PARSED BACK")
    print("=" * 78)
    print(f"  parser   {parsed.parser}")
    print(f"  pages    {parsed.page_count}")
    print(f"  lines    {len(parsed.all_lines())}")
    print(f"  tables   {len(parsed.all_tables())}")
    for table in parsed.all_tables():
        print(f"    {table.table_id}: {table.row_count} rows x {table.col_count} cols")
        for row in table.rows()[:3]:
            print(f"      {row}")

    # The specific things this fixture exists to test. If a probe is missing from the parse, the
    # fixture cannot exercise what it claims to.
    text = parsed.full_text
    probes = {
        "prose pressure rating": "400 PSI WOG",
        "prose steam rating": "125 PSI WSP",
        "prose alloy": "C89833",
        "metric designation": "DN25",
        "reduced port row": "Reduced",
        "footnote handle override": "tee handle",
        "superseded part": "77C-102",
        "tolerance band torque": "14 and 19 ft-lb",
        "lead-free certification": "NSF/ANSI 372",
    }
    print("\n  probes:")
    missing = []
    for label, needle in probes.items():
        found = needle in text
        if not found:
            missing.append(label)
        print(f"    {'ok  ' if found else 'MISS'} {label:<26} {needle!r}")

    if missing:
        print(
            f"\n  {len(missing)} probe(s) did not survive rendering. The fixture would be "
            f"testing the PDF renderer rather than the extractor; adjust the layout."
        )
    else:
        print("\n  every feature survived rendering and is available to extraction")


if __name__ == "__main__":
    raise SystemExit(main())
