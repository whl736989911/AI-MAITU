"""Unit tests for ``workspace_dir`` (independent of ``root_dir`` for harness)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.agents.workspace_dir import (
    default_agent_workspace_dir,
    harness_workspace_path,
    resolve_workspace_host_path,
    scoped_workspace_dir_str,
    seed_workspace_dir_on_create,
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
