"""Host filesystem directory helpers for dashboard root_dir pickers."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

ProbeCode = Literal[
    "not_directory",
    "permission_denied",
    "write_failed",
    "not_allowed",
    "outside_home",
    "outside_root",
]

_MAX_LIST_ENTRIES = 1000

# Host paths that must not be browsed or used as workspace roots (POSIX).
_DENIED_PREFIXES_POSIX = ("/proc", "/sys", "/dev", "/etc", "/root")


def _install_root() -> Path:
    raw = os.environ.get("MAITU_HOME", "").strip() or os.environ.get("OCTOP_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".maitu"


def _license_config() -> dict[str, Any]:
    """Read the public license section without importing the domain layer.

    ``infra/utils`` must stay below the licensing/service modules. The wizard
    and settings page persist trust paths and the center URL in ``config.json``,
    so hardened-install checks must consider that file, not only env vars.
    """
    home = _install_root()
    candidates = [home / "config.json"]
    env_home = os.environ.get("MAITU_HOME", "").strip() or os.environ.get("OCTOP_HOME", "").strip()
    if not env_home:
        legacy = Path.home() / ".octop" / "config.json"
        if legacy != candidates[0]:
            candidates.append(legacy)
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        section = raw.get("license") if isinstance(raw, dict) else None
        if isinstance(section, dict):
            return section
    return {}


def _resolve_license_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip()).expanduser()
    return candidate if candidate.is_absolute() else _install_root() / candidate


def _license_trust_paths() -> tuple[list[Path], list[Path], list[Path]]:
    """Return existing/configured keyring, CA and state locations."""
    cfg = _license_config()
    home = _install_root()
    key_candidates: list[Path] = []
    ca_candidates: list[Path] = []
    state_candidates: list[Path] = []
    env_key = os.environ.get("MAITU_LICENSE_TRUSTED_KEYS_FILE", "").strip()
    env_ca = os.environ.get("MAITU_LICENSE_CA_FILE", "").strip()
    env_state = os.environ.get("MAITU_LICENSE_STATE_DIR", "").strip()
    for value, target in (
        (cfg.get("trusted_keys_file"), key_candidates),
        (env_key, key_candidates),
    ):
        resolved = _resolve_license_path(value)
        if resolved is not None and resolved not in target:
            target.append(resolved)
    default_key = home / "license" / "trusted-keys.json"
    if default_key not in key_candidates:
        key_candidates.append(default_key)
    for value, target in ((cfg.get("ca_file"), ca_candidates), (env_ca, ca_candidates)):
        resolved = _resolve_license_path(value)
        if resolved is not None and resolved not in target:
            target.append(resolved)
    for value, target in ((cfg.get("state_dir"), state_candidates), (env_state, state_candidates)):
        resolved = _resolve_license_path(value)
        if resolved is not None and resolved not in target:
            target.append(resolved)
    if cfg:
        default_state = home / "license" / "state"
        if default_state not in state_candidates:
            state_candidates.append(default_state)
    state_candidates.extend(
        [
            home.parent / ".maitu-license",
            Path.home() / ".maitu-license",
        ]
    )
    return key_candidates, ca_candidates, state_candidates


def license_managed_install() -> bool:
    """True when this install is license-managed.

    Trust material may come from the environment (deployment scripts) or from
    the first-run wizard/application settings (``config.json`` + protected
    ``license/`` files). Both forms must enable the same hardened defaults.
    """
    keys, _, _ = _license_trust_paths()
    if any(path.is_file() for path in keys):
        return True
    if os.environ.get("MAITU_LICENSE_TRUSTED_KEYS_FILE", "").strip():
        return True
    cfg = _license_config()
    return bool(isinstance(cfg.get("trusted_keys_file"), str) and cfg["trusted_keys_file"].strip())


def license_protected_dir_covered_by(root_dir: str) -> bool:
    """True when *root_dir* would expose license identity/trust locations.

    A backend root that *contains* the protected directories (e.g. the process
    home covering ``~/.maitu-license``) is as bad as pointing at them directly:
    scoped jail roots must not span the trust locations on managed installs.
    """
    if not license_managed_install():
        return False
    root_real = os.path.realpath(os.path.expanduser(root_dir))
    root_n = os.path.normcase(root_real)
    root_prefix = root_n if root_n.endswith(os.sep) else root_n + os.sep
    for prefix in _license_protected_prefixes():
        prefix_n = os.path.normcase(os.path.realpath(os.path.expanduser(prefix)))
        if root_n == prefix_n or prefix_n.startswith(root_prefix):
            return True
    return False


def _license_protected_prefixes() -> list[str]:
    """Protected license identity/trust locations on license-managed installs.

    Managed trust can be provisioned through env vars or through the setup
    wizard/settings page; both paths must be hidden from workspace pickers.
    The license state directory (identity private key, entitlement ledger,
    cached credentials) and the directories holding pinned trust keys must
    never be browsable or selectable as a workspace/data root by dashboard
    users, even admins.
    """
    keys, ca_files, states = _license_trust_paths()
    prefixes: list[str] = [str(path) for path in states]
    prefixes.extend(str(path.parent) for path in keys)
    prefixes.extend(str(path.parent) for path in ca_files)
    env_update_key = os.environ.get("MAITU_UPDATE_TRUSTED_KEYS_FILE", "").strip()
    if env_update_key:
        prefixes.append(str(Path(env_update_key).parent))
    deduped: list[str] = []
    for prefix in prefixes:
        if prefix and prefix not in ("/", "C:\\", "C:/") and prefix not in deduped:
            deduped.append(prefix)
    return deduped


_OUTSIDE_HOME_MSG = "path outside home"
_OUTSIDE_ROOT_MSG = "path outside allowed workspace root"
_NOT_ALLOWED_MSG = "path not allowed"


def host_path_text(path: Path) -> str:
    """Serialize a host path for API/UI (POSIX separators, even on Windows)."""
    return Path(os.path.realpath(os.path.expanduser(str(path)))).as_posix()


def host_home_dir() -> Path:
    """Absolute home directory of the OS user running the Octop process."""
    return Path(os.path.realpath(os.path.expanduser(str(Path.home()))))


def host_fs_tree_root(*, allow_outside_home: bool) -> str:
    """Browse-tree root for the root_dir picker.

    When *allow_outside_home* is true (current API default for all users):
    host ``/`` on POSIX, or the home drive root (e.g. ``C:/``) on Windows.
    Otherwise: the process home directory (legacy helper mode).
    """
    home = host_home_dir()
    if not allow_outside_home:
        return host_path_text(home)
    if os.name == "posix":
        return "/"
    return host_path_text(Path(home.anchor))


def running_in_container() -> bool:
    """True when the Octop process appears to run inside a container.

    Used so Docker (and Podman) deployments default local backend ``root_dir``
    to filesystem root — the container is already the isolation boundary.

    Override with ``OCTOP_IN_CONTAINER=1|0`` (also ``true``/``false``).
    """
    forced = os.environ.get("OCTOP_IN_CONTAINER", "").strip().lower()
    if forced in {"1", "true", "yes", "on"}:
        return True
    if forced in {"0", "false", "no", "off"}:
        return False
    if os.name != "posix":
        return False
    return Path("/.dockerenv").is_file() or Path("/run/.containerenv").is_file()


def default_host_root_dir(*, allow_outside_home: bool) -> str:
    """UI default for local backend ``root_dir``.

    Process home on bare metal; filesystem root when running in a container
    (and browsing outside home is allowed). Policy-restricted callers should
    pass their jail path instead of calling this helper.
    """
    if allow_outside_home and running_in_container():
        return host_fs_tree_root(allow_outside_home=True)
    return host_path_text(host_home_dir())


def _path_within_base(resolved: str, base: str) -> bool:
    """True when *resolved* equals *base* or is a subdirectory (normcase-safe).

    Uses ``startswith`` after ``os.path.realpath`` so CodeQL treats this as a
    path-injection containment barrier (``Path.resolve`` / ``relative_to`` are
    not modeled as sanitizers).
    """
    resolved_n = os.path.normcase(resolved)
    base_n = os.path.normcase(base)
    if resolved_n == base_n:
        return True
    if base_n.endswith(os.sep):
        return resolved_n.startswith(base_n)
    return resolved_n.startswith(base_n + os.sep)


def is_within_host_home(resolved: Path, *, home: Path | None = None) -> bool:
    """True when *resolved* is the host home directory or a subdirectory."""
    base = os.path.realpath(str(home or host_home_dir()))
    target = os.path.realpath(str(resolved))
    return _path_within_base(target, base)


def normalize_host_path(path: str) -> Path:
    """Canonical absolute path via ``os.path.realpath`` (CodeQL-recognized)."""
    raw = path.strip() or ("/" if os.name == "posix" else str(Path.home().anchor))
    return Path(os.path.realpath(os.path.expanduser(raw)))


def _browse_tree_base() -> str:
    """Containment root for home-jailed picker paths."""
    return os.path.realpath(str(host_home_dir()))


def _is_denied_host_path(resolved: Path) -> bool:
    if os.name != "posix":
        return False
    # Process home may coincide with a denylist prefix (uid 0 → ``/root``).
    # The UI defaults ``root_dir`` to home, so home and its subdirs must stay
    # selectable; ``/root`` remains denied for non-root process homes.
    if is_within_host_home(resolved):
        return False
    text = resolved.as_posix()
    # macOS resolves /etc → /private/etc (and similar). Strip that prefix so
    # denied policy still matches the logical system locations.
    if text == "/private" or text.startswith("/private/"):
        text = text[len("/private") :] or "/"
    if any(text == denied or text.startswith(f"{denied}/") for denied in _DENIED_PREFIXES_POSIX):
        return True
    for protected in _license_protected_prefixes():
        protected_s = os.path.normcase(os.path.realpath(os.path.expanduser(protected)))
        resolved_s = os.path.normcase(text)
        if resolved_s == protected_s or resolved_s.startswith(protected_s + os.sep):
            return True
    return False


def _within_restrict_root(resolved_s: str, restrict_to_root: str | None) -> bool:
    if not restrict_to_root or not str(restrict_to_root).strip():
        return True
    base = os.fspath(normalize_host_path(str(restrict_to_root).strip()))
    return _path_within_base(resolved_s, base)


def assert_safe_host_path(
    path: str,
    *,
    restrict_to_home: bool = False,
    restrict_to_root: str | None = None,
) -> Path:
    """Resolve *path* and reject traversal tricks / disallowed host locations.

    When *restrict_to_home* is true, only the process home directory and its
    subdirectories are allowed. The HTTP filesystem / expert APIs pass
    ``False`` so any authenticated user may pick outside home (denylist still
    applies).

    Normalization uses ``os.path.realpath`` and containment uses ``startswith``
    against the browse-tree base — the pattern CodeQL recognizes for
    ``py/path-injection`` (unlike ``Path.resolve`` alone).
    """
    if not path or "\0" in path:
        raise ValueError("invalid path")
    try:
        # normalize_host_path already realpath's; keep a str for startswith
        # so CodeQL sees a containment sanitizer on the FS path we return.
        resolved_s = os.fspath(normalize_host_path(path))
    except OSError as exc:
        raise ValueError("invalid path") from exc
    if not os.path.isabs(resolved_s):
        raise ValueError("path must be absolute")
    resolved = Path(resolved_s)
    if _is_denied_host_path(resolved):
        raise ValueError(_NOT_ALLOWED_MSG)
    # Home jail only. The host-root case (restrict_to_home=False) allows any
    # absolute path: on POSIX everything sits under "/", and on Windows the
    # denylist is empty while windows_neutralize_host_root rewrites "/" at
    # runtime, so a drive-relative "/" must not be rejected there.
    if restrict_to_home:
        base = _browse_tree_base()
        if not _path_within_base(resolved_s, base):
            raise ValueError(_OUTSIDE_HOME_MSG)
    if restrict_to_root and not _within_restrict_root(resolved_s, restrict_to_root):
        raise ValueError(_OUTSIDE_ROOT_MSG)
    return resolved


def list_host_subdirs(
    path: str,
    *,
    restrict_to_home: bool = False,
    restrict_to_root: str | None = None,
) -> list[dict[str, Any]]:
    """List readable child directories under *path*."""
    root = assert_safe_host_path(
        path, restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
    )
    if not root.is_dir():
        raise ValueError(f"not a directory: {path}")

    entries: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise ValueError(f"permission denied: {path}") from exc

    for child in children:
        if len(entries) >= _MAX_LIST_ENTRIES:
            break
        if not child.is_dir():
            continue
        try:
            resolved = Path(os.path.realpath(str(child)))
            if not resolved.is_dir():
                continue
            if _is_denied_host_path(resolved):
                continue
            if restrict_to_home and not is_within_host_home(resolved):
                continue
            if restrict_to_root and not _within_restrict_root(
                os.fspath(resolved), restrict_to_root
            ):
                continue
            if not os.access(resolved, os.R_OK | os.X_OK):
                continue
        except OSError:
            continue
        entries.append({"path": host_path_text(resolved), "name": child.name})
    return entries


def probe_host_root_dir(
    path: str,
    *,
    restrict_to_home: bool = False,
    restrict_to_root: str | None = None,
) -> dict[str, Any]:
    """Verify *path* exists; write-probe only when not filesystem root ``/``."""
    try:
        root = assert_safe_host_path(
            path, restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
        )
    except ValueError as exc:
        message = str(exc)
        if _OUTSIDE_HOME_MSG in message:
            code: ProbeCode = "outside_home"
        elif _OUTSIDE_ROOT_MSG in message:
            code = "outside_root"
        elif _NOT_ALLOWED_MSG in message:
            code = "not_allowed"
        else:
            code = "not_directory"
        return {"ok": False, "code": code, "detail": message}

    if not root.is_dir():
        return {"ok": False, "code": "not_directory"}

    if os.name == "posix" and root.as_posix() == "/":
        return {"ok": True, "path": "/"}

    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        return {"ok": False, "code": "permission_denied"}

    probe_file = root / f".octop-root-probe-{uuid.uuid4().hex}"
    try:
        probe_file.write_text("", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "code": "write_failed", "detail": str(exc)}
    finally:
        with contextlib.suppress(OSError):
            probe_file.unlink(missing_ok=True)

    return {"ok": True, "path": host_path_text(root)}


def _validate_dir_basename(name: str) -> str:
    """Reject empty names, path segments, and traversal tokens."""
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid name")
    if "/" in cleaned or "\\" in cleaned or "\0" in cleaned:
        raise ValueError("invalid name")
    if cleaned != Path(cleaned).name:
        raise ValueError("invalid name")
    return cleaned


def _unique_child_name(parent: Path, base_name: str) -> str:
    candidate = base_name
    index = 2
    while (parent / candidate).exists():
        candidate = f"{base_name} ({index})"
        index += 1
    return candidate


def mkdir_host_subdir(
    parent: str,
    *,
    base_name: str = "New Folder",
    restrict_to_home: bool = False,
    restrict_to_root: str | None = None,
) -> dict[str, Any]:
    """Create a child directory under *parent* with an unused name from *base_name*."""
    root = assert_safe_host_path(
        parent, restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
    )
    if not root.is_dir():
        raise ValueError(f"not a directory: {parent}")
    name = _unique_child_name(root, _validate_dir_basename(base_name))
    target = root / name
    # Re-check after composing path (deny creating into denied prefixes).
    assert_safe_host_path(
        str(target), restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
    )
    try:
        target.mkdir(exist_ok=False)
    except OSError as exc:
        raise ValueError(f"mkdir failed: {exc}") from exc
    return {"path": host_path_text(target), "name": name}


def rename_host_dir(
    path: str,
    new_name: str,
    *,
    restrict_to_home: bool = False,
    restrict_to_root: str | None = None,
) -> dict[str, Any]:
    """Rename a host directory in place (basename only)."""
    source = assert_safe_host_path(
        path, restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
    )
    if not source.is_dir():
        raise ValueError(f"not a directory: {path}")
    name = _validate_dir_basename(new_name)
    target = source.parent / name
    assert_safe_host_path(
        str(target), restrict_to_home=restrict_to_home, restrict_to_root=restrict_to_root
    )
    if target.exists():
        raise ValueError("already exists")
    try:
        source.rename(target)
    except OSError as exc:
        raise ValueError(f"rename failed: {exc}") from exc
    return {"path": host_path_text(target), "name": name}


def iter_local_backend_root_dirs(spec: Any) -> list[str]:
    """Collect ``root_dir`` values from local_shell / filesystem backend specs."""
    if not isinstance(spec, dict):
        return []
    kind = str(spec.get("type") or "").strip()
    found: list[str] = []
    if kind in {"local_shell", "filesystem"}:
        root = spec.get("root_dir")
        if isinstance(root, str) and root.strip():
            found.append(root.strip())
        elif root is None or (isinstance(root, str) and not root.strip()):
            # Missing root_dir historically meant host root ``/``.
            found.append("/")
    elif kind == "composite":
        default = spec.get("default")
        found.extend(iter_local_backend_root_dirs(default))
        routes = spec.get("routes")
        if isinstance(routes, dict):
            for route_spec in routes.values():
                found.extend(iter_local_backend_root_dirs(route_spec))
    return found


def assert_backend_root_dirs_allowed(spec: Any, *, restrict_to_home: bool) -> None:
    """Raise ``ValueError`` when a local backend ``root_dir`` is not browsable."""
    for root_dir in iter_local_backend_root_dirs(spec):
        assert_safe_host_path(root_dir, restrict_to_home=restrict_to_home)
