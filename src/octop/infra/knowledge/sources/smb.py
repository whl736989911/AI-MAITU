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

from typing import Any

from octop.infra.knowledge.sources.base import (
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
                else:
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
