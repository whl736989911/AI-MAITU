"""Agent tools for authoring a feature agent and safely unpacking uploaded ZIPs."""

from __future__ import annotations

import json
import re
import stat
import unicodedata
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from octop.infra.agents.feature_agent import feature_agent_id
from octop.infra.agents.kinds import KIND_FEATURE, is_feature_agent
from octop.infra.agents.manager import AgentCreateSpec
from octop.infra.users.permissions import unit_permissions, user_has_permission

MAX_ARCHIVE_ENTRIES = 500
MAX_ARCHIVE_FILE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 100 * 1024 * 1024


def _turn_identity() -> tuple[str, int]:
    try:
        config = get_config()
    except RuntimeError:
        return "", 0
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    raw_user = configurable.get("user")
    user_id = int(raw_user) if isinstance(raw_user, int | str) and str(raw_user).isdigit() else 0
    return str(configurable.get("agent_id") or ""), user_id


def _owned_turn(registry: Any) -> tuple[str, int, Any]:
    agent_id, user_id = _turn_identity()
    if not agent_id or not user_id:
        raise ValueError("This tool requires the current agent and signed-in user context.")
    row = registry.get_row(agent_id)
    if row is None or getattr(row, "user_id", None) != user_id:
        raise ValueError("This tool is available only in an agent owned by the current user.")
    return agent_id, user_id, row


def _json_ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _json_error(exc: Exception) -> str:
    return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


def _safe_member_path(raw: str) -> str:
    if not raw or "\x00" in raw:
        raise ValueError("ZIP contains an empty or invalid file path.")
    # ZIP uses '/', but treat backslashes as separators too so a hostile archive
    # cannot become traversal when extracted on Windows.
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"ZIP entry uses an absolute path: {raw!r}")
    parts = normalized.split("/")
    if normalized.endswith("/"):
        parts = parts[:-1]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"ZIP entry has an unsafe path: {raw!r}")
    path = PurePosixPath(*parts)
    if path.is_absolute():
        raise ValueError(f"ZIP entry has an unsafe path: {raw!r}")
    return path.as_posix() + ("/" if normalized.endswith("/") else "")


def _validate_archive(data: bytes) -> list[tuple[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("The selected file is not a readable ZIP archive.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(f"ZIP has {len(infos)} entries; limit is {MAX_ARCHIVE_ENTRIES}.")
        planned: list[tuple[str, zipfile.ZipInfo]] = []
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = _safe_member_path(info.filename)
            collision_key = name.rstrip("/").casefold()
            if collision_key in seen:
                raise ValueError(f"ZIP has duplicate or case-conflicting entry: {name!r}.")
            seen.add(collision_key)
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type == stat.S_IFLNK:
                raise ValueError(f"ZIP symbolic links are not allowed: {name!r}.")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f"ZIP special files are not allowed: {name!r}.")
            if info.file_size < 0 or info.file_size > MAX_ARCHIVE_FILE_BYTES:
                raise ValueError(
                    f"ZIP entry {name!r} is {info.file_size} bytes; per-file limit is "
                    f"{MAX_ARCHIVE_FILE_BYTES} bytes."
                )
            total += info.file_size
            if total > MAX_ARCHIVE_TOTAL_BYTES:
                raise ValueError(
                    f"ZIP expands to more than the {MAX_ARCHIVE_TOTAL_BYTES}-byte total limit."
                )
            planned.append((name, info))
        return planned


def _safe_workspace_relative(path: str, *, what: str) -> str:
    raw = path.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"{what} must be a workspace-relative path, such as inbound/archive.zip.")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{what} contains an unsafe path component.")
    return PurePosixPath(*parts).as_posix()


def build_feature_creation_tools(*, registry: Any, repos: Any) -> list[StructuredTool]:
    """Give user-owned agents feature creation and ZIP extraction capabilities."""

    async def feature_create(
        name: str,
        feature_id: str = "",
        description: str | None = None,
        default_model: str | None = None,
        icon_name: str | None = None,
        color: str | None = None,
    ) -> str:
        """Create an enabled feature agent owned by the current user."""
        try:
            _agent_id, user_id, caller_row = _owned_turn(registry)
            if is_feature_agent(str(getattr(caller_row, "kind", ""))):
                raise ValueError("A feature agent cannot create another feature agent.")
            user = repos.user_repo.get(user_id)
            if user is None:
                raise ValueError("The current user no longer exists.")
            unit_repo = getattr(repos, "org_unit_repo", None)
            grants = (
                unit_permissions(getattr(user, "org_unit", None), unit_repo) if unit_repo else set()
            )
            if not user_has_permission(user, "features", unit_grants=grants):
                raise ValueError("The current user does not have the 'features' permission.")
            cleaned_name = name.strip()
            if not cleaned_name:
                raise ValueError("Feature name must not be empty.")
            public_id = feature_id.strip() or re.sub(
                r"[^a-z0-9]+", "-", cleaned_name.lower()
            ).strip("-")
            if not public_id:
                raise ValueError("Could not derive a feature id; provide feature_id explicitly.")
            agent_id = feature_agent_id(public_id)
            if agent_id is None:
                raise ValueError(
                    "feature_id cannot be used as an agent id; use a short lowercase id with letters, digits, and hyphens."
                )
            created = await registry.create(
                AgentCreateSpec(
                    agent_id=agent_id,
                    name=cleaned_name,
                    user_id=user_id,
                    kind=KIND_FEATURE,
                    description=description,
                    icon_name=icon_name,
                    color=color,
                    default_model=default_model,
                )
            )
            return _json_ok(
                {
                    "created": True,
                    "feature_id": public_id,
                    "agent_id": created.agent_id,
                    "name": created.name,
                    "state": created.last_state or "unknown",
                    "conversation": f"Open the new feature agent conversation for {created.agent_id}.",
                    "workspace": f"The feature's own workspace is attached to agent {created.agent_id}; use its conversation to write SOUL.md and .octop/workflow.json.",
                }
            )
        except Exception as exc:
            return _json_error(exc)

    async def archive_extract(archive_path: str, destination: str = "") -> str:
        """Safely extract a ZIP in this agent's workspace and report its files."""
        try:
            agent_id, _user_id, _row = _owned_turn(registry)
            workspace = registry.workspace_for_agent(agent_id)
            if workspace is None:
                raise ValueError("This agent's workspace is not reachable.")
            source = _safe_workspace_relative(archive_path, what="archive_path")
            if not source.startswith("inbound/"):
                raise ValueError("ZIP archives must be uploaded under inbound/.")
            if not source.lower().endswith(".zip"):
                raise ValueError("Only .zip archives are supported.")
            dest = (
                _safe_workspace_relative(destination, what="destination")
                if destination
                else source[:-4]
            )
            if dest == "inbound" or not dest.startswith("inbound/"):
                raise ValueError("Extraction destination must be a directory under inbound/.")
            data = await workspace.adownload_bytes(source)
            if data is None:
                raise ValueError(
                    f"Cannot read {source!r}; confirm the ZIP was uploaded to this agent's inbound/ directory."
                )
            planned = _validate_archive(data)
            paths: list[str] = []
            total_written = 0
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for name, info in planned:
                    target = (
                        f"{dest}/{name.rstrip('/')}" if name.endswith("/") else f"{dest}/{name}"
                    )
                    if info.is_dir() or name.endswith("/"):
                        await workspace.amkdir(target)
                        paths.append(target + "/")
                        continue
                    parent = str(PurePosixPath(target).parent)
                    if parent != ".":
                        await workspace.amkdir(parent)
                    with archive.open(info, "r") as entry:
                        chunks: list[bytes] = []
                        size = 0
                        while chunk := entry.read(64 * 1024):
                            size += len(chunk)
                            total_written += len(chunk)
                            if size > MAX_ARCHIVE_FILE_BYTES:
                                raise ValueError(
                                    f"ZIP entry {name!r} exceeded the per-file extraction limit while reading."
                                )
                            if total_written > MAX_ARCHIVE_TOTAL_BYTES:
                                raise ValueError(
                                    "ZIP expanded beyond the total extraction limit while reading."
                                )
                            chunks.append(chunk)
                        payload = b"".join(chunks)
                    if len(payload) != info.file_size:
                        raise ValueError(f"ZIP entry {name!r} has inconsistent size metadata.")
                    await workspace.aupload_bytes(target, payload)
                    paths.append(target)
            tree = sorted(paths, key=str.casefold)
            return _json_ok(
                {
                    "extracted_to": dest,
                    "files": tree,
                    "next": "Read the extracted README.md or entry-point files first, then inspect relevant skills/<slug>/SKILL.md and agents/<slug>.md. Write feature instructions at workspace-root SOUL.md/AGENTS.md and define the runnable feature in .octop/workflow.json using feature_workflow_save.",
                }
            )
        except Exception as exc:
            return _json_error(exc)

    return [
        StructuredTool.from_function(
            coroutine=feature_create,
            name="feature_create",
            description=(
                "Create and start a new feature agent owned by the current user. "
                "Returns its id and where to continue in its conversation/workspace."
            ),
        ),
        StructuredTool.from_function(
            coroutine=archive_extract,
            name="archive_extract",
            description=(
                "Extract an uploaded ZIP from this agent's inbound/ directory into a safe "
                "inbound/ subdirectory. Rejects traversal, links, non-ZIP input, and oversized archives."
            ),
        ),
    ]


__all__ = ["build_feature_creation_tools"]
