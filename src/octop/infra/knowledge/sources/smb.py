"""An SMB/CIFS share, reached directly rather than through an OS mount.

Design §4 asks the platform to connect to SMB itself instead of depending on
whatever the operating system pre-mounted, and ``smbprotocol``'s high-level
``smbclient`` does that: it speaks SMB2/3 in pure Python, so a Windows server
and a Linux one behave the same and no mount is required.

Two rules shape this module:

* **Credentials travel as call arguments, never in the path.** The tempting
  ``smb://user:password@host/share`` form puts the secret inside a string that
  ends up in exception messages and tracebacks; arguments do not.
* **Messages are secret-free.** Every error this module raises carries the
  server, share and path — never the username's password (design §4).

``smbclient`` is optional — a deployment that only indexes local folders never
connects to a share — so this module imports it lazily and says so when it is
absent, rather than making the whole platform fail to start. The extra that
would declare it is not in ``pyproject.toml`` yet (see the note there); until
it is, the message below is what an operator acts on.
"""

from __future__ import annotations

import errno
import os
import shutil
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from types import TracebackType
from typing import Any

from octop.infra.knowledge.sources.base import (
    UPLOAD_TEMP_PREFIX,
    SourceDependencyMissing,
    SourceEntry,
    SourceError,
)

_MISSING_DEPENDENCY = (
    "SMB sources need the smbprotocol package, which is not installed. "
    "Install it with: pip install smbprotocol"
)


def _smbclient() -> Any:
    try:
        import smbclient
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SourceDependencyMissing(_MISSING_DEPENDENCY) from exc
    return smbclient


class SmbConnector:
    """A folder on an SMB share."""

    def __init__(
        self,
        *,
        server: str,
        share: str,
        root_path: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        self._server = _required(server, "a server")
        self._share = _required(share, "a share name")
        self._root = _clean_relative(root_path)
        self._username = username or ""
        self._password = password or ""

    @property
    def unc_root(self) -> str:
        """The share root as a UNC path — no credential is ever part of it."""
        return "\\\\" + self._server + "\\" + self._share

    def walk(self) -> list[SourceEntry]:
        client = _smbclient()
        entries: list[SourceEntry] = []
        pending = [self._root] if self._root else [""]
        while pending:
            folder = pending.pop()
            for entry in self._scandir(client, folder):
                # Built from the folder being scanned and the entry's *name*:
                # ``DirEntry.path`` is the full path, UNC prefix included, and
                # returning that as a source path would put the server and share
                # into every stored path.
                child = f"{folder}/{entry.name}" if folder else str(entry.name)
                if entry.is_dir():
                    pending.append(child)
                    entries.append(SourceEntry(child, 0, None, True))
                # A staging file is a half-written upload: invisible to scans
                # until publication turns it into the real name.
                elif not entry.name.startswith(UPLOAD_TEMP_PREFIX):
                    entries.append(self._file_entry(client, child))
        return entries

    def read_bytes(self, path: str) -> bytes:
        client = _smbclient()
        relative = _clean_relative(path)
        if not relative:
            raise SourceError("empty source path")
        try:
            with client.open_file(self._unc(relative), mode="rb", **self._credentials()) as handle:
                # Declared, not inferred: ``client`` is Any, and returning an Any
                # from a function promising bytes is what ``warn_return_any`` is
                # for — the handle really does yield bytes.
                data: bytes = handle.read()
        except Exception as exc:
            raise SourceError(f"cannot read {relative} on {self._share}: {_reason(exc)}") from exc
        return data

    def copy_to(self, path: str, destination: Path) -> None:
        client = _smbclient()
        relative = _clean_relative(path)
        if not relative:
            raise SourceError("empty source path")
        try:
            with (
                client.open_file(self._unc(relative), mode="rb", **self._credentials()) as source,
                destination.open("wb") as output,
            ):
                shutil.copyfileobj(source, output, length=1 << 20)
        except Exception as exc:
            raise SourceError(f"cannot read {relative} on {self._share}: {_reason(exc)}") from exc

    def stage_upload(self, path: str, content: bytes) -> AbstractContextManager[SourceEntry]:
        """Stage an upload inside its destination folder. See the protocol."""
        return _SmbUpload(self, path, content)

    def test(self) -> str:
        client = _smbclient()
        entries = self._scandir(client, self._root)
        return f"{len(entries)} item(s) in {self.unc_root}" + (
            f"\\{self._root}" if self._root else ""
        )

    def _credentials(self) -> dict[str, str]:
        """Kwargs ``smbclient`` authenticates with.

        The username carries any domain (``DOMAIN\\user``), which is how SMB
        names an account and saves a separate field nothing else needs.
        """
        kwargs: dict[str, str] = {}
        if self._username:
            kwargs["username"] = self._username
        if self._password:
            kwargs["password"] = self._password
        return kwargs

    def _unc(self, relative: str) -> str:
        return self.unc_root + ("\\" + relative.replace("/", "\\") if relative else "")

    def _scandir(self, client: Any, folder: str) -> list[Any]:
        try:
            return list(client.scandir(self._unc(folder), **self._credentials()))
        except Exception as exc:
            target = self._unc(folder)
            raise SourceError(f"cannot list {target}: {_reason(exc)}") from exc

    def _file_entry(self, client: Any, path: str) -> SourceEntry:
        try:
            info = client.stat(self._unc(path), **self._credentials())
        except Exception as exc:
            raise SourceError(f"cannot stat {path} on {self._share}: {_reason(exc)}") from exc
        return SourceEntry(
            path=path,
            size=int(getattr(info, "st_size", 0)),
            modified_at=_mtime(info),
            is_dir=False,
        )


_FILE_ATTRIBUTE_REPARSE_POINT = 0x0000_0400


class _SmbUpload:
    """Staging state of one :meth:`SmbConnector.stage_upload` call.

    The bytes are staged in the destination folder itself under the reserved
    prefix, and a clean exit publishes them with ``smbclient.rename``, which
    sends ``FileRenameInformation`` with ``ReplaceIfExists`` false. That makes
    the rename the atomic no-overwrite decision: a target that exists — whether
    it was there before the upload started or is created while the context is
    open — fails the rename with a collision instead of being replaced, and the
    staged modification time survives the move.
    """

    def __init__(self, connector: SmbConnector, path: str, content: bytes) -> None:
        self._connector = connector
        self._path = path
        self._content = content
        self._client: Any = None
        self._staged = ""
        self._final = ""

    def __enter__(self) -> SourceEntry:
        client = _smbclient()
        self._client = client
        relative = _upload_relative(self._path, self._connector._root)
        parts = relative.split("/")
        # A reparse point in a middle component — a mount point or symlink the
        # administrator did not configure — would redirect the write to wherever
        # it points while every textual check still looks contained. The
        # configured root itself is the trusted boundary.
        for index in range(1, len(parts)):
            folder = "/".join(parts[:index])
            if self._connector._root and folder == self._connector._root:
                continue
            self._assert_not_reparse(client, folder)
        staged = ""
        try:
            parent = self._connector._unc("/".join(parts[:-1]))
            staged = f"{parent}\\{UPLOAD_TEMP_PREFIX}{uuid.uuid4().hex}"
            with client.open_file(staged, mode="wb", **self._connector._credentials()) as handle:
                handle.write(self._content)
            info = client.stat(staged, **self._connector._credentials())
        except Exception as exc:
            self._discard(staged)
            raise SourceError(
                f"cannot stage {self._path} on {self._connector._share}: {_reason(exc)}"
            ) from exc
        self._staged = staged
        self._final = self._connector._unc(relative)
        return SourceEntry(
            path=relative,
            size=int(getattr(info, "st_size", 0)),
            modified_at=_mtime(info),
            is_dir=False,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        staged = self._staged
        self._staged = ""
        if not staged:
            return
        try:
            if exc_type is None:
                self._publish(staged)
        finally:
            # Either the staged name has been renamed onto the final one, or
            # publication failed and it must leave no trace behind.
            self._discard(staged)

    def _publish(self, staged: str) -> None:
        """Rename the staging file onto its final name, never replacing a file."""
        try:
            self._client.rename(staged, self._final, **self._connector._credentials())
        except Exception as rename_exc:
            if self._target_exists(self._final):
                # Created before the upload or mid-flight: either way the
                # existing file won, and it is exactly as it was.
                raise FileExistsError(
                    errno.EEXIST, os.strerror(errno.EEXIST), self._path
                ) from rename_exc
            raise SourceError(
                f"cannot publish {self._path} on {self._connector._share}: {_reason(rename_exc)}"
            ) from rename_exc

    def _assert_not_reparse(self, client: Any, folder: str) -> None:
        try:
            info = client.lstat(self._connector._unc(folder), **self._connector._credentials())
        except Exception as exc:
            # A missing parent is the common case: staging never creates one.
            raise SourceError(
                f"cannot stage {self._path} on {self._connector._share}: {_reason(exc)}"
            ) from exc
        if getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT or getattr(
            info, "st_reparse_tag", 0
        ):
            raise SourceError(f"source path traverses a reparse point: {self._path}")

    def _target_exists(self, final: str) -> bool:
        try:
            self._client.stat(final, **self._connector._credentials())
        except Exception:
            # The rename error is the honest signal when even the check fails.
            return False
        return True

    def _discard(self, staged: str) -> None:
        """Remove only this context's staging file; never the final target.

        A cleanup failure is swallowed on purpose: the file carries the
        reserved prefix, so it stays invisible to scans, and a failed upload
        must surface its own error rather than this one.
        """
        if not staged:
            return
        with suppress(Exception):
            self._client.unlink(staged, **self._connector._credentials())


def _upload_relative(path: str, configured_root: str) -> str:
    """Validate an upload target, returned in the ``walk()`` path convention.

    Stricter than :func:`_clean_relative`, which stays the read path's
    normalizer: an upload is a write, so absolute forms, Windows drive letters
    and alternate data streams are refused outright. ``walk()`` paths carry the
    configured root as their first component, and a target outside it — say
    ``other/x.txt`` while the source is scoped to ``reports`` — would write to
    a folder the source was never configured for, so that is refused too.
    """
    text = (path or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise SourceError(f"source path must be relative: {path}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise SourceError(f"invalid source path: {path}")
    if any(":" in part for part in parts):
        raise SourceError(f"source path must not name an alternate data stream: {path}")
    if parts[-1].startswith(UPLOAD_TEMP_PREFIX):
        raise SourceError(f"source file name uses the reserved upload prefix: {path}")
    relative = "/".join(parts)
    inside_root = (
        not configured_root
        or relative == configured_root
        or relative.startswith(configured_root + "/")
    )
    if not inside_root:
        raise SourceError(f"source path escapes the configured source root: {path}")
    return relative


def _mtime(info: Any) -> int | None:
    """The source's own modification time, or ``None`` when it reports none.

    A share is allowed not to expose one, and inventing a value here would make
    change detection always agree with itself — every file would look unchanged.
    """
    value = getattr(info, "st_mtime", None)
    return int(value) if value is not None else None


def _required(value: str, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise SourceError(f"an SMB source needs {what}")
    if any(char in text for char in "\\/\0"):
        raise SourceError(f"{what} contains an invalid character")
    return text


def _clean_relative(path: str) -> str:
    """A source-relative path, refused when it tries to leave the share."""
    text = (path or "").replace("\\", "/").strip().strip("/")
    if not text:
        return ""
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise SourceError(f"invalid source path: {path}")
    return "/".join(parts)


def _reason(exc: Exception) -> str:
    """A secret-free reason for an administrator to read.

    ``smbclient``'s own messages name the share and the path, which is what is
    useful here; the credential never entered the path, so it cannot be in them.
    """
    text = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text}"
