"""Task-owned scratch directories, with cross-process leases for crash recovery.

Only directories made by this module are eligible for automatic removal. Atomic
sibling staging files and user workspace content deliberately remain outside it.
"""

from __future__ import annotations

import errno
import importlib
import logging
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from octop.infra.utils.paths import PathLayout

logger = logging.getLogger(__name__)
_MAGIC = b"octop-temp-v1\n"
_LEASE = ".owner.lock"
_NAME = re.compile(r"task-[A-Za-z0-9_-]{0,40}[a-f0-9]{32}\Z")


def _plain_directory(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _scratch_root(root: Path | None, *, create: bool) -> Path:
    target = root if root is not None else PathLayout.from_env().root / "tmp"
    # Refuse redirected scratch trees rather than cleaning another location.
    for parent in reversed((target, *target.parents)):
        if (parent.exists() or parent.is_symlink()) and not _plain_directory(parent):
            raise OSError(f"temporary directory traverses a link or non-directory: {parent}")
    if create:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def _lock(stream: BinaryIO, *, blocking: bool) -> bool:
    try:
        stream.seek(0)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except OSError as exc:
        if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise


def _remove_tree(directory: Path) -> None:
    try:
        shutil.rmtree(directory)
    except PermissionError:
        # Git/download tools can leave read-only files on Windows. Restore
        # owner permissions inside this owned tree only; never chmod a link
        # target or a parent directory outside the workspace.
        pending = [directory]
        while pending:
            current = pending.pop()
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                continue
            if stat.S_ISDIR(info.st_mode):
                current.chmod(stat.S_IMODE(info.st_mode) | stat.S_IRWXU)
                with os.scandir(current) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(info.st_mode):
                current.chmod(stat.S_IMODE(info.st_mode) | stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(directory)


@contextmanager
def managed_tempdir(prefix: str = "task-", *, root: Path | None = None) -> Iterator[Path]:
    """Yield private scratch space and remove it on success, error or cancellation."""
    parent = _scratch_root(root, create=True)
    label = re.sub(r"[^A-Za-z0-9_-]", "-", prefix)[:40]
    directory = parent / f"task-{label}{uuid.uuid4().hex}"
    directory.mkdir(mode=0o700)
    try:
        with (directory / _LEASE).open("xb+") as lease:
            lease.write(_MAGIC)
            lease.flush()
            _lock(lease, blocking=True)
            yield directory
    finally:
        # Closing the handle releases the OS lease, including on BaseException.
        # Windows requires the handle closed before the directory can be removed.
        try:
            _remove_tree(directory)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("temporary workspace cleanup failed: %s", directory, exc_info=True)


def cleanup_stale_tempdirs(*, root: Path | None = None, max_age_seconds: float = 86400) -> int:
    """Remove expired *unleased* owned workspaces; never infer liveness from PID/age."""
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    parent = _scratch_root(root, create=False)
    if not parent.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    with os.scandir(parent) as entries:
        for entry in entries:
            if not _NAME.fullmatch(entry.name):
                continue
            directory = Path(entry.path)
            try:
                if not _plain_directory(directory) or directory.stat().st_mtime > cutoff:
                    continue
                marker = directory / _LEASE
                info = marker.lstat()
                if not stat.S_ISREG(info.st_mode) or (
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    continue
                with marker.open("rb") as lease:
                    if not _lock(lease, blocking=False):
                        continue
                    if lease.read(len(_MAGIC) + 1) != _MAGIC:
                        continue
                    # The worker has exited. Close the lease before removal:
                    # Windows cannot delete a directory containing an open file.
                _remove_tree(directory)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning(
                    "stale temporary workspace cleanup failed: %s", directory, exc_info=True
                )
    return removed
