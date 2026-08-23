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
    FetchedResource,
    IngestError,
    LocalArtifactStore,
    MappingMemory,
    UrlFetchError,
    check_url,
    detect_document_type,
    filename_for,
    fold_header,
    infer_mapping,
    ingest_bytes,
    ingest_fetched_resource,
    ingest_file,
    ingest_url,
    is_url,
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


# ===================================================== the supplier-file entry point
#
# scripts/ingest_supplier_file.py is the reachable path for everything above. The inference and
# the memory were already covered; what these guard is the projection from a mapped file into
# canonical records, and the validation that stops a typo being persisted as a mapping.

from scripts.ingest_supplier_file import (  # noqa: E402 - a script, imported for its logic
    _apply_mapping,
    _identity_resolved,
    _parse_overrides,
)

SAMPLE_FEED = Path(__file__).resolve().parents[1] / "data" / "samples" / "supplier-feed.csv"


@pytest.fixture
def feed() -> object:
    return read_flat_file(SAMPLE_FEED.read_bytes(), filename=SAMPLE_FEED.name)


def test_the_checked_in_sample_feed_reads(feed):
    """The sample exists so the entry point is runnable with no arguments to invent."""
    assert len(feed) == 18
    assert "PN" in feed.headers
    assert "WT/EA (lb)" in feed.headers


def test_the_sample_feed_maps_most_of_its_columns(feed):
    """A realistically messy header row should mostly resolve without human help.

    Not all of it: `Category` has no canonical target and `WT/EA (lb)` folds too far from any
    synonym to clear the fuzzy bar. Those are the columns an operator confirms once.
    """
    mapping = infer_mapping(list(feed.headers))

    assert mapping.resolved["mpn"] == "PN"
    assert mapping.resolved["sku"] == "Our Part #"
    assert mapping.resolved["nominal_size"] == "Size"
    assert mapping.resolved["case_quantity"] == "QTY/CS"
    assert mapping.resolved["selling_uom"] == "U/M"
    assert mapping.coverage() >= 0.75
    assert "Category" in mapping.unmapped


def test_a_unit_in_the_header_is_captured(feed):
    """Supplier files routinely put the unit in the header and a bare number in the cell.
    Losing it makes the number dimensionless."""
    mapping = infer_mapping(list(feed.headers))
    by_header = {m.header: m for m in mapping.matches}

    assert by_header["PRESSURE (psi)"].unit_hint == "psi"
    assert by_header["WT/EA (lb)"].unit_hint == "lb"


def test_mapped_rows_keep_raw_strings(feed):
    """No normalisation here, deliberately. Extraction observes the same separation so that a
    bad unit conversion can never masquerade as a bad mapping."""
    mapping = infer_mapping(list(feed.headers), known={"each_weight": "WT/EA (lb)"})
    rows = _apply_mapping(feed, mapping)

    first = rows[0]
    assert first["mpn"] == "BA-100-025"
    assert first["attributes"]["nominal_size"] == '1/4"', "not converted to 0.25 in"
    assert first["attributes"]["each_weight"] == "0.45", "still a string, still in lb"


def test_empty_cells_become_gaps_not_empty_values(feed):
    """A mapped-but-blank column has to read as absent. Carrying "" would count as coverage
    and deliver nothing, which is the exact trap non_empty_ratio exists to expose."""
    mapping = infer_mapping(list(feed.headers))
    rows = _apply_mapping(feed, mapping)

    # UPC is mapped to gtin and is empty for every row in the sample.
    assert mapping.resolved.get("gtin") == "UPC"
    assert all("gtin" not in row["attributes"] for row in rows)


def test_unmapped_columns_are_carried_not_discarded(feed):
    """The operator who has to resolve an unmapped column needs to see what was in it."""
    mapping = infer_mapping(list(feed.headers))
    rows = _apply_mapping(feed, mapping)

    assert rows[0]["unmapped"]["Category"] == "Valves"


def test_an_override_target_must_exist_in_the_schema(feed):
    """A typo'd target would otherwise be persisted by --confirm and silently map a column
    to an attribute the schema has never heard of."""
    with pytest.raises(ValueError, match="neither a schema attribute nor a record field"):
        _parse_overrides(["Category=nonesuch"], feed, {"each_weight"})


def test_an_override_header_must_exist_in_the_file(feed):
    """A typo'd header maps nothing at all, which is worse than an error because it looks
    like it worked."""
    with pytest.raises(ValueError, match="not in this file"):
        _parse_overrides(["Nope=sku"], feed, {"sku"})


def test_a_record_field_is_a_valid_override_target(feed):
    """`sku` and `mpn` are product-record fields rather than schema attributes, and both are
    legitimate mapping targets."""
    assert _parse_overrides(["Cat No=mpn"], feed, set()) == {"mpn": "Cat No"}


def test_a_file_with_no_identity_column_is_rejected():
    """Rows with no sku and no mpn cannot be joined to anything, so ingesting them is not a
    partial success — it is a failure that would otherwise be logged as fine."""
    flat = read_flat_file(b"Colour,Notes\nred,none\n", filename="junk.csv")
    mapping = infer_mapping(list(flat.headers))

    assert _identity_resolved(mapping) is False
    assert _identity_resolved(infer_mapping(["Part #"])) is True


def test_a_confirmed_mapping_survives_a_round_trip(tmp_path: Path, feed):
    """The feature an operator actually feels: map a supplier once, and the next file from them
    maps itself at full confidence with no human step."""
    memory = MappingMemory(tmp_path / "mappings.json")
    first = infer_mapping(
        list(feed.headers), known={"each_weight": "WT/EA (lb)"}, supplier_id="milwaukee"
    )
    memory.remember("milwaukee", first.resolved)

    replayed = infer_mapping(
        list(feed.headers), known=MappingMemory(tmp_path / "mappings.json").get("milwaukee")
    )
    remembered = [m for m in replayed.matches if m.method == "supplier_memory"]

    assert len(remembered) >= 12
    assert all(m.confidence == 1.0 for m in remembered)
    assert replayed.resolved["each_weight"] == "WT/EA (lb)", "the hand-fixed column persisted"


# ===================================================================== URL ingestion
#
# The fetcher is injected throughout. A test that needs the internet to check filename derivation
# or scheme validation is a test that gets skipped, and then this code rots.


def stub_fetch(
    data: bytes = b"<html><body><p>600 PSI WOG</p></body></html>",
    *,
    url: str = "https://example.com/docs/ba100.html",
    content_type: str = "text/html; charset=utf-8",
    last_modified: str | None = None,
):
    calls: list[dict] = []

    def fetch(target: str, *, timeout: float, max_bytes: int) -> FetchedResource:
        calls.append({"url": target, "timeout": timeout, "max_bytes": max_bytes})
        return FetchedResource(
            data=data,
            url=url,
            content_type=content_type,
            last_modified=last_modified,
        )

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_a_fetched_page_is_stored_and_cited_by_url(tmp_path: Path):
    """The URL, not the store path, becomes the document's uri. A web citation that cannot say
    where on the internet the claim came from is not provenance."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_url(
        "https://example.com/docs/ba100.html", store, fetcher=stub_fetch(), supplier_id="milwaukee"
    )

    assert artifact.document.uri == "https://example.com/docs/ba100.html"
    assert artifact.document.doc_type is DocumentType.WEB_PAGE
    assert artifact.document.supplier_id == "milwaukee"
    assert artifact.sha256 == sha256_bytes(b"<html><body><p>600 PSI WOG</p></body></html>")
    assert store.exists(artifact.storage_uri), "the original bytes are retrievable"


def test_the_redirect_target_is_what_gets_cited(tmp_path: Path):
    """Recording where the operator was pointed rather than where the bytes came from would make
    the citation unresolvable."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    fetch = stub_fetch(
        url="https://cdn.example.com/final/ba100.pdf", content_type="application/pdf"
    )
    artifact = ingest_url("https://example.com/redirect", store, fetcher=fetch)

    assert artifact.document.uri == "https://cdn.example.com/final/ba100.pdf"
    assert artifact.original_filename == "ba100.pdf"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "ftp://example.com/x.pdf",
        "data:text/html,<h1>hi</h1>",
        "gopher://example.com/",
    ],
)
def test_only_http_schemes_are_fetchable(tmp_path: Path, url: str):
    """A URL arriving in a spreadsheet cell must not be able to read the local disk."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UrlFetchError, match="scheme"):
        ingest_url(url, store, fetcher=stub_fetch())


def test_plain_http_is_refused_by_default(tmp_path: Path):
    """Hashing bytes that arrived with no integrity guarantee and calling it provenance would
    defeat the point of hashing them."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UrlFetchError, match="integrity"):
        ingest_url("http://example.com/x.html", store, fetcher=stub_fetch())


def test_plain_http_is_recorded_when_explicitly_allowed(tmp_path: Path):
    """Opting in is fine. Forgetting six months later is not, so the document says so."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_url(
        "http://example.com/x.html",
        store,
        fetcher=stub_fetch(url="http://example.com/x.html"),
        allow_insecure_http=True,
    )

    assert "not integrity-protected" in (artifact.document.license_note or "")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/x.html",
        "https://127.0.0.1/x.html",
        "https://10.0.0.5/x.html",
        "https://192.168.1.1/x.html",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_loopback_and_private_addresses_are_refused(tmp_path: Path, url: str):
    """Includes the EC2 metadata address, which is the one that actually gets attacked."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UrlFetchError):
        ingest_url(url, store, fetcher=stub_fetch())


def test_a_dns_name_resolving_private_is_refused(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))
        ],
    )

    with pytest.raises(UrlFetchError, match="non-public"):
        check_url("https://metadata.example/x", verify_public_address=True)


def test_the_global_nat64_prefix_is_not_mistaken_for_a_private_address(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::96ab:6e53", 0, 0, 0))
        ],
    )

    assert check_url("https://manufacturer.example/x", verify_public_address=True)


def test_an_unexpected_content_type_is_refused(tmp_path: Path):
    """A login wall stored as a datasheet is worse than a failed fetch, because it gets cited."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    fetch = stub_fetch(data=b"\x00\x01binary", content_type="application/x-shockwave-flash")

    with pytest.raises(UrlFetchError, match="not an ingestable document type"):
        ingest_url("https://example.com/x", store, fetcher=fetch)

    # Overridable, because octet-stream portals are real and so is operator judgement.
    artifact = ingest_url(
        "https://example.com/x", store, fetcher=fetch, allow_any_content_type=True
    )
    assert artifact.size_bytes == len(b"\x00\x01binary")


def test_an_empty_body_is_refused(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UrlFetchError, match="empty body"):
        ingest_url("https://example.com/x.html", store, fetcher=stub_fetch(data=b""))


def test_a_browser_resource_cannot_bypass_the_byte_ceiling(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    resource = FetchedResource(
        data=b"x" * 11,
        url="https://example.com/rendered.html",
        content_type="text/html",
    )

    with pytest.raises(UrlFetchError, match="10-byte ceiling"):
        ingest_fetched_resource(resource, store, max_bytes=10)


def test_a_browser_resource_cannot_turn_an_http_error_into_evidence(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    resource = FetchedResource(
        data=b"<html><body>SKU-1 not found</body></html>",
        url="https://manufacturer.example/missing",
        content_type="text/html",
        status=404,
    )

    with pytest.raises(UrlFetchError, match="returned HTTP 404"):
        ingest_fetched_resource(resource, store)


def test_a_browser_resource_uses_its_final_url_as_provenance(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    resource = FetchedResource(
        data=b"<html><body>SKU-1</body></html>",
        url="https://manufacturer.example/final/product",
        content_type="text/html",
    )

    artifact = ingest_fetched_resource(
        resource,
        store,
        requested_url="https://manufacturer.example/redirect",
    )

    assert artifact.document.uri == "https://manufacturer.example/final/product"
    assert artifact.document.doc_type is DocumentType.WEB_PAGE


def test_last_modified_becomes_the_revision_label(tmp_path: Path):
    """Revision awareness is how a newer datasheet wins over an older one when sources conflict,
    and for a web source the header is the only revision signal available."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = ingest_url(
        "https://example.com/docs/ba100.html",
        store,
        fetcher=stub_fetch(last_modified="Tue, 05 Aug 2025 10:00:00 GMT"),
    )

    assert artifact.document.revision_label == "Tue, 05 Aug 2025 10:00:00 GMT"


@pytest.mark.parametrize(
    ("url", "media", "expected"),
    [
        ("https://x.com/docs/ba100.pdf", "application/pdf", "ba100.pdf"),
        # No extension in the path: the declared media type supplies one, because the suffix is
        # what routes the parser downstream.
        ("https://x.com/docs/ba100", "application/pdf", "ba100.pdf"),
        ("https://x.com/feed", "text/csv", "feed.csv"),
        # Nothing usable in the path at all, so the host stands in.
        ("https://x.com/", "text/html", "x.com.html"),
        ("https://x.com/a%20b/c%20d.pdf", "application/pdf", "c-d.pdf"),
    ],
)
def test_a_filename_is_derived_so_the_suffix_routes_the_parser(url, media, expected):
    """The suffix is load-bearing: read_flat_file and parse_artifact both consult it."""
    assert filename_for(url, media) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/p/../..%2Fetc%2Fpasswd",
        "https://x.com/%2e%2e%2f%2e%2e%2fetc%2fshadow",
        "https://x.com/dir%5Cwin.ini",
        "https://x.com/....//x.pdf",
    ],
)
def test_a_derived_filename_cannot_escape_a_directory(url: str):
    """This name reaches the filesystem, so traversal has to be gone rather than merely encoded.

    Two independent defences: only the final path component survives, and anything outside
    ``[A-Za-z0-9._-]`` is replaced. Asserting the property rather than an exact string keeps this
    honest across platforms — Windows and POSIX disagree about whether a backslash is a separator.
    """
    name = filename_for(url, "text/html")

    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name
    assert not name.startswith(".")
    assert name, "a name is still produced rather than an empty string"


def test_is_url_does_not_mistake_a_windows_path_for_a_url():
    """`C:\\feeds\\x.csv` parses with a one-letter scheme, so a naive check misroutes it."""
    assert is_url("https://example.com/x.pdf") is True
    assert is_url("http://example.com/x.pdf") is True
    assert is_url(r"C:\feeds\x.csv") is False
    assert is_url("data/samples/ba100.txt") is False
    assert is_url("file:///etc/passwd") is False


def test_the_fetcher_receives_the_ceiling_and_the_timeout(tmp_path: Path):
    """Both are the defence against a hostile endpoint, so they have to actually arrive."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    fetch = stub_fetch()
    ingest_url(
        "https://example.com/docs/ba100.html",
        store,
        fetcher=fetch,
        timeout=5.0,
        max_bytes=1024,
    )

    assert fetch.calls[0]["timeout"] == 5.0
    assert fetch.calls[0]["max_bytes"] == 1024
