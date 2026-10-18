"""Unit tests for user-supplied custom agent ids on expert creation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.agents.manager import AgentCreateSpec, validate_custom_agent_id
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


class TestValidateCustomAgentId:
    def test_accepts_slug_style_ids(self) -> None:
        for ok in ("abc", "my-writer", "writer_02", "Ab1", "a" * 64, "Agent-9"):
            assert validate_custom_agent_id(ok) == ok

    def test_rejects_bad_format(self) -> None:
        for bad in (
            "ab",  # too short
            "a" * 65,  # too long
            "-abc",  # leading hyphen
            "abc-",  # trailing hyphen
            "ab c",  # space
            "ab/cd",  # path separator
            "ab.cd",  # dot
            "../etc",  # traversal
            "中文",  # non-ascii
            "",
        ):
            with pytest.raises(OctopError) as exc:
                validate_custom_agent_id(bad)
            assert exc.value.code == ErrorCode.AGENT_ID_INVALID

    def test_rejects_reserved_words(self) -> None:
        for reserved in ("api", "API", "Admin", "agents", "EXPERTS"):
            with pytest.raises(OctopError) as exc:
                validate_custom_agent_id(reserved)
            assert exc.value.code == ErrorCode.AGENT_ID_INVALID


def _make_manager(*, existing_agent_ids: set[str], root: Path):
    """Minimal manager for create-time ID rejection, without a DB or runtime."""
    from octop.infra.agents import manager as manager_mod

    def repo_get(agent_id: str):
        return object() if agent_id in existing_agent_ids else None

    mgr = object.__new__(manager_mod.AgentManager)
    mgr._repos = SimpleNamespace(
        agent_repo=SimpleNamespace(get=repo_get),
        user_policy_repo=SimpleNamespace(get=lambda *args, **kwargs: None),
    )
    mgr._paths = PathLayout(root)
    mgr._lock = asyncio.Lock()
    mgr._assert_agent_name_available = lambda user_id, name: None  # type: ignore[method-assign]
    return mgr


class TestCreateWithCustomAgentId:
    @pytest.mark.asyncio
    async def test_duplicate_custom_id_rejected_before_workspace(self, tmp_path: Path) -> None:
        mgr = _make_manager(existing_agent_ids={"taken-id"}, root=tmp_path)
        spec = AgentCreateSpec(name="New", agent_id="taken-id", user_id=1)
        with pytest.raises(OctopError) as exc:
            await mgr.create(spec)
        assert exc.value.code == ErrorCode.AGENT_ID_TAKEN

    @pytest.mark.asyncio
    async def test_invalid_custom_id_rejected_before_repo_lookup(self, tmp_path: Path) -> None:
        mgr = _make_manager(existing_agent_ids=set(), root=tmp_path)
        spec = AgentCreateSpec(name="New", agent_id="bad id!", user_id=1)
        with pytest.raises(OctopError) as exc:
            await mgr.create(spec)
        assert exc.value.code == ErrorCode.AGENT_ID_INVALID
