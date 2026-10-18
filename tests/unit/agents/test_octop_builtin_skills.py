"""Tests for Octop-owned built-in Skills and the Skill Manager helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from importlib import resources
from pathlib import Path

import pytest
from tests.support.fakes import FakeHarnessAgent

from octop.infra.agents.builtin_skills import sync_octop_builtin_skills

_PACKAGE = "octop.infra.agents.builtin_skills"


def _manager_script() -> Path:
    script = (
        resources.files(_PACKAGE)
        .joinpath("skill-manager")
        .joinpath("scripts")
        .joinpath("manage_skills.py")
    )
    return Path(str(script))


def _run_manager(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_manager_script()), "--workspace", str(workspace), *args],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_sync_seeds_manager_and_retires_old_installer(tmp_path: Path) -> None:
    agent = FakeHarnessAgent(workspace_dir=tmp_path, virtual_mode=False)
    await agent.workspace.awrite_text(
        "_builtin_skills/install-skill/SKILL.md",
        "---\nname: install-skill\ndescription: old\n---\n",
        force=True,
    )

    synced = await sync_octop_builtin_skills(agent.workspace)

    assert synced == ["skill-manager"]
    assert await agent.workspace.aexists("skills")
    assert await agent.workspace.aread_text("_builtin_skills/install-skill/SKILL.md") is None
    manager = await agent.workspace.aread_text("_builtin_skills/skill-manager/SKILL.md")
    script = await agent.workspace.aread_text(
        "_builtin_skills/skill-manager/scripts/manage_skills.py"
    )
    assert manager is not None and "name: skill-manager" in manager
    # The shipped template teaches the platform SkillHub tools; no host path is rendered.
    assert "search_skill_market" in manager
    assert "{{OCTOP_WORKSPACE}}" not in manager
    assert script is not None and "def main()" in script

    deployed = tmp_path / "_builtin_skills/skill-manager/scripts/manage_skills.py"
    inferred = subprocess.run(
        [sys.executable, str(deployed), "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inferred.returncode == 0, inferred.stderr or inferred.stdout
    assert json.loads(inferred.stdout) == []


@pytest.mark.asyncio
async def test_sync_migrates_only_the_existing_platform_assistant_skill(tmp_path: Path) -> None:
    agent = FakeHarnessAgent(
        workspace_dir=tmp_path,
        virtual_mode=False,
        system_files_path=".octop",
    )
    await agent.workspace.awrite_text(
        ".octop/skills/octop-assistant/SKILL.md",
        "user note\nuse execute_shell_command for config\n",
        force=True,
    )
    await agent.workspace.awrite_text(
        ".octop/skills/my-custom-skill/SKILL.md",
        "user-authored execute_shell_command guidance\n",
        force=True,
    )

    await sync_octop_builtin_skills(agent.workspace)
    await sync_octop_builtin_skills(agent.workspace)

    assistant = await agent.workspace.aread_text(".octop/skills/octop-assistant/SKILL.md")
    custom = await agent.workspace.aread_text(".octop/skills/my-custom-skill/SKILL.md")
    builtin = await agent.workspace.aread_text(".octop/_builtin_skills/skill-manager/SKILL.md")
    assert assistant == "user note\nuse execute for config\n"
    assert custom == "user-authored execute_shell_command guidance\n"
    assert builtin is not None and "execute_shell_command" not in builtin


@pytest.mark.asyncio
async def test_sync_migrates_versioned_office_skills_only(tmp_path: Path) -> None:
    agent = FakeHarnessAgent(
        workspace_dir=tmp_path,
        virtual_mode=False,
        system_files_path=".octop",
    )
    await agent.workspace.awrite_text(
        ".octop/skills/pdf/SKILL.md",
        "---\nmetadata:\n  builtin_skill_version: '1.1'\n---\n"
        "> 或使用 `execute_shell_command` 的 `cwd` 参数。\nuser paragraph\n",
        force=True,
    )
    await agent.workspace.awrite_text(
        ".octop/skills/pdf/forms.md",
        "> 运行方式：`cd {this_skill_dir} && python scripts/...`\n"
        "> 或使用 `execute_shell_command` 的 `cwd` 参数。\nuser form note\n",
        force=True,
    )
    await agent.workspace.awrite_text(
        "skills/docx/SKILL.md",
        "---\nmetadata:\n  builtin_skill_version: '1.1'\n---\n"
        "execute_shell_command is the old instruction\n",
        force=True,
    )
    await agent.workspace.awrite_text(
        ".octop/skills/xlsx/SKILL.md",
        "user-authored skill mentioning execute_shell_command\n",
        force=True,
    )

    await sync_octop_builtin_skills(agent.workspace)

    pdf = await agent.workspace.aread_text(".octop/skills/pdf/SKILL.md")
    forms = await agent.workspace.aread_text(".octop/skills/pdf/forms.md")
    docx = await agent.workspace.aread_text("skills/docx/SKILL.md")
    xlsx = await agent.workspace.aread_text(".octop/skills/xlsx/SKILL.md")
    assert pdf is not None and "execute_shell_command" not in pdf
    assert "cd {this_skill_dir} && python scripts/..." in pdf
    assert "user paragraph" in pdf
    assert forms is not None and "execute_shell_command" not in forms
    assert "user form note" in forms
    assert docx == (
        "---\nmetadata:\n  builtin_skill_version: '1.1'\n---\nexecute is the old instruction\n"
    )
    assert xlsx == "user-authored skill mentioning execute_shell_command\n"


@pytest.mark.asyncio
async def test_sync_uses_system_files_path(tmp_path: Path) -> None:
    agent = FakeHarnessAgent(
        workspace_dir=tmp_path,
        virtual_mode=False,
        system_files_path=".octop",
    )

    synced = await sync_octop_builtin_skills(agent.workspace)

    assert synced == ["skill-manager"]
    manager = await agent.workspace.aread_text("_builtin_skills/skill-manager/SKILL.md")
    assert manager is not None
    assert "{{OCTOP_SKILLS}}" not in manager
    assert "search_skill_market" in manager
    assert (tmp_path / ".octop" / "_builtin_skills" / "skill-manager" / "SKILL.md").is_file()
    assert not (tmp_path / "_builtin_skills").exists()
    assert (tmp_path / "AGENTS.md").exists() is False

    deployed = tmp_path / ".octop/_builtin_skills/skill-manager/scripts/manage_skills.py"
    inferred = subprocess.run(
        [sys.executable, str(deployed), "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert inferred.returncode == 0, inferred.stderr or inferred.stdout
    assert json.loads(inferred.stdout) == []


def test_manager_script_compiles() -> None:
    script = _manager_script()
    assert script.is_file()
    compile(script.read_text(encoding="utf-8"), "manage_skills.py", "exec")


def test_manager_installs_lists_removes_and_restores_zip(tmp_path: Path) -> None:
    archive = tmp_path / "example.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "example/SKILL.md",
            "---\nname: example\ndescription: Example test skill.\n---\n\n# Example\n",
        )
        bundle.writestr("example/references/note.md", "hello\n")

    inspected = _run_manager(tmp_path, "inspect", str(archive))
    assert inspected.returncode == 0, inspected.stderr or inspected.stdout
    assert json.loads(inspected.stdout)[0]["slug"] == "example"

    installed = _run_manager(tmp_path, "install", str(archive))
    assert installed.returncode == 0, installed.stderr or installed.stdout
    assert (tmp_path / "skills" / "example" / "references" / "note.md").is_file()

    listed = _run_manager(tmp_path, "list")
    assert listed.returncode == 0, listed.stderr or listed.stdout
    assert json.loads(listed.stdout)[0]["valid"] is True

    unconfirmed = _run_manager(tmp_path, "remove", "example")
    assert unconfirmed.returncode == 1
    assert (tmp_path / "skills" / "example").is_dir()

    removed = _run_manager(tmp_path, "remove", "example", "--yes")
    assert removed.returncode == 0, removed.stderr or removed.stdout
    trash_name = json.loads(removed.stdout)["trash_name"]
    assert not (tmp_path / "skills" / "example").exists()

    restored = _run_manager(tmp_path, "restore", trash_name)
    assert restored.returncode == 0, restored.stderr or restored.stdout
    assert (tmp_path / "skills" / "example" / "SKILL.md").is_file()
    assert not (tmp_path / "skills" / "example" / ".skill-manager-trash.json").exists()


def test_manager_rejects_archive_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../SKILL.md", "not safe")

    result = _run_manager(tmp_path, "inspect", str(archive))

    assert result.returncode == 1
    assert "unsafe archive path" in result.stdout
    assert not (tmp_path.parent / "SKILL.md").exists()


def test_manager_refuses_overwrite_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: stable\ndescription: First version.\n---\n\n# Stable\n",
        encoding="utf-8",
    )
    first = _run_manager(tmp_path, "install", str(source))
    assert first.returncode == 0, first.stderr or first.stdout

    (source / "SKILL.md").write_text(
        "---\nname: stable\ndescription: Second version.\n---\n\n# Stable\n",
        encoding="utf-8",
    )
    refused = _run_manager(tmp_path, "install", str(source))
    assert refused.returncode == 1
    assert "ask before using --force" in refused.stdout
    assert "First version" in (tmp_path / "skills" / "stable" / "SKILL.md").read_text()

    replaced = _run_manager(tmp_path, "install", str(source), "--force")
    assert replaced.returncode == 0, replaced.stderr or replaced.stdout
    assert "Second version" in (tmp_path / "skills" / "stable" / "SKILL.md").read_text()


def test_manager_refuses_to_replace_itself(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: skill-manager\ndescription: Untrusted replacement.\n---\n",
        encoding="utf-8",
    )

    result = _run_manager(tmp_path, "install", str(source), "--force")

    assert result.returncode == 1
    # Brand-neutral: the refusal must say why, without pinning the brand name.
    assert "cannot replace" in result.stdout
    assert "built-in" in result.stdout
    assert not (tmp_path / "skills" / "skill-manager").exists()


def test_manager_installs_namespaced_skillhub_page_url(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log_path = tmp_path / "skillhub-args.json"
    fake_skillhub = fake_bin / ("skillhub.py" if os.name == "nt" else "skillhub")
    fake_skillhub.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
Path(os.environ["FAKE_SKILLHUB_LOG"]).write_text(json.dumps(args), encoding="utf-8")
slug = args[args.index("install") + 1]
namespace = args[args.index("--namespace") + 1]
root = Path(args[args.index("--dir") + 1]) / f"@{namespace}" / slug
root.mkdir(parents=True)
(root / "SKILL.md").write_text(
    f"---\\nname: {slug}\\ndescription: Namespaced SkillHub test.\\n---\\n",
    encoding="utf-8",
)
print(json.dumps({"installed": slug}))
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        (fake_bin / "skillhub.cmd").write_text(
            f'@"{sys.executable}" "%~dp0skillhub.py" %*\n',
            encoding="utf-8",
        )
    else:
        fake_skillhub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_SKILLHUB_LOG": str(log_path),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(_manager_script()),
            "--workspace",
            str(tmp_path),
            "install",
            "https://skillhub.cn/skills/user_741dc82b/dev-expert?from=skill-hunt",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "skills/dev-expert/SKILL.md").is_file()
    args = json.loads(log_path.read_text(encoding="utf-8"))
    assert args[args.index("install") + 1] == "dev-expert"
    assert args[args.index("--namespace") + 1] == "user_741dc82b"
