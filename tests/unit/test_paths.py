"""tests/unit/test_paths.py"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from octop.infra.utils import paths as paths_module
from octop.infra.utils.legacy_names import (
    LEGACY_DB_FILENAME,
    LEGACY_HOME_DIRNAME,
    LEGACY_LOG_FILENAME,
    LEGACY_LOGIN_FILENAME,
)
from octop.infra.utils.paths import (
    DB_FILENAME,
    DEFAULT_HOME_DIRNAME,
    LOG_FILENAME,
    LOGIN_FILENAME,
    PathLayout,
    migrate_legacy_home,
    upgrade_legacy_names,
)


def test_root_paths(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    assert p.root == tmp_path / ".maitu"
    assert p.db == tmp_path / ".maitu" / "maitu.db"
    assert p.logs_dir == tmp_path / ".maitu" / "logs"
    assert p.log == tmp_path / ".maitu" / "logs" / "maitu.log"
    assert p.config == tmp_path / ".maitu" / "config.json"


def test_default_names_carry_the_brand_id() -> None:
    assert DEFAULT_HOME_DIRNAME == ".maitu"
    assert DB_FILENAME == "maitu.db"
    assert LOG_FILENAME == "maitu.log"
    assert LOGIN_FILENAME == "maitu-login.txt"


def test_log_dir_creation(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    assert not (tmp_path / ".maitu" / "logs").exists()
    assert p.ensure_log() == tmp_path / ".maitu" / "logs" / "maitu.log"
    assert (tmp_path / ".maitu" / "logs").is_dir()
    assert p.log == tmp_path / ".maitu" / "logs" / "maitu.log"


def test_user_dir_uses_username(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    assert p.user_dir("alice") == tmp_path / ".maitu" / "users" / "alice"


def test_agent_workspace(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    ws = p.agent_workspace("agent01")
    assert ws == tmp_path / ".maitu" / "agents" / "agent01"


def test_ensure_root_creates_dir(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    p.ensure_root()
    assert (tmp_path / ".maitu").is_dir()


def test_ensure_agent_workspace(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    out = p.ensure_agent_workspace("a1")
    assert out.is_dir()
    assert out == tmp_path / ".maitu" / "agents" / "a1"


def test_backups_dir(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    assert p.backups_dir == tmp_path / ".maitu" / "backups"
    assert p.ensure_backups_dir().is_dir()


def test_knowledge_dir(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".maitu")
    assert p.knowledge_dir == tmp_path / ".maitu" / "knowledge"


def test_path_layout_from_env_defaults_to_dot_maitu(monkeypatch) -> None:
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    assert PathLayout.from_env().root == Path.home() / ".maitu"


def test_path_layout_from_env_honors_maitu_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAITU_HOME", str(tmp_path))
    assert PathLayout.from_env().root == tmp_path


def test_path_layout_from_env_honors_legacy_octop_home(monkeypatch, tmp_path: Path) -> None:
    """LEGACY: existing installs pinned the old environment variable."""
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    assert PathLayout.from_env().root == tmp_path


def test_path_layout_from_env_prefers_new_home_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAITU_HOME", str(tmp_path / "new"))
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "old"))
    assert PathLayout.from_env().root == tmp_path / "new"


def _seed_legacy_home(home: Path) -> Path:
    """Create a pre-white-label home with the files the migration must upgrade."""
    legacy = home / LEGACY_HOME_DIRNAME
    (legacy / "logs").mkdir(parents=True)
    (legacy / LEGACY_DB_FILENAME).write_bytes(b"DB-CONTENT")
    (legacy / f"{LEGACY_DB_FILENAME}-wal").write_bytes(b"WAL-CONTENT")
    (legacy / "config.json").write_text(
        json.dumps(
            {"port": 8088, "database": {"driver": "sqlite", "sqlite_path": LEGACY_DB_FILENAME}}
        ),
        encoding="utf-8",
    )
    (legacy / "logs" / LEGACY_LOG_FILENAME).write_text("log-content", encoding="utf-8")
    (legacy / "logs" / f"{LEGACY_LOG_FILENAME}.2026-07-16").write_text("old", encoding="utf-8")
    (home / LEGACY_LOGIN_FILENAME).write_text("LegacyPass1\n", encoding="utf-8")
    return legacy


def test_migrate_moves_legacy_home_and_renames_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    home = Path.home()
    legacy = _seed_legacy_home(home)

    layout = migrate_legacy_home()

    assert layout.root == home / ".maitu"
    assert not legacy.exists()
    assert (layout.root / "maitu.db").read_bytes() == b"DB-CONTENT"
    assert (layout.root / "maitu.db-wal").read_bytes() == b"WAL-CONTENT"
    assert (layout.root / "logs" / "maitu.log").read_text(encoding="utf-8") == "log-content"
    assert (layout.root / "logs" / "maitu.log.2026-07-16").read_text(encoding="utf-8") == "old"
    assert not (layout.root / "logs" / LEGACY_LOG_FILENAME).exists()
    assert (home / "maitu-login.txt").read_text(encoding="utf-8").strip() == "LegacyPass1"
    assert not (home / LEGACY_LOGIN_FILENAME).exists()
    config = json.loads((layout.root / "config.json").read_text(encoding="utf-8"))
    assert config["database"]["sqlite_path"] == "maitu.db"
    # Second run is a no-op.
    assert migrate_legacy_home().root == layout.root


def test_migrate_keeps_legacy_home_when_the_new_home_already_exists(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    home = Path.home()
    legacy = home / LEGACY_HOME_DIRNAME
    legacy.mkdir()
    (legacy / "keep.txt").write_text("legacy", encoding="utf-8")
    new_home = home / ".maitu"
    new_home.mkdir()
    (new_home / "keep.txt").write_text("new", encoding="utf-8")

    layout = migrate_legacy_home()

    assert layout.root == new_home
    assert (legacy / "keep.txt").read_text(encoding="utf-8") == "legacy"
    assert (new_home / "keep.txt").read_text(encoding="utf-8") == "new"


def test_migrate_upgrades_explicit_env_home_in_place(monkeypatch, tmp_path: Path) -> None:
    """A custom legacy home is upgraded without moving customer data."""
    custom = tmp_path / "custom-home"
    custom.mkdir()
    (custom / LEGACY_DB_FILENAME).write_bytes(b"DB-CONTENT")
    (custom / "logs").mkdir()
    (custom / "logs" / LEGACY_LOG_FILENAME).write_text("log", encoding="utf-8")
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.setenv("OCTOP_HOME", str(custom))

    layout = migrate_legacy_home()

    assert layout.root == custom
    assert (custom / "maitu.db").read_bytes() == b"DB-CONTENT"
    assert (custom / "logs" / "maitu.log").exists()
    assert not (Path.home() / ".maitu").exists()


def test_migrate_falls_back_to_legacy_home_when_move_fails(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("MAITU_HOME", raising=False)
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    home = Path.home()
    legacy = _seed_legacy_home(home)

    def _boom(source: Path, target: Path) -> None:
        raise OSError("simulated cross-device failure")

    monkeypatch.setattr(paths_module, "_move_directory", _boom)
    try:
        with caplog.at_level(logging.WARNING, logger="octop.infra.utils.paths"):
            layout = migrate_legacy_home()

        assert layout.root == legacy
        assert not (home / ".maitu").exists()
        # Data is preserved in the legacy directory (renamed in place).
        assert (legacy / "maitu.db").read_bytes() == b"DB-CONTENT"
        assert (home / "maitu-login.txt").exists()
        # Subsequent resolvers keep pointing at the fallback root.
        assert PathLayout.from_env().root == legacy
        assert os.environ["MAITU_HOME"] == str(legacy)
        assert any("migration failed" in record.message for record in caplog.records)
    finally:
        os.environ.pop("MAITU_HOME", None)


def test_upgrade_legacy_names_is_idempotent(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / ".maitu")
    layout.root.mkdir()
    (layout.root / LEGACY_DB_FILENAME).write_bytes(b"DB")
    upgrade_legacy_names(layout)
    upgrade_legacy_names(layout)
    assert (layout.root / "maitu.db").read_bytes() == b"DB"
