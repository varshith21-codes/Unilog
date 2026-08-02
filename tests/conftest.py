"""Shared fixtures.

The datasheet fixture is the backbone of most tests in this suite. It is shaped like a real
two-piece ball valve spec sheet and carries the traps that separate a working extractor from
a plausible one:

* a specification block using dot leaders (a two-column list, *not* a table)
* an ordering table where the target SKU sits between neighbouring rows
* a footnote that substitutes a different handle for larger sizes
* a torque figure qualified to one size only
* a value deferred with "Consult factory"
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from axiom.core.evidence import DocumentType, SourceDocument

DATASHEET_TEXT = """\
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

SHA_PLACEHOLDER = "9f2c" + "0" * 60


@pytest.fixture
def datasheet_text() -> str:
    return DATASHEET_TEXT


@pytest.fixture
def source_document() -> SourceDocument:
    return SourceDocument(
        document_id="milwaukee-ba100@9f2c0000",
        uri="local://9f/2c/" + SHA_PLACEHOLDER + ".pdf",
        sha256=SHA_PLACEHOLDER,
        doc_type=DocumentType.SPEC_SHEET,
        fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        revision_label="Rev C 2024-08",
        supplier_id="milwaukee",
    )


@pytest.fixture
def parsed_datasheet(datasheet_text: str, source_document: SourceDocument):
    from axiom.docintel import parse_text

    return parse_text(datasheet_text, source_document)
