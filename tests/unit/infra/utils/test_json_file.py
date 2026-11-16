"""Unit tests for the shared JSON config helpers (issue #730)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from octop.infra.utils.json_file import (
    JsonFileCorruptError,
    read_json_object,
    write_json_atomic,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")


def test_read_absent_file_returns_none(tmp_path: Path) -> None:
    assert read_json_object(tmp_path / "missing.json") is None


def test_read_valid_object(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"bind_host": "0.0.0.0", "port": 8088}), encoding="utf-8")
    assert read_json_object(path) == {"bind_host": "0.0.0.0", "port": 8088}


def test_read_corrupt_raises_with_position_and_never_leaks_contents(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    # Trailing comma — the classic hand-edit slip. The password must not surface.
    path.write_text('{"database": {"password": "s3cret"},}', encoding="utf-8")
    with pytest.raises(JsonFileCorruptError) as excinfo:
        read_json_object(path)
    err = excinfo.value
    assert err.path == path
    assert "line" in err.detail and "column" in err.detail
    assert "s3cret" not in err.detail
    assert "s3cret" not in str(err)


def test_read_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(JsonFileCorruptError) as excinfo:
        read_json_object(path)
    assert "list" in excinfo.value.detail


def test_write_is_pretty_newline_terminated_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_json_atomic(path, {"a": 1})
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {"a": 1}
    assert text.endswith("\n")
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_short_write_still_commits_a_complete_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"bind_host": "127.0.0.1"}', encoding="utf-8")
    original_write = os.write
    short_write = True

    def write_part(fd: int, data: bytes | memoryview) -> int:
        nonlocal short_write
        if short_write:
            short_write = False
            return original_write(fd, data[:7])
        return original_write(fd, data)

    monkeypatch.setattr("octop.infra.utils.json_file.os.write", write_part)
    expected = {"bind_host": "0.0.0.0", "database": {"driver": "postgresql"}}

    write_json_atomic(path, expected)

    assert read_json_object(path) == expected


def test_write_creates_missing_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.json"
    write_json_atomic(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_failed_replace_leaves_original_intact_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A torn write must not be observable: the old file survives byte-for-byte."""
    path = tmp_path / "config.json"
    original = '{"bind_host": "0.0.0.0", "database": {"driver": "postgresql"}}'
    path.write_text(original, encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("octop.infra.utils.json_file.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_json_atomic(path, {"port": 1})
    assert path.read_text(encoding="utf-8") == original
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


@posix_only
def test_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """A config write must never silently change an existing file's mode."""
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o640)
    write_json_atomic(path, {"a": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@posix_only
def test_write_new_file_uses_umask_default_not_mkstemp_0600(tmp_path: Path) -> None:
    """mkstemp's 0600 must not leak into a new config.json.

    A root-created 0600 file would be unreadable to the service user after
    ``octop service start --scope system``; the two pre-existing config.json
    writers (``db/rebind.py``, ``backup/auto.py``) produce the umask default.
    """
    mask = os.umask(0o022)
    os.umask(mask)
    path = tmp_path / "fresh.json"
    write_json_atomic(path, {"a": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~mask


@posix_only
def test_config_write_does_not_weaken_another_threads_file_permissions(tmp_path: Path) -> None:
    code = dedent("""
        import json
        import os
        from pathlib import Path
        import stat
        import sys
        import threading

        sys.path[:] = json.loads(sys.argv[2])
        from octop.infra.utils.json_file import write_json_atomic

        root = Path(sys.argv[1])
        original_umask = os.umask
        original_umask(0o077)
        start_peer = threading.Event()
        peer_done = threading.Event()
        failures = []

        def interleaved_umask(value):
            previous = original_umask(value)
            if value == 0o022:
                start_peer.set()
                if not peer_done.wait(5):
                    raise RuntimeError("peer creation did not finish")
            return previous

        def writer():
            try:
                write_json_atomic(root / "config.json", {"setting": "saved"})
            except BaseException as error:
                failures.append(error)
            finally:
                start_peer.set()

        def peer():
            try:
                if not start_peer.wait(5):
                    raise RuntimeError("configuration writer did not finish")
                (root / "private.txt").write_text("private fixture", encoding="utf-8")
            except BaseException as error:
                failures.append(error)
            finally:
                peer_done.set()

        os.umask = interleaved_umask
        threads = [threading.Thread(target=writer), threading.Thread(target=peer)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert all(not thread.is_alive() for thread in threads)
        assert not failures, failures
        assert stat.S_IMODE((root / "private.txt").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / "config.json").stat().st_mode) == 0o600
        assert json.loads((root / "config.json").read_text()) == {"setting": "saved"}
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(tmp_path), json.dumps(sys.path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
