"""A folder on the server's own filesystem.

The design's first source kind (§4). Two guards matter here, and both are about
what an administrator cannot accidentally index:

* ``assert_safe_host_path`` — the denylist the agent workspace picker already
  uses, so ``/etc`` and ``/proc`` are not selectable.
* the platform's own ``OCTOP_HOME`` — indexing it would put the database, the
  Fernet key in ``secrets`` and every stored credential into a searchable index.

Neither is a re-check of the caller's permissions: those are the *transport*
account's reach, and design §5.3 keeps that separate from who inside the
platform may read the result.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from types import TracebackType

from octop.infra.knowledge.sources.base import (
    UPLOAD_TEMP_PREFIX,
    SourceEntry,
    SourceError,
)
from octop.infra.utils.host_dirs import assert_safe_host_path
from octop.infra.utils.paths import PathLayout


class LocalFolderConnector:
    """A directory the server can already read."""

    def __init__(self, root_path: str) -> None:
        root_text = (root_path or "").strip()
        if not root_text:
            raise SourceError("a local source needs a root path")
        # Absolute, before the guard below: ``assert_safe_host_path`` realpaths
        # its input, so a relative root would be resolved against whatever
        # working directory the server was started in and the source would point
        # somewhere different depending on how it was launched.
        if not os.path.isabs(root_text):
            raise SourceError("a local source needs an absolute root path")
        try:
            root = assert_safe_host_path(root_text)
        except ValueError as exc:
            raise SourceError(str(exc)) from exc
        _assert_not_platform_home(root)
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def walk(self) -> list[SourceEntry]:
        if not self._root.is_dir():
            raise SourceError(f"source root is not a directory: {self._root}")
        entries: list[SourceEntry] = []
        try:
            # ``followlinks`` stays off (the default): a symlink loop would
            # otherwise make a scan unbounded.
            for current, dirnames, filenames in os.walk(self._root):
                base = Path(current)
                entries.extend(self._entry(base / name, is_dir=True) for name in dirnames)
                # A staging file is a half-written upload: invisible to scans
                # until publication turns it into the real name.
                entries.extend(
                    self._entry(base / name, is_dir=False)
                    for name in filenames
                    if not name.startswith(UPLOAD_TEMP_PREFIX)
                )
        except OSError as exc:
            raise SourceError(f"cannot list the source root: {_reason(exc)}") from exc
        return entries

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._resolve(path).read_bytes()
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {_reason(exc)}") from exc

    def copy_to(self, path: str, destination: Path) -> None:
        try:
            with self._resolve(path).open("rb") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1 << 20)
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {_reason(exc)}") from exc

    def stage_upload(self, path: str, content: bytes) -> AbstractContextManager[SourceEntry]:
        """Stage an upload inside its destination folder. See the protocol."""
        return _LocalUpload(self, path, content)

    def test(self) -> str:
        """Reachability, not a full scan: listing the root is the cheap proof.

        A deep walk here would make "test" as expensive as the scan it is
        meant to precede, on a share that may hold a million files.
        """
        if not self._root.exists():
            raise SourceError(f"source root does not exist: {self._root}")
        if not self._root.is_dir():
            raise SourceError(f"source root is not a directory: {self._root}")
        try:
            names = os.listdir(self._root)
        except OSError as exc:
            raise SourceError(f"cannot list the source root: {_reason(exc)}") from exc
        return f"{len(names)} item(s) under {self._root}"

    def _entry(self, absolute: Path, *, is_dir: bool) -> SourceEntry:
        try:
            info = absolute.stat()
        except OSError as exc:
            raise SourceError(f"cannot stat {absolute.name}: {_reason(exc)}") from exc
        return SourceEntry(
            path=self._relative(absolute),
            size=0 if is_dir else int(info.st_size),
            modified_at=int(info.st_mtime),
            is_dir=is_dir,
        )

    def _relative(self, absolute: Path) -> str:
        return absolute.relative_to(self._root).as_posix()

    def _resolve(self, path: str) -> Path:
        """The absolute path of a source-relative one, confined to the root.

        Containment is ``realpath`` + ``startswith`` rather than ``resolve()``
        alone: the same pattern ``host_dirs`` documents, and the one that
        survives a symlink inside the source pointing out of it.
        """
        relative = _relative_parts(path)
        base = os.path.realpath(os.fspath(self._root))
        resolved = os.path.realpath(os.path.join(base, *relative))
        if resolved != base and not resolved.startswith(base + os.sep):
            raise SourceError(f"source path escapes the source root: {path}")
        return Path(resolved)


class _LocalUpload:
    """Staging state of one :meth:`LocalFolderConnector.stage_upload` call.

    The bytes land in the destination folder itself under the reserved prefix —
    same directory as the final file, so publication stays a same-volume
    operation — and a clean exit publishes them by hard-linking the staged file
    onto its final name. A link is the one primitive that is atomic *and*
    refuses an existing destination on both POSIX and Windows, and the staged
    modification time travels with the inode it points at. Where a filesystem
    has no hard links (FAT, a mounted NAS), publication falls back to the
    platform's no-replace rename — and fails closed where the platform has
    none, because a plain rename could silently replace an existing file.
    """

    def __init__(self, connector: LocalFolderConnector, path: str, content: bytes) -> None:
        self._connector = connector
        self._path = path
        self._content = content
        self._final = Path()
        self._staged = Path()

    def __enter__(self) -> SourceEntry:
        parts = _relative_parts(self._path)
        # An upload is a write, so it is held to a stricter shape than the read
        # paths: an NTFS alternate data stream ("file.txt:stream") would quietly
        # receive bytes the index will never see.
        if any(":" in part for part in parts):
            raise SourceError(f"source path must not name an alternate data stream: {self._path}")
        if parts[-1].startswith(UPLOAD_TEMP_PREFIX):
            raise SourceError(f"source file name uses the reserved upload prefix: {self._path}")
        final = self._connector._resolve(self._path)
        parent = final.parent
        if not parent.is_dir():
            # Creating parents would publish into a folder no scan has ever
            # agreed to index, one ``..``-like surprise at a time.
            raise SourceError(f"destination folder does not exist: {self._path}")
        staged = parent / f"{UPLOAD_TEMP_PREFIX}{uuid.uuid4().hex}"
        self._final = final
        self._staged = staged
        try:
            # Exclusive create: the staged name is a fresh uuid, and "xb" turns
            # the practically impossible collision into an error instead of a
            # clobber of a concurrent upload's staging file.
            with staged.open("xb") as handle:
                handle.write(self._content)
            info = staged.stat()
        except OSError as exc:
            self._discard()
            raise SourceError(f"cannot stage {self._path}: {_reason(exc)}") from exc
        return SourceEntry(
            path="/".join(parts),
            size=int(info.st_size),
            modified_at=int(info.st_mtime),
            is_dir=False,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        staged, final = self._staged, self._final
        self._staged = Path()
        if not staged.name:
            return
        try:
            if exc_type is None:
                self._publish(staged, final)
        finally:
            # Either the staged name is now a hard link that has done its job,
            # or publication failed and it must leave no trace behind.
            _discard_file(staged)

    def _publish(self, staged: Path, final: Path) -> None:
        """Make the staged bytes visible as ``final``, never replacing a file."""
        try:
            os.link(staged, final)
            return
        except FileExistsError:
            raise
        except OSError as link_exc:
            self._publish_without_link(staged, final, link_exc)

    def _publish_without_link(self, staged: Path, final: Path, link_exc: OSError) -> None:
        """Publish on a filesystem that refused the hard link, or refuse to."""
        try:
            if os.name == "nt":
                # A filesystem without hard links (FAT, some network mounts):
                # on Windows ``os.rename`` itself refuses an existing
                # destination, so the no-overwrite guarantee survives.
                os.rename(staged, final)
            else:
                # POSIX: renameat2 with RENAME_NOREPLACE is the one kernel
                # primitive that stays atomic without replacing. Where it does
                # not exist, publishing fails closed — a plain rename here
                # could silently replace an existing file.
                _rename_noreplace(staged, final)
        except FileExistsError:
            raise
        except OSError as exc:
            raise SourceError(
                f"cannot publish {self._path}: this filesystem offers no safe "
                f"no-overwrite publication (link: {_reason(link_exc)}; "
                f"rename: {_reason(exc)})"
            ) from exc

    def _discard(self) -> None:
        _discard_file(self._staged)
        self._staged = Path()


def _discard_file(path: Path) -> None:
    """Remove one staging file, tolerating a name that never got created.

    A cleanup failure is swallowed on purpose: after a successful publication
    the uploaded file already exists under its real name, and a failed upload
    must surface its own error rather than this one. Either way the file
    carries the reserved prefix, so it stays invisible to scans, and nothing
    but the exact staged path is ever removed — the final target is out of
    reach by construction.
    """
    with suppress(OSError):
        path.unlink()


def _rename_noreplace(staged: Path, final: Path) -> None:
    """Rename ``staged`` onto ``final``, refusing an existing destination.

    A stdlib-only call to ``renameat2(RENAME_NOREPLACE)`` through ``ctypes``:
    glibc has exposed the symbol since 2.28 and musl since 1.1.24, and the
    kernel turns an existing destination into ``EEXIST`` instead of a
    replacement. Where the platform lacks the symbol, this raises ``ENOSYS``
    so the caller can fail closed rather than risk a replacing rename.
    """
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "renameat2 is not available on this platform") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100  # AT_FDCWD: resolve both paths against the current root
    rename_noreplace = 1
    if renameat2(at_fdcwd, os.fsencode(staged), at_fdcwd, os.fsencode(final), rename_noreplace):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), os.fspath(staged), None, os.fspath(final))


def _relative_parts(path: str) -> tuple[str, ...]:
    """A source-relative path as parts, refusing anything that is not one."""
    text = (path or "").replace("\\", "/").strip().strip("/")
    if not text:
        raise SourceError("empty source path")
    parts = tuple(part for part in text.split("/") if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise SourceError(f"invalid source path: {path}")
    if os.path.isabs(path) or (len(parts[0]) == 2 and parts[0][1] == ":"):
        raise SourceError(f"source path must be relative: {path}")
    return parts


def _assert_not_platform_home(root: Path) -> None:
    """Refuse a root that would index the platform's own data directory."""
    try:
        home = PathLayout.from_env().root
    except Exception:  # pragma: no cover - path layout only fails without a home
        return
    home_real = os.path.realpath(os.fspath(home))
    root_real = os.path.realpath(os.fspath(root))
    if root_real == home_real or root_real.startswith(home_real + os.sep):
        raise SourceError("a source root cannot be the platform's own data directory")
    if home_real.startswith(root_real + os.sep):
        raise SourceError("a source root cannot contain the platform's own data directory")


def _reason(exc: OSError) -> str:
    return exc.strerror or exc.__class__.__name__
