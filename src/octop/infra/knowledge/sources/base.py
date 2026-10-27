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

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

UPLOAD_TEMP_PREFIX = ".octop-upload-"
"""Reserved name prefix for in-flight upload staging files.

A connector stages an upload inside the destination folder itself under this
prefix and keeps such files out of :meth:`SourceConnector.walk`, so a scan
never indexes partially written bytes. The prefix is reserved for staging: an
upload whose final name starts with it is refused, and a staging file left
behind by a crash stays invisible to scans until an administrator removes it.
"""


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
    """The transport surface a scan and an upload need — nothing more.

    Deliberately small: four methods are what listing, comparing, ingesting and
    receiving one file add up to, and a fake that implements them is enough to
    test everything above this module without a live share.
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

    def copy_to(self, path: str, destination: Path) -> None:
        """Stream one source file into a temporary parser input without loading it whole."""
        ...

    def stage_upload(self, path: str, content: bytes) -> AbstractContextManager[SourceEntry]:
        """Stage ``content`` for publication at ``path`` when the block exits.

        The bytes are staged in the destination folder itself under the
        reserved :data:`UPLOAD_TEMP_PREFIX` — never in a shared temporary
        directory, so publication stays a same-volume rename — and the returned
        context manager publishes them, atomically, only when the block exits
        without an exception. Publication never replaces an existing file: a
        collision, including one created while the context is open, raises
        :class:`FileExistsError` and leaves the existing file untouched.

        The yielded entry describes the *final* path with the staged file's
        size and modification time, both of which survive publication.

        ``path`` uses the same source-relative convention as :meth:`walk`. The
        destination's parent must already exist — staging never creates
        folders — and a target that is absolute, escapes the source root,
        traverses a symlink or reparse point out of it, names a Windows
        alternate data stream or claims the reserved prefix is refused with
        :class:`SourceError`, as is any other I/O failure. When the block fails
        or publication is refused, every staging file this call created is
        removed and nothing else in the source is touched.
        """
        ...

    def test(self) -> str:
        """Prove the connector can reach and list its root.

        Returns a short, secret-free description of what it saw, for the
        administrator who pressed "test". Raises :class:`SourceError` when the
        source is not usable.
        """
        ...
