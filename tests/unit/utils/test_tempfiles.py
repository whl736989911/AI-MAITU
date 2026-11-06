from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from octop.infra.utils.tempfiles import cleanup_stale_tempdirs, managed_tempdir


def test_workspace_is_removed_but_neighboring_user_files_survive(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    root.mkdir()
    original = root / "user-original.txt"
    original.write_bytes(b"keep")
    with managed_tempdir("parse-", root=root) as directory:
        (directory / "nested").mkdir()
        (directory / "nested" / "converted.pdf").write_bytes(b"intermediate")
        assert cleanup_stale_tempdirs(root=root, max_age_seconds=0) == 0
        assert (directory / "nested" / "converted.pdf").read_bytes() == b"intermediate"
    assert not directory.exists()
    assert original.read_bytes() == b"keep"


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_failure_and_cancellation_remove_intermediate_files(
    tmp_path: Path, error: type[BaseException]
) -> None:
    with pytest.raises(error), managed_tempdir(root=tmp_path / "tmp") as directory:
        (directory / "partial-download").write_bytes(b"partial")
        raise error()
    assert not directory.exists()


@pytest.mark.parametrize("read_only", [False, True])
def test_live_process_is_protected_and_crashed_process_is_reaped(
    tmp_path: Path, read_only: bool
) -> None:
    root = tmp_path / "tmp"
    script = (
        "import sys,time,stat\n"
        "from pathlib import Path\n"
        "from octop.infra.utils.tempfiles import managed_tempdir\n"
        "with managed_tempdir('worker-', root=Path(sys.argv[1])) as directory:\n"
        "    (directory / 'result').write_bytes(b'working')\n"
        "    if sys.argv[2] == 'readonly':\n"
        "        for item in directory.iterdir():\n"
        "            item.chmod(stat.S_IREAD)\n"
        "    print(directory, flush=True)\n"
        "    time.sleep(60)\n"
    )
    worker = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(root), "readonly" if read_only else "normal"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        line = worker.stdout.readline().strip()
        assert line, "worker did not acquire a temporary workspace"
        directory = Path(line)
        old = time.time() - 172800
        os.utime(directory, (old, old))
        assert cleanup_stale_tempdirs(root=root) == 0
        assert (directory / "result").read_bytes() == b"working"
        worker.kill()
        worker.communicate(timeout=10)
        assert cleanup_stale_tempdirs(root=root) == 1
        assert not directory.exists()
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=10)


def test_unknown_and_recent_directories_are_not_deleted(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    root.mkdir()
    unknown = root / ("task-" + "a" * 32)
    unknown.mkdir()
    (unknown / ".owner.lock").write_bytes(b"not-a-platform-lease\n")
    old = time.time() - 172800
    os.utime(unknown, (old, old))
    with managed_tempdir(root=root) as current:
        assert cleanup_stale_tempdirs(root=root) == 0
        assert current.is_dir()
    assert (unknown / ".owner.lock").read_bytes() == b"not-a-platform-lease\n"


def test_absent_root_is_not_created_by_sweep(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    assert cleanup_stale_tempdirs(root=root) == 0
    assert not root.exists()


@pytest.mark.skipif(os.name != "posix", reason="Symlink creation requires privileges on Windows")
def test_sweep_refuses_redirected_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "original"
    original.write_bytes(b"keep")
    root = tmp_path / "tmp"
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="link or non-directory"):
        cleanup_stale_tempdirs(root=root)
    assert original.read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "posix", reason="Symlink creation requires privileges on Windows")
def test_workdir_removal_does_not_follow_nested_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "original"
    outside.write_bytes(b"keep")
    with managed_tempdir(root=tmp_path / "tmp") as directory:
        (directory / "alias").symlink_to(outside)
    assert outside.read_bytes() == b"keep"


def test_readonly_conversion_files_are_removed_without_changing_user_files(tmp_path: Path) -> None:
    original = tmp_path / "original.bin"
    original.write_bytes(b"keep")
    original.chmod(stat.S_IRUSR)
    try:
        with managed_tempdir("readonly-", root=tmp_path / "tmp") as directory:
            nested = directory / "converted"
            nested.mkdir()
            output = nested / "output.bin"
            output.write_bytes(b"intermediate")
            output.chmod(stat.S_IRUSR)
            nested.chmod(stat.S_IRUSR | stat.S_IXUSR)
        assert not directory.exists()
        assert original.read_bytes() == b"keep"
        assert original.stat().st_mode & stat.S_IWUSR == 0
    finally:
        original.chmod(stat.S_IRUSR | stat.S_IWUSR)
