"""Filesystem layout for the user data home (``~/.maitu/``).

The home directory and the core database/log filenames carry the white-label
brand id. Installs created under the pre-white-label name are upgraded in place
by :func:`migrate_legacy_home`; the LEGACY literals it matches live in
:mod:`octop.infra.utils.legacy_names` so a product-name replacement script
cannot rewrite them.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from octop.infra.utils.legacy_names import (
    LEGACY_BRAND,
    LEGACY_DB_FILENAME,
    LEGACY_HOME_DIRNAME,
    LEGACY_LOG_FILENAME,
    LEGACY_LOGIN_FILENAME,
)

logger = logging.getLogger(__name__)

# Lowercase product id used on disk (directories and file names). Keep in sync
# with ``brand.config.json`` -> ``$tokens.id``; it is deliberately a filesystem
# constant rather than the display name, so changing the display brand never
# moves customer data.
BRAND_ID = "maitu"
HOME_ENV = "MAITU_HOME"
# LEGACY: pre-white-label environment override. The new name wins when both are set.
LEGACY_HOME_ENV = f"{LEGACY_BRAND.upper()}_HOME"
DEFAULT_HOME_DIRNAME = f".{BRAND_ID}"
DB_FILENAME = f"{BRAND_ID}.db"
LOG_FILENAME = f"{BRAND_ID}.log"
LOGIN_FILENAME = f"{BRAND_ID}-login.txt"


@dataclass(frozen=True)
class PathLayout:
    root: Path

    @classmethod
    def from_env(cls) -> PathLayout:
        """Resolve install root from ``MAITU_HOME``/``OCTOP_HOME`` or ``~/.maitu``."""
        raw = os.environ.get(HOME_ENV, "").strip()
        if not raw:
            raw = os.environ.get(LEGACY_HOME_ENV, "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / DEFAULT_HOME_DIRNAME)

    @property
    def db(self) -> Path:
        return self.root / DB_FILENAME

    @property
    def logs_dir(self) -> Path:
        """Structured runtime logs: ``<home>/logs/``."""
        return self.root / "logs"

    @property
    def log(self) -> Path:
        return self.logs_dir / LOG_FILENAME

    def ensure_logs_dir(self) -> Path:
        """Create the logs directory and return it."""
        out = self.logs_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_log(self) -> Path:
        """Create the logs directory and return the log file path."""
        self.ensure_logs_dir()
        return self.log

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def users_dir(self) -> Path:
        return self.root / "users"

    def user_dir(self, username: str) -> Path:
        return self.users_dir / username

    @property
    def agents_dir(self) -> Path:
        """Global agents directory: ``<home>/agents/``."""
        return self.root / "agents"

    @property
    def expert_market_dir(self) -> Path:
        """Cached SkillHub expert templates: ``<home>/expert_market/``."""
        return self.root / "expert_market"

    @property
    def published_experts_dir(self) -> Path:
        """User-published expert snapshots: ``<home>/published_experts/``."""
        return self.root / "published_experts"

    @property
    def skill_packages_dir(self) -> Path:
        """Global skill package content: ``<home>/skill-packages/``."""
        return self.root / "skill-packages"

    @property
    def knowledge_dir(self) -> Path:
        """Global knowledge base files: ``<home>/knowledge/``."""
        return self.root / "knowledge"

    @property
    def knowledge_collections_dir(self) -> Path:
        """Private collection Markdown snapshots, never ingested as source."""
        return self.root / "knowledge-collections"

    def agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace: ``<home>/agents/<agent_id>/``."""
        return self.agents_dir / agent_id

    def ensure_agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace, mkdir -p."""
        out = self.agent_workspace(agent_id)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def tool_guard_rules_dir(self) -> Path:
        """User-editable command guard rules: ``<home>/security/tool_guard/``."""
        return self.root / "security" / "tool_guard"

    @property
    def tool_guard_rules_file(self) -> Path:
        return self.tool_guard_rules_dir / "dangerous_shell_commands.yaml"

    @property
    def backups_dir(self) -> Path:
        """Stored system backup archives: ``<home>/backups/``."""
        return self.root / "backups"

    def ensure_backups_dir(self) -> Path:
        out = self.backups_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def backup_file(self, filename: str) -> Path:
        """Resolve a backup archive path under :attr:`backups_dir` (basename only)."""
        return self.backups_dir / Path(filename).name

    @property
    def ssl_dir(self) -> Path:
        """TLS certificates and ACME account keys: ``<home>/ssl/``."""
        return self.root / "ssl"

    def ensure_ssl_dir(self) -> Path:
        out = self.ssl_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    @property
    def connector_cli_dir(self) -> Path:
        """Per-instance CLI config roots: ``<home>/connector-cli/``."""
        return self.root / "connector-cli"

    def connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        """Isolated config dir for one connector CLI instance."""
        safe_kind = Path(kind).name
        safe_key = (
            "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in instance_key)[:80]
            or "default"
        )
        return self.connector_cli_dir / safe_kind / safe_key

    def ensure_connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        out = self.connector_cli_instance_dir(kind, instance_key)
        out.mkdir(parents=True, exist_ok=True)
        return out


def _home_env_configured() -> bool:
    """True when either the new or the legacy home override is explicitly set."""
    return bool(os.environ.get(HOME_ENV, "").strip() or os.environ.get(LEGACY_HOME_ENV, "").strip())


def _move_directory(source: Path, target: Path) -> None:
    """Move a whole home directory, preferring an atomic same-volume rename."""
    try:
        os.replace(source, target)
        return
    except OSError:
        logger.debug("atomic rename %s -> %s failed; copying instead", source, target)
    shutil.copytree(source, target, dirs_exist_ok=True)
    try:
        shutil.rmtree(source)
    except OSError:
        # The data is already duplicated at the new root; a leftover source tree
        # is harmless and must never turn a successful migration into a failure.
        logger.warning("legacy home copied to %s but source cleanup failed: %s", target, source)


def _rename_legacy_db(root: Path) -> None:
    legacy_db = root / LEGACY_DB_FILENAME
    if not legacy_db.is_file():
        return
    target = root / DB_FILENAME
    if target.exists():
        logger.warning("both %s and %s exist; keeping %s", legacy_db, target, target)
        return
    try:
        legacy_db.replace(target)
    except OSError:
        logger.warning("could not rename %s -> %s", legacy_db, target, exc_info=True)
        return
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = root / f"{LEGACY_DB_FILENAME}{suffix}"
        if sidecar.exists():
            try:
                sidecar.replace(root / f"{DB_FILENAME}{suffix}")
            except OSError:
                logger.warning("could not rename sqlite sidecar %s", sidecar, exc_info=True)


def _rename_legacy_logs(paths: PathLayout) -> None:
    log_dir = paths.logs_dir
    if not log_dir.is_dir():
        return
    target = paths.log
    legacy_log = log_dir / LEGACY_LOG_FILENAME
    if legacy_log.is_file() and not target.exists():
        try:
            legacy_log.replace(target)
        except OSError:
            logger.warning("could not rename %s -> %s", legacy_log, target, exc_info=True)
    legacy_rotated_prefix = f"{LEGACY_LOG_FILENAME}."
    for entry in log_dir.glob(f"{legacy_rotated_prefix}*"):
        if not entry.is_file():
            continue
        renamed = log_dir / f"{LOG_FILENAME}.{entry.name[len(legacy_rotated_prefix) :]}"
        if renamed.exists():
            continue
        try:
            entry.replace(renamed)
        except OSError:
            logger.warning("could not rename rotated log %s", entry, exc_info=True)


def _rename_legacy_root_log(paths: PathLayout) -> None:
    """Move the very old single-file ``<home>/<legacy>.log`` into ``logs/``."""
    legacy = paths.root / LEGACY_LOG_FILENAME
    if not legacy.is_file() or paths.log.exists():
        return
    try:
        paths.ensure_logs_dir()
        legacy.replace(paths.log)
    except OSError:
        logger.warning("could not move legacy log %s", legacy, exc_info=True)


def _rewrite_legacy_sqlite_config(root: Path) -> None:
    """Point a persisted ``database.sqlite_path`` at the renamed database file."""
    config_path = root / "config.json"
    if not config_path.is_file():
        return
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("could not read %s while upgrading sqlite_path", config_path, exc_info=True)
        return
    if not isinstance(raw, dict):
        return
    database = raw.get("database")
    if not isinstance(database, dict):
        return
    value = database.get("sqlite_path")
    if not isinstance(value, str) or not value.strip():
        return
    candidate = Path(value).expanduser()
    resolved = candidate if candidate.is_absolute() else root / candidate
    if resolved != root / LEGACY_DB_FILENAME:
        return
    new_value = str(root / DB_FILENAME) if candidate.is_absolute() else DB_FILENAME
    database["sqlite_path"] = new_value
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(config_path)
    logger.info("updated config.json database.sqlite_path -> %s", new_value)


def _rename_legacy_login_file(home: Path) -> None:
    legacy = home / LEGACY_LOGIN_FILENAME
    if not legacy.is_file():
        return
    target = home / LOGIN_FILENAME
    if target.exists():
        return
    try:
        legacy.replace(target)
    except OSError:
        logger.warning("could not rename %s -> %s", legacy, target, exc_info=True)


def upgrade_legacy_names(paths: PathLayout) -> None:
    """Rename pre-white-label files inside an existing home directory (idempotent)."""
    if not paths.root.is_dir():
        return
    _rename_legacy_db(paths.root)
    _rename_legacy_logs(paths)
    _rename_legacy_root_log(paths)
    _rewrite_legacy_sqlite_config(paths.root)


def migrate_legacy_home(paths: PathLayout | None = None) -> PathLayout:
    """Upgrade a pre-white-label home to the current brand and return the effective layout.

    Called early by the CLI, the server and the launcher. When no home override
    is set and ``~/.maitu`` does not exist while ``~/.octop`` does, the whole
    legacy tree is moved (same-volume atomic rename first, copy otherwise). A
    failed move keeps the legacy directory in use and points ``MAITU_HOME`` at
    it, so subsequent resolvers and child processes keep using the same data.

    File-level renames (database, logs, login file, backup prefixes are handled
    by the backup store) also run for explicitly configured homes, so a custom
    ``OCTOP_HOME``/``MAITU_HOME`` install upgrades without moving anything.
    """
    layout = paths if paths is not None else PathLayout.from_env()

    if not _home_env_configured():
        legacy_root = Path.home() / LEGACY_HOME_DIRNAME
        root = layout.root
        if not root.exists() and legacy_root.is_dir() and legacy_root.resolve() != root.resolve():
            try:
                _move_directory(legacy_root, root)
            except OSError as exc:
                logger.warning(
                    "legacy data directory migration failed (%s -> %s): %s; "
                    "continuing with the legacy directory and its data",
                    legacy_root,
                    root,
                    exc,
                )
                if root.exists():
                    shutil.rmtree(root, ignore_errors=True)
                os.environ[HOME_ENV] = str(legacy_root)
                layout = PathLayout(legacy_root)
            else:
                logger.info("migrated legacy data directory %s -> %s", legacy_root, root)

    upgrade_legacy_names(layout)
    _rename_legacy_login_file(Path.home())
    return layout
