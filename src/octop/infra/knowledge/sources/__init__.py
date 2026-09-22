"""Folder sources — the connectors, and the one place a kind becomes one.

The design's §4 kinds are ``local``, ``smb`` and ``nfs``. Two of them have a
connector in this build and the third deliberately does not; :func:`build_connector`
is where that decision is made, so nothing above this package has to know which
kind is real.
"""

from __future__ import annotations

from octop.infra.knowledge.sources.base import (
    SourceConnector,
    SourceDependencyMissing,
    SourceEntry,
    SourceError,
    SourceUnsupported,
)
from octop.infra.knowledge.sources.local import LocalFolderConnector
from octop.infra.knowledge.sources.smb import SmbConnector

__all__ = [
    "FOLDER_KINDS",
    "KIND_LOCAL",
    "KIND_NFS",
    "KIND_SMB",
    "LocalFolderConnector",
    "SmbConnector",
    "SourceConnector",
    "SourceDependencyMissing",
    "SourceEntry",
    "SourceError",
    "SourceUnsupported",
    "build_connector",
    "is_folder_kind",
]

KIND_LOCAL = "local"
KIND_SMB = "smb"
KIND_NFS = "nfs"

FOLDER_KINDS: tuple[str, ...] = (KIND_LOCAL, KIND_SMB, KIND_NFS)
"""Kinds that name a folder. ``upload`` / ``url`` / ``connector`` name content."""

_NFS_MESSAGE = (
    "NFS sources are not supported: there is no pure-Python NFS client, and the "
    "one that exists needs the native libnfs library this build does not ship. "
    "Mount the export on the server and add it as a local source instead."
)


def is_folder_kind(kind: str) -> bool:
    """Whether ``kind`` refers to a folder this package can connect to."""
    return (kind or "").strip().lower() in FOLDER_KINDS


def build_connector(
    *,
    kind: str,
    root_path: str = "",
    server: str = "",
    share: str = "",
    username: str = "",
    password: str = "",
) -> SourceConnector:
    """The connector for one source, from its stored fields.

    ``password`` is passed straight through and never echoed: an unreachable
    share must produce an error an administrator can read without the credential
    ending up in a log line (design §4).
    """
    normalized = (kind or "").strip().lower()
    if normalized == KIND_LOCAL:
        return LocalFolderConnector(root_path)
    if normalized == KIND_SMB:
        return SmbConnector(
            server=server,
            share=share,
            root_path=root_path,
            username=username,
            password=password,
        )
    if normalized == KIND_NFS:
        raise SourceUnsupported(_NFS_MESSAGE)
    raise SourceUnsupported(f"unknown source kind: {kind!r}")
