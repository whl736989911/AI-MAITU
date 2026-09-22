"""Filesystem layout and safe document persistence for knowledge bases."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from octop.infra.utils.paths import PathLayout


def knowledge_base_dir(kb_id: str) -> Path:
    return PathLayout.from_env().knowledge_dir / kb_id


def documents_dir(kb_id: str) -> Path:
    path = knowledge_base_dir(kb_id) / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def document_path(kb_id: str, doc_id: str, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    return documents_dir(kb_id) / f"{doc_id}{suffix}"


def write_document(kb_id: str, doc_id: str, filename: str, content: bytes) -> Path:
    path = document_path(kb_id, doc_id, filename)
    path.write_bytes(content)
    return path


def delete_document_file(kb_id: str, doc_id: str, filename: str) -> None:
    document_path(kb_id, doc_id, filename).unlink(missing_ok=True)


def delete_knowledge_base_files(kb_id: str) -> None:
    shutil.rmtree(knowledge_base_dir(kb_id), ignore_errors=True)


_BLOCK = 1 << 20


def document_digest(content: bytes) -> str:
    """The content hash a document is identified by (design §7.3, §8.1).

    It is what tells a file that was *touched* apart from one that *changed*,
    which size and modification time cannot: re-saving a spreadsheet usually
    moves its timestamp and leaves its bytes alone.
    """
    return hashlib.sha256(content).hexdigest()


def file_digest(path: Path) -> str:
    """The same digest for a file, read in blocks so a large one is not loaded."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()
