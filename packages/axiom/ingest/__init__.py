"""Source Fabric: turn every arrival channel into one hashed, attributed artifact."""

from axiom.ingest.columns import (
    RECORD_FIELDS,
    SYNONYMS,
    ColumnMapping,
    ColumnMatch,
    MappingMemory,
    fold_header,
    infer_mapping,
)
from axiom.ingest.fabric import (
    FLAT_FILE_SUFFIXES,
    FlatFile,
    IngestedArtifact,
    IngestError,
    detect_document_type,
    ingest_bytes,
    ingest_file,
    pdf_page_count,
    read_flat_file,
)
from axiom.ingest.store import (
    ArtifactStore,
    LocalArtifactStore,
    artifact_key,
    sha256_bytes,
    sha256_file,
)

__all__ = [
    "FLAT_FILE_SUFFIXES",
    "RECORD_FIELDS",
    "SYNONYMS",
    "ArtifactStore",
    "ColumnMapping",
    "ColumnMatch",
    "FlatFile",
    "IngestError",
    "IngestedArtifact",
    "LocalArtifactStore",
    "MappingMemory",
    "artifact_key",
    "detect_document_type",
    "fold_header",
    "infer_mapping",
    "ingest_bytes",
    "ingest_file",
    "pdf_page_count",
    "read_flat_file",
    "sha256_bytes",
    "sha256_file",
]
