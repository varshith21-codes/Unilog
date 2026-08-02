"""Tests for the Source Fabric.

The properties worth guarding: identical bytes are stored once, hashes are stable so
citations stay resolvable, messy supplier files still parse, and column mapping proposes
rather than assumes.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from axiom.core.evidence import DocumentType
from axiom.ingest import (
    ColumnMapping,
    IngestError,
    LocalArtifactStore,
    MappingMemory,
    detect_document_type,
    fold_header,
    infer_mapping,
    ingest_bytes,
    ingest_file,
    read_flat_file,
    sha256_bytes,
)
from openpyxl import Workbook

SUPPLIER_CSV = b"""\
Part #,DESCR1,Size,Material,PRESSURE (psi),WT/EA (lb),QTY/CS,U/M,Cat No
BA-100-075,VLV BALL 3/4 BRZ 600WOG,3/4",Bronze C84400,600,0.75,12,EA,BA100075
BA-100-100,VLV BALL 1 BRZ 600WOG,1",Bronze C84400,600,1.10,12,EA,BA100100
"""


# --------------------------------------------------------------------- content addressing


def test_identical_bytes_are_stored_once(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    first = store.put(b"same content", suffix=".pdf")
    second = store.put(b"same content", suffix=".pdf")
    assert first == second
    assert store.get(first) == b"same content"


def test_different_bytes_get_different_keys(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    assert store.put(b"rev A") != store.put(b"rev B")


def test_reissued_datasheet_does_not_overwrite_the_cited_version(tmp_path: Path):
    """The reason storage is content-addressed rather than filename-addressed."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    rev_c = ingest_bytes(b"%PDF-1.7 rev C body", store, filename="datasheet.pdf")
    rev_d = ingest_bytes(b"%PDF-1.7 rev D body", store, filename="datasheet.pdf")

    assert rev_c.sha256 != rev_d.sha256
    # the older bytes remain retrievable, so an existing citation still resolves
    assert store.get(rev_c.storage_uri) == b"%PDF-1.7 rev C body"
    assert store.get(rev_d.storage_uri) == b"%PDF-1.7 rev D body"


def test_store_verify_detects_corruption(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    uri = store.put(b"original bytes", suffix=".txt")
    assert store.verify(uri) is True

    target = store._path_for(uri)
    target.write_bytes(b"tampered bytes")
    assert store.verify(uri) is False, "a hash mismatch must be detectable"


def test_missing_artifact_raises(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(FileNotFoundError):
        store.get("local://ab/cd/" + "0" * 64)


# --------------------------------------------------------------------- ingest


def test_ingest_records_provenance(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_bytes(
        b"%PDF-1.7 spec",
        store,
        filename="milwaukee-ba100.pdf",
        supplier_id="milwaukee",
        revision_label="Rev C 2024-08",
        source_uri="https://example.com/ba100.pdf",
        license_note="manufacturer site, public download",
    )
    doc = artifact.document
    assert doc.sha256 == sha256_bytes(b"%PDF-1.7 spec")
    assert doc.supplier_id == "milwaukee"
    assert doc.revision_label == "Rev C 2024-08"
    assert doc.uri == "https://example.com/ba100.pdf"
    assert doc.license_note is not None
    assert doc.doc_type is DocumentType.SPEC_SHEET
    assert artifact.was_already_stored is False


def test_reingesting_the_same_file_is_a_noop(tmp_path: Path):
    """Suppliers resend unchanged files constantly; the second time must be free."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    ingest_bytes(b"%PDF-1.7 spec", store, filename="a.pdf")
    again = ingest_bytes(b"%PDF-1.7 spec", store, filename="a.pdf")
    assert again.was_already_stored is True


def test_document_id_is_hash_derived_so_two_suppliers_share_one_document(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    a = ingest_bytes(b"%PDF-1.7 shared", store, filename="ds.pdf", supplier_id="dist-a")
    b = ingest_bytes(b"%PDF-1.7 shared", store, filename="ds.pdf", supplier_id="dist-b")
    assert a.document.document_id == b.document.document_id


def test_zero_bytes_is_refused(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(IngestError, match="zero bytes"):
        ingest_bytes(b"", store, filename="empty.pdf")


def test_ingest_file_rejects_a_directory(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(IngestError, match="not a file"):
        ingest_file(tmp_path, store)


# --------------------------------------------------------------------- type detection


def test_content_beats_a_lying_extension():
    """A spreadsheet named .pdf occurs in real supplier feeds."""
    assert detect_document_type(b"PK\x03\x04junk", "actually-a-workbook.pdf") is (
        DocumentType.SUPPLIER_FEED
    )
    assert detect_document_type(b"%PDF-1.7", "mislabelled.csv") is DocumentType.SPEC_SHEET


def test_extension_used_when_content_is_ambiguous():
    assert detect_document_type(b"Part,Size\n1,2", "feed.csv") is DocumentType.SUPPLIER_FEED
    assert detect_document_type(b"<html></html>", "page.html") is DocumentType.WEB_PAGE


def test_image_detection():
    assert detect_document_type(b"\x89PNG\r\n\x1a\n") is DocumentType.PRODUCT_IMAGE
    assert detect_document_type(b"\xff\xd8\xffstuff") is DocumentType.PRODUCT_IMAGE


def test_unknown_content_and_no_filename():
    assert detect_document_type(b"\x00\x01\x02") is DocumentType.UNKNOWN


# --------------------------------------------------------------------- flat files


def test_csv_parsing():
    flat = read_flat_file(SUPPLIER_CSV, filename="supplier.csv")
    assert len(flat) == 2
    assert flat.rows[0]["Part #"] == "BA-100-075"
    assert flat.rows[0]["Size"] == '3/4"'
    assert flat.non_empty_ratio("Material") == 1.0


def test_semicolon_delimited_file_is_sniffed():
    data = b"Part;Size;Material\nBA-1;3/4;Bronze\nBA-2;1;Bronze\n"
    flat = read_flat_file(data, filename="euro.csv")
    assert flat.headers == ("Part", "Size", "Material")
    assert flat.rows[1]["Material"] == "Bronze"


def test_cp1252_file_does_not_crash():
    """Supplier CSVs are cp1252 as often as utf-8; rejecting the file loses real data."""
    # U+201D (right double quote) encodes to byte 0x94 in cp1252, which is invalid utf-8.
    data = "Part,Desc\nBA-1,3/4\u201d valve\n".encode("cp1252")
    assert b"\x94" in data
    flat = read_flat_file(data, filename="legacy.csv")
    assert flat.rows[0]["Part"] == "BA-1"
    assert "valve" in flat.rows[0]["Desc"]


def test_blank_and_duplicate_headers_stay_addressable():
    data = b"Part,,Size,Size\nBA-1,x,3/4,19mm\n"
    flat = read_flat_file(data, filename="messy.csv")
    assert flat.headers == ("Part", "column_2", "Size", "Size__1")
    assert flat.rows[0]["Size"] == "3/4"
    assert flat.rows[0]["Size__1"] == "19mm"


def test_fully_blank_rows_are_skipped():
    data = b"Part,Size\nBA-1,3/4\n,\n\nBA-2,1\n"
    assert len(read_flat_file(data, filename="gappy.csv")) == 2


def test_header_only_file_yields_no_rows():
    assert len(read_flat_file(b"Part,Size\n", filename="empty.csv")) == 0


def test_missing_header_row_raises():
    with pytest.raises(IngestError, match="no header row"):
        read_flat_file(b"", filename="nothing.csv")


def test_xlsx_parsing():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Items"
    sheet.append(["Part #", "Size", "QTY/CS"])
    sheet.append(["BA-100-075", '3/4"', 12])
    sheet.append([None, None, None])
    sheet.append(["BA-100-100", '1"', 12])
    buffer = io.BytesIO()
    workbook.save(buffer)

    flat = read_flat_file(buffer.getvalue(), filename="items.xlsx")
    assert flat.sheet_name == "Items"
    assert len(flat) == 2
    assert flat.rows[0]["QTY/CS"] == "12"


# --------------------------------------------------------------------- column mapping


def test_header_folding_strips_units_and_punctuation():
    assert fold_header("Weight (lb)") == "weight"
    assert fold_header("WT/EA") == "wtea"
    assert fold_header("PRESSURE [psi]") == "pressure"


def test_real_supplier_headers_map_correctly():
    flat = read_flat_file(SUPPLIER_CSV, filename="supplier.csv")
    mapping = infer_mapping(list(flat.headers), supplier_id="milwaukee")
    resolved = mapping.resolved

    assert resolved["mpn"] == "Part #"
    assert resolved["description"] == "DESCR1"
    assert resolved["nominal_size"] == "Size"
    assert resolved["body_material"] == "Material"
    assert resolved["pressure_rating_wog"] == "PRESSURE (psi)"
    assert resolved["case_quantity"] == "QTY/CS"
    assert resolved["selling_uom"] == "U/M"


def test_unit_in_the_header_is_captured():
    """Supplier files put the unit in the header and a bare number in the cell."""
    mapping = infer_mapping(["WT/EA (lb)", "PRESSURE (psi)"])
    hints = {m.header: m.unit_hint for m in mapping.matches}
    assert hints["WT/EA (lb)"] == "lb"
    assert hints["PRESSURE (psi)"] == "psi"


def test_unrecognised_header_is_left_unmapped_rather_than_guessed():
    """A wrong mapping silently corrupts a whole column, so abstaining is cheaper."""
    mapping = infer_mapping(["Part #", "Zorblatt Index"])
    assert "Zorblatt Index" in mapping.unmapped
    assert mapping.resolved.get("mpn") == "Part #"


def test_near_miss_is_proposed_for_confirmation_not_auto_applied():
    mapping = infer_mapping(["Materal"])  # transposed letters
    proposed = mapping.needs_confirmation
    assert len(proposed) == 1
    assert proposed[0].target == "body_material"
    assert proposed[0].confidence < 0.80, "a fuzzy hit must not be auto-applied"


def test_duplicate_targets_are_flagged():
    mapping = infer_mapping(["Part #", "Part Number"])
    assert any("same target" in note for note in mapping.notes)


def test_targets_outside_the_active_schema_are_flagged():
    mapping = infer_mapping(["Size", "Cv"], valid_targets={"nominal_size"})
    assert any("absent from the active schema" in note for note in mapping.notes)


def test_empty_header_is_reported_not_mapped():
    mapping = infer_mapping(["", "  "])
    assert mapping.resolved == {}
    assert len(mapping.unmapped) == 2


def test_coverage_reflects_confident_matches():
    mapping = infer_mapping(["Part #", "Size", "Zorblatt Index"])
    assert mapping.coverage() == pytest.approx(2 / 3)


def test_supplier_memory_wins_over_inference(tmp_path: Path):
    """A human already decided; re-guessing would be wasteful and disrespectful of that."""
    memory = MappingMemory(tmp_path / "mappings.json")
    memory.remember("acme", {"cv_flow_coefficient": "Special Flow Column"})

    mapping = infer_mapping(
        ["Special Flow Column", "Part #"],
        supplier_id="acme",
        known=memory.get("acme"),
    )
    assert mapping.resolved["cv_flow_coefficient"] == "Special Flow Column"
    match = next(m for m in mapping.matches if m.header == "Special Flow Column")
    assert match.method == "supplier_memory"
    assert match.confidence == 1.0


def test_mapping_memory_persists_across_instances(tmp_path: Path):
    path = tmp_path / "mappings.json"
    MappingMemory(path).remember("acme", {"mpn": "Widget ID"})
    assert MappingMemory(path).get("acme") == {"mpn": "Widget ID"}
    assert MappingMemory(path).suppliers() == ["acme"]


def test_mapping_memory_merges_rather_than_replaces(tmp_path: Path):
    path = tmp_path / "mappings.json"
    memory = MappingMemory(path)
    memory.remember("acme", {"mpn": "Widget ID"})
    memory.remember("acme", {"nominal_size": "Dim"})
    assert MappingMemory(path).get("acme") == {"mpn": "Widget ID", "nominal_size": "Dim"}


def test_empty_mapping_has_zero_coverage():
    assert ColumnMapping(matches=()).coverage() == 0.0
