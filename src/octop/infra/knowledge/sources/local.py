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

import os
from pathlib import Path

from octop.infra.knowledge.sources.base import SourceEntry, SourceError
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
                entries.extend(self._entry(base / name, is_dir=False) for name in filenames)
        except OSError as exc:
            raise SourceError(f"cannot list the source root: {_reason(exc)}") from exc
        return entries

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._resolve(path).read_bytes()
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {_reason(exc)}") from exc

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
