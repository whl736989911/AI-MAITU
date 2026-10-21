"""Workspace I/O path resolution for download / file APIs."""

from __future__ import annotations

from types import SimpleNamespace

from octop.api.routers.workspace import (
    _agent_facing_virtual_workspace_rel,
    _workspace_io_path,
)


class _Registry:
    def __init__(self, config: dict[str, str]) -> None:
        self.config = config

    def get_config(self, _agent_id: str) -> dict[str, str]:
        return self.config


def _server(workspace_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        app_runtime=SimpleNamespace(agent_registry=_Registry({"workspace_dir": workspace_dir}))
    )


def test_agent_facing_virtual_root_maps_only_its_configured_namespace() -> None:
    server = _server("/.maitu/workspaces/agt_current")
    assert (
        _agent_facing_virtual_workspace_rel(
            "/.maitu/workspaces/agt_current/report.docx",
            server=server,
            agent_id="agt_current",
        )
        == "report.docx"
    )
    assert (
        _agent_facing_virtual_workspace_rel(
            "/.maitu/workspaces/agt_other/report.docx",
            server=server,
            agent_id="agt_current",
        )
        is None
    )
    assert (
        _agent_facing_virtual_workspace_rel(
            "/.octop/workspaces/agt_current/report.docx",
            server=server,
            agent_id="agt_current",
        )
        is None
    )
    legacy_server = _server("/.octop/workspaces/agt_legacy")
    assert (
        _agent_facing_virtual_workspace_rel(
            "/.octop/workspaces/agt_legacy/report.docx",
            server=legacy_server,
            agent_id="agt_legacy",
        )
        == "report.docx"
    )


def test_agent_facing_virtual_root_preserves_file_and_host_path_semantics() -> None:
    server = _server("/.octop/workspaces/agt_current")
    assert (
        _agent_facing_virtual_workspace_rel(
            "file:///.octop/workspaces/agt_current/report.docx",
            server=server,
            agent_id="agt_current",
        )
        is None
    )
    assert (
        _agent_facing_virtual_workspace_rel(
            "/Users/me/.octop/workspaces/agt_current/report.docx",
            server=server,
            agent_id="agt_current",
        )
        is None
    )


def test_from_workspace_false_slash_is_host_absolute() -> None:
    abs_path = "/Users/jubaoliang/Desktop/IronMan_PPT/钢铁侠.pptx"
    assert _workspace_io_path(abs_path, from_workspace=False) == abs_path
    assert _workspace_io_path("/logo.png", from_workspace=False) == "/logo.png"


def test_from_workspace_true_slash_is_workspace_relative() -> None:
    assert _workspace_io_path("/logo.png", from_workspace=True) == "logo.png"
    assert _workspace_io_path("/outbound/a.pptx", from_workspace=True) == "outbound/a.pptx"
    assert _workspace_io_path("/", from_workspace=True) == "."


def test_relative_without_slash_always_workspace() -> None:
    assert (
        _workspace_io_path("generated/water-ppt/a.pptx", from_workspace=False)
        == "generated/water-ppt/a.pptx"
    )
    assert (
        _workspace_io_path("generated/water-ppt/a.pptx", from_workspace=True)
        == "generated/water-ppt/a.pptx"
    )


def test_file_url_always_host_absolute() -> None:
    assert (
        _workspace_io_path("file:///Users/me/report.pptx", from_workspace=False)
        == "/Users/me/report.pptx"
    )
    assert (
        _workspace_io_path("file:///Users/me/report.pptx", from_workspace=True)
        == "/Users/me/report.pptx"
    )


def test_default_from_workspace_is_false() -> None:
    assert _workspace_io_path("/Users/me/a.pptx") == "/Users/me/a.pptx"
