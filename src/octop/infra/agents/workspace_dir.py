"""Agent ``workspace_dir`` (persisted in ``config_json``).

``workspace_dir`` and ``backend.root_dir`` are different dimensions:

* ``root_dir`` — local backend rootfs (agent-visible ``/``).
* ``workspace_dir`` — the agent's working directory. After create, the DB value
  is authoritative and is what harness receives **verbatim** (never
  ``root_dir``-joined).

Create-time defaults when unset:

* Default workspace → on-disk ``{OCTOP_HOME}/agents/<id>/`` and persisted as
  that host absolute path.
* Scoped ``root_dir`` mapping is retained for legacy path resolution only;
  runtime confines local backend roots to the managed per-agent workspace.

An explicitly supplied ``workspace_dir`` is accepted only when its resolved
host path equals ``{OCTOP_HOME}/agents/<id>/``; external paths are rejected.

:func:`resolve_workspace_host_path` is only for Octop host FS ops (delete,
memory sqlite path, etc.) when the persisted value is the agent-facing
``/.maitu/workspaces/…`` form.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.legacy_names import LEGACY_BRAND
from octop.infra.utils.paths import BRAND_ID, PathLayout

logger = logging.getLogger(__name__)

# The system directory is an on-disk identifier, not display copy: it carries the
# same lowercase brand id as the home directory and database file name.
SYSTEM_FILES_DIRNAME = "." + BRAND_ID
DEFAULT_SYSTEM_FILES_PATH = SYSTEM_FILES_DIRNAME

# The previous default. It is assembled from the shared legacy fragments on
# purpose: this is data about already-created workspaces, and a brand-replacement
# pass over display strings must not rewrite it into the new name (that would
# lose the ability to find — and migrate — an old workspace).
LEGACY_SYSTEM_FILES_DIRNAME = "." + LEGACY_BRAND
LEGACY_AUTH_DIRNAME = LEGACY_SYSTEM_FILES_DIRNAME + "-auth"
"""Standalone auth directory of the old layout (``.octop-auth`` at workspace root)."""

MIGRATED_AUTH_DIRNAME = SYSTEM_FILES_DIRNAME + "-auth"
"""Where an old standalone auth directory is moved (``.maitu-auth``)."""

AUTH_DIRNAME = "auth"
"""Canonical auth directory inside the system directory (``.maitu/auth``)."""

SCOPED_WORKSPACE_DIRNAME = "workspaces"

_WINDOWS_HOST_PATH_RE = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\|//)")


def _running_on_windows() -> bool:
    return os.name == "nt"


def _is_host_root_sentinel(root: str | Path | None) -> bool:
    if root is None:
        return True
    return str(root).strip() in ("/", "\\", "")


def _is_windows_style_host_path(raw: str) -> bool:
    return bool(_WINDOWS_HOST_PATH_RE.match(raw.strip()))


def _is_agent_facing_system_root(text: str) -> bool:
    """Whether *text* is ``/`` or an agent-facing workspace under the system root.

    Both the current (``/.maitu/…``) and the legacy (``/.octop/…``) agent-facing
    forms qualify: a persisted ``workspace_dir`` written by an older build must
    keep resolving. That host root is not moved by the layout migration — only
    the system directory nested inside the workspace is.
    """
    normalized = text.replace("\\", "/")
    if normalized == "/":
        return True
    return normalized.startswith((f"/{SYSTEM_FILES_DIRNAME}/", f"/{LEGACY_SYSTEM_FILES_DIRNAME}/"))


def local_backend_root_dir(cfg: dict[str, Any] | None) -> str | None:
    """Return ``root_dir`` from a local ``filesystem`` / ``local_shell`` backend spec."""
    backend = (cfg or {}).get("backend")
    if not isinstance(backend, dict):
        return None
    kind = str(backend.get("type") or "").lower()
    if kind in {"local_shell", "filesystem"}:
        raw = backend.get("root_dir")
        return str(raw).strip() if raw is not None and str(raw).strip() else None
    if kind == "composite":
        default = backend.get("default")
        if isinstance(default, dict):
            nested = str(default.get("type") or "").lower()
            if nested in {"local_shell", "filesystem"}:
                raw = default.get("root_dir")
                return str(raw).strip() if raw is not None and str(raw).strip() else None
    return None


def uses_scoped_workspace_default(cfg: dict[str, Any] | None) -> bool:
    root_raw = local_backend_root_dir(cfg)
    return root_raw is not None and not _is_host_root_sentinel(root_raw)


def scoped_workspace_dir_str(agent_id: str) -> str:
    """Agent-facing / harness workspace path for scoped-root create defaults."""
    return f"/{DEFAULT_SYSTEM_FILES_PATH}/{SCOPED_WORKSPACE_DIRNAME}/{agent_id}"


def default_agent_workspace_dir(
    paths: PathLayout,
    agent_id: str,
    *,
    cfg: dict[str, Any] | None = None,
    ensure: bool = True,
) -> Path:
    """Resolve legacy on-disk defaults (mkdir -p unless ``ensure=False``).

    New agents use ``seed_workspace_dir_on_create`` and the managed directory.
    """
    root_raw = local_backend_root_dir(cfg)
    if root_raw is not None and not _is_host_root_sentinel(root_raw):
        try:
            root = Path(root_raw).expanduser().resolve()
        except OSError:
            if ensure:
                return paths.ensure_agent_workspace(agent_id)
            return paths.agent_workspace(agent_id)
        out = root / DEFAULT_SYSTEM_FILES_PATH / SCOPED_WORKSPACE_DIRNAME / agent_id
        if ensure:
            out.mkdir(parents=True, exist_ok=True)
        return out
    if ensure:
        return paths.ensure_agent_workspace(agent_id)
    return paths.agent_workspace(agent_id)


def seed_workspace_dir_on_create(
    config: dict[str, Any],
    *,
    paths: PathLayout,
    agent_id: str,
) -> Path:
    """Ensure ``config["workspace_dir"]`` at create; return on-disk Path for seeding."""
    expected = paths.agent_workspace(agent_id).resolve()
    raw = config.get("workspace_dir")
    if isinstance(raw, str) and raw.strip():
        host = resolve_workspace_host_path(raw.strip(), config)
        if host != expected:
            raise OctopError(
                ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED,
                "Agent workspaces must remain inside MAITU Smart Manufacturing's managed workspace directory.",
            )
    expected.mkdir(parents=True, exist_ok=True)
    config["workspace_dir"] = str(expected)
    return expected


def _relative_under(child: Path, root: Path) -> Path:
    if not _running_on_windows():
        return child.resolve().relative_to(root.resolve())
    child_s = str(child.resolve())
    root_s = str(root.resolve())
    if child_s.casefold() == root_s.casefold():
        return Path(".")
    prefix = root_s if root_s.endswith(("\\", "/")) else root_s + os.sep
    if not child_s.casefold().startswith(prefix.casefold()):
        raise ValueError(f"{child_s!r} is not under {root_s!r}")
    return Path(child_s[len(prefix) :].replace("\\", "/"))


def _path_under(child: Path, root: Path) -> bool:
    try:
        _relative_under(child, root)
        return True
    except (ValueError, OSError):
        return False


def resolve_workspace_host_path(raw: str, cfg: dict[str, Any] | None = None) -> Path:
    """Map persisted ``workspace_dir`` to an on-disk path for Octop host FS ops.

    Harness receives the persisted string as-is (no join here). When the DB
    holds an agent-facing ``/.maitu/workspaces/…`` (or a legacy
    ``/.octop/workspaces/…``) and the backend has a scoped ``root_dir``, the
    on-disk tree is ``{root_dir}/<that path>``.
    """
    text = raw.strip()
    if not text:
        raise ValueError("workspace_dir is empty")
    if _is_windows_style_host_path(text):
        return Path(text).expanduser().resolve()

    root_raw = local_backend_root_dir(cfg)
    if root_raw is None or _is_host_root_sentinel(root_raw):
        return Path(text).expanduser().resolve()

    root = Path(root_raw).expanduser().resolve()
    normalized = text.replace("\\", "/").strip()
    candidate = Path(text).expanduser()

    if _is_agent_facing_system_root(normalized):
        rel = normalized.lstrip("/")
        return (root / rel).resolve() if rel else root

    try:
        host_resolved = candidate.resolve()
    except OSError:
        host_resolved = candidate

    if _path_under(host_resolved, root):
        return host_resolved

    if candidate.is_absolute():
        return (
            host_resolved if host_resolved.exists() or not _running_on_windows() else host_resolved
        )

    return (root / text).resolve()


def harness_workspace_path(raw: str, cfg: dict[str, Any] | None = None) -> Path:
    """Path handed to harness as ``workspace_dir``.

    The persisted value goes through as-is whenever the platform can express it
    as an absolute path. Windows cannot: the agent-facing
    ``/.maitu/workspaces/<id>`` (or a legacy ``/.octop/…``) form has no drive
    letter, and harness rejects a non-absolute workspace — fall back to the
    on-disk host mapping there.
    """
    text = raw.strip()
    if not text:
        raise ValueError("workspace_dir is empty")
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    return resolve_workspace_host_path(text, cfg)


def _backend_virtual_mode(cfg: dict[str, Any] | None) -> bool:
    backend = (cfg or {}).get("backend")
    if not isinstance(backend, dict):
        return False
    kind = str(backend.get("type") or "").lower()
    target = backend
    if kind == "composite":
        default = backend.get("default")
        if not isinstance(default, dict):
            return False
        target = default
        kind = str(target.get("type") or "").lower()
    if kind not in {"local_shell", "filesystem"}:
        return False
    return bool(target.get("virtual_mode", True))


def agent_facing_workspace_root(
    workspace_dir: Path | str,
    *,
    root_dir: Path | str | None = None,
    virtual_mode: bool = False,
) -> str:
    """Agent-visible workspace directory (never ``root_dir``-joined).

    Scoped virtual backends map the on-disk workspace back to a rootfs path
    such as ``/.maitu/workspaces/<id>`` (a persisted legacy ``/.octop/…`` form
    passes through unchanged). Host-rooted layouts keep the workspace path
    string as-is.
    """
    text = str(workspace_dir).strip().replace("\\", "/")
    if not text:
        return ""
    if _is_agent_facing_system_root(text):
        return text if text != "/" else "/"
    if not virtual_mode or root_dir is None or _is_host_root_sentinel(root_dir):
        return text
    try:
        ws = Path(text).expanduser().resolve()
        root = Path(str(root_dir)).expanduser().resolve()
        rel = _relative_under(ws, root).as_posix()
    except (OSError, ValueError):
        return text
    if not rel or rel == ".":
        return "/"
    return f"/{rel}"


def agent_facing_workspace_dir_from_config(cfg: dict[str, Any] | None) -> str:
    """Persisted / agent-visible ``workspace_dir`` for path hints and artifacts."""
    raw = (cfg or {}).get("workspace_dir")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = raw.strip().replace("\\", "/")
    if _is_agent_facing_system_root(text):
        return text
    return agent_facing_workspace_root(
        text,
        root_dir=local_backend_root_dir(cfg),
        virtual_mode=_backend_virtual_mode(cfg),
    )


def join_agent_facing(root: str, *parts: str) -> str:
    """Join workspace-relative segments under an agent-facing root."""
    base = (root or "").replace("\\", "/").rstrip("/") or ""
    segs: list[str] = []
    for part in parts:
        piece = str(part or "").replace("\\", "/").strip("/")
        if piece and piece != ".":
            segs.append(piece)
    if not segs:
        return base or "/"
    tail = "/".join(segs)
    if not base or base == "/":
        return f"/{tail}"
    return f"{base}/{tail}"


def workspace_dir_from_config(
    cfg: dict[str, Any] | None,
    *,
    paths: PathLayout,
    agent_id: str,
    ensure: bool = True,
) -> Path:
    """On-disk workspace for Octop host ops (may map ``/.maitu/…`` under root_dir).

    Read-only callers (backup) pass ``ensure=False`` so a stale or unwritable
    ``workspace_dir`` is not created.
    """
    raw = (cfg or {}).get("workspace_dir")
    if isinstance(raw, str) and raw.strip():
        out = resolve_workspace_host_path(raw, cfg)
        if ensure:
            out.mkdir(parents=True, exist_ok=True)
        return out
    return default_agent_workspace_dir(paths, agent_id, cfg=cfg, ensure=ensure)


def system_files_path_from_config(cfg: dict[str, Any] | None) -> str:
    """Configured system-directory name, with the old default answered as the new one.

    ``system_files_path`` is internal (clients cannot set it), but rows written by
    older builds may still carry ``.octop``; those are treated as the current
    default so every caller looks at the directory this build creates. An empty
    value keeps its meaning: the legacy layout with system files at the workspace
    root.
    """
    raw = (cfg or {}).get("system_files_path")
    if not isinstance(raw, str):
        return ""
    text = raw.strip().replace("\\", "/").strip("/")
    if not text or text == ".":
        return ""
    if ".." in text.split("/"):
        return ""
    if text == LEGACY_SYSTEM_FILES_DIRNAME:
        return SYSTEM_FILES_DIRNAME
    return text


def system_dir_relpaths(rel: str) -> tuple[str, ...]:
    """Workspace-relative paths of *rel* inside the system directory, current first.

    For readers that have no agent config at hand (published-snapshot compat,
    workflow definitions): the current layout is tried first, then the old one.
    Writers always use the current prefix.
    """
    leaf = rel.strip().replace("\\", "/").strip("/")
    current = f"{SYSTEM_FILES_DIRNAME}/{leaf}" if leaf else SYSTEM_FILES_DIRNAME
    legacy = f"{LEGACY_SYSTEM_FILES_DIRNAME}/{leaf}" if leaf else LEGACY_SYSTEM_FILES_DIRNAME
    return tuple(dict.fromkeys((current, legacy)))


def host_system_dir(workspace_dir: Path, cfg: dict[str, Any] | None) -> Path:
    prefix = system_files_path_from_config(cfg)
    return workspace_dir / prefix if prefix else workspace_dir


def agent_auth_dir(workspace_dir: Path, cfg: dict[str, Any] | None) -> Path:
    """Auth directory: canonical ``{system}/auth``, else a standalone old location.

    Three layouts exist: the canonical one inside the system directory, the
    migrated standalone ``.maitu-auth``, and the pre-rename ``.octop-auth``. A
    populated legacy directory wins over an empty canonical one so credentials
    written by an older build keep being found (and are not silently ignored).
    """
    prefix = system_files_path_from_config(cfg)
    canonical = workspace_dir / prefix / AUTH_DIRNAME if prefix else workspace_dir / AUTH_DIRNAME
    for legacy_name in (MIGRATED_AUTH_DIRNAME, LEGACY_AUTH_DIRNAME):
        legacy = workspace_dir / legacy_name
        try:
            legacy_has_files = legacy.is_dir() and any(legacy.iterdir())
            canonical_empty = not canonical.exists() or not any(canonical.iterdir())
        except OSError:
            legacy_has_files = False
            canonical_empty = True
        if legacy_has_files and canonical_empty:
            return legacy
    return canonical


def skills_discovery_roots(workspace_dir: Path) -> list[Path]:
    """Skill roots, current layout first; legacy/root roots only when they exist.

    New layouts keep skills under the system directory; older ones used the
    workspace root. Both are scanned so a half-migrated or unmigrated workspace
    does not lose its skills, and the root entry still covers the documented
    ``skills/`` override directory.
    """
    roots = [
        workspace_dir / SYSTEM_FILES_DIRNAME / "skills",
        workspace_dir / "skills",
    ]
    legacy = workspace_dir / LEGACY_SYSTEM_FILES_DIRNAME / "skills"
    if legacy.is_dir():
        roots.append(legacy)
    return roots


def workspace_dir_from_config_json(
    config_json: str | None,
    *,
    paths: PathLayout,
    agent_id: str,
    ensure: bool = True,
) -> Path:
    try:
        parsed = json.loads(config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    cfg = parsed if isinstance(parsed, dict) else {}
    return workspace_dir_from_config(cfg, paths=paths, agent_id=agent_id, ensure=ensure)


@dataclass(frozen=True)
class SystemDirMigration:
    """What :func:`migrate_workspace_system_dirs` did to one workspace."""

    moved_system: bool = False
    moved_auth: bool = False
    copied: bool = False
    failures: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.moved_system or self.moved_auth


def _migrate_directory(source: Path, target: Path) -> tuple[bool, bool, str | None]:
    """Move *source* to *target*; copy (keeping source read-only) when rename fails.

    Returns ``(moved, copied, error)``. Never deletes data: a copy leaves the old
    directory behind for recovery, made read-only so a stray writer does not
    resurrect it as the live layout while readers already prefer the new one.
    """
    if not source.is_dir() or target.exists() or target.is_symlink():
        return False, False, None
    try:
        source.rename(target)
        return True, False, None
    except OSError:
        try:
            shutil.copytree(source, target, symlinks=True)
        except OSError as exc:
            shutil.rmtree(target, ignore_errors=True)
            return False, False, f"{source.name} -> {target.name}: {exc}"
        try:
            os.chmod(source, stat.S_IREAD | stat.S_IEXEC)
        except OSError:
            # Best effort: a platform that refuses the mode change still keeps
            # the copy (readers already prefer the migrated directory).
            logger.debug("could not mark %s read-only", source, exc_info=True)
        return True, True, None


def migrate_workspace_system_dirs(
    workspace_dir: Path, cfg: dict[str, Any] | None = None
) -> SystemDirMigration:
    """Move an older workspace's system and auth directories to the current names.

    ``{workspace}/.octop`` becomes ``{workspace}/.maitu`` and a standalone
    ``{workspace}/.octop-auth`` becomes ``{workspace}/.maitu-auth``. The system
    directory is only moved when the agent has one (an empty
    ``system_files_path`` means the legacy root layout, which has nothing to
    rename).

    Idempotent and non-destructive: rename is the normal path; on failure the
    contents are copied and the old directory kept read-only. Failures are logged
    and reported in the result, never raised — an unmigrated workspace must keep
    working (readers fall back to the legacy path).
    """
    if not workspace_dir.is_dir():
        return SystemDirMigration()
    prefix = system_files_path_from_config(cfg)
    source = workspace_dir / LEGACY_SYSTEM_FILES_DIRNAME
    target = workspace_dir / prefix if prefix else source
    moved_system = copied = False
    failures: list[str] = []
    if target != source:
        if source.is_dir() and target.exists():
            logger.warning(
                "workspace %s holds both %s and %s; keeping the current layout",
                workspace_dir,
                LEGACY_SYSTEM_FILES_DIRNAME,
                prefix,
            )
        moved_system, copied, error = _migrate_directory(source, target)
        if error:
            failures.append(error)
    moved_auth, copied_auth, error = _migrate_directory(
        workspace_dir / LEGACY_AUTH_DIRNAME,
        workspace_dir / MIGRATED_AUTH_DIRNAME,
    )
    copied = copied or copied_auth
    if error:
        failures.append(error)
    if moved_system or moved_auth:
        logger.info(
            "workspace %s migrated to the current system layout (system=%s auth=%s copied=%s)",
            workspace_dir,
            moved_system,
            moved_auth,
            copied,
        )
    for failure in failures:
        logger.warning("workspace %s system layout migration failed: %s", workspace_dir, failure)
    return SystemDirMigration(
        moved_system=moved_system,
        moved_auth=moved_auth,
        copied=copied,
        failures=tuple(failures),
    )


__all__ = [
    "AUTH_DIRNAME",
    "DEFAULT_SYSTEM_FILES_PATH",
    "LEGACY_AUTH_DIRNAME",
    "LEGACY_SYSTEM_FILES_DIRNAME",
    "MIGRATED_AUTH_DIRNAME",
    "SCOPED_WORKSPACE_DIRNAME",
    "SYSTEM_FILES_DIRNAME",
    "SystemDirMigration",
    "agent_auth_dir",
    "agent_facing_workspace_dir_from_config",
    "agent_facing_workspace_root",
    "default_agent_workspace_dir",
    "harness_workspace_path",
    "host_system_dir",
    "join_agent_facing",
    "local_backend_root_dir",
    "migrate_workspace_system_dirs",
    "resolve_workspace_host_path",
    "scoped_workspace_dir_str",
    "seed_workspace_dir_on_create",
    "skills_discovery_roots",
    "system_dir_relpaths",
    "system_files_path_from_config",
    "uses_scoped_workspace_default",
    "workspace_dir_from_config",
    "workspace_dir_from_config_json",
]
