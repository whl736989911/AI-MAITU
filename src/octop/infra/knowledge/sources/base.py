"""What a folder source looks like to everything above it.

A data source is a folder — on the server's own disk or on an SMB share — and a
connector is the one thing that can list and read it. A scan works on
``SourceEntry`` values, so the transports differ in exactly one place instead of
in the scanner, the change detection and the index.

Every path a connector handles is *relative to the source root* and uses ``/``
as its separator, whatever the transport underneath uses. That is the same
shape ``knowledge_documents.path`` already has, and it is what keeps a Windows
share and a POSIX folder from producing two different indexes for one tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SourceEntry:
    """One file or folder inside a source, as the source itself describes it.

    ``modified_at`` is the source's own timestamp in epoch seconds, and ``None``
    when the transport cannot report one — an SMB server is allowed to omit it.
    The scan treats "no timestamp" as "has no timestamp" rather than inventing
    one, because a fabricated timestamp would defeat change detection.
    """

    path: str
    size: int
    modified_at: int | None
    is_dir: bool


class SourceError(RuntimeError):
    """A source could not be reached, listed, or read.

    The message is shown to an administrator, so it must never carry a secret:
    no passwords, no full connection strings, no authorization headers
    (design §4).
    """


class SourceUnsupported(SourceError):
    """The source's kind has no connector in this build."""


class SourceDependencyMissing(SourceError):
    """The connector's optional dependency is not installed."""


class SourceConnector(Protocol):
    """The transport surface a scan needs — nothing more.

    Deliberately small: three methods are what listing, comparing and ingesting
    a tree add up to, and a fake that implements them is enough to test
    everything above this module without a live share.
    """

    def walk(self) -> list[SourceEntry]:
        """Every file and folder under the root, relative to it.

        Recursive, and the root itself is not included. Folders are listed so an
        empty one is still visible to the index.
        """
        ...

    def read_bytes(self, path: str) -> bytes:
        """The full contents of one file, addressed relative to the root."""
        ...

    def test(self) -> str:
        """Prove the connector can reach and list its root.

        Returns a short, secret-free description of what it saw, for the
        administrator who pressed "test". Raises :class:`SourceError` when the
        source is not usable.
        """
        ...
