"""Unit tests for custom MCP deferred load + user-level tool cache."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.connectors.mcp_tool_cache import (
    fingerprint_mcp_spec,
    wrap_tools_for_shared_use,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


def test_fingerprint_stable_and_ignores_enabled() -> None:
    a = fingerprint_mcp_spec(
        {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer a"},
            "enabled": True,
        }
    )
    b = fingerprint_mcp_spec(
        {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer a"},
            "enabled": False,
        }
    )
    assert a == b
    c = fingerprint_mcp_spec(
        {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer b"},
        }
    )
    assert a != c


@pytest.mark.asyncio
async def test_wrap_tools_serialize_ainvoke() -> None:
    order: list[str] = []

    class _FakeTool:
        name = "demo_tool"
        description = "demo"
        args_schema = None

        async def ainvoke(self, *_a: Any, **_k: Any) -> str:
            order.append("start")
            await asyncio.sleep(0.05)
            order.append("end")
            return "ok"

        def invoke(self, *_a: Any, **_k: Any) -> str:
            return "ok"

    lock = asyncio.Lock()
    wrapped = wrap_tools_for_shared_use([_FakeTool()], lock)
    assert len(wrapped) == 1
    assert isinstance(wrapped[0], BaseTool)
    assert isinstance(wrapped[0], StructuredTool)

    async def _call() -> Any:
        return await wrapped[0].ainvoke({})

    await asyncio.gather(_call(), _call())
    assert order == ["start", "end", "start", "end"]


@pytest.fixture
def mcp_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AgentManager]:
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    monkeypatch.setenv("OCTOP_HOME", str(paths.root))
    monkeypatch.setattr("octop.infra.users.permissions.license_managed_install", lambda: False)
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    services = build_shared_services(db=pool, paths=paths, config=OctopConfig())
    try:
        yield AgentManager(repos=services.repos, paths=paths)
    finally:
        pool.close()


def _spec(token: str) -> dict[str, Any]:
    return {
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": token},
    }


def _install_document_transport(
    monkeypatch: pytest.MonkeyPatch, documents: dict[str, Path]
) -> None:
    async def load(configs: dict[str, Any]) -> list[StructuredTool]:
        spec = next(iter(configs.values()))
        document = documents[spec["headers"]["Authorization"]]

        def read_document() -> str:
            """Read the private document accessible to this credential."""
            return document.read_text(encoding="utf-8")

        return [StructuredTool.from_function(read_document, name="shared_read_document")]

    monkeypatch.setattr("harness_agent.mcp.aload_mcp_tools", load)


@pytest.mark.asyncio
async def test_cached_mcp_access_is_rechecked_after_permission_revocation(
    mcp_manager: AgentManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = mcp_manager._repos.user_repo.create(
        username="revoked", role="user", permissions=["connectors"]
    )
    document = tmp_path / "private.txt"
    document.write_text("private document", encoding="utf-8")
    _install_document_transport(monkeypatch, {"credential": document})
    tools = await mcp_manager._get_or_load_mcp_tools(user_id, "shared", _spec("credential"))
    assert await tools[0].ainvoke({}) == "private document"
    mcp_manager._repos.user_repo.set_permissions(user_id, [])

    with pytest.raises(OctopError) as error:
        await mcp_manager._get_or_load_mcp_tools(user_id, "shared", _spec("credential"))

    assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_same_server_cache_isolates_users_and_rotated_credentials(
    mcp_manager: AgentManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    users = [
        mcp_manager._repos.user_repo.create(username=name, role="user", permissions=["connectors"])
        for name in ("first", "second")
    ]
    documents: dict[str, Path] = {}
    for token in ("first", "second", "rotated"):
        documents[token] = tmp_path / f"{token}.txt"
        documents[token].write_text(f"{token} private document", encoding="utf-8")
    _install_document_transport(monkeypatch, documents)
    first = await mcp_manager._get_or_load_mcp_tools(users[0], "shared", _spec("first"))
    second = await mcp_manager._get_or_load_mcp_tools(users[1], "shared", _spec("second"))
    assert await first[0].ainvoke({}) == "first private document"
    assert await second[0].ainvoke({}) == "second private document"

    rotated = await mcp_manager._get_or_load_mcp_tools(users[0], "shared", _spec("rotated"))
    second = await mcp_manager._get_or_load_mcp_tools(users[1], "shared", _spec("second"))

    assert await rotated[0].ainvoke({}) == "rotated private document"
    assert await second[0].ainvoke({}) == "second private document"


@pytest.mark.asyncio
async def test_owner_and_guest_turns_use_only_their_own_credentials(
    mcp_manager: AgentManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    users = [
        mcp_manager._repos.user_repo.create(username=name, role="user", permissions=["connectors"])
        for name in ("owner", "guest")
    ]
    mcp_manager._repos.agent_repo.create(agent_id="shared-agent", user_id=users[0], name="shared")
    documents: dict[str, Path] = {}
    for token in ("owner", "guest"):
        documents[token] = tmp_path / f"{token}.txt"
        documents[token].write_text(f"{token} private document", encoding="utf-8")
    _install_document_transport(monkeypatch, documents)
    monkeypatch.setattr(
        mcp_manager._connector_svc,
        "custom_harness_configs",
        lambda user_id: {"shared": _spec("owner" if user_id == users[0] else "guest")},
    )

    for user_id in users:
        assert (
            await mcp_manager.prepare_chat_mcp(
                "shared-agent", ["shared"], connector_user_id=user_id
            )
            == []
        )
    with pytest.raises(OctopError):
        await mcp_manager.prepare_chat_mcp("shared-agent", ["shared"])

    owner_tools = mcp_manager.turn_mcp_tools("shared-agent", users[0], ["shared"])
    guest_tools = mcp_manager.turn_mcp_tools("shared-agent", users[1], ["shared"])
    assert await owner_tools["shared"][0].ainvoke({}) == "owner private document"
    assert await guest_tools["shared"][0].ainvoke({}) == "guest private document"


@pytest.mark.asyncio
async def test_cached_mcp_cannot_keep_grants_from_replaced_control_plane(
    mcp_manager: AgentManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = mcp_manager._repos.user_repo.create(
        username="original", role="user", permissions=["connectors"]
    )
    document = tmp_path / "private.txt"
    document.write_text("original private document", encoding="utf-8")
    _install_document_transport(monkeypatch, {"credential": document})
    tools = await mcp_manager._get_or_load_mcp_tools(user_id, "shared", _spec("credential"))
    assert await tools[0].ainvoke({}) == "original private document"

    restored = SqlitePool(tmp_path / "restored.db")
    try:
        run_migrations(restored)
        services = build_shared_services(
            db=restored, paths=mcp_manager._paths, config=OctopConfig()
        )
        restored_id = services.repos.user_repo.create(
            username="restored", role="user", permissions=[]
        )
        assert restored_id == user_id
        mcp_manager.replace_persistence(services.repos, OctopConfig())

        with pytest.raises(OctopError) as error:
            await mcp_manager._get_or_load_mcp_tools(user_id, "shared", _spec("credential"))

        assert error.value.code == ErrorCode.FORBIDDEN
    finally:
        restored.close()
