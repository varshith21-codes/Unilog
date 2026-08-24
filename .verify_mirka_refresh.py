import logging
import os
import shutil
import tempfile
from pathlib import Path

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

from axiom.ingest import LocalArtifactStore
from axiom.pipeline.retrieval import retrieve_documents
from axiom.retrieve import SerperSearch

ROOT = Path(__file__).resolve().parent
line = next(
    value
    for value in (ROOT / ".env.deploy").read_text(encoding="utf-8").splitlines()
    if value.startswith("AXIOM_SERPER_API_KEY=")
)
os.environ["AXIOM_SERPER_API_KEY"] = line.split("=", 1)[1]

with tempfile.TemporaryDirectory(prefix="axiom-mirka-refresh-") as temp_dir:
    index = Path(temp_dir) / "index.json"
    shutil.copy2(ROOT / "data" / "library" / "index.json", index)
    attempt = retrieve_documents(
        "8896700140",
        store=LocalArtifactStore(ROOT / "data" / "cache" / "artifacts"),
        library_path=index,
        manufacturer="Mirka",
        search=SerperSearch.from_env(),
        renderer=None,
        discover=False,
        refresh_sources=True,
    )

print(f"found={str(attempt.found).lower()}")
print(f"refresh_requested={str(attempt.refresh_requested).lower()}")
print(f"from_library={str(attempt.from_library).lower()}")
print(f"requests_made={attempt.requests_made}")
print(f"document_count={len(attempt.documents)}")
for position, document in enumerate(attempt.documents, start=1):
    print(f"document_{position}_type={document.document.doc_type.value}")
    print(f"document_{position}_pages={document.page_count}")
    print(f"document_{position}_uri={document.document.uri}")
    print(f"document_{position}_contains_sku={str('8896700140' in document.full_text).lower()}")
print(f"primary_uri={attempt.primary.document.uri if attempt.primary else ''}")
print(
    "tds_discovered="
    + str(
        any("TDS_Abranet_Max_Flap_Disc.pdf" in document.document.uri for document in attempt.documents)
    ).lower()
)
