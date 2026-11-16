"""Backup archive manifest."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from octop.infra.utils.legacy_names import LEGACY_DB_ARCHIVE_PATH
from octop.infra.utils.paths import BRAND_ID

MANIFEST_VERSION = 1
_NEW_DB_ARCHIVE_PATH = f"db/{BRAND_ID}.db"


def _boolean_field(data: dict[str, Any], key: str, default: bool) -> bool:
    """Parse a JSON boolean control flag without truthiness coercion."""
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(key)
    return value


@dataclass
class AgentBackupEntry:
    agent_id: str
    name: str
    workspace_included: bool = True


@dataclass
class BackupManifest:
    manifest_version: int
    octop_version: str
    schema_version: int
    created_at: str
    home: str
    db_file: str = _NEW_DB_ARCHIVE_PATH
    database_driver: str = "sqlite"
    database_dump_format: str = "sqlite_file"
    agents: list[AgentBackupEntry] = field(default_factory=list)
    includes_config: bool = True
    includes_env: bool = False
    includes_skill_packages: bool = True
    includes_plugins: bool = False
    includes_knowledge: bool = False
    includes_chats: bool = True

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "octop_version": self.octop_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "home": self.home,
            "db_file": self.db_file,
            "database_driver": self.database_driver,
            "database_dump_format": self.database_dump_format,
            "includes_config": self.includes_config,
            "includes_env": self.includes_env,
            "includes_skill_packages": self.includes_skill_packages,
            "includes_plugins": self.includes_plugins,
            "includes_knowledge": self.includes_knowledge,
            "includes_chats": self.includes_chats,
            "agents": [asdict(a) for a in self.agents],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupManifest:
        try:
            manifest_version = int(data.get("manifest_version", 0))
            schema_version = int(data.get("schema_version", 0))
        except OverflowError as exc:
            raise ValueError("manifest_version/schema_version") from exc
        agents_raw = data.get("agents", [])
        if not isinstance(agents_raw, list):
            raise ValueError("agents")
        if any(not isinstance(row, dict) for row in agents_raw):
            raise ValueError("agents[]")
        agents = [
            AgentBackupEntry(
                agent_id=str(row.get("agent_id", "")),
                name=str(row.get("name", "")),
                workspace_included=_boolean_field(row, "workspace_included", True),
            )
            for row in agents_raw
        ]
        return cls(
            manifest_version=manifest_version,
            octop_version=str(data.get("octop_version", "")),
            schema_version=schema_version,
            created_at=str(data.get("created_at", "")),
            home=str(data.get("home", "")),
            # LEGACY: archives written before the rename packed the old DB path;
            # archives that predate the field entirely used the same path.
            db_file=str(data.get("db_file", LEGACY_DB_ARCHIVE_PATH)),
            database_driver=str(data.get("database_driver", "sqlite")),
            database_dump_format=str(data.get("database_dump_format", "sqlite_file")),
            agents=agents,
            includes_config=_boolean_field(data, "includes_config", True),
            includes_env=_boolean_field(data, "includes_env", False),
            includes_skill_packages=_boolean_field(data, "includes_skill_packages", True),
            # Older archives never packed ~/.octop/plugins; missing key means omit on restore.
            includes_plugins=_boolean_field(data, "includes_plugins", False),
            # Older archives never packed ~/.octop/knowledge.
            includes_knowledge=_boolean_field(data, "includes_knowledge", False),
            # Legacy archives dumped the full database; missing key means chats are present.
            includes_chats=_boolean_field(data, "includes_chats", True),
        )

    @classmethod
    def load_text(cls, text: str) -> BackupManifest:
        data = json.loads(text)
        if not isinstance(data, dict):
            msg = "manifest must be a JSON object"
            raise ValueError(msg)
        return cls.from_dict(data)
