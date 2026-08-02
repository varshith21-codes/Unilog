"""Content-addressed artifact storage.

Raw bytes are kept forever, addressed by their own SHA-256. Two consequences that matter:

* **Citations stay resolvable.** An Enrichment Certificate points at a document hash. If a
  supplier reissues the datasheet under the same filename, the hash differs, so the old
  citation still resolves to the bytes it was actually derived from. Storing by filename
  would silently invalidate every existing citation.
* **Re-ingesting the same file is free.** Identical bytes produce an identical key, so the
  second upload is a no-op rather than a duplicate.

The :class:`ArtifactStore` protocol keeps the pipeline independent of where bytes live.
:class:`LocalArtifactStore` is used for development and tests; an S3-backed implementation
is a drop-in replacement because the interface is deliberately tiny.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Protocol


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Hash a file without loading it into memory — datasheets can be large."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_key(sha256: str, *, suffix: str = "") -> str:
    """Fan out by hash prefix so no single directory or S3 prefix becomes a hotspot."""
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}{suffix}"


class ArtifactStore(Protocol):
    """Minimal byte store. Small on purpose so S3 and filesystem are interchangeable."""

    def put(self, data: bytes, *, suffix: str = "") -> str:
        """Store bytes, returning a stable URI. Idempotent for identical content."""
        ...

    def get(self, uri: str) -> bytes: ...

    def exists(self, uri: str) -> bool: ...


class LocalArtifactStore:
    """Filesystem-backed content-addressed store."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, uri: str) -> Path:
        return self.root / uri.removeprefix("local://")

    def put(self, data: bytes, *, suffix: str = "") -> str:
        uri = "local://" + artifact_key(sha256_bytes(data), suffix=suffix)
        target = self._path_for(uri)
        if target.exists():
            return uri  # identical content already stored; nothing to do
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary sibling then move, so a crash mid-write cannot leave a
        # truncated file sitting at a hash that claims to describe complete content.
        staging = target.with_suffix(target.suffix + ".partial")
        staging.write_bytes(data)
        shutil.move(str(staging), str(target))
        return uri

    def put_file(self, path: Path) -> str:
        return self.put(Path(path).read_bytes(), suffix=Path(path).suffix.lower())

    def get(self, uri: str) -> bytes:
        target = self._path_for(uri)
        if not target.exists():
            raise FileNotFoundError(f"artifact not found: {uri}")
        return target.read_bytes()

    def exists(self, uri: str) -> bool:
        return self._path_for(uri).exists()

    def verify(self, uri: str) -> bool:
        """Confirm stored bytes still hash to the key they are filed under.

        Cheap tamper and corruption detection. If this ever returns False, every citation
        into that document is suspect.
        """
        target = self._path_for(uri)
        if not target.exists():
            return False
        expected = target.stem
        return sha256_file(target) == expected
