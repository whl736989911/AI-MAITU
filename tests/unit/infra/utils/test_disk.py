"""The disk headroom guard refuses work a full filesystem cannot finish."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils import disk


def test_require_headroom_allows_plenty_of_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: _usage(free=10 << 30))
    assert disk.require_headroom(tmp_path, minimum_bytes=1 << 30) is None


def test_require_headroom_refuses_a_full_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: _usage(free=200 << 20))

    with pytest.raises(OctopError) as exc_info:
        disk.require_headroom(tmp_path, minimum_bytes=512 << 20, label="chat turn")

    error = exc_info.value
    assert error.code is ErrorCode.DISK_FULL
    assert error.status == 507
    assert error.details["available_bytes"] == 200 << 20
    assert error.details["required_bytes"] == 512 << 20
    assert str(tmp_path) in error.details["path"]
    assert "chat turn" in error.message


def test_unmeasurable_backend_never_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote workspaces have no local filesystem to measure."""

    def _unmeasurable(_p: object) -> object:
        raise OSError("no such filesystem")

    monkeypatch.setattr(disk.shutil, "disk_usage", _unmeasurable)
    assert disk.available_bytes(tmp_path) is None
    assert disk.require_headroom(tmp_path, minimum_bytes=1 << 30) is None


def _usage(*, free: int) -> object:
    total = free + (8 << 30)
    return type("Usage", (), {"total": total, "used": total - free, "free": free})()
