"""Unit tests for ``workspace_dir`` (independent of ``root_dir`` for harness)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from octop.infra.agents.workspace_dir import (
    DEFAULT_SYSTEM_FILES_PATH,
    LEGACY_AUTH_DIRNAME,
    LEGACY_SYSTEM_FILES_DIRNAME,
    MIGRATED_AUTH_DIRNAME,
    SYSTEM_FILES_DIRNAME,
    agent_auth_dir,
    default_agent_workspace_dir,
    harness_workspace_path,
    migrate_workspace_system_dirs,
    resolve_workspace_host_path,
    scoped_workspace_dir_str,
    seed_workspace_dir_on_create,
    skills_discovery_roots,
    system_dir_relpaths,
    system_files_path_from_config,
    workspace_dir_from_config,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


def _scoped_cfg(root: Path, **extra: object) -> dict:
    return {
        "backend": {
            "type": "local_shell",
            "root_dir": str(root),
            "virtual_mode": True,
        },
        **extra,
    }


def test_create_workspace_does_not_adopt_backend_root(tmp_path: Path) -> None:
    root = tmp_path / "host-root"
    paths = PathLayout(tmp_path / "octop-home")
    cfg = _scoped_cfg(root)
    host = seed_workspace_dir_on_create(cfg, paths=paths, agent_id="J1BT2X")
    assert host == paths.agent_workspace("J1BT2X").resolve()
    assert host.is_dir()
    assert resolve_workspace_host_path(cfg["workspace_dir"], cfg) == host
    assert not root.exists()


def test_host_ops_map_octop_workspaces_under_root(tmp_path: Path) -> None:
    root = tmp_path / "home"
    expected = root / ".octop" / "workspaces" / "J1BT2X"
    expected.mkdir(parents=True)
    cfg = _scoped_cfg(root)
    assert resolve_workspace_host_path("/.octop/workspaces/J1BT2X", cfg) == expected.resolve()


def test_create_rejects_external_workspace_before_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "home"
    custom = tmp_path / "custom-ws"
    paths = PathLayout(tmp_path / "octop-home")
    cfg = _scoped_cfg(root, workspace_dir=str(custom))
    with pytest.raises(OctopError) as caught:
        seed_workspace_dir_on_create(cfg, paths=paths, agent_id="USR1")
    assert caught.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
    assert not custom.exists()
    assert not paths.agent_workspace("USR1").exists()


def test_host_rooted_default_uses_octop_home(tmp_path: Path) -> None:
    paths = PathLayout(tmp_path / ".octop")
    cfg = {
        "backend": {"type": "local_shell", "root_dir": "/", "virtual_mode": True},
    }
    host = seed_workspace_dir_on_create(cfg, paths=paths, agent_id="A1")
    assert cfg["workspace_dir"] == str(host)
    assert host == (tmp_path / ".octop" / "agents" / "A1").resolve()


def test_harness_workspace_keeps_absolute_persisted_value(tmp_path: Path) -> None:
    root = tmp_path / "home"
    cfg = _scoped_cfg(root, workspace_dir=str(tmp_path / "ws"))
    assert harness_workspace_path(cfg["workspace_dir"], cfg) == tmp_path / "ws"


def test_harness_workspace_maps_non_absolute_persisted_value(tmp_path: Path) -> None:
    """Windows cannot express ``/.octop/workspaces/<id>`` as absolute — host-map it."""
    root = tmp_path / "home"
    cfg = _scoped_cfg(root, workspace_dir=".octop/workspaces/W1N")
    assert harness_workspace_path(cfg["workspace_dir"], cfg) == (
        root / ".octop" / "workspaces" / "W1N"
    )


def test_workspace_dir_from_config_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "home"
    paths = PathLayout(tmp_path / "octop-home")
    cfg = _scoped_cfg(root)
    host = default_agent_workspace_dir(paths, "RT001", cfg=cfg)
    cfg["workspace_dir"] = scoped_workspace_dir_str("RT001")
    assert workspace_dir_from_config(cfg, paths=paths, agent_id="RT001") == host.resolve()


def test_workspace_dir_from_config_ensure_false_does_not_mkdir(tmp_path: Path) -> None:
    paths = PathLayout(tmp_path / "octop-home")
    missing = tmp_path / "gone" / "YZQ7X4"
    cfg = {"workspace_dir": str(missing)}
    out = workspace_dir_from_config(cfg, paths=paths, agent_id="YZQ7X4", ensure=False)
    assert out == missing.resolve()
    assert not missing.exists()


def test_default_workspace_ensure_false_does_not_mkdir(tmp_path: Path) -> None:
    paths = PathLayout(tmp_path / "octop-home")
    out = default_agent_workspace_dir(paths, "A1", ensure=False)
    assert out == paths.agent_workspace("A1")
    assert not out.exists()


def test_new_agents_use_the_current_system_directory(tmp_path: Path) -> None:
    assert DEFAULT_SYSTEM_FILES_PATH == SYSTEM_FILES_DIRNAME == ".maitu"
    assert scoped_workspace_dir_str("A1") == "/.maitu/workspaces/A1"

    root = tmp_path / "host-root"
    cfg = _scoped_cfg(root)
    host = default_agent_workspace_dir(PathLayout(tmp_path / "home"), "A1", cfg=cfg)
    assert host == (root / ".maitu" / "workspaces" / "A1").resolve()
    assert host.is_dir()


def test_agent_facing_current_form_resolves_under_the_backend_root(tmp_path: Path) -> None:
    root = tmp_path / "home"
    cfg = _scoped_cfg(root)
    assert (
        resolve_workspace_host_path("/.maitu/workspaces/A1", cfg)
        == (root / ".maitu" / "workspaces" / "A1").resolve()
    )


def test_system_files_path_treats_the_legacy_value_as_the_current_default() -> None:
    assert system_files_path_from_config({"system_files_path": ".octop"}) == ".maitu"
    assert system_files_path_from_config({"system_files_path": "/.octop/"}) == ".maitu"
    assert system_files_path_from_config({"system_files_path": ".maitu"}) == ".maitu"
    assert system_files_path_from_config({}) == ""


def test_system_dir_relpaths_put_the_current_layout_first() -> None:
    assert system_dir_relpaths("workflow.json") == (
        ".maitu/workflow.json",
        ".octop/workflow.json",
    )


def _legacy_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / LEGACY_SYSTEM_FILES_DIRNAME / "skills").mkdir(parents=True)
    (ws / LEGACY_SYSTEM_FILES_DIRNAME / "skills" / "keep.md").write_text("kept", encoding="utf-8")
    legacy_auth = ws / LEGACY_AUTH_DIRNAME
    legacy_auth.mkdir()
    (legacy_auth / "token.json").write_text("{}", encoding="utf-8")
    return ws


def test_migration_renames_both_legacy_directories(tmp_path: Path) -> None:
    ws = _legacy_workspace(tmp_path)

    result = migrate_workspace_system_dirs(ws, {"system_files_path": ".octop"})

    assert result.moved_system and result.moved_auth
    assert result.changed and not result.failures
    assert (ws / ".maitu" / "skills" / "keep.md").read_text(encoding="utf-8") == "kept"
    assert (ws / MIGRATED_AUTH_DIRNAME / "token.json").is_file()
    assert not (ws / LEGACY_SYSTEM_FILES_DIRNAME).exists()
    assert not (ws / LEGACY_AUTH_DIRNAME).exists()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    ws = _legacy_workspace(tmp_path)
    assert migrate_workspace_system_dirs(ws, {"system_files_path": ".octop"}).changed

    second = migrate_workspace_system_dirs(ws, {"system_files_path": ".octop"})

    assert second.changed is False
    assert second.failures == ()


def test_migration_copies_and_keeps_the_old_directory_when_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _legacy_workspace(tmp_path)

    def _cross_device(self: Path, target: Path) -> None:
        raise OSError(18, "cross-device link")

    monkeypatch.setattr(Path, "rename", _cross_device)

    result = migrate_workspace_system_dirs(ws, {"system_files_path": ".octop"})

    assert result.moved_system and result.copied
    assert (ws / ".maitu" / "skills" / "keep.md").read_text(encoding="utf-8") == "kept"
    # The old tree is never destroyed: it stays as the recovery copy.
    assert (ws / LEGACY_SYSTEM_FILES_DIRNAME / "skills" / "keep.md").is_file()
    assert (ws / MIGRATED_AUTH_DIRNAME / "token.json").is_file()
    assert (ws / LEGACY_AUTH_DIRNAME / "token.json").is_file()
    if os.name == "posix":
        mode = (ws / LEGACY_AUTH_DIRNAME).stat().st_mode
        assert not mode & stat.S_IWUSR


def test_migration_leaves_a_current_layout_alone(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / SYSTEM_FILES_DIRNAME / "skills").mkdir(parents=True)
    (ws / SYSTEM_FILES_DIRNAME / "skills" / "keep.md").write_text("kept", encoding="utf-8")

    result = migrate_workspace_system_dirs(ws, {"system_files_path": ".maitu"})

    assert result.changed is False
    assert (ws / SYSTEM_FILES_DIRNAME / "skills" / "keep.md").is_file()


def test_migration_keeps_both_directories_when_the_target_exists(tmp_path: Path) -> None:
    ws = _legacy_workspace(tmp_path)
    (ws / SYSTEM_FILES_DIRNAME).mkdir()

    result = migrate_workspace_system_dirs(ws, {"system_files_path": ".maitu"})

    assert result.moved_system is False
    assert (ws / LEGACY_SYSTEM_FILES_DIRNAME).is_dir()


def test_agent_auth_dir_prefers_a_populated_canonical_directory(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    canonical = ws / ".maitu" / "auth"
    canonical.mkdir(parents=True)
    (canonical / "token.json").write_text("{}", encoding="utf-8")
    legacy = ws / LEGACY_AUTH_DIRNAME
    legacy.mkdir()
    (legacy / "token.json").write_text("{}", encoding="utf-8")

    assert agent_auth_dir(ws, {"system_files_path": ".octop"}) == canonical


def test_agent_auth_dir_reads_either_standalone_legacy_directory(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    migrated = ws / MIGRATED_AUTH_DIRNAME
    migrated.mkdir()
    (migrated / "token.json").write_text("{}", encoding="utf-8")
    assert agent_auth_dir(ws, {"system_files_path": ".maitu"}) == migrated

    old = tmp_path / "old"
    old.mkdir()
    legacy = old / LEGACY_AUTH_DIRNAME
    legacy.mkdir()
    (legacy / "token.json").write_text("{}", encoding="utf-8")
    assert agent_auth_dir(old, {"system_files_path": ".maitu"}) == legacy


def test_skills_discovery_roots_include_the_legacy_dir_only_when_present(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    assert skills_discovery_roots(ws) == [
        ws / SYSTEM_FILES_DIRNAME / "skills",
        ws / "skills",
    ]

    legacy = ws / LEGACY_SYSTEM_FILES_DIRNAME / "skills"
    legacy.mkdir(parents=True)
    assert skills_discovery_roots(ws)[-1] == legacy
