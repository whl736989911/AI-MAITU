"""Octop-owned built-in Skills seeded into every agent workspace."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

OCTOP_BUILTIN_SKILLS_ROOT = "_builtin_skills"
RETIRED_BUILTIN_SKILLS = ("install-skill",)
_EXECUTE_TOOL_MIGRATION = b"execute_" + b"shell_command"
_OLD_CWD_GUIDANCE = ("> 或使用 `" + _EXECUTE_TOOL_MIGRATION.decode() + "` 的 `cwd` 参数。").encode(
    "utf-8"
)
_NEW_CWD_GUIDANCE = (
    "> 可将 `cd {this_skill_dir} && python scripts/...` 命令直接传给平台的 "
    "`execute` 工具；该工具接收命令字符串，不单独提供 `cwd` 参数。"
    "第三方技能要求旧工具名时，同样改用 `execute`，不要尝试调用未注册的工具。"
).encode()
_BUILTIN_SKILL_VERSION = b"builtin_skill_version:"
_MANAGED_EXPERT_SKILLS = {
    "pdf": ("SKILL.md", "forms.md"),
    "docx": ("SKILL.md",),
    "pptx": ("SKILL.md", "editing.md"),
    "xlsx": ("SKILL.md",),
    "file_reader": ("SKILL.md",),
}
_WORKSPACE_TOKEN = b"{{OCTOP_WORKSPACE}}"
_SKILLS_TOKEN = b"{{OCTOP_SKILLS}}"
_BUILTIN_TOKEN = b"{{OCTOP_BUILTIN_SKILLS}}"
_PACKAGE = "octop.infra.agents.builtin_skills"


def _collect_files(source: Traversable, prefix: str, out: list[tuple[str, bytes]]) -> None:
    for entry in source.iterdir():
        if entry.name.startswith((".", "__")):
            continue
        path = f"{prefix}/{entry.name}"
        if entry.is_dir():
            _collect_files(entry, path, out)
        else:
            out.append((path, entry.read_bytes()))


async def _migrate_known_system_skill(
    workspace: Any, skill_path: str, *, require_builtin_version: bool = False
) -> None:
    contents = await workspace.adownload_bytes(skill_path)
    if contents is None:
        return
    if require_builtin_version and _BUILTIN_SKILL_VERSION not in contents:
        return
    updated = contents.replace(_OLD_CWD_GUIDANCE, _NEW_CWD_GUIDANCE).replace(
        _EXECUTE_TOOL_MIGRATION, b"execute"
    )
    if updated != contents:
        await workspace.aupload_many([(skill_path, updated)])


async def _migrate_platform_skills(workspace: Any) -> None:
    """Migrate exact stale tool references in known platform-owned system skills."""
    system_skills = workspace.system_rel("skills")
    await _migrate_known_system_skill(workspace, f"{system_skills}/octop-assistant/SKILL.md")

    roots = {system_skills, "skills"}
    for root in roots:
        for slug, filenames in _MANAGED_EXPERT_SKILLS.items():
            skill_root = f"{root}/{slug}"
            manifest_path = f"{skill_root}/SKILL.md"
            manifest = await workspace.adownload_bytes(manifest_path)
            if manifest is None or _BUILTIN_SKILL_VERSION not in manifest:
                continue
            for filename in filenames:
                await _migrate_known_system_skill(
                    workspace,
                    f"{skill_root}/{filename}",
                    require_builtin_version=filename == "SKILL.md",
                )


async def sync_octop_builtin_skills(workspace: Any) -> list[str]:
    """Overwrite Octop-owned built-ins and remove superseded runtime copies."""
    # DeepAgents scans both roots on the first turn. Keep the writable root
    # present even before the user installs their first Skill so that scan is
    # clean on fresh expert instances.
    await workspace.amkdir("skills")

    package_root = resources.files(_PACKAGE)
    uploads: list[tuple[str, bytes]] = []
    skill_names: list[str] = []
    for entry in package_root.iterdir():
        if entry.name.startswith((".", "__")) or not entry.is_dir():
            continue
        if not entry.joinpath("SKILL.md").is_file():
            continue
        skill_names.append(entry.name)
        _collect_files(entry, f"{OCTOP_BUILTIN_SKILLS_ROOT}/{entry.name}", uploads)

    # Render the agent-facing workspace path into the instructions (not the
    # host ``root_dir`` join that ``resolve_path`` returns for I/O).
    from octop.infra.gateway.media.inbound_store import (  # noqa: PLC0415
        agent_facing_workspace_path,
    )

    workspace_path = agent_facing_workspace_path(workspace, ".").encode("utf-8")
    skills_rel = workspace.system_rel("skills")
    builtin_rel = workspace.system_rel(OCTOP_BUILTIN_SKILLS_ROOT)
    skills_path = agent_facing_workspace_path(workspace, skills_rel).encode("utf-8")
    builtin_path = agent_facing_workspace_path(workspace, builtin_rel).encode("utf-8")
    uploads = [
        (
            path,
            data.replace(_WORKSPACE_TOKEN, workspace_path)
            .replace(_SKILLS_TOKEN, skills_path)
            .replace(_BUILTIN_TOKEN, builtin_path),
        )
        for path, data in uploads
    ]

    if uploads:
        await workspace.aupload_many(uploads)
    await _migrate_platform_skills(workspace)

    for name in RETIRED_BUILTIN_SKILLS:
        path = f"{OCTOP_BUILTIN_SKILLS_ROOT}/{name}"
        if await workspace.aexists(path):
            await workspace.adelete(path)
    return sorted(skill_names)


__all__ = ["OCTOP_BUILTIN_SKILLS_ROOT", "sync_octop_builtin_skills"]
