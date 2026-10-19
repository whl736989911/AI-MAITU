"""Unit tests for the wizard password file lifecycle."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from octop.infra.setup.password_file import (
    WIZARD_FILE_NAME,
    boot_self_heal,
    ensure_password,
    read_password,
)
from octop.infra.utils.legacy_names import LEGACY_LOGIN_FILENAME


def _file(home: Path) -> Path:
    return home / WIZARD_FILE_NAME


def _legacy_file(home: Path) -> Path:
    return home / LEGACY_LOGIN_FILENAME


def test_ensure_password_writes_file_when_missing(tmp_path: Path) -> None:
    pw = ensure_password(tmp_path)
    assert pw is not None
    assert _file(tmp_path).read_text(encoding="utf-8").strip() == pw
    assert len(pw) >= 20  # token_urlsafe(16) ≈ 22 chars


def test_ensure_password_returns_none_when_present(tmp_path: Path) -> None:
    first = ensure_password(tmp_path)
    second = ensure_password(tmp_path)
    assert first is not None
    assert second is None
    assert _file(tmp_path).read_text(encoding="utf-8").strip() == first


def test_file_mode_is_0600(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file mode bits")
    ensure_password(tmp_path)
    mode = stat.S_IMODE(os.stat(_file(tmp_path)).st_mode)
    assert mode == 0o600


def test_read_password_returns_stored_value(tmp_path: Path) -> None:
    pw = ensure_password(tmp_path)
    assert read_password(tmp_path) == pw


def test_read_password_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_password(tmp_path) is None


def test_boot_self_heal_generates_when_no_users(tmp_path: Path) -> None:
    pw = boot_self_heal(tmp_path, user_count=0)
    assert pw is not None
    assert _file(tmp_path).exists()


def test_boot_self_heal_redisplays_existing_password_with_no_users(tmp_path: Path) -> None:
    first = boot_self_heal(tmp_path, user_count=0)
    second = boot_self_heal(tmp_path, user_count=0)
    assert first is not None
    assert second == first
    assert read_password(tmp_path) == first


def test_boot_self_heal_keeps_file_when_users_exist(tmp_path: Path) -> None:
    ensure_password(tmp_path)
    pw = boot_self_heal(tmp_path, user_count=1)
    assert pw is None
    assert _file(tmp_path).exists()


def test_boot_self_heal_no_op_when_users_exist_and_no_file(tmp_path: Path) -> None:
    pw = boot_self_heal(tmp_path, user_count=1)
    assert pw is None
    assert not _file(tmp_path).exists()


def test_wizard_file_name_uses_the_brand_id() -> None:
    assert WIZARD_FILE_NAME == "maitu-login.txt"


def test_read_password_falls_back_to_legacy_file(tmp_path: Path) -> None:
    """LEGACY: an upgraded install may still only have the old file name."""
    _legacy_file(tmp_path).write_text("LegacyPass1\n", encoding="utf-8")
    assert read_password(tmp_path) == "LegacyPass1"


def test_read_password_prefers_current_file(tmp_path: Path) -> None:
    _legacy_file(tmp_path).write_text("LegacyPass1\n", encoding="utf-8")
    _file(tmp_path).write_text("CurrentPass1\n", encoding="utf-8")
    assert read_password(tmp_path) == "CurrentPass1"


def test_ensure_password_renames_legacy_file_and_keeps_value(tmp_path: Path) -> None:
    _legacy_file(tmp_path).write_text("LegacyPass1\n", encoding="utf-8")
    assert ensure_password(tmp_path) is None
    assert not _legacy_file(tmp_path).exists()
    assert _file(tmp_path).read_text(encoding="utf-8").strip() == "LegacyPass1"
    assert read_password(tmp_path) == "LegacyPass1"
