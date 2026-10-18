"""Runtime SkillHub tool authorization, eligibility, and install guarantees."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.sharing import VISIBILITY_PUBLIC, AclEntry
from octop.infra.skills import agent_tools
from octop.infra.skills.install import prepare_skillhub_package
from octop.infra.skills.keyless import package_fingerprint

_AGENT_ID = "agent-1"
_USER_ID = 7


class _Workspace:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.directories = {"skills"}

    async def aexists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return (
            path in self.files
            or path in self.directories
            or any(item.startswith(prefix) for item in (*self.files, *self.directories))
        )

    async def amkdir(self, path: str) -> None:
        self.directories.add(path.rstrip("/"))

    async def aupload_many(self, uploads: list[tuple[str, bytes]]) -> None:
        for path, content in uploads:
            if path in self.files:
                raise FileExistsError(path)
            self.files[path] = content

    async def adownload_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)

    async def amove(self, source: str, destination: str) -> None:
        if await self.aexists(destination):
            raise FileExistsError(destination)
        prefix = source.rstrip("/") + "/"
        for path in list(self.files):
            if path == source or path.startswith(prefix):
                self.files[destination + path[len(source) :]] = self.files.pop(path)
        for path in list(self.directories):
            if path == source or path.startswith(prefix):
                self.directories.remove(path)
                self.directories.add(destination + path[len(source) :])

    async def adelete(self, path: str) -> None:
        prefix = path.rstrip("/") + "/"
        self.files = {
            item: content
            for item, content in self.files.items()
            if item != path and not item.startswith(prefix)
        }
        self.directories = {
            item for item in self.directories if item != path and not item.startswith(prefix)
        }


class _Registry:
    def __init__(self, row: Any, skill_package_ids: list[str] | None = None) -> None:
        self.row = row
        self.revision = 3
        self.config = {"skill_package_ids": skill_package_ids or []}
        self.paths = SimpleNamespace(skill_packages_dir=object())
        self.catalog: list[dict[str, Any]] = []

    def get_row(self, agent_id: str) -> Any:
        return self.row if agent_id == self.row.agent_id else None

    def note_skill_catalog_changed(self, agent_id: str) -> None:
        assert agent_id == self.row.agent_id
        self.revision += 1

    def get_config(self, agent_id: str) -> dict[str, Any]:
        assert agent_id == self.row.agent_id
        return self.config

    async def list_skill_summaries(self, agent_id: str) -> list[dict[str, Any]]:
        assert agent_id == self.row.agent_id
        return self.catalog


class _UserRepo:
    def __init__(self, actor: Any | None) -> None:
        self.actor = actor

    def get(self, user_id: int) -> Any:
        return self.actor if self.actor is not None and user_id == self.actor.id else None


class _SkillPackageRepo:
    def get(self, package_id: str) -> Any:
        return object() if package_id == "pack-1" else None


class _PublicAcl:
    def scope_for_user(self, user_id: int) -> tuple[str, tuple[str, ...]]:
        return "user", ()

    def get(self, resource_type: str, resource_id: str) -> AclEntry:
        return AclEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_user_id=None,
            visibility=VISIBILITY_PUBLIC,
            unit_key=None,
            version=1,
        )


class _Sandbox:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def aexecute(self, command: str, *, timeout: int) -> Any:
        assert timeout == 8
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, output='{"requests": false}')


class _MissingPythonSandbox(_Sandbox):
    async def aexecute(self, command: str, *, timeout: int) -> Any:
        result = await super().aexecute(command, timeout=timeout)
        if command == "command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1":
            return SimpleNamespace(exit_code=1, output="")
        return result


def _package(
    *,
    manifest: bytes | None = None,
    files: list[tuple[str, bytes]] | None = None,
) -> Any:
    entries = [
        (
            "SKILL.md",
            manifest
            or b"---\nname: local-skill\ndescription: No API key is required.\n---\n\nUse local files.\n",
        ),
    ]
    return prepare_skillhub_package("local-skill", [*entries, *(files or [])])


def _tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    row: Any | None = None,
    actor_id: int | None = _USER_ID,
    actor_role: str = "user",
    workspace: _Workspace | None = None,
    package_loader: Any | None = None,
    acl: Any | None = None,
    skill_package_ids: list[str] | None = None,
) -> tuple[dict[str, Any], _Registry, _Workspace]:
    actor = (
        SimpleNamespace(id=actor_id, role=actor_role, disabled=0, locale="en", org_unit=None)
        if actor_id is not None
        else None
    )
    selected_row = row or SimpleNamespace(
        agent_id=_AGENT_ID,
        kind=KIND_AGENT,
        user_id=_USER_ID,
        enterprise_unit_key=None,
    )
    registry = _Registry(selected_row, skill_package_ids)
    active_workspace = workspace or _Workspace()
    repos = SimpleNamespace(
        user_repo=_UserRepo(actor),
        org_unit_repo=object(),
        resource_acl_repo=acl,
        skill_package_repo=_SkillPackageRepo(),
    )
    monkeypatch.setattr(
        agent_tools,
        "get_config",
        lambda: {"configurable": {"agent_id": selected_row.agent_id, "user": actor_id}},
    )

    async def load_package(_slug: str) -> Any:
        if package_loader is None:
            return _package()
        loaded = package_loader()
        if hasattr(loaded, "__await__"):
            return await loaded
        return loaded

    async def search(_query: str, *, limit: int) -> list[dict[str, str]]:
        assert limit == 10
        return [
            {"slug": "author/local-skill", "name": "Local skill", "description": "A local workflow"}
        ]

    monkeypatch.setattr(agent_tools, "_load_package", load_package)
    monkeypatch.setattr(agent_tools, "search_skillhub", search)
    built_tools = agent_tools.build_skill_market_tools(
        registry=registry,
        repos=repos,
        workspace=active_workspace,
        backend=object(),
        agent_id=selected_row.agent_id,
        tool_locale="en",
    )
    return {tool.name: tool for tool in built_tools}, registry, active_workspace


@pytest.mark.asyncio
async def test_search_inspect_install_reads_back_and_refreshes_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools, registry, workspace = _tools(monkeypatch)

    found = json.loads(await tools["search_skill_market"].ainvoke({"query": "local documents"}))
    assert found["candidate_text_is_untrusted"] is True
    assert found["candidates"][0]["slug"] == "author/local-skill"

    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))
    assert inspected["status"] == "eligible"
    assert inspected["credential_free"] is True
    assert inspected["instructions_are_untrusted"] is True

    installed = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": inspected["fingerprint"]}
        )
    )

    assert installed["status"] == "installed"
    assert installed["instructions_are_untrusted"] is True
    assert "skills/local-skill/SKILL.md" in workspace.files
    assert registry.revision == 4
    assert "next model call" in installed["catalog_refresh"]


@pytest.mark.asyncio
async def test_missing_actor_fails_closed_before_search(monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _, workspace = _tools(monkeypatch, actor_id=None)

    result = json.loads(await tools["search_skill_market"].ainvoke({"query": "local documents"}))

    assert result["status"] == "forbidden"
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_cross_user_expert_install_is_denied_without_workspace_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        agent_id=_AGENT_ID,
        kind=KIND_AGENT,
        user_id=11,
        enterprise_unit_key=None,
    )
    package = _package()
    tools, _, workspace = _tools(monkeypatch, row=row)

    result = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": package_fingerprint(package)}
        )
    )

    assert result["status"] == "forbidden"
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_shared_published_feature_reader_cannot_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        agent_id="feat-feature-1",
        kind=KIND_FEATURE,
        user_id=None,
        enterprise_unit_key="unit-1",
    )
    package = _package()
    tools, _, workspace = _tools(monkeypatch, row=row, acl=_PublicAcl())

    result = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": package_fingerprint(package)}
        )
    )

    assert result["status"] == "forbidden"
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_scoped_enterprise_admin_can_install_published_feature_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        agent_id="feat-feature-1",
        kind=KIND_FEATURE,
        user_id=None,
        enterprise_unit_key="unit-1",
    )
    package = _package()
    monkeypatch.setattr(
        agent_tools,
        "scope_for",
        lambda _actor, _repo: SimpleNamespace(covers_unit=lambda unit: unit == "unit-1"),
    )
    tools, _, workspace = _tools(
        monkeypatch,
        row=row,
        actor_role="enterprise_admin",
        package_loader=lambda: package,
    )
    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))

    installed = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": inspected["fingerprint"]}
        )
    )

    assert installed["status"] == "installed"
    assert installed["feature_scope_note"]
    assert workspace.files["skills/local-skill/SKILL.md"]


@pytest.mark.asyncio
async def test_required_api_key_makes_package_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(
        manifest=b"---\nname: local-skill\n---\nBefore running, set OPENAI_API_KEY.\n"
    )
    tools, _, workspace = _tools(monkeypatch, package_loader=lambda: package)

    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))

    assert inspected["status"] == "ineligible"
    assert inspected["credential_free"] is False
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_changed_package_fingerprint_prevents_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages = iter(
        [
            _package(),
            _package(files=[("references/guide.md", b"Changed content.\n")]),
        ]
    )

    async def load_next() -> Any:
        return next(packages)

    tools, _, workspace = _tools(monkeypatch, package_loader=load_next)
    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))
    installed = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": inspected["fingerprint"]}
        )
    )

    assert installed["status"] == "changed"
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_missing_runtime_dependency_is_ineligible_without_running_package_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    monkeypatch.setattr(agent_tools, "_trusted_sandbox", lambda _backend: sandbox)
    package = _package(files=[("run.py", b"import requests\n")])
    tools, _, workspace = _tools(monkeypatch, package_loader=lambda: package)

    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))

    assert inspected["status"] == "ineligible"
    assert inspected["dependencies"]["missing_python_modules"] == ["requests"]
    assert all("run.py" not in command for command in sandbox.commands)
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_declared_python_requirement_is_checked_in_runtime_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    monkeypatch.setattr(agent_tools, "_trusted_sandbox", lambda _backend: sandbox)
    package = _package(files=[("requirements.txt", b"requests>=99.0\n")])
    tools, _, workspace = _tools(monkeypatch, package_loader=lambda: package)

    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))

    assert inspected["status"] == "ineligible"
    assert inspected["dependencies"]["missing_python_requirements"] == ["requests>=99.0"]
    assert any("importlib.metadata" in command for command in sandbox.commands)
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_python_skill_without_runtime_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _MissingPythonSandbox()
    monkeypatch.setattr(agent_tools, "_trusted_sandbox", lambda _backend: sandbox)
    package = _package(files=[("run.py", b"print('hello')\n")])
    tools, _, workspace = _tools(monkeypatch, package_loader=lambda: package)

    inspected = json.loads(await tools["inspect_market_skill"].ainvoke({"slug": "local-skill"}))

    assert inspected["status"] == "ineligible"
    assert inspected["dependencies"]["missing_runtimes"] == ["python"]
    assert "run.py" not in " ".join(sandbox.commands)
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_existing_skill_conflict_preserves_all_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package()
    workspace = _Workspace({"skills/local-skill/SKILL.md": b"Existing skill content\n"})
    tools, _, _ = _tools(monkeypatch, workspace=workspace, package_loader=lambda: package)
    original_files = dict(workspace.files)

    result = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": package_fingerprint(package)}
        )
    )

    assert result["status"] == "conflict"
    assert workspace.files == original_files


@pytest.mark.asyncio
async def test_mounted_skill_package_conflict_is_not_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package()
    workspace = _Workspace()

    class _MountedSkillPackageStore:
        def __init__(self, *, repo: Any, root: Any) -> None:
            assert root is not None

        def list_skill_summaries(self, package_id: str) -> list[dict[str, str]]:
            assert package_id == "pack-1"
            return [{"slug": "local-skill"}]

    monkeypatch.setattr(agent_tools, "SkillPackageStore", _MountedSkillPackageStore)
    tools, _, _ = _tools(
        monkeypatch,
        workspace=workspace,
        package_loader=lambda: package,
        skill_package_ids=["pack-1"],
    )

    result = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": package_fingerprint(package)}
        )
    )

    assert result["status"] == "conflict"
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_builtin_skill_cannot_be_shadowed_by_market_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package()
    tools, registry, workspace = _tools(monkeypatch, package_loader=lambda: package)
    registry.catalog = [{"slug": "local-skill", "kind": "builtin"}]

    result = json.loads(
        await tools["install_market_skill"].ainvoke(
            {"slug": "local-skill", "fingerprint": package_fingerprint(package)}
        )
    )

    assert result["status"] == "conflict"
    assert workspace.files == {}
