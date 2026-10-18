"""Runtime SkillHub tools for authorized expert and feature agents."""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath
import shlex
import uuid
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import BaseModel, Field

from octop.i18n import tr
from octop.infra.agents.kinds import KIND_AGENT, feature_id_of_agent, is_feature_agent
from octop.infra.sharing import can_access
from octop.infra.skills.install import prepare_skillhub_package
from octop.infra.skills.keyless import inspect_keyless_package, package_fingerprint
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.skills.skill_packages import ResolvedSkillPackage, validate_skill_slug
from octop.infra.skills.skillhub_market import download_skillhub_package, search_skillhub
from octop.infra.users.identity import Role, resolved_role
from octop.infra.users.scope import scope_for
from octop.infra.utils.locale import resolve_user_locale

_MAX_QUERY_LENGTH = 256
_MAX_SLUG_LENGTH = 256
_MAX_MANIFEST_CHARS = 20_000
_MAX_RETURNED_PATHS = 100


class SkillMarketSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=_MAX_QUERY_LENGTH)
    limit: int = Field(default=10, ge=1, le=20)


class SkillMarketInspectArgs(BaseModel):
    slug: str = Field(..., min_length=1, max_length=_MAX_SLUG_LENGTH)


class SkillMarketInstallArgs(BaseModel):
    slug: str = Field(..., min_length=1, max_length=_MAX_SLUG_LENGTH)
    fingerprint: str = Field(..., min_length=64, max_length=64)


def _turn_identity() -> tuple[str, int]:
    try:
        config = get_config()
    except RuntimeError:
        return "", 0
    configurable = (config.get("configurable") or {}) if isinstance(config, dict) else {}
    agent_id = str(configurable.get("agent_id") or "")
    raw_user = configurable.get("user")
    if isinstance(raw_user, bool) or not isinstance(raw_user, int | str):
        return agent_id, 0
    text = str(raw_user)
    return agent_id, int(text) if text.isdecimal() else 0


def _locale(repos: Any, user_id: int) -> str:
    return str(resolve_user_locale(user_repo=repos.user_repo, user_id=user_id))


def _message(locale: str, key: str, **values: object) -> dict[str, object]:
    return {"message": tr(f"skill_market.{key}", locale, **values)}


def _json_result(status: str, locale: str, key: str, **values: Any) -> str:
    return json.dumps(
        {"status": status, **_message(locale, key), **values},
        ensure_ascii=False,
        indent=2,
    )


def _current_context(
    registry: Any, repos: Any, expected_agent_id: str, fallback_locale: str
) -> tuple[Any, Any, int, str]:
    agent_id, user_id = _turn_identity()
    locale = _locale(repos, user_id) if user_id else fallback_locale
    if not agent_id or not user_id or agent_id != expected_agent_id:
        raise PermissionError(tr("skill_market.missing_identity", locale))
    row = registry.get_row(agent_id)
    actor = repos.user_repo.get(user_id)
    if row is None or actor is None or getattr(actor, "disabled", False):
        raise PermissionError(tr("skill_market.missing_identity", locale))
    if str(getattr(row, "kind", "")) != KIND_AGENT and not is_feature_agent(
        str(getattr(row, "kind", ""))
    ):
        raise PermissionError(tr("skill_market.unavailable", locale))
    return row, actor, user_id, locale


def _can_read(row: Any, actor: Any, user_id: int, repos: Any) -> bool:
    role = resolved_role(actor)
    if role == Role.ADMIN.value:
        return True
    if not is_feature_agent(str(getattr(row, "kind", ""))):
        if getattr(row, "user_id", None) == user_id:
            return True
    else:
        unit_key = getattr(row, "enterprise_unit_key", None)
        if unit_key is None:
            if getattr(row, "user_id", None) == user_id:
                return True
            author_id = getattr(row, "user_id", None)
            author = repos.user_repo.get(author_id) if author_id is not None else None
            if (
                role == Role.ENTERPRISE_ADMIN.value
                and author is not None
                and scope_for(actor, repos.org_unit_repo).covers_unit(author.org_unit)
            ):
                return True
        elif role == Role.ENTERPRISE_ADMIN.value and scope_for(
            actor, repos.org_unit_repo
        ).covers_unit(unit_key):
            return True
    acl = getattr(repos, "resource_acl_repo", None)
    if acl is None:
        return False
    acl_role, unit_keys = acl.scope_for_user(user_id)
    agent_id = str(getattr(row, "agent_id", ""))
    targets = [("agent", agent_id)]
    feature_id = (
        feature_id_of_agent(agent_id) if is_feature_agent(str(getattr(row, "kind", ""))) else None
    )
    if feature_id:
        targets.append(("feature", feature_id))
    return any(
        (entry := acl.get(resource_type, resource_id)) is not None
        and can_access(entry, user_id=user_id, role=acl_role, unit_keys=unit_keys)
        for resource_type, resource_id in targets
    )


def _can_write(row: Any, actor: Any, user_id: int, repos: Any) -> bool:
    role = resolved_role(actor)
    if role == Role.ADMIN.value:
        return True
    if not is_feature_agent(str(getattr(row, "kind", ""))):
        return getattr(row, "user_id", None) == user_id
    unit_key = getattr(row, "enterprise_unit_key", None)
    if unit_key is None:
        return getattr(row, "user_id", None) == user_id
    return role == Role.ENTERPRISE_ADMIN.value and scope_for(
        actor, repos.org_unit_repo
    ).covers_unit(unit_key)


def _remote_slug(value: str) -> str:
    slug = value.strip()
    if not slug or len(slug) > _MAX_SLUG_LENGTH or any(ord(char) < 32 for char in slug):
        raise ValueError("invalid SkillHub slug")
    return slug


def _workspace_slug(remote_slug: str) -> str:
    local_slug = validate_skill_slug(remote_slug.replace("/", "--"))
    if len(local_slug) > _MAX_SLUG_LENGTH:
        raise ValueError("invalid SkillHub slug")
    return local_slug


async def _load_package(remote_slug: str) -> ResolvedSkillPackage:
    files = await download_skillhub_package(remote_slug)
    return prepare_skillhub_package(_workspace_slug(remote_slug), files)


def _is_mounted_package_skill(registry: Any, repos: Any, agent_id: str, slug: str) -> bool:
    config = registry.get_config(agent_id)
    package_ids = config.get("skill_package_ids")
    if not isinstance(package_ids, list):
        return False
    store = SkillPackageStore(
        repo=repos.skill_package_repo,
        root=registry.paths.skill_packages_dir,
    )
    for raw_package_id in package_ids:
        package_id = str(raw_package_id).strip()
        if not package_id or repos.skill_package_repo.get(package_id) is None:
            continue
        if any(
            str(summary.get("slug") or "").strip() == slug
            for summary in store.list_skill_summaries(package_id)
        ):
            return True
    return False


def _trusted_sandbox(backend: Any) -> Any | None:
    current = backend
    seen: set[int] = set()
    for _ in range(5):
        if current is None or id(current) in seen:
            return None
        seen.add(id(current))
        module = type(current).__module__
        name = type(current).__name__
        if (
            (
                module == "octop.infra.backend.isolated_execute"
                and name
                in {
                    "IsolatedFilesystemBackend",
                    "IsolatedCompositeBackend",
                }
            )
            or (
                module == "octop.infra.backend.managed_shell"
                and name
                in {
                    "ManagedShellBackend",
                    "SourceShellBackend",
                }
            )
            or (module == "harness_agent.backends.docker_sandbox" and name == "DockerSandbox")
            or (module == "harness_agent.backends.opensandbox_sandbox" and name == "OpenSandbox")
        ):
            return current
        current = getattr(current, "default", None)
    return None


async def _execute_probe(sandbox: Any, command: str) -> tuple[int, str]:
    aexecute = getattr(sandbox, "aexecute", None)
    if callable(aexecute):
        result = await aexecute(command, timeout=8)
    else:
        execute = getattr(sandbox, "execute", None)
        if not callable(execute):
            return 125, ""
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: execute(command, timeout=8)
        )
    return int(getattr(result, "exit_code", 125)), str(getattr(result, "output", ""))


def _required_runtimes(package: ResolvedSkillPackage) -> tuple[set[str], list[str]]:
    required: set[str] = set()
    unverifiable: list[str] = []
    for path, _content in package.files:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in {".py", ".pyw"}:
            required.add("python")
        elif suffix in {".js", ".mjs", ".cjs"}:
            required.add("node")
        elif suffix == ".sh":
            required.add("sh")
        elif suffix == ".bash":
            required.add("bash")
        elif suffix == ".zsh":
            required.add("zsh")
        elif suffix in {".jsx", ".ts", ".tsx"}:
            unverifiable.append(path)
    return required, unverifiable


async def _probe_requirements(
    backend: Any, requirements: Any, package: ResolvedSkillPackage
) -> dict[str, Any]:
    python_requirements = tuple(getattr(requirements, "python_requirements", ()))
    required_runtimes, unverifiable_runtime_files = _required_runtimes(package)
    if not requirements.has_runnable_code and not (
        requirements.binaries
        or requirements.python_modules
        or requirements.node_modules
        or python_requirements
        or required_runtimes
    ):
        return {"available": True, "sandbox": "not_required"}
    sandbox = _trusted_sandbox(backend)
    if sandbox is None:
        return {"available": False, "reason": "sandbox_unavailable"}

    missing_binaries: list[str] = []
    missing_python: list[str] = []
    missing_python_requirements: list[str] = []
    missing_node: list[str] = []
    missing_runtimes: list[str] = []
    runtime_commands = {
        "python": "command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1",
        "node": "command -v node >/dev/null 2>&1",
        "sh": "command -v sh >/dev/null 2>&1",
        "bash": "command -v bash >/dev/null 2>&1",
        "zsh": "command -v zsh >/dev/null 2>&1",
    }
    for runtime in sorted(required_runtimes):
        if runtime not in runtime_commands:
            missing_runtimes.append(runtime)
            continue
        status, _output = await _execute_probe(sandbox, runtime_commands[runtime])
        if status != 0:
            missing_runtimes.append(runtime)

    if requirements.binaries:
        checks = " && ".join(
            f"command -v {shlex.quote(name)} >/dev/null 2>&1" for name in requirements.binaries
        )
        status, _output = await _execute_probe(sandbox, checks or "true")
        if status != 0:
            for name in requirements.binaries:
                check, _ = await _execute_probe(
                    sandbox,
                    f"command -v {shlex.quote(name)} >/dev/null 2>&1",
                )
                if check != 0:
                    missing_binaries.append(name)

    if requirements.python_modules:
        modules_b64 = base64.b64encode(
            json.dumps(list(requirements.python_modules)).encode("utf-8")
        ).decode("ascii")
        script = (
            "import base64,importlib.util,json; "
            f"modules=json.loads(base64.b64decode('{modules_b64}')); "
            "print(json.dumps({m: importlib.util.find_spec(m.split('.')[0]) is not None "
            "for m in modules}))"
        )
        python_command = (
            f"if command -v python3 >/dev/null 2>&1; then python3 -c {shlex.quote(script)}; "
            f"elif command -v python >/dev/null 2>&1; then python -c {shlex.quote(script)}; "
            "else exit 127; fi"
        )
        status, output = await _execute_probe(sandbox, python_command)
        try:
            found = json.loads(output.strip().splitlines()[-1]) if status == 0 else {}
        except (IndexError, json.JSONDecodeError):
            found = {}
        missing_python = [module for module in requirements.python_modules if not found.get(module)]

    if python_requirements:
        requirements_b64 = base64.b64encode(
            json.dumps(list(python_requirements)).encode("utf-8")
        ).decode("ascii")
        script = (
            "import base64,importlib.metadata,json\n"
            "from packaging.requirements import Requirement\n"
            "from packaging.version import Version\n"
            f"requirements=json.loads(base64.b64decode('{requirements_b64}'))\n"
            "available={}\n"
            "for raw in requirements:\n"
            "    try:\n"
            "        requirement=Requirement(raw)\n"
            "        if requirement.marker and not requirement.marker.evaluate():\n"
            "            available[raw]=True\n"
            "            continue\n"
            "        version=Version(importlib.metadata.version(requirement.name))\n"
            "        available[raw]=not requirement.specifier or version in requirement.specifier\n"
            "    except Exception:\n"
            "        available[raw]=False\n"
            "print(json.dumps(available))\n"
        )
        python_command = (
            f"if command -v python3 >/dev/null 2>&1; then python3 -c {shlex.quote(script)}; "
            f"elif command -v python >/dev/null 2>&1; then python -c {shlex.quote(script)}; "
            "else exit 127; fi"
        )
        status, output = await _execute_probe(sandbox, python_command)
        try:
            found = json.loads(output.strip().splitlines()[-1]) if status == 0 else {}
        except (IndexError, json.JSONDecodeError):
            found = {}
        missing_python_requirements = [item for item in python_requirements if not found.get(item)]

    if requirements.node_modules:
        modules_b64 = base64.b64encode(
            json.dumps(list(requirements.node_modules)).encode("utf-8")
        ).decode("ascii")
        script = (
            "const m=JSON.parse(Buffer.from('" + modules_b64 + "','base64').toString());const o={};"
            "for(const x of m){try{require.resolve(x);o[x]=true;}catch{ o[x]=false; }}"
            "console.log(JSON.stringify(o));"
        )
        node_command = f"node -e {shlex.quote(script)}"
        status, output = await _execute_probe(sandbox, node_command)
        try:
            found = json.loads(output.strip().splitlines()[-1]) if status == 0 else {}
        except (IndexError, json.JSONDecodeError):
            found = {}
        missing_node = [module for module in requirements.node_modules if not found.get(module)]

    if requirements.has_runnable_code and not (
        requirements.binaries
        or requirements.python_modules
        or requirements.node_modules
        or python_requirements
        or required_runtimes
    ):
        status, _output = await _execute_probe(sandbox, "true")
        if status != 0:
            return {"available": False, "reason": "sandbox_unavailable"}

    return {
        "available": not (
            missing_binaries
            or missing_python
            or missing_python_requirements
            or missing_node
            or missing_runtimes
            or unverifiable_runtime_files
        ),
        "missing_binaries": missing_binaries,
        "missing_python_modules": missing_python,
        "missing_python_requirements": missing_python_requirements,
        "missing_node_modules": missing_node,
        "missing_runtimes": missing_runtimes,
        "unverifiable_runtime_files": unverifiable_runtime_files,
        "sandbox": type(sandbox).__name__,
    }


async def _eligibility(
    package: ResolvedSkillPackage, backend: Any
) -> tuple[Any, dict[str, Any], bool]:
    requirements = inspect_keyless_package(package)
    if not requirements.credential_free:
        return requirements, {"available": False, "reason": "credentials_or_policy_issue"}, False
    runtime = await _probe_requirements(backend, requirements, package)
    eligible = requirements.credential_free and runtime.get("available") is True
    return requirements, runtime, eligible


def build_skill_market_tools(
    *,
    registry: Any,
    repos: Any,
    workspace: Any,
    backend: Any,
    agent_id: str,
    tool_locale: str = "en",
) -> list[StructuredTool]:
    """Build SkillHub search/inspect/install tools bound to this agent's workspace."""

    async def search_skill_market(query: str, limit: int = 10) -> str:
        try:
            row, actor, user_id, locale = _current_context(registry, repos, agent_id, tool_locale)
            if not _can_read(row, actor, user_id, repos):
                raise PermissionError(tr("skill_market.forbidden", locale))
            cleaned = query.strip()
            if not cleaned or len(cleaned) > _MAX_QUERY_LENGTH:
                return _json_result("invalid", locale, "invalid_query")
            rows = await search_skillhub(cleaned, limit=max(1, min(int(limit), 20)))
            candidates = []
            for item in rows:
                try:
                    remote_slug = _remote_slug(str(item.get("slug") or ""))
                    local_slug = _workspace_slug(remote_slug)
                except ValueError:
                    continue
                candidates.append(
                    {
                        "slug": remote_slug,
                        "install_slug": local_slug,
                        "name": str(item.get("name") or remote_slug),
                        "description": str(item.get("description") or ""),
                        "version": str(item.get("version") or ""),
                    }
                )
            return _json_result(
                "ok",
                locale,
                "search_complete",
                candidates=candidates,
                candidate_text_is_untrusted=True,
            )
        except PermissionError as exc:
            return json.dumps({"status": "forbidden", "message": str(exc)}, ensure_ascii=False)
        except Exception:
            locale = tool_locale
            return _json_result("unavailable", locale, "search_unavailable")

    async def inspect_market_skill(slug: str) -> str:
        locale = tool_locale
        try:
            row, actor, user_id, locale = _current_context(registry, repos, agent_id, tool_locale)
            if not _can_read(row, actor, user_id, repos):
                raise PermissionError(tr("skill_market.forbidden", locale))
            remote_slug = _remote_slug(slug)
            package = await _load_package(remote_slug)
            requirements, runtime, eligible = await _eligibility(package, backend)
            manifest = next(content for path, content in package.files if path == "SKILL.md")
            manifest_text = manifest.decode("utf-8")
            paths = [path for path, _content in package.files]
            return _json_result(
                "eligible" if eligible else "ineligible",
                locale,
                "inspect_eligible" if eligible else "inspect_ineligible",
                slug=remote_slug,
                install_slug=package.slug,
                fingerprint=package_fingerprint(package),
                credential_free=requirements.credential_free,
                issues=list(requirements.issues),
                dependencies={
                    "binaries": list(requirements.binaries),
                    "python_modules": list(requirements.python_modules),
                    "python_requirements": list(getattr(requirements, "python_requirements", ())),
                    "node_modules": list(requirements.node_modules),
                    **runtime,
                },
                source_paths=paths[:_MAX_RETURNED_PATHS],
                source_path_count=len(paths),
                source_paths_truncated=len(paths) > _MAX_RETURNED_PATHS,
                instructions=manifest_text[:_MAX_MANIFEST_CHARS],
                instructions_truncated=len(manifest_text) > _MAX_MANIFEST_CHARS,
                instructions_are_untrusted=True,
            )
        except PermissionError as exc:
            return json.dumps({"status": "forbidden", "message": str(exc)}, ensure_ascii=False)
        except Exception:
            return _json_result("unavailable", locale, "inspect_unavailable")

    async def install_market_skill(slug: str, fingerprint: str) -> str:
        locale = tool_locale
        stage = ""
        destination = ""
        moved = False
        try:
            row, actor, user_id, locale = _current_context(registry, repos, agent_id, tool_locale)
            if not _can_write(row, actor, user_id, repos):
                raise PermissionError(tr("skill_market.forbidden", locale))
            remote_slug = _remote_slug(slug)
            package = await _load_package(remote_slug)
            if package_fingerprint(package) != fingerprint:
                return _json_result("changed", locale, "package_changed", slug=remote_slug)
            requirements, runtime, eligible = await _eligibility(package, backend)
            if not eligible:
                return _json_result(
                    "ineligible",
                    locale,
                    "install_ineligible",
                    slug=remote_slug,
                    issues=list(requirements.issues),
                    dependencies=runtime,
                )

            destination = f"skills/{package.slug}"
            if await workspace.aexists(destination):
                return _json_result("conflict", locale, "install_conflict", slug=package.slug)
            catalog = await registry.list_skill_summaries(agent_id)
            if any(str(item.get("slug") or "") == package.slug for item in catalog):
                return _json_result("conflict", locale, "install_conflict", slug=package.slug)
            mounted_conflict = await asyncio.get_running_loop().run_in_executor(
                None, _is_mounted_package_skill, registry, repos, agent_id, package.slug
            )
            if mounted_conflict:
                return _json_result("conflict", locale, "install_conflict", slug=package.slug)

            stage = f".octop-skillhub-stage-{uuid.uuid4().hex}"
            await workspace.amkdir(stage)
            directories = {""}
            uploads: list[tuple[str, bytes]] = []
            for path, content in package.files:
                if path.endswith("/"):
                    directories.add(path.rstrip("/"))
                    continue
                parent = posixpath.dirname(path)
                while parent:
                    directories.add(parent)
                    parent = posixpath.dirname(parent)
                uploads.append((f"{stage}/{path}", content))
            for directory in sorted(directories - {""}):
                await workspace.amkdir(f"{stage}/{directory}")
            await workspace.aupload_many(uploads)

            for path, content in package.files:
                if path.endswith("/"):
                    continue
                staged = await workspace.adownload_bytes(f"{stage}/{path}")
                if staged != content:
                    return _json_result("failed", locale, "stage_readback_failed")

            await workspace.amkdir("skills")
            await workspace.amove(stage, destination)
            moved = True
            stage = ""
            readback: list[tuple[str, bytes]] = []
            for path, _content in package.files:
                if path.endswith("/"):
                    continue
                content = await workspace.adownload_bytes(f"{destination}/{path}")
                if content is None:
                    raise OSError("installed file could not be read back")
                readback.append((path, content))
            verified = prepare_skillhub_package(package.slug, readback)
            if package_fingerprint(verified) != fingerprint:
                raise OSError("installed package readback did not match inspected content")
            registry.note_skill_catalog_changed(agent_id)
            manifest_text = next(
                content.decode("utf-8") for path, content in readback if path == "SKILL.md"
            )
            paths = [path for path, _content in readback]
            return _json_result(
                "installed",
                locale,
                "install_verified",
                slug=package.slug,
                source_slug=remote_slug,
                fingerprint=fingerprint,
                source_paths=paths[:_MAX_RETURNED_PATHS],
                source_path_count=len(paths),
                instructions=manifest_text[:_MAX_MANIFEST_CHARS],
                instructions_truncated=len(manifest_text) > _MAX_MANIFEST_CHARS,
                instructions_are_untrusted=True,
                catalog_refresh=tr("skill_market.catalog_refresh_next_model_step", locale),
                feature_scope_note=(
                    tr("skill_market.feature_scope_note", locale)
                    if is_feature_agent(str(getattr(row, "kind", "")))
                    else None
                ),
            )
        except PermissionError as exc:
            return json.dumps({"status": "forbidden", "message": str(exc)}, ensure_ascii=False)
        except Exception:
            if moved and destination:
                with suppress(Exception):
                    await workspace.adelete(destination)
            return _json_result("failed", locale, "install_failed")
        finally:
            if stage:
                with suppress(Exception):
                    await workspace.adelete(stage)

    return [
        StructuredTool.from_function(
            coroutine=search_skill_market,
            name="search_skill_market",
            args_schema=SkillMarketSearchArgs,
            description=tr("skill_market.search_tool_description", tool_locale),
        ),
        StructuredTool.from_function(
            coroutine=inspect_market_skill,
            name="inspect_market_skill",
            args_schema=SkillMarketInspectArgs,
            description=tr("skill_market.inspect_tool_description", tool_locale),
        ),
        StructuredTool.from_function(
            coroutine=install_market_skill,
            name="install_market_skill",
            args_schema=SkillMarketInstallArgs,
            description=tr("skill_market.install_tool_description", tool_locale),
        ),
    ]


__all__ = ["build_skill_market_tools"]
