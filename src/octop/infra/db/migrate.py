"""Apply numbered SQL migrations.

Each file is ``NNN_description.sql`` (SQLite) or ``NNN_description.pg.sql``
(PostgreSQL). Version is stored in ``_schema_version``.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from octop.infra.agents.kinds import KIND_FEATURE
from octop.infra.agents.profile import dump_id_list, parse_id_list_json
from octop.infra.db.pool import DatabasePool
from octop.infra.utils.ulid import new_short_id, new_ulid

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SQL_STMT_RE = re.compile(r";\s*\n")
_DOLLAR_QUOTE_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def _pg_statement_terminator(sql: str, start: int) -> re.Match[str] | None:
    """The next statement terminator at or after *start*, or ``None``.

    Semicolons inside a ``--``/``/* */`` comment, a ``'...'`` literal (``''``
    escapes included) or a ``$tag$ ... $tag$`` body do not end a statement.
    ``_SQL_STMT_RE`` alone cannot tell those apart, so it is applied only
    outside them -- otherwise a ``DO $$ ... $$`` block would be torn into
    fragments psycopg cannot parse. Comments must be recognised too: an
    apostrophe in prose (``SQLite's``) would otherwise open a string that never
    closes and swallow the rest of the file.
    """
    i = start
    n = len(sql)
    while i < n:
        char = sql[i]
        if char == "-" and sql.startswith("--", i):
            newline = sql.find("\n", i)
            if newline < 0:
                break
            i = newline + 1
            continue
        if char == "/" and sql.startswith("/*", i):
            close = sql.find("*/", i + 2)
            if close < 0:
                break
            i = close + 2
            continue
        if char == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        if char == "$":
            tag = _DOLLAR_QUOTE_RE.match(sql, i)
            if tag is not None:
                close = sql.find(tag.group(0), tag.end())
                if close < 0:
                    break
                i = close + len(tag.group(0))
                continue
        elif char == ";":
            terminator = _SQL_STMT_RE.match(sql, i)
            if terminator is not None:
                return terminator
        i += 1
    return None


def _split_pg_sql(sql: str) -> list[str]:
    parts: list[str] = []
    start = 0
    while True:
        terminator = _pg_statement_terminator(sql, start)
        if terminator is None:
            parts.append(sql[start:])
            break
        parts.append(sql[start : terminator.start()])
        start = terminator.end()
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Drop leading full-line comments so header+DDL blocks are kept.
        lines = part.splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)
        cleaned = "\n".join(lines).strip()
        if cleaned:
            out.append(cleaned)
    return out


def _discover(dialect: str = "sqlite") -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    if not _MIGRATIONS_DIR.exists():
        return out
    for entry in sorted(_MIGRATIONS_DIR.iterdir()):
        name = entry.name
        if dialect == "postgresql":
            m = re.match(r"^(\d{3})_.*\.pg\.sql$", name)
        else:
            if name.endswith(".pg.sql"):
                continue
            m = re.match(r"^(\d{3})_.*\.sql$", name)
        if m:
            out.append((int(m.group(1)), entry))
    seen: dict[int, Path] = {}
    for version, path in out:
        prior = seen.get(version)
        if prior is not None:
            raise RuntimeError(
                f"Duplicate migration version {version:03d}: {prior.name} and {path.name}. "
                "Fold unreleased schema into a single NNN_*.sql / NNN_*.pg.sql pair."
            )
        seen[version] = path
    return out


def _current_version(db: DatabasePool) -> int:
    with db.connect() as conn:
        try:
            row = conn.execute("SELECT version FROM _schema_version").fetchone()
            if row is None:
                return 0
            version = row["version"] if isinstance(row, Mapping) else row[0]
            return int(version)
        except Exception:
            return 0


def _table_columns(db: DatabasePool, table: str) -> set[str]:
    with db.connect() as conn:
        if db.dialect == "postgresql":
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (table,),
            ).fetchall()
            names: set[str] = set()
            for row in rows:
                if isinstance(row, Mapping):
                    names.add(str(row["column_name"]))
                else:
                    names.add(str(row[0]))
            return names
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _table_exists(db: DatabasePool, table: str) -> bool:
    with db.connect() as conn:
        if db.dialect == "postgresql":
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (table,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
    return row is not None


def _ensure_column(db: DatabasePool, table: str, column: str, definition: str) -> None:
    """Add a missing column on databases created by older Octop builds."""
    if column in _table_columns(db, table):
        return
    with db.connect() as conn:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


_AGENT_PROFILE_COLUMNS = (
    "color",
    "icon_name",
    "icon_url",
    "skill_package_ids",
    "published_expert_id",
    "welcome_message",
    "knowledge_base_ids",
    "mcp_servers",
)


def _drop_column(db: DatabasePool, table: str, column: str) -> None:
    if column not in _table_columns(db, table):
        return
    with db.connect() as conn:
        if db.dialect == "postgresql":
            conn.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
        else:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _ensure_agent_profile_columns(db: DatabasePool) -> None:
    if not _table_exists(db, "agents"):
        return
    for column in _AGENT_PROFILE_COLUMNS:
        _ensure_column(db, "agents", column, "TEXT")
    _collapse_legacy_agent_welcome_columns(db)


def _ensure_cron_jobs_schema(db: DatabasePool) -> None:
    if not _table_exists(db, "cron_jobs"):
        return
    _ensure_column(db, "cron_jobs", "name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(
        db,
        "cron_jobs",
        "task_type",
        "TEXT NOT NULL DEFAULT 'agent' CHECK (task_type IN ('text', 'agent'))",
    )
    _ensure_column(
        db,
        "cron_jobs",
        "mcp_servers",
        "TEXT NOT NULL DEFAULT '[]'",
    )


def _collapse_legacy_agent_welcome_columns(db: DatabasePool) -> None:
    """Fold welcome_message_zh/en into a single instance-owned welcome_message."""
    if not _table_exists(db, "agents"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn:
            conn.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS welcome_message TEXT")
    else:
        _ensure_column(db, "agents", "welcome_message", "TEXT")
    cols = _table_columns(db, "agents")
    has_zh = "welcome_message_zh" in cols
    has_en = "welcome_message_en" in cols
    if not has_zh and not has_en:
        return
    select_cols = ["agent_id", "welcome_message"]
    if has_zh:
        select_cols.append("welcome_message_zh")
    if has_en:
        select_cols.append("welcome_message_en")
    with db.transaction() as conn:
        rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM agents").fetchall()
        for row in rows:
            current = str(row["welcome_message"] or "").strip()
            if current:
                continue
            zh = str(row["welcome_message_zh"] or "").strip() if has_zh else ""
            en = str(row["welcome_message_en"] or "").strip() if has_en else ""
            picked = zh or en
            if not picked:
                continue
            conn.execute(
                "UPDATE agents SET welcome_message = ? WHERE agent_id = ?",
                (picked, row["agent_id"]),
            )
    _drop_column(db, "agents", "welcome_message_zh")
    _drop_column(db, "agents", "welcome_message_en")


def _backfill_agent_profile_from_config(db: DatabasePool) -> None:
    """Copy display metadata out of ``config_json`` into first-class columns."""
    if not _table_exists(db, "agents"):
        return
    cols = _table_columns(db, "agents")
    if "icon_name" not in cols:
        return
    from octop.infra.agents.profile import (  # noqa: PLC0415
        PROFILE_CONFIG_KEYS,
        extract_profile_from_config,
        parse_config_json,
        strip_profile_config,
    )

    profile_columns = (
        "color",
        "icon_name",
        "icon_url",
        "skill_package_ids",
        "published_expert_id",
        "welcome_message",
        "knowledge_base_ids",
        "mcp_servers",
    )
    present = [column for column in profile_columns if column in cols]
    select_cols = ["agent_id", "template_name", *present, "config_json"]

    with db.transaction() as conn:
        rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM agents").fetchall()
        for row in rows:
            cfg = parse_config_json(row["config_json"])
            if not cfg:
                continue
            profile = extract_profile_from_config(cfg)
            needs_strip = any(key in cfg for key in PROFILE_CONFIG_KEYS)
            updates: dict[str, object] = {}
            for column in present:
                if column not in profile:
                    continue
                current = row[column]
                if current is None or not str(current).strip():
                    updates[column] = profile[column]
            template = row["template_name"]
            if (template is None or not str(template).strip()) and profile.get("template_name"):
                updates["template_name"] = profile["template_name"]
            if needs_strip:
                updates["config_json"] = json.dumps(
                    strip_profile_config(cfg),
                    ensure_ascii=False,
                )
            if not updates:
                continue
            assignments = ", ".join(f"{column} = ?" for column in updates)
            params: list[object] = [*updates.values(), row["agent_id"]]
            conn.execute(
                f"UPDATE agents SET {assignments} WHERE agent_id = ?",
                params,
            )


def _integer_pk_sql(db: DatabasePool) -> str:
    if db.dialect == "postgresql":
        return "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"


def _skill_packages_identity_ready(db: DatabasePool) -> bool:
    return _table_exists(db, "skill_packages") and "skill_package_id" in _table_columns(
        db, "skill_packages"
    )


def _create_skill_packages_identity_table(db: DatabasePool) -> None:
    pk = _integer_pk_sql(db)
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS skill_packages (
              id {pk},
              skill_package_id TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              skill_count INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              icon_url TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_packages_name ON skill_packages(name)"
        )


def _rebuild_skill_packages_identity_schema(db: DatabasePool) -> None:
    """Give skill_packages an integer PK plus public ``skill_package_id``."""
    if _skill_packages_identity_ready(db):
        return
    if not _table_exists(db, "skill_packages"):
        _create_skill_packages_identity_table(db)
        return
    if "icon_name" not in _table_columns(db, "skill_packages"):
        _ensure_column(db, "skill_packages", "icon_name", "TEXT NOT NULL DEFAULT ''")
    if "icon_url" not in _table_columns(db, "skill_packages"):
        _ensure_column(db, "skill_packages", "icon_url", "TEXT NOT NULL DEFAULT ''")
    pk = _integer_pk_sql(db)
    with db.transaction() as conn:
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE skill_packages RENAME TO skill_packages_legacy")
        conn.execute(
            f"""
            CREATE TABLE skill_packages (
              id {pk},
              skill_package_id TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              skill_count INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              icon_url TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO skill_packages(
              skill_package_id, name, description, created_by, skill_count,
              icon_name, icon_url, created_at, updated_at
            )
            SELECT id, name, description, created_by, skill_count,
              COALESCE(icon_name, ''), COALESCE(icon_url, ''), created_at, updated_at
            FROM skill_packages_legacy
            """
        )
        conn.execute("DROP TABLE skill_packages_legacy")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_packages_name ON skill_packages(name)"
        )
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = ON")


def _ensure_skill_packages_schema(db: DatabasePool) -> None:
    """Create or rebuild skill_packages to the integer-PK identity schema."""
    _rebuild_skill_packages_identity_schema(db)


def _published_experts_identity_ready(db: DatabasePool) -> bool:
    return _table_exists(db, "published_experts") and "published_expert_id" in _table_columns(
        db, "published_experts"
    )


def _create_published_experts_identity_table(db: DatabasePool) -> None:
    pk = _integer_pk_sql(db)
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS published_experts (
              id {pk},
              published_expert_id TEXT NOT NULL UNIQUE,
              slug TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              source_agent_id TEXT,
              icon_name TEXT NOT NULL DEFAULT '',
              color TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_published_experts_slug "
            "ON published_experts(slug)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_experts_created_by "
            "ON published_experts(created_by)"
        )


def _rebuild_published_experts_identity_schema(db: DatabasePool) -> None:
    """Give published_experts an integer PK plus public ``published_expert_id``."""
    if _published_experts_identity_ready(db):
        return
    if not _table_exists(db, "published_experts"):
        _create_published_experts_identity_table(db)
        return
    pk = _integer_pk_sql(db)
    with db.transaction() as conn:
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE published_experts RENAME TO published_experts_legacy")
        conn.execute(
            f"""
            CREATE TABLE published_experts (
              id {pk},
              published_expert_id TEXT NOT NULL UNIQUE,
              slug TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL,
              source_agent_id TEXT,
              icon_name TEXT NOT NULL DEFAULT '',
              color TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO published_experts(
              published_expert_id, slug, name, description, created_by,
              source_agent_id, icon_name, color, created_at, updated_at
            )
            SELECT id, slug, name, description, created_by,
              source_agent_id, COALESCE(icon_name, ''), COALESCE(color, ''),
              created_at, updated_at
            FROM published_experts_legacy
            """
        )
        conn.execute("DROP TABLE published_experts_legacy")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_published_experts_slug "
            "ON published_experts(slug)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_experts_created_by "
            "ON published_experts(created_by)"
        )
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = ON")


def _ensure_published_experts_schema(db: DatabasePool) -> None:
    """Create or rebuild published_experts to the integer-PK identity schema."""
    _rebuild_published_experts_identity_schema(db)


def _relation_exists(db: DatabasePool, table: str, conn: Any | None = None) -> bool:
    def _run(cursor: Any) -> bool:
        if db.dialect == "postgresql":
            row = cursor.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ?",
                (table,),
            ).fetchone()
        else:
            row = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        return row is not None

    if conn is not None:
        return _run(conn)
    with db.connect() as cursor:
        return _run(cursor)


def _relation_columns(db: DatabasePool, table: str) -> set[str]:
    with db.connect() as conn:
        if db.dialect == "postgresql":
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ?",
                (table,),
            ).fetchall()
            return {str(row["column_name"] if isinstance(row, Mapping) else row[0]) for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _knowledge_identity_ready(db: DatabasePool) -> bool:
    if not _relation_exists(db, "knowledge_bases"):
        return False
    cols = _relation_columns(db, "knowledge_bases")
    doc_cols = (
        _relation_columns(db, "knowledge_documents")
        if _relation_exists(db, "knowledge_documents")
        else set()
    )
    return "knowledge_base_id" in cols and "document_id" in doc_cols and "path" in doc_cols


def _create_knowledge_identity_tables(db: DatabasePool) -> None:
    pk = _integer_pk_sql(db)
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_bases (
              id {pk},
              knowledge_base_id TEXT NOT NULL UNIQUE,
              owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              default_open INTEGER NOT NULL DEFAULT 0,
              shared INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              embedding_model TEXT NOT NULL DEFAULT '',
              embedding_dim INTEGER NOT NULL DEFAULT 0,
              doc_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(owner_user_id, name)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner ON knowledge_bases(owner_user_id)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_documents (
              id {pk},
              document_id TEXT NOT NULL UNIQUE,
              kb_id TEXT NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              filename TEXT NOT NULL,
              is_dir INTEGER NOT NULL DEFAULT 0,
              content_type TEXT NOT NULL,
              byte_size INTEGER NOT NULL,
              content_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              error_message TEXT NOT NULL DEFAULT '',
              chunk_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(kb_id, path)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_kb ON knowledge_documents(kb_id)"
        )


def _rebuild_knowledge_identity_schema(db: DatabasePool) -> None:
    """Give knowledge tables integer PKs + public string ids; add document folders."""
    if _knowledge_identity_ready(db):
        return
    if not _relation_exists(db, "knowledge_bases"):
        _create_knowledge_identity_tables(db)
        return
    pk = _integer_pk_sql(db)
    with db.transaction() as conn:
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = OFF")
        if _relation_exists(db, "knowledge_base_members", conn):
            conn.execute("DROP TABLE knowledge_base_members")
        conn.execute("ALTER TABLE knowledge_bases RENAME TO knowledge_bases_legacy")
        if _relation_exists(db, "knowledge_documents", conn):
            conn.execute("ALTER TABLE knowledge_documents RENAME TO knowledge_documents_legacy")
        else:
            conn.execute(
                """
                CREATE TABLE knowledge_documents_legacy (
                  id TEXT PRIMARY KEY,
                  kb_id TEXT NOT NULL,
                  filename TEXT NOT NULL,
                  content_type TEXT NOT NULL,
                  byte_size INTEGER NOT NULL,
                  content_hash TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'pending',
                  error_message TEXT NOT NULL DEFAULT '',
                  chunk_count INTEGER NOT NULL DEFAULT 0,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                )
                """
            )
        conn.execute(
            f"""
            CREATE TABLE knowledge_bases (
              id {pk},
              knowledge_base_id TEXT NOT NULL UNIQUE,
              owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              default_open INTEGER NOT NULL DEFAULT 0,
              shared INTEGER NOT NULL DEFAULT 0,
              icon_name TEXT NOT NULL DEFAULT '',
              embedding_model TEXT NOT NULL DEFAULT '',
              embedding_dim INTEGER NOT NULL DEFAULT 0,
              doc_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(owner_user_id, name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO knowledge_bases(
              knowledge_base_id, owner_user_id, name, description, default_open,
              shared, icon_name, embedding_model, embedding_dim, doc_count,
              created_at, updated_at
            )
            SELECT id, owner_user_id, name, description, default_open,
              COALESCE(shared, 0), COALESCE(icon_name, ''), embedding_model,
              embedding_dim, doc_count, created_at, updated_at
            FROM knowledge_bases_legacy
            """
        )
        conn.execute(
            f"""
            CREATE TABLE knowledge_documents (
              id {pk},
              document_id TEXT NOT NULL UNIQUE,
              kb_id TEXT NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              filename TEXT NOT NULL,
              is_dir INTEGER NOT NULL DEFAULT 0,
              content_type TEXT NOT NULL,
              byte_size INTEGER NOT NULL,
              content_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              error_message TEXT NOT NULL DEFAULT '',
              chunk_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(kb_id, path)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO knowledge_documents(
              document_id, kb_id, path, filename, is_dir, content_type, byte_size,
              content_hash, status, error_message, chunk_count, created_at, updated_at
            )
            SELECT id, kb_id, filename, filename, 0, content_type, byte_size,
              content_hash, status, error_message, chunk_count, created_at, updated_at
            FROM knowledge_documents_legacy
            """
        )
        conn.execute("DROP TABLE knowledge_documents_legacy")
        conn.execute("DROP TABLE knowledge_bases_legacy")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner ON knowledge_bases(owner_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_kb ON knowledge_documents(kb_id)"
        )
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = ON")


def _drop_knowledge_base_members(db: DatabasePool) -> None:
    if not _relation_exists(db, "knowledge_base_members"):
        return
    with db.connect() as conn:
        conn.execute("DROP TABLE knowledge_base_members")


def _ensure_knowledge_bases_schema(db: DatabasePool) -> None:
    """Create or rebuild knowledge tables to the integer-PK identity schema."""
    _rebuild_knowledge_identity_schema(db)
    _drop_knowledge_base_members(db)
    # Schema v10: per-knowledge-base configurable document limit.
    _ensure_column(db, "knowledge_bases", "max_documents", "INTEGER NOT NULL DEFAULT 100")


def _ensure_sso_oidc_schema(db: DatabasePool) -> None:
    """Apply OIDC SSO tables and nullable password_hash (SQLite users rebuild)."""
    if _table_exists(db, "sso_providers"):
        return
    # The rebuild below copies ``users`` column by column, so the v17 scope
    # columns must exist on the source table first — fresh databases reach this
    # helper before migration 017 has been applied.
    _ensure_org_units_schema(db)
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sso_providers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              enabled INTEGER NOT NULL DEFAULT 0,
              display_name TEXT NOT NULL DEFAULT '',
              issuer TEXT NOT NULL DEFAULT '',
              client_id TEXT NOT NULL DEFAULT '',
              client_secret_enc BLOB,
              scopes TEXT NOT NULL DEFAULT 'openid profile email',
              dashboard_origin TEXT,
              kind TEXT NOT NULL DEFAULT 'oidc',
              extra TEXT NOT NULL DEFAULT '{}',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sso_providers_kind ON sso_providers(kind);

            CREATE TABLE IF NOT EXISTS sso_login_states (
              state TEXT PRIMARY KEY,
              provider_id INTEGER NOT NULL REFERENCES sso_providers(id) ON DELETE CASCADE,
              nonce TEXT NOT NULL,
              code_verifier TEXT NOT NULL,
              redirect_after TEXT NOT NULL DEFAULT '/chat',
              login_code TEXT,
              user_id INTEGER,
              expires_at INTEGER NOT NULL,
              consumed_at INTEGER,
              created_at INTEGER NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_sso_login_states_login_code
              ON sso_login_states(login_code) WHERE login_code IS NOT NULL;

            PRAGMA foreign_keys = OFF;

            CREATE TABLE users_new (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              username            TEXT NOT NULL,
              password_hash       TEXT,
              role                TEXT NOT NULL,
              display_name        TEXT,
              disabled            INTEGER NOT NULL DEFAULT 0,
              locale              TEXT NOT NULL DEFAULT 'zh',
              created_at          INTEGER NOT NULL,
              login_failed_count  INTEGER NOT NULL DEFAULT 0,
              login_locked_until  INTEGER NOT NULL DEFAULT 0,
              preferences_json    TEXT NOT NULL DEFAULT '{}',
              email               TEXT,
              sso_provider_id     INTEGER REFERENCES sso_providers(id),
              sso_subject         TEXT,
              permissions         TEXT NOT NULL DEFAULT '[]',
              org_unit            TEXT,
              denied_permissions  TEXT
            );

            INSERT INTO users_new (
              id,
              username,
              password_hash,
              role,
              display_name,
              disabled,
              locale,
              created_at,
              login_failed_count,
              login_locked_until,
              preferences_json,
              permissions,
              org_unit,
              denied_permissions
            )
            SELECT
              id,
              username,
              password_hash,
              role,
              display_name,
              disabled,
              locale,
              created_at,
              login_failed_count,
              login_locked_until,
              preferences_json,
              '[]',
              org_unit,
              denied_permissions
            FROM users;

            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
              ON users(email) WHERE email IS NOT NULL AND email != '';
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sso
              ON users(sso_provider_id, sso_subject)
              WHERE sso_provider_id IS NOT NULL AND sso_subject IS NOT NULL;

            PRAGMA foreign_keys = ON;
            """
        )


def _ensure_sso_provider_kind_schema(db: DatabasePool) -> None:
    """Add kind/extra columns and unique-kind index on existing SSO provider tables."""
    if not _table_exists(db, "sso_providers"):
        return
    _ensure_column(db, "sso_providers", "kind", "TEXT NOT NULL DEFAULT 'oidc'")
    _ensure_column(db, "sso_providers", "extra", "TEXT NOT NULL DEFAULT '{}'")
    with db.connect() as conn:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sso_providers_kind ON sso_providers(kind)"
        )
    _ensure_user_sso_identities_schema(db)


def _ensure_user_sso_identities_schema(db: DatabasePool) -> None:
    """Create ``user_sso_identities`` and backfill from legacy ``users.sso_*`` slots."""
    if not _table_exists(db, "users") or not _table_exists(db, "sso_providers"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn, conn.transaction():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sso_identities (
                  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  provider_id BIGINT NOT NULL REFERENCES sso_providers(id) ON DELETE CASCADE,
                  subject TEXT NOT NULL,
                  created_at BIGINT NOT NULL,
                  UNIQUE(user_id, provider_id),
                  UNIQUE(provider_id, subject)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO user_sso_identities (user_id, provider_id, subject, created_at)
                SELECT id, sso_provider_id, sso_subject, created_at
                FROM users
                WHERE sso_provider_id IS NOT NULL AND sso_subject IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )
        return
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_sso_identities (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              provider_id INTEGER NOT NULL REFERENCES sso_providers(id) ON DELETE CASCADE,
              subject TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              UNIQUE(user_id, provider_id),
              UNIQUE(provider_id, subject)
            );
            INSERT OR IGNORE INTO user_sso_identities
              (user_id, provider_id, subject, created_at)
            SELECT id, sso_provider_id, sso_subject, created_at
            FROM users
            WHERE sso_provider_id IS NOT NULL AND sso_subject IS NOT NULL;
            """
        )


_DROPPED_FEATURE_TABLES = (
    "feature_step_dispatches",
    "feature_step_edits",
    "feature_step_runs",
    "feature_runs",
    "feature_cases",
    "feature_rules",
    "feature_tasks",
)
"""The deleted feature subsystem's tables (schema v16-v25), children first.

Schema v26 drops them: nothing reads them any more — ``src/octop/infra/features``
and its router are gone — and they are not a shape the rebuilt model keeps, where
a feature *is* an agent.
"""

_DROPPED_FEATURE_TABLE_VERSIONS = frozenset({16, 19, 22, 23, 24, 25})
"""Versions whose only DDL built those tables (see ``_apply_sqlite_migration``)."""

_DEFAULT_AGENT_KIND = "agent"
"""What ``agents.kind`` reads as on a row that is nobody's feature."""


def _ensure_agent_kind_column(db: DatabasePool) -> None:
    """Add ``agents.kind`` (schema v26) and mark the rows it was introduced for.

    The backfill runs in the one moment the column is added, which is what makes
    it a historical fact rather than a rule. Before v26 an app-owned agent
    (``user_id IS NULL``) could only ever be a feature's own — the feature
    subsystem was the only creation path that left the owner NULL — and its
    memory was frozen on exactly that ownership. Marking those rows keeps every
    one of them behaving as it did; re-running the UPDATE on a later boot would
    relabel an app-owned row created afterwards.
    """
    if not _table_exists(db, "agents") or "kind" in _table_columns(db, "agents"):
        return
    _ensure_column(db, "agents", "kind", f"TEXT NOT NULL DEFAULT '{_DEFAULT_AGENT_KIND}'")
    with db.connect() as conn:
        conn.execute(f"UPDATE agents SET kind = '{KIND_FEATURE}' WHERE user_id IS NULL")


def _drop_feature_tables(db: DatabasePool) -> None:
    """Drop the deleted feature subsystem's tables (schema v26).

    Idempotent and re-run on every boot, like the other v18+ ensure helpers: a
    database whose watermark skipped 26 must not keep tables nothing reads.
    Children first, so no dialect needs ``CASCADE`` to resolve the references.
    """
    for table in _DROPPED_FEATURE_TABLES:
        if not _table_exists(db, table):
            continue
        with db.connect() as conn:
            conn.execute(f"DROP TABLE {table}")


def _ensure_data_sources_schema(db: DatabasePool) -> None:
    """Create the data-source table (schema v20) when missing.

    Runs on every boot, like the v18/v19 helpers: databases whose watermark
    skipped 20 — a clamp, or a build that stamped the version without the DDL —
    still get the table the control plane writes to. The FK targets the public
    ``knowledge_base_id`` exactly like ``knowledge_documents.kb_id``, so the
    knowledge identity rebuild in ``_repair_legacy_schema`` stays coherent.
    """
    if not _table_exists(db, "knowledge_bases") or not _table_exists(db, "users"):
        return
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS data_sources (
              id                TEXT PRIMARY KEY,
              knowledge_base_id TEXT NOT NULL
                                REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
              name              TEXT NOT NULL,
              kind              TEXT NOT NULL,
              config_json       TEXT NOT NULL DEFAULT '{{}}',
              created_by        {int_type} REFERENCES users(id) ON DELETE SET NULL,
              sync_status       TEXT NOT NULL DEFAULT 'idle',
              sync_error        TEXT,
              last_synced_at    {int_type},
              created_at        {int_type} NOT NULL,
              updated_at        {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_data_sources_kb "
            "ON data_sources (knowledge_base_id, name)"
        )


_ENTERPRISE_SPACE_NAME = "企业知识库"
"""Display name seeded for the enterprise knowledge space.

The dashboard renders a localized label for the singleton, so this is what an
API consumer sees rather than the copy a reader does. Seeding never updates an
existing row, so an administrator who renames the space keeps the new name.
"""

_KNOWLEDGE_BASES_V27_DDL = """
CREATE TABLE knowledge_bases (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  knowledge_base_id TEXT NOT NULL UNIQUE,
  owner_user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  default_open      INTEGER NOT NULL DEFAULT 0,
  icon_name         TEXT NOT NULL DEFAULT '',
  embedding_model   TEXT NOT NULL DEFAULT '',
  embedding_dim     INTEGER NOT NULL DEFAULT 0,
  doc_count         INTEGER NOT NULL DEFAULT 0,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  max_documents     INTEGER NOT NULL DEFAULT 100,
  is_enterprise     INTEGER NOT NULL DEFAULT 0
)
"""
"""``knowledge_bases`` as v27 declares it: nullable owner, plus the flag."""

_KNOWLEDGE_BASES_V27_COLUMNS = (
    "id",
    "knowledge_base_id",
    "owner_user_id",
    "name",
    "description",
    "default_open",
    "icon_name",
    "embedding_model",
    "embedding_dim",
    "doc_count",
    "created_at",
    "updated_at",
    "max_documents",
)

_KNOWLEDGE_BASES_V27_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner ON knowledge_bases(owner_user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_bases_enterprise "
    "ON knowledge_bases(is_enterprise) WHERE is_enterprise = 1",
)


def _ensure_enterprise_knowledge_space(db: DatabasePool) -> None:
    """Make ``knowledge_bases`` the deployment's one enterprise space (v27).

    Idempotent and re-run on every boot, like the other v18+ ensure helpers: a
    database whose watermark skipped 27 still has to end up with a nullable
    owner, the ``is_enterprise`` flag and the single row phase 1 is about.

    The SQLite rebuild is guarded by the flag's absence, which is exactly the
    one boot that needs it — every later boot finds the column and goes
    straight to the (also guarded) row seed. Rows that predate v27 keep their
    owner and are copied across untouched, so nothing about an existing
    knowledge base's reach changes here.
    """
    if not _table_exists(db, "knowledge_bases"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn:
            conn.execute("ALTER TABLE knowledge_bases ALTER COLUMN owner_user_id DROP NOT NULL")
        _ensure_column(db, "knowledge_bases", "is_enterprise", "INTEGER NOT NULL DEFAULT 0")
    elif "is_enterprise" not in _table_columns(db, "knowledge_bases"):
        _rebuild_sqlite_table(
            db,
            "knowledge_bases",
            ddl=_KNOWLEDGE_BASES_V27_DDL,
            columns=_KNOWLEDGE_BASES_V27_COLUMNS,
            indexes=_KNOWLEDGE_BASES_V27_INDEXES,
        )
    # Unconditionally, on both dialects and both branches: a rebuild of this
    # table recreates it without its indexes, so the one that says "at most one
    # enterprise space" would otherwise be lost on the boot that rebuilt it.
    with db.connect() as conn:
        for statement in _KNOWLEDGE_BASES_V27_INDEXES:
            conn.execute(statement)
    _seed_enterprise_knowledge_space(db)


def _seed_enterprise_knowledge_space(db: DatabasePool) -> None:
    """Insert the enterprise space row and its ACL row, each when missing.

    Two independent guards, because the two can go missing independently. Every
    table carries its own history: a database whose ``resource_acl`` tables were
    rebuilt goes through the generic backfill, which describes every knowledge
    base by its legacy ``shared`` flag — and the space has none, so it lands
    there as private. Turning only the knowledge row into a guard would leave
    that database's space readable by administrators alone.

    Creating a *missing* ACL row and never overwriting a present one is what
    keeps this from fighting the product: an administrator who narrows the
    space through the sharing pipeline keeps the narrowing.

    The ACL row matters as much as the knowledge row: access is granted by a
    row and never by its absence. ``owner_user_id`` is NULL — system-owned — and
    the visibility is ``public``, which is how this codebase already spells the
    enterprise-wide audience (design §5.1). Narrowing it to folders and files is
    what the design's phase 8 adds; until then the space is one audience.
    """
    if not _table_exists(db, "knowledge_bases") or not _table_exists(db, "resource_acl"):
        return
    ts = int(time.time())
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1"
        ).fetchone()
        if existing is None:
            kb_id = new_short_id()
            conn.execute(
                "INSERT INTO knowledge_bases("
                "knowledge_base_id, owner_user_id, name, description, default_open, "
                "icon_name, embedding_model, embedding_dim, doc_count, created_at, "
                "updated_at, is_enterprise"
                ") VALUES (?, NULL, ?, '', 0, '', '', 0, 0, ?, ?, 1)",
                (kb_id, _ENTERPRISE_SPACE_NAME, ts, ts),
            )
        else:
            kb_id = str(existing["knowledge_base_id"])
        conn.execute(
            "INSERT INTO resource_acl("
            "resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at"
            ") VALUES ('knowledge_base', ?, NULL, 'public', NULL, 1, ?) "
            "ON CONFLICT (resource_type, resource_id) DO NOTHING",
            (kb_id, ts),
        )


def _merge_legacy_knowledge_bases(db: DatabasePool) -> None:
    """Fold every legacy user knowledge base into the enterprise space (v35).

    design §13: the deployment has one logical knowledge base, so a base that
    predates the space does not survive as a second one. Its documents move into
    the space, their files move with them, its data sources come along, and the
    base's own audience is written onto each document as a file-level entry
    *before* the base stops being the answer.

    That order is the whole point. The space is readable by everyone — v27 seeds
    it ``public`` — so a document that only some people could read has to carry
    the narrower rule itself; skipping this step would hand out access rather
    than migrate it, which is what design §14 forbids. A base with no ACL row is
    the same problem in its sharpest form ("no row" means administrators only),
    so its documents get an explicit system-owned private entry instead of
    silently inheriting the space.

    Bindings move too: an agent that named a legacy base names the space
    afterwards, because that is where the documents it was reading went. Leaving
    the old id behind would answer a live reference with a dead one.

    Idempotent — each step is guarded on the state it creates, so reaching 35
    twice moves nothing twice — but deliberately *not* in the every-boot repair
    list the schema helpers live in: this one deletes rows, and that belongs to
    the single boot that crosses this version rather than to every boot after it.
    """
    if not _table_exists(db, "knowledge_bases"):
        return
    with db.connect() as conn:
        space = conn.execute(
            "SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1"
        ).fetchone()
        legacy = [
            str(row["knowledge_base_id"])
            for row in conn.execute(
                "SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 0"
            ).fetchall()
        ]
    if space is None or not legacy:
        return
    space_id = str(space["knowledge_base_id"])
    ts = int(time.time())
    for kb_id in legacy:
        _carry_base_audience_onto_its_documents(db, kb_id, ts)
        _move_base_contents(db, kb_id, space_id, ts)
    _repoint_agent_knowledge_bindings(db, legacy, space_id)
    _drop_emptied_legacy_bases(db, legacy)


def _carry_base_audience_onto_its_documents(db: DatabasePool, kb_id: str, ts: int) -> None:
    """Write *kb_id*'s audience onto every document it still holds.

    A document that already wears an entry of its own keeps it: whoever wrote
    that rule meant it, and this migration is not the place to overrule them.
    """
    with db.connect() as conn:
        entry = conn.execute(
            "SELECT owner_user_id, visibility, unit_key, version FROM resource_acl "
            "WHERE resource_type = 'knowledge_base' AND resource_id = ?",
            (kb_id,),
        ).fetchone()
        grants = conn.execute(
            "SELECT grantee_type, grantee_id FROM resource_acl_grants "
            "WHERE resource_type = 'knowledge_base' AND resource_id = ?",
            (kb_id,),
        ).fetchall()
        documents = [
            str(row["document_id"])
            for row in conn.execute(
                # ``document_id`` is the public identifier the ACL rows are
                # keyed by; ``id`` is this table's integer primary key.
                "SELECT document_id FROM knowledge_documents WHERE kb_id = ?",
                (kb_id,),
            ).fetchall()
        ]
        for document_id in documents:
            present = conn.execute(
                "SELECT 1 FROM resource_acl WHERE resource_type = 'knowledge_document' "
                "AND resource_id = ?",
                (document_id,),
            ).fetchone()
            if present is not None:
                continue
            if entry is None:
                # "No row" is administrators only, and the space says otherwise:
                # the narrower rule has to be written down, not inherited.
                conn.execute(
                    "INSERT INTO resource_acl("
                    "resource_type, resource_id, owner_user_id, visibility, unit_key, "
                    "version, updated_at"
                    ") VALUES ('knowledge_document', ?, NULL, 'private', NULL, 1, ?) "
                    "ON CONFLICT (resource_type, resource_id) DO NOTHING",
                    (document_id, ts),
                )
                continue
            conn.execute(
                "INSERT INTO resource_acl("
                "resource_type, resource_id, owner_user_id, visibility, unit_key, "
                "version, updated_at"
                ") VALUES ('knowledge_document', ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (resource_type, resource_id) DO NOTHING",
                (
                    document_id,
                    entry["owner_user_id"],
                    entry["visibility"],
                    entry["unit_key"],
                    int(entry["version"]),
                    ts,
                ),
            )
            for grant in grants:
                conn.execute(
                    "INSERT INTO resource_acl_grants("
                    "resource_type, resource_id, grantee_type, grantee_id"
                    ") VALUES ('knowledge_document', ?, ?, ?) ON CONFLICT DO NOTHING",
                    (document_id, grant["grantee_type"], grant["grantee_id"]),
                )


def _move_base_contents(db: DatabasePool, kb_id: str, space_id: str, ts: int) -> None:
    """Move *kb_id*'s documents, their files, and its data sources into the space.

    The stored path is derived from the base a document belongs to, so the bytes
    have to follow the row: a row that moved without its file would be a document
    the space lists and cannot open.
    """
    from octop.infra.knowledge.files import (
        delete_knowledge_base_files,
        document_path,
        documents_dir,
    )
    from octop.infra.knowledge.index import KnowledgeIndex

    with db.connect() as conn:
        documents = conn.execute(
            # ``document_id``, not ``id``: the stored file is named after the
            # public identifier (:func:`document_path`).
            "SELECT document_id, filename FROM knowledge_documents WHERE kb_id = ?",
            (kb_id,),
        ).fetchall()
        conn.execute(
            "UPDATE knowledge_documents SET kb_id = ?, updated_at = ? WHERE kb_id = ?",
            (space_id, ts, kb_id),
        )
        conn.execute(
            "UPDATE data_sources SET knowledge_base_id = ? WHERE knowledge_base_id = ?",
            (space_id, kb_id),
        )
    # The chunks live in a per-base sidecar file (``<base>/index.sqlite``), so
    # they have to move too: a document whose row moved but whose chunks did not
    # is a document the space lists and cannot find.
    source_index = KnowledgeIndex(kb_id)
    target_index = KnowledgeIndex(space_id)
    for row in documents:
        document_id = str(row["document_id"])
        chunks = source_index.doc_chunks(document_id)
        if chunks:
            target_index.replace_doc_chunks(
                document_id,
                [text for text, _vector, _meta in chunks],
                [vector for _text, vector, _meta in chunks],
                metadata=[meta for _text, _vector, meta in chunks],
            )
    documents_dir(space_id).mkdir(parents=True, exist_ok=True)
    for row in documents:
        document_id = str(row["document_id"])
        filename = str(row["filename"])
        source = document_path(kb_id, document_id, filename)
        if not source.is_file():
            # Folders keep no bytes, and a file that is already gone was moved
            # by an earlier run of this same step.
            continue
        target = document_path(space_id, document_id, filename)
        if target.exists():
            source.unlink()
            continue
        shutil.move(str(source), str(target))
    delete_knowledge_base_files(kb_id)


def _repoint_agent_knowledge_bindings(db: DatabasePool, legacy: list[str], space_id: str) -> None:
    """Every agent that named a legacy base names the space afterwards."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, knowledge_base_ids FROM agents WHERE knowledge_base_ids IS NOT NULL"
        ).fetchall()
        for row in rows:
            repointed = repointed_knowledge_ids(str(row["knowledge_base_ids"]), legacy, space_id)
            if repointed is None:
                continue
            conn.execute(
                "UPDATE agents SET knowledge_base_ids = ? WHERE id = ?",
                (repointed, row["id"]),
            )


def repointed_knowledge_ids(value: str, legacy: list[str], space_id: str) -> str | None:
    """*value* with legacy base ids replaced by *space_id*, or ``None``.

    ``None`` means "leave it alone": either the stored value is not the JSON
    array this column holds everywhere else, or it names none of the legacy
    bases. Both are complete answers — a value that names no base has nothing to
    repoint — so neither is an error to report. Reading and writing go through
    the same two helpers the composer uses, so a repointed list is byte-for-byte
    what :meth:`persist_knowledge_base_ids` would have written.
    """
    ids = parse_id_list_json(value)
    if ids is None:
        return None
    mapped = [space_id if item in legacy else item for item in ids]
    deduped = list(dict.fromkeys(mapped))
    if deduped == ids:
        return None
    return dump_id_list(deduped)


def _drop_emptied_legacy_bases(db: DatabasePool, legacy: list[str]) -> None:
    """Delete the bases whose contents have moved, and recount the space.

    design §1 is one logical knowledge base per enterprise, and an emptied base
    is not a second library — it is a name, a description and an ACL row for
    documents that now live in the space. Its audience moved with them
    (:func:`_carry_base_audience_onto_its_documents`), so the row is dropped and
    the space's ``doc_count`` is recomputed from what it actually holds.
    """
    with db.connect() as conn:
        for kb_id in legacy:
            conn.execute(
                "DELETE FROM resource_acl_grants "
                "WHERE resource_type = 'knowledge_base' AND resource_id = ?",
                (kb_id,),
            )
            conn.execute(
                "DELETE FROM resource_acl "
                "WHERE resource_type = 'knowledge_base' AND resource_id = ?",
                (kb_id,),
            )
            conn.execute(
                "DELETE FROM knowledge_bases WHERE knowledge_base_id = ? AND is_enterprise = 0",
                (kb_id,),
            )
        conn.execute(
            "UPDATE knowledge_bases SET doc_count = ("
            "SELECT COUNT(*) FROM knowledge_documents d "
            "WHERE d.kb_id = knowledge_bases.knowledge_base_id AND d.is_dir = 0"
            ") WHERE is_enterprise = 1"
        )


def _ensure_data_sources_connection_schema(db: DatabasePool) -> None:
    """Add the folder-source columns to ``data_sources`` (schema v27).

    ``config_json`` keeps carrying the per-kind payload it always did; these
    columns are the ones a query filters on (a connection status, a scan
    interval) or that must never travel through JSON — the connecting user's
    secret, which lives encrypted in ``credentials_enc`` and is only ever
    reported as present.
    """
    if not _table_exists(db, "data_sources"):
        return
    blob_type = "BYTEA" if db.dialect == "postgresql" else "BLOB"
    columns = (
        ("server", "TEXT NOT NULL DEFAULT ''"),
        ("share", "TEXT NOT NULL DEFAULT ''"),
        ("root_path", "TEXT NOT NULL DEFAULT ''"),
        ("username", "TEXT NOT NULL DEFAULT ''"),
        ("credentials_enc", blob_type),
        ("read_only", "INTEGER NOT NULL DEFAULT 1"),
        ("include_globs", "TEXT NOT NULL DEFAULT ''"),
        ("exclude_globs", "TEXT NOT NULL DEFAULT ''"),
        ("scan_interval_seconds", "INTEGER NOT NULL DEFAULT 0"),
        ("connection_status", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("connection_error", "TEXT"),
        ("last_scan_at", "INTEGER"),
        ("last_scan_ok_at", "INTEGER"),
    )
    for column, definition in columns:
        _ensure_column(db, "data_sources", column, definition)


_KNOWLEDGE_FILE_INDEX_COLUMNS = (
    ("data_source_id", "TEXT REFERENCES data_sources(id) ON DELETE CASCADE"),
    ("source_path", "TEXT NOT NULL DEFAULT ''"),
    ("source_size", "INTEGER NOT NULL DEFAULT 0"),
    ("source_modified_at", "INTEGER"),
    ("obs_size", "INTEGER"),
    ("obs_modified_at", "INTEGER"),
    ("obs_at", "INTEGER"),
    ("delete_pending_since", "INTEGER"),
)
"""What a scan adds to the file index (schema v28).

``data_source_id`` cascades so a source's index dies with it; ``obs_*`` is the
previous scan's observation, which is what makes the debounce in design §8.3
possible — a file still being copied differs between two consecutive scans.
"""


def _ensure_knowledge_file_index_schema(db: DatabasePool) -> None:
    """Give ``knowledge_documents`` the columns a scan needs (schema v28)."""
    if not _table_exists(db, "knowledge_documents") or not _table_exists(db, "data_sources"):
        # The foreign key below targets ``data_sources``, and a database old or
        # partial enough to be missing it has no external source to index.
        return
    for column, definition in _KNOWLEDGE_FILE_INDEX_COLUMNS:
        _ensure_column(db, "knowledge_documents", column, definition)
    with db.connect() as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_source "
            "ON knowledge_documents (data_source_id, source_path)"
        )


def _ensure_knowledge_sync_runs_schema(db: DatabasePool) -> None:
    """Create the scan's task table (schema v28).

    One row per scan, with its counts and its stop reason: design §8.4 wants
    every extraction to be a traceable task, and the per-file outcome stays on
    the document row so a run is a summary rather than the only record.
    """
    if not _table_exists(db, "data_sources"):
        return
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_sync_runs (
              id             TEXT PRIMARY KEY,
              data_source_id TEXT NOT NULL
                             REFERENCES data_sources(id) ON DELETE CASCADE,
              trigger        TEXT NOT NULL,
              status         TEXT NOT NULL,
              started_at     {int_type} NOT NULL,
              finished_at    {int_type},
              scanned        INTEGER NOT NULL DEFAULT 0,
              added          INTEGER NOT NULL DEFAULT 0,
              updated        INTEGER NOT NULL DEFAULT 0,
              removed        INTEGER NOT NULL DEFAULT 0,
              deferred       INTEGER NOT NULL DEFAULT 0,
              failed         INTEGER NOT NULL DEFAULT 0,
              error          TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_sync_runs_source "
            "ON knowledge_sync_runs (data_source_id, started_at)"
        )


def _ensure_knowledge_derived_schema(db: DatabasePool) -> None:
    """Give ``knowledge_documents`` the parsed structure it derives (schema v29).

    Emptiness is meaningful: ``''`` says "this file has not been parsed since
    the structure started being stored", which is what a re-derivation keys off.

    The column holds the *structure* (design §6.2) and not the text: the chunk
    table already holds the text, and what the text cannot express — which
    sheet a table came from, which heading a paragraph sits under — is the
    reason to store it at all.
    """
    if not _table_exists(db, "knowledge_documents"):
        return
    _ensure_column(db, "knowledge_documents", "derived_json", "TEXT NOT NULL DEFAULT ''")


def _ensure_extract_templates_schema(db: DatabasePool) -> None:
    """Create extraction templates, their versions, and their bindings (v33).

    Design §7: a template is an enterprise resource, editing one writes a new
    version rather than overwriting it, and a binding names where it applies
    (the whole source, a folder, or one file) through its path alone.
    """
    if not _table_exists(db, "users") or not _table_exists(db, "data_sources"):
        # The three tables reference both; a database old enough to be missing
        # them has no source to bind a template to.
        return
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_extract_templates (
              id              TEXT PRIMARY KEY,
              name            TEXT NOT NULL,
              description     TEXT NOT NULL DEFAULT '',
              status          TEXT NOT NULL DEFAULT 'active',
              current_version INTEGER NOT NULL DEFAULT 0,
              created_by      {int_type} REFERENCES users(id) ON DELETE SET NULL,
              created_at      {int_type} NOT NULL,
              updated_at      {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_extract_template_versions (
              id            TEXT PRIMARY KEY,
              template_id   TEXT NOT NULL
                            REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
              version       INTEGER NOT NULL,
              fields_json   TEXT NOT NULL DEFAULT '[]',
              instruction   TEXT NOT NULL DEFAULT '',
              applies_to    TEXT NOT NULL DEFAULT '',
              note          TEXT NOT NULL DEFAULT '',
              created_by    {int_type} REFERENCES users(id) ON DELETE SET NULL,
              created_at    {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_template_versions_unique "
            "ON knowledge_extract_template_versions (template_id, version)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_extract_bindings (
              id             TEXT PRIMARY KEY,
              template_id    TEXT NOT NULL
                             REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
              data_source_id TEXT NOT NULL
                             REFERENCES data_sources(id) ON DELETE CASCADE,
              path           TEXT NOT NULL DEFAULT '',
              extension      TEXT NOT NULL DEFAULT '',
              mime_type      TEXT NOT NULL DEFAULT '',
              name_pattern   TEXT NOT NULL DEFAULT '',
              match_regex    TEXT NOT NULL DEFAULT '',
              created_by     {int_type} REFERENCES users(id) ON DELETE SET NULL,
              created_at     {int_type} NOT NULL,
              updated_at     {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extract_bindings_source "
            "ON knowledge_extract_bindings (data_source_id, path)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extract_bindings_template "
            "ON knowledge_extract_bindings (template_id)"
        )


def _ensure_extract_results_schema(db: DatabasePool) -> None:
    """Create the table that records what a template produced (schema v34).

    One row per document and template, replaced on a re-run. The unique index is
    the point: a template applied twice to one file is not two answers, and the
    design's "新结果成功后原子替换" (§8.3) needs one row to replace.
    """
    if not _table_exists(db, "knowledge_extract_templates") or not _table_exists(
        db, "knowledge_documents"
    ):
        # Both are foreign keys of this table.
        return
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_extract_results (
              id               TEXT PRIMARY KEY,
              document_id      TEXT NOT NULL
                               REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
              template_id      TEXT NOT NULL
                               REFERENCES knowledge_extract_templates(id) ON DELETE CASCADE,
              template_version {int_type} NOT NULL,
              status           TEXT NOT NULL DEFAULT 'pending',
              fields_json      TEXT NOT NULL DEFAULT '{{}}',
              error            TEXT,
              model            TEXT NOT NULL DEFAULT '',
              parser_version   TEXT NOT NULL DEFAULT '',
              content_hash     TEXT NOT NULL DEFAULT '',
              created_at       {int_type} NOT NULL,
              updated_at       {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_results_document_template "
            "ON knowledge_extract_results (document_id, template_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extract_results_template "
            "ON knowledge_extract_results (template_id, status)"
        )


def _ensure_org_units_schema(db: DatabasePool) -> None:
    """Create org units + unit grants and the user scope columns (schema v17)."""
    if _table_exists(db, "users"):
        _ensure_column(db, "users", "org_unit", "TEXT")
        _ensure_column(
            db,
            "users",
            "denied_permissions",
            "JSONB" if db.dialect == "postgresql" else "TEXT",
        )
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS org_units (
              key        TEXT PRIMARY KEY,
              label_zh   TEXT NOT NULL,
              label_en   TEXT NOT NULL,
              parent_key TEXT REFERENCES org_units(key) ON DELETE SET NULL,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS org_unit_permissions (
              unit_key       TEXT NOT NULL,
              permission_key TEXT NOT NULL,
              PRIMARY KEY (unit_key, permission_key)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_org_units_parent ON org_units (parent_key)")


# resource_type, table, public id column, owner column, legacy flag column.
#
# The flag column only exists on pre-v21 databases — schema v21 dropped all
# three — and the guard below skips any table whose flag is already gone, so
# this backfill stays the v18 mechanism and nothing more.
_RESOURCE_ACL_BACKFILL_SOURCES = (
    ("agent", "agents", "agent_id", "user_id", "is_shared"),
    ("connector", "connectors", "instance_id", "user_id", "shared"),
    ("knowledge_base", "knowledge_bases", "knowledge_base_id", "owner_user_id", "shared"),
)


def _ensure_resource_acl_schema(db: DatabasePool) -> None:
    """Create the unified ACL tables (schema v18) and mirror the legacy flags.

    The backfill is ``ON CONFLICT DO NOTHING``: it never clobbers a later ACL
    edit, and it runs on every boot so databases whose watermark skipped 018
    still get their rows.
    """
    if not _table_exists(db, "users"):
        return
    # ``resource_acl.unit_key`` references ``org_units``: create it first, as
    # PostgreSQL needs the FK target to exist at DDL time.
    _ensure_org_units_schema(db)
    int_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS resource_acl (
              resource_type TEXT NOT NULL,
              resource_id   TEXT NOT NULL,
              owner_user_id {int_type} REFERENCES users(id) ON DELETE SET NULL,
              visibility    TEXT NOT NULL,
              unit_key      TEXT REFERENCES org_units(key) ON DELETE SET NULL,
              version       INTEGER NOT NULL DEFAULT 1,
              updated_at    {int_type} NOT NULL,
              PRIMARY KEY (resource_type, resource_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_acl_grants (
              resource_type TEXT NOT NULL,
              resource_id   TEXT NOT NULL,
              grantee_type  TEXT NOT NULL,
              grantee_id    TEXT NOT NULL,
              PRIMARY KEY (resource_type, resource_id, grantee_type, grantee_id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS resource_acl_changes (
              id            TEXT PRIMARY KEY,
              resource_type TEXT NOT NULL,
              resource_id   TEXT NOT NULL,
              actor_user_id {int_type} NOT NULL,
              from_version  INTEGER NOT NULL,
              to_version    INTEGER NOT NULL,
              before_json   TEXT NOT NULL,
              after_json    TEXT NOT NULL,
              impact_scope  TEXT NOT NULL,
              status        TEXT NOT NULL,
              reason        TEXT,
              created_at    {int_type} NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_acl_owner ON resource_acl (owner_user_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_acl_unit ON resource_acl (unit_key)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_acl_grants_grantee "
            "ON resource_acl_grants (grantee_type, grantee_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_acl_changes_resource "
            "ON resource_acl_changes (resource_type, resource_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_acl_changes_status "
            "ON resource_acl_changes (status, created_at)"
        )
    ts = int(time.time())
    for (
        resource_type,
        table,
        id_column,
        owner_column,
        flag_column,
    ) in _RESOURCE_ACL_BACKFILL_SOURCES:
        if not _table_exists(db, table):
            continue
        columns = _table_columns(db, table)
        if not {id_column, owner_column, flag_column}.issubset(columns):
            continue
        with db.connect() as conn:
            # Every legacy row gets an ACL row, ownerless ones included: a NULL
            # ``owner_user_id`` means "system-owned" and ``can_access`` rule 2
            # can never match it, so the row stays admin-only unless the legacy
            # flag publishes it.
            #
            # ``WHERE 1 = 1`` is load-bearing: SQLite refuses to parse an upsert
            # clause directly after ``INSERT ... SELECT`` without a WHERE (the
            # ON could belong to a join), and the error only shows up at runtime.
            conn.execute(
                f"""
                INSERT INTO resource_acl(
                  resource_type, resource_id, owner_user_id, visibility, unit_key,
                  version, updated_at
                )
                SELECT ?, {id_column}, {owner_column},
                  CASE WHEN {flag_column} = 1 THEN 'public' ELSE 'private' END,
                  NULL, 1, ?
                FROM {table}
                WHERE 1 = 1
                ON CONFLICT (resource_type, resource_id) DO NOTHING
                """,
                (resource_type, ts),
            )


# Schema v21 retires the legacy global share booleans. Each entry is
# (table, legacy column, SQLite rebuild DDL, rebuilt column list, rebuilt
# indexes).
#
# SQLite cannot ``DROP COLUMN`` here: both flag indexes are partial indexes on
# the flag itself. The tables are rebuilt instead, and the column lists are
# written out in full — ``knowledge_bases.max_documents`` (v10) and the agent
# profile columns (v7) were appended by ALTER, so a list copied from an older
# build silently drops them, which is exactly the failure this shape prevents.
_LEGACY_SHARE_COLUMNS = (
    (
        "agents",
        "is_shared",
        """
        CREATE TABLE agents (
          id                  INTEGER PRIMARY KEY AUTOINCREMENT,
          agent_id            TEXT NOT NULL UNIQUE,
          user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
          name                TEXT NOT NULL,
          description         TEXT,
          persona_mbti        TEXT,
          default_model       TEXT,
          system_prompt       TEXT,
          enabled             INTEGER NOT NULL DEFAULT 1,
          config_json         TEXT,
          last_state          TEXT,
          last_error          TEXT,
          icon                TEXT,
          template_name       TEXT,
          created_at          INTEGER NOT NULL,
          updated_at          INTEGER NOT NULL,
          color               TEXT,
          icon_name           TEXT,
          icon_url            TEXT,
          skill_package_ids   TEXT,
          published_expert_id TEXT,
          welcome_message     TEXT,
          knowledge_base_ids  TEXT,
          mcp_servers         TEXT
        )
        """,
        (
            "id",
            "agent_id",
            "user_id",
            "name",
            "description",
            "persona_mbti",
            "default_model",
            "system_prompt",
            "enabled",
            "config_json",
            "last_state",
            "last_error",
            "icon",
            "template_name",
            "created_at",
            "updated_at",
            "color",
            "icon_name",
            "icon_url",
            "skill_package_ids",
            "published_expert_id",
            "welcome_message",
            "knowledge_base_ids",
            "mcp_servers",
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_user_name "
            "ON agents(user_id, name) WHERE user_id IS NOT NULL",
        ),
    ),
    (
        "connectors",
        "shared",
        """
        CREATE TABLE connectors (
          id                    INTEGER PRIMARY KEY AUTOINCREMENT,
          instance_id           TEXT NOT NULL UNIQUE,
          user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind                  TEXT NOT NULL,
          display_name          TEXT NOT NULL,
          status                TEXT NOT NULL DEFAULT 'active',
          mcp_server_name       TEXT NOT NULL UNIQUE,
          credential_blob       BLOB,
          credential_expires_at INTEGER,
          credential_rotated_at INTEGER,
          config_json           TEXT,
          created_at            INTEGER NOT NULL,
          updated_at            INTEGER NOT NULL
        )
        """,
        (
            "id",
            "instance_id",
            "user_id",
            "kind",
            "display_name",
            "status",
            "mcp_server_name",
            "credential_blob",
            "credential_expires_at",
            "credential_rotated_at",
            "config_json",
            "created_at",
            "updated_at",
        ),
        (
            "CREATE INDEX IF NOT EXISTS idx_connectors_user ON connectors(user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_user_display_name "
            "ON connectors(user_id, display_name) WHERE kind <> 'custom-mcp'",
        ),
    ),
    (
        "knowledge_bases",
        "shared",
        """
        CREATE TABLE knowledge_bases (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          knowledge_base_id TEXT NOT NULL UNIQUE,
          owner_user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
          name              TEXT NOT NULL,
          description       TEXT NOT NULL DEFAULT '',
          default_open      INTEGER NOT NULL DEFAULT 0,
          icon_name         TEXT NOT NULL DEFAULT '',
          embedding_model   TEXT NOT NULL DEFAULT '',
          embedding_dim     INTEGER NOT NULL DEFAULT 0,
          doc_count         INTEGER NOT NULL DEFAULT 0,
          created_at        INTEGER NOT NULL,
          updated_at        INTEGER NOT NULL,
          max_documents     INTEGER NOT NULL DEFAULT 100,
          is_enterprise     INTEGER NOT NULL DEFAULT 0,
          UNIQUE(owner_user_id, name)
        )
        """,
        (
            "id",
            "knowledge_base_id",
            "owner_user_id",
            "name",
            "description",
            "default_open",
            "icon_name",
            "embedding_model",
            "embedding_dim",
            "doc_count",
            "created_at",
            "updated_at",
            "max_documents",
            "is_enterprise",
        ),
        ("CREATE INDEX IF NOT EXISTS idx_knowledge_bases_owner ON knowledge_bases(owner_user_id)",),
    ),
)


def _drop_legacy_share_columns(db: DatabasePool) -> None:
    """Drop the legacy global share booleans (schema v21).

    ``resource_acl`` has decided access since v18 and the v18 backfill copied
    every legacy flag into it, so these columns only ever mirrored the ACL — and
    a share applied through the sharing pipeline never reached them at all.

    The mirror runs first, per table: while the columns still exist they are the
    last surviving record of "this was published", and dropping one before
    copying it would silently turn every published resource private.

    Idempotent: a table whose flag is already gone is skipped, so a boot that
    lands between the PostgreSQL and SQLite paths converges.

    The SQLite DDL is each table's *current* shape, not its v20 one, because
    this helper is re-run on every boot rather than once: a later migration that
    changes one of these tables is reflected in ``_LEGACY_SHARE_COLUMNS`` too.
    ``_live_columns`` then keeps the copy to the columns the live table has, so
    a column that migration has not added yet takes the DDL's default rather
    than failing the INSERT.
    """
    if not _table_exists(db, "users"):
        return
    _ensure_resource_acl_schema(db)
    for table, column, ddl, columns, indexes in _LEGACY_SHARE_COLUMNS:
        if column not in _table_columns(db, table):
            continue
        if db.dialect == "postgresql":
            # ``DROP COLUMN`` takes the partial flag index with it.
            _drop_column(db, table, column)
            continue
        _rebuild_sqlite_table(
            db,
            table,
            ddl=ddl,
            columns=_live_columns(db, table, columns),
            indexes=indexes,
        )


def _live_columns(db: DatabasePool, table: str, columns: tuple[str, ...]) -> tuple[str, ...]:
    """*columns* narrowed to the ones the table actually has right now.

    The rebuild DDL describes each table's *current* shape, because the helper
    that runs it is re-run on every boot rather than once — while ``columns``
    also lists what later migrations added to the same table. Naming a column
    the live table lacks would fail the copy instead of letting the DDL's
    default stand, so the two lists are reconciled here.
    """
    live = _table_columns(db, table)
    return tuple(name for name in columns if name in live)


def _rebuild_sqlite_table(
    db: DatabasePool,
    table: str,
    *,
    ddl: str,
    columns: tuple[str, ...],
    indexes: tuple[str, ...],
) -> None:
    """Rebuild *table* from *columns*, keeping the rows and dropping the rest.

    SQLite cannot drop a constraint (a ``NOT NULL``) or a column that an index
    covers, so both callers go through a rename/copy/drop cycle. Any column
    *ddl* declares but *columns* omits is created empty, taking its declared
    default.
    """
    legacy = f"{table}_legacy"
    column_list = ", ".join(columns)
    with db.connect() as conn:
        # Two pragmas, and both are load-bearing:
        #
        # * ``foreign_keys = OFF`` stops ``DROP TABLE {table}_legacy`` from
        #   cascading into the rows that point here (``threads.agent_id``,
        #   ``knowledge_documents.kb_id``, ...).
        # * ``legacy_alter_table = ON`` keeps ``ALTER TABLE RENAME`` from
        #   rewriting those same tables' ``REFERENCES`` clauses to the legacy
        #   copy — SQLite 3.53 does that even with foreign keys off, and the
        #   result is a schema pointing at a table the next statement drops.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("BEGIN")
            conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
            conn.execute(ddl)
            conn.execute(f"INSERT INTO {table}({column_list}) SELECT {column_list} FROM {legacy}")
            conn.execute(f"DROP TABLE {legacy}")
            for statement in indexes:
                conn.execute(statement)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")


def _ensure_usage_cache_schema(db: DatabasePool) -> None:
    """Backfill cache-aware usage columns for upgraded and repaired databases."""
    if not _table_exists(db, "usage_log"):
        return
    token_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    for column in (
        "uncached_input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    ):
        _ensure_column(
            db,
            "usage_log",
            column,
            f"{token_type} NOT NULL DEFAULT 0",
        )
    _ensure_column(db, "usage_log", "model_calls", "INTEGER NOT NULL DEFAULT 1")
    with db.connect() as conn:
        conn.execute(
            "UPDATE usage_log SET uncached_input_tokens = input_tokens "
            "WHERE uncached_input_tokens = 0 AND input_tokens > 0 "
            "AND cache_read_tokens = 0 AND cache_write_tokens = 0"
        )


def _ensure_user_invites_schema(db: DatabasePool) -> None:
    """Create ``user_invites`` when missing (clamp / repair paths)."""
    if _table_exists(db, "user_invites") or not _table_exists(db, "users"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn, conn.transaction():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_invites (
                  id                BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                  code              TEXT NOT NULL UNIQUE,
                  created_by        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  note              TEXT,
                  created_at        BIGINT NOT NULL,
                  expires_at        BIGINT NOT NULL,
                  used_at           BIGINT,
                  used_by_user_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
                  revoked_at        BIGINT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_invites_created_at "
                "ON user_invites(created_at DESC)"
            )
        return
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_invites (
              id                INTEGER PRIMARY KEY AUTOINCREMENT,
              code              TEXT NOT NULL UNIQUE,
              created_by        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              note              TEXT,
              created_at        INTEGER NOT NULL,
              expires_at        INTEGER NOT NULL,
              used_at           INTEGER,
              used_by_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
              revoked_at        INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_user_invites_created_at
              ON user_invites(created_at DESC);
            """
        )


def _ensure_thread_message_projection_schema(db: DatabasePool) -> None:
    """Create dashboard history projection tables if missing (schema v10).

    Idempotent repair for installs that bumped ``_schema_version`` to 10 via a
    colliding parallel ``010_*.sql`` without applying the projection DDL.
    """
    if not _table_exists(db, "threads"):
        return
    if _table_exists(db, "thread_messages") and _table_exists(db, "thread_history_projection"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn, conn.transaction():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_messages (
                  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                  thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
                  seq BIGINT NOT NULL,
                  message_id TEXT,
                  role TEXT NOT NULL,
                  message_json TEXT NOT NULL,
                  created_at BIGINT NOT NULL,
                  UNIQUE(thread_id, seq),
                  UNIQUE(thread_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
                  ON thread_messages(thread_id, seq DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_history_projection (
                  thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id) ON DELETE CASCADE,
                  status TEXT NOT NULL,
                  updated_at BIGINT NOT NULL,
                  error TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO thread_history_projection(thread_id, status, updated_at, error)
                SELECT thread_id,
                       CASE WHEN last_active > 0 OR title IS NOT NULL
                            THEN 'pending' ELSE 'ready' END,
                       EXTRACT(EPOCH FROM NOW())::BIGINT,
                       NULL
                FROM threads
                ON CONFLICT(thread_id) DO NOTHING
                """
            )
        return
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS thread_messages (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id     TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
              seq           INTEGER NOT NULL,
              message_id    TEXT,
              role          TEXT NOT NULL,
              message_json  TEXT NOT NULL,
              created_at    INTEGER NOT NULL,
              UNIQUE(thread_id, seq),
              UNIQUE(thread_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
              ON thread_messages(thread_id, seq DESC);

            CREATE TABLE IF NOT EXISTS thread_history_projection (
              thread_id    TEXT PRIMARY KEY REFERENCES threads(thread_id) ON DELETE CASCADE,
              status       TEXT NOT NULL,
              updated_at   INTEGER NOT NULL,
              error        TEXT
            );

            INSERT OR IGNORE INTO thread_history_projection(thread_id, status, updated_at, error)
            SELECT thread_id,
                   CASE WHEN last_active > 0 OR title IS NOT NULL THEN 'pending' ELSE 'ready' END,
                   CAST(strftime('%s', 'now') AS INTEGER),
                   NULL
            FROM threads;
            """
        )


def _ensure_trajectory_events_schema(db: DatabasePool) -> None:
    """Create trajectory_events (schema v12) and ensure thread_id ON DELETE CASCADE."""
    if not _table_exists(db, "threads"):
        return
    if db.dialect == "postgresql":
        _ensure_trajectory_events_postgresql(db)
        return
    if not _table_exists(db, "trajectory_events"):
        with db.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trajectory_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id TEXT NOT NULL UNIQUE,
                  agent_id TEXT NOT NULL,
                  thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
                  seq INTEGER NOT NULL,
                  ts REAL NOT NULL,
                  kind TEXT NOT NULL,
                  turn_id TEXT,
                  request_seq INTEGER,
                  is_error INTEGER NOT NULL DEFAULT 0,
                  summary TEXT NOT NULL DEFAULT '',
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  UNIQUE (thread_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_trajectory_events_thread_seq
                  ON trajectory_events (thread_id, seq);
                """
            )
        return
    if _sqlite_references_threads(db, "trajectory_events"):
        return
    with db.transaction() as conn:
        conn.execute("ALTER TABLE trajectory_events RENAME TO trajectory_events_legacy")
        conn.execute(
            """
            CREATE TABLE trajectory_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              agent_id TEXT NOT NULL,
              thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
              seq INTEGER NOT NULL,
              ts REAL NOT NULL,
              kind TEXT NOT NULL,
              turn_id TEXT,
              request_seq INTEGER,
              is_error INTEGER NOT NULL DEFAULT 0,
              summary TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE (thread_id, seq)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trajectory_events(
              event_id, agent_id, thread_id, seq, ts, kind, turn_id,
              request_seq, is_error, summary, payload_json
            )
            SELECT
              event_id, agent_id, thread_id, seq, ts, kind, turn_id,
              request_seq, is_error, summary, payload_json
            FROM trajectory_events_legacy
            WHERE thread_id IN (SELECT thread_id FROM threads)
            """
        )
        conn.execute("DROP TABLE trajectory_events_legacy")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_events_thread_seq "
            "ON trajectory_events (thread_id, seq)"
        )


def _ensure_trajectory_events_postgresql(db: DatabasePool) -> None:
    with db.connect() as conn, conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trajectory_events (
              id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
              event_id TEXT NOT NULL UNIQUE,
              agent_id TEXT NOT NULL,
              thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
              seq BIGINT NOT NULL,
              ts DOUBLE PRECISION NOT NULL,
              kind TEXT NOT NULL,
              turn_id TEXT,
              request_seq BIGINT,
              is_error INTEGER NOT NULL DEFAULT 0,
              summary TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE (thread_id, seq)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_events_thread_seq "
            "ON trajectory_events(thread_id, seq)"
        )
        conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'trajectory_events_thread_id_fkey'
              ) THEN
                ALTER TABLE trajectory_events
                  ADD CONSTRAINT trajectory_events_thread_id_fkey
                  FOREIGN KEY (thread_id)
                  REFERENCES threads(thread_id) ON DELETE CASCADE;
              END IF;
            END $$
            """
        )


def _ensure_connectors_v13_schema(db: DatabasePool) -> None:
    """Ensure connectors supports multiple named instances."""
    if not _table_exists(db, "connectors"):
        return
    if db.dialect == "postgresql":
        with db.connect() as conn, conn.transaction():
            conn.execute(
                "ALTER TABLE connectors DROP CONSTRAINT IF EXISTS connectors_user_id_kind_key"
            )
            conn.execute(
                """
                WITH ranked AS (
                  SELECT id, display_name, instance_id,
                         ROW_NUMBER() OVER (
                           PARTITION BY user_id, display_name ORDER BY id
                         ) AS duplicate_number
                  FROM connectors
                  WHERE kind <> 'custom-mcp'
                )
                UPDATE connectors AS c
                SET display_name = ranked.display_name || ' (' ||
                    right(ranked.instance_id, 6) || ')'
                FROM ranked
                WHERE c.id = ranked.id AND ranked.duplicate_number > 1
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_user_display_name "
                "ON connectors(user_id, display_name) WHERE kind <> 'custom-mcp'"
            )
        return

    with db.connect() as conn:
        v13_ready = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_connectors_user_display_name",),
            ).fetchone()
            is not None
        )
    if v13_ready:
        # ``idx_connectors_user_display_name`` is created by the v13 rebuild and
        # by nothing else. The old "already has ``shared``?" guard cannot be
        # used any more: schema v21 dropped that column, and treating a modern
        # database as pre-v13 would rebuild it on every boot.
        return

    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute("ALTER TABLE connectors RENAME TO connectors_legacy")
            conn.execute(
                """
                CREATE TABLE connectors (
                  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                  instance_id           TEXT NOT NULL UNIQUE,
                  user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  kind                  TEXT NOT NULL,
                  display_name          TEXT NOT NULL,
                  status                TEXT NOT NULL DEFAULT 'active',
                  mcp_server_name       TEXT NOT NULL UNIQUE,
                  credential_blob       BLOB,
                  credential_expires_at INTEGER,
                  credential_rotated_at INTEGER,
                  config_json           TEXT,
                  created_at            INTEGER NOT NULL,
                  updated_at            INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO connectors(
                  id, instance_id, user_id, kind, display_name, status,
                  mcp_server_name, credential_blob, credential_expires_at,
                  credential_rotated_at, config_json, created_at, updated_at
                )
                SELECT
                  id, instance_id, user_id, kind,
                  CASE
                    WHEN kind = 'custom-mcp' THEN display_name
                    WHEN ROW_NUMBER() OVER (
                      PARTITION BY user_id, display_name ORDER BY id
                    ) = 1 THEN display_name
                    ELSE display_name || ' (' || substr(instance_id, -6) || ')'
                  END,
                  status, mcp_server_name, credential_blob, credential_expires_at,
                  credential_rotated_at, config_json, created_at, updated_at
                FROM connectors_legacy
                """
            )
            conn.execute("DROP TABLE connectors_legacy")
            conn.execute("CREATE INDEX idx_connectors_user ON connectors(user_id)")
            conn.execute(
                "CREATE UNIQUE INDEX idx_connectors_user_display_name "
                "ON connectors(user_id, display_name) WHERE kind <> 'custom-mcp'"
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


_USER_POLICY_IDENTITY_COLUMNS = {
    "id",
    "policy_id",
    "user_id",
    "name",
    "enabled",
    "value",
    "created_at",
    "updated_at",
}


def _user_policies_identity_ready(db: DatabasePool) -> bool:
    return _table_exists(db, "user_policies") and _USER_POLICY_IDENTITY_COLUMNS.issubset(
        _table_columns(db, "user_policies")
    )


def _create_user_policies_table(db: DatabasePool) -> None:
    pk = _integer_pk_sql(db)
    id_type = "BIGINT" if db.dialect == "postgresql" else "INTEGER"
    with db.connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS user_policies (
              id {pk},
              policy_id TEXT NOT NULL UNIQUE,
              user_id {id_type} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              value TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(user_id, name)
            )
            """
        )


def _insert_user_policy_row(conn: Any, *, user_id: int, name: str, value: str, ts: int) -> None:
    conn.execute(
        """
        INSERT INTO user_policies(
          policy_id, user_id, name, enabled, value, created_at, updated_at
        )
        VALUES (?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(user_id, name) DO UPDATE SET
          enabled = 1,
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (new_ulid(), user_id, name, value, ts, ts),
    )


def _ensure_user_policy_schema(db: DatabasePool) -> None:
    if not _table_exists(db, "users"):
        return
    if _table_exists(db, "user_policies") and not _user_policies_identity_ready(db):
        _rebuild_user_policies_from_legacy_kv(db)
    if not _table_exists(db, "user_policies"):
        _create_user_policies_table(db)
    _copy_legacy_user_resource_policies(db)
    _copy_legacy_user_policy_columns(db)


def _rebuild_user_policies_from_legacy_kv(db: DatabasePool) -> None:
    cols = _table_columns(db, "user_policies")
    if "key" not in cols:
        with db.connect() as conn:
            conn.execute("DROP TABLE IF EXISTS user_policies")
        _create_user_policies_table(db)
        return
    ts = int(time.time())
    with db.connect() as conn:
        rows = conn.execute("SELECT user_id, key, value FROM user_policies").fetchall()
    legacy = [
        (int(row["user_id"]), str(row["key"]), str(row["value"]))
        for row in rows
        if row["key"] and row["value"] is not None
    ]
    with db.transaction() as conn:
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE user_policies RENAME TO user_policies_kv_legacy")
    _create_user_policies_table(db)
    with db.transaction() as conn:
        for user_id, name, value in legacy:
            if not str(value).strip():
                continue
            _insert_user_policy_row(conn, user_id=user_id, name=name, value=value, ts=ts)
        conn.execute("DROP TABLE IF EXISTS user_policies_kv_legacy")
        if db.dialect == "sqlite":
            conn.execute("PRAGMA foreign_keys = ON")


def _copy_legacy_user_policy_columns(db: DatabasePool) -> None:
    user_columns = _table_columns(db, "users")
    ts = int(time.time())
    if "workspace_root_dir" in user_columns:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, workspace_root_dir FROM users "
                "WHERE workspace_root_dir IS NOT NULL AND workspace_root_dir <> ''"
            ).fetchall()
        with db.transaction() as conn:
            for row in rows:
                _insert_user_policy_row(
                    conn,
                    user_id=int(row["id"]),
                    name="workspace_root_dir",
                    value=str(row["workspace_root_dir"]),
                    ts=ts,
                )
        _drop_column(db, "users", "workspace_root_dir")
    if "token_quota" in user_columns:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, token_quota FROM users WHERE token_quota IS NOT NULL"
            ).fetchall()
        with db.transaction() as conn:
            for row in rows:
                _insert_user_policy_row(
                    conn,
                    user_id=int(row["id"]),
                    name="token_quota",
                    value=str(row["token_quota"]),
                    ts=ts,
                )
        _drop_column(db, "users", "token_quota")


def _copy_legacy_user_resource_policies(db: DatabasePool) -> None:
    if not _table_exists(db, "user_resource_policies"):
        return
    cols = _table_columns(db, "user_resource_policies")
    ts = int(time.time())
    if {"user_id", "workspace_root_dir", "token_quota"}.issubset(cols):
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT user_id, workspace_root_dir, token_quota FROM user_resource_policies"
            ).fetchall()
        with db.transaction() as conn:
            for row in rows:
                uid = int(row["user_id"])
                root = row["workspace_root_dir"]
                quota = row["token_quota"]
                if root:
                    _insert_user_policy_row(
                        conn,
                        user_id=uid,
                        name="workspace_root_dir",
                        value=str(root),
                        ts=ts,
                    )
                if quota is not None:
                    _insert_user_policy_row(
                        conn,
                        user_id=uid,
                        name="token_quota",
                        value=str(quota),
                        ts=ts,
                    )
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS user_resource_policies")


def _sqlite_references_threads(db: DatabasePool, table: str) -> bool:
    with db.connect() as conn:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return any(str(row["table"]) == "threads" for row in rows)


def _repair_legacy_schema(db: DatabasePool) -> None:
    """Idempotent compatibility repairs for local databases from old builds."""
    if _table_exists(db, "users"):
        _ensure_column(db, "users", "locale", "TEXT NOT NULL DEFAULT 'zh'")
        _ensure_column(db, "users", "login_failed_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "users", "login_locked_until", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "users", "preferences_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(db, "users", "email", "TEXT")
        _ensure_column(db, "users", "sso_provider_id", "INTEGER")
        _ensure_column(db, "users", "sso_subject", "TEXT")
        # Pre-squash DBs may already report version ≥6; reconcile can clamp to 6
        # without applying 006_user_permissions.sql — ensure the column here.
        _ensure_column(db, "users", "permissions", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_cron_jobs_schema(db)
    if _table_exists(db, "threads"):
        _ensure_column(db, "threads", "model_ref", "TEXT")
        _ensure_column(db, "threads", "reasoning_mode", "TEXT")
        _ensure_column(db, "threads", "reasoning_effort", "TEXT")
        _ensure_column(db, "threads", "artifacts", "TEXT NOT NULL DEFAULT '[]'")
    if _table_exists(db, "agents"):
        # Pre-v21 builds recorded "published" here. Schema v21 drops the column,
        # so only older databases get it backfilled — for them the v18 backfill
        # still needs it to seed ``resource_acl``, while re-adding it on a v21
        # database would resurrect the column on every boot.
        if _current_version(db) < 21:
            _ensure_column(db, "agents", "is_shared", "INTEGER NOT NULL DEFAULT 0")
        _ensure_agent_profile_columns(db)
        _backfill_agent_profile_from_config(db)
    _ensure_skill_packages_schema(db)
    _ensure_published_experts_schema(db)
    _ensure_usage_cache_schema(db)
    # Cover pre-squash develop DBs that already recorded version ≥5 but only
    # applied a subset of the former 005–009 files (or the old thin 005).
    # Require ``users`` first — SSO rebuild and knowledge FKs need it, and a
    # brand-new DB has not applied 001 yet when repair runs.
    if _table_exists(db, "users"):
        # Before the SSO rebuild below: it copies ``users`` column by column, so
        # the unit scope columns must already exist on the source table.
        _ensure_org_units_schema(db)
        _ensure_sso_oidc_schema(db)
        _ensure_knowledge_bases_schema(db)
        # SSO rebuild recreates ``users``; ensure permissions after that path.
        _ensure_column(db, "users", "permissions", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_resource_acl_schema(db)
        _ensure_data_sources_schema(db)


def _max_discovered_version(dialect: str) -> int:
    versions = [version for version, _ in _discover(dialect)]
    return max(versions) if versions else 0


def _reconcile_pre_squash_schema_version(db: DatabasePool) -> None:
    """Clamp develop DBs that applied split 005–009 down to the consolidated max.

    Before squash, local/develop installs could sit at schema versions 6–9.
    After those files are merged into a single 005, leaving version > max would
    permanently skip future migrations (e.g. a new 006).
    """
    current = _current_version(db)
    max_version = _max_discovered_version(db.dialect)
    if max_version <= 0 or current <= max_version:
        return
    if db.dialect == "postgresql":
        # v7 SQL rebuilds tables with RENAME; re-applying it would copy integer
        # ``id`` into public ``*_id`` columns. Use idempotent helpers instead.
        if max_version >= 7:
            _ensure_agent_profile_columns(db)
            _backfill_agent_profile_from_config(db)
            if _table_exists(db, "threads"):
                _ensure_column(db, "threads", "artifacts", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_knowledge_bases_schema(db)
            _ensure_skill_packages_schema(db)
            _ensure_published_experts_schema(db)
            if max_version >= 8:
                _ensure_usage_cache_schema(db)
            if max_version >= 9:
                _ensure_user_invites_schema(db)
            if max_version >= 10:
                _ensure_thread_message_projection_schema(db)
            if max_version >= 11:
                _ensure_cron_jobs_schema(db)
            if max_version >= 12:
                _ensure_trajectory_events_schema(db)
            if max_version >= 13:
                _ensure_connectors_v13_schema(db)
            if max_version >= 14:
                _ensure_user_policy_schema(db)
            if max_version >= 15:
                _ensure_sso_provider_kind_schema(db)
            with db.connect() as conn:
                conn.execute("UPDATE _schema_version SET version = %s", (max_version,))
            return
        path = next(path for version, path in _discover("postgresql") if version == max_version)
        sql = path.read_text(encoding="utf-8")
        with db.connect() as conn, conn.transaction():
            _apply_postgresql_migration(conn, sql)
            conn.execute("UPDATE _schema_version SET version = %s", (max_version,))
        return
    # SQLite: ensure helpers already ran via _repair_legacy_schema; also apply
    # the max migration's ensure path so clamping to a new max (e.g. 6) does not
    # skip ADD COLUMN for DBs that previously sat at version 7–9.
    if max_version >= 6 and _table_exists(db, "users"):
        _ensure_column(db, "users", "permissions", "TEXT NOT NULL DEFAULT '[]'")
    if max_version >= 7:
        _ensure_agent_profile_columns(db)
        _backfill_agent_profile_from_config(db)
        if _table_exists(db, "threads"):
            _ensure_column(db, "threads", "artifacts", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_knowledge_bases_schema(db)
        _ensure_skill_packages_schema(db)
        _ensure_published_experts_schema(db)
    if max_version >= 8:
        _ensure_usage_cache_schema(db)
    if max_version >= 9:
        _ensure_user_invites_schema(db)
    if max_version >= 10:
        _ensure_thread_message_projection_schema(db)
    if max_version >= 11:
        _ensure_cron_jobs_schema(db)
    if max_version >= 12:
        _ensure_trajectory_events_schema(db)
    if max_version >= 13:
        _ensure_connectors_v13_schema(db)
    if max_version >= 14:
        _ensure_user_policy_schema(db)
    if max_version >= 15:
        _ensure_sso_provider_kind_schema(db)
    with db.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = ?", (max_version,))


def _apply_postgresql_migration(conn: Any, sql: str) -> None:
    for stmt in _split_pg_sql(sql):
        conn.execute(stmt)


def _apply_sqlite_migration(db: DatabasePool, version: int, path: Path) -> None:
    """Apply one SQLite migration.

    Version 2 uses ``_ensure_column`` / table helpers so re-running after a
    partial upgrade does not fail.

    Version 3 bumps schema then rewrites legacy hard-cut thread titles in Python.
    Version 4 adds composer columns idempotently after legacy schema repair.
    Version 5 adds shared experts, published templates, OIDC SSO, and knowledge
    bases idempotently after legacy schema repair.
    Version 6 adds ``users.permissions`` idempotently (also covered by
    ``_repair_legacy_schema`` for DBs whose version was clamped past 006).
    Version 7 matches ``007_resource_identity_and_profile.sql``: profile
    columns, thread artifacts, integer PK + public string ids, document
    folders, drop ``knowledge_base_members``. SQLite uses these helpers
    (idempotent); PostgreSQL runs the ``.pg.sql`` file then the same helpers
    as a no-op safety net. ``config_json`` profile keys are backfilled in
    Python either way.
    Version 8 adds cache-aware usage buckets and model call counts.
    Version 9 adds one-time ``user_invites`` codes.
    Version 10 adds the dashboard thread-message projection when the legacy
    database actually contains conversation tables.
    Version 11 adds cron job display names.
    Version 12 adds the append-only chat trajectory event ledger.
    Version 13 adds multi-instance and shared connectors.
    Version 14 adds per-user named policy rows.
    Version 15 adds pluggable SSO provider ``kind`` / ``extra`` and
    multi-identity ``user_sso_identities``.
    Version 17 adds org units, unit permission grants, and the
    ``users.org_unit`` / ``users.denied_permissions`` scope columns. SQLite
    ``ALTER TABLE ADD COLUMN`` is not idempotent and ``_repair_legacy_schema``
    already added the columns, so this branch calls the ensure helper.
    Version 18 adds the unified resource ACL tables and mirrors the legacy
    ``is_shared`` / ``shared`` booleans. The backfill must also reach databases
    whose watermark already passed 18, so this branch calls the ensure helper.
    Version 20 adds ``data_sources``. The table must also reach databases whose
    watermark already passed 20, so this branch calls the ensure helper.
    Version 21 drops the three legacy global share booleans. The drop is a table
    rebuild on SQLite and runs through the ensure helper so the legacy flags are
    mirrored into ``resource_acl`` one last time first.
    Versions 16-25 built the deleted feature subsystem's tables
    (``_DROPPED_FEATURE_TABLE_VERSIONS``). They only move the watermark: v26
    drops what they would create, and their DDL is not re-runnable.
    Version 26 adds ``agents.kind`` — marking the app-owned rows the old model
    froze as the feature agents they were — and drops those tables.
    Version 27 makes ``knowledge_bases`` the single enterprise space (nullable
    owner, ``is_enterprise``, one seeded row) and gives ``data_sources`` the
    folder-connection columns. Both must also reach databases whose watermark
    already passed 27, so this branch calls the ensure helpers.
    Version 28 gives ``knowledge_documents`` the columns a file scan needs and
    creates ``knowledge_sync_runs``. Same reason: the ensure helpers must also
    reach databases whose watermark already passed 28.
    Version 35 folds the legacy user knowledge bases into the enterprise space.
    It is a *data* migration — documents, their files, their audience and the
    agent bindings that named them — so it runs through the helper in both
    dialects; see ``_merge_legacy_knowledge_bases`` for why the files make SQL
    alone impossible and why running it twice changes nothing.
    """
    if version == 2:
        if _table_exists(db, "cron_jobs"):
            _ensure_column(
                db,
                "cron_jobs",
                "mcp_servers",
                "TEXT NOT NULL DEFAULT '[]'",
            )
        _ensure_skill_packages_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 3:
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        if _table_exists(db, "threads"):
            from octop.infra.db.repos.threads import repair_all_legacy_thread_titles

            repair_all_legacy_thread_titles(db)
        return
    if version == 4:
        if _table_exists(db, "threads"):
            _ensure_column(db, "threads", "model_ref", "TEXT")
            _ensure_column(db, "threads", "reasoning_mode", "TEXT")
            _ensure_column(db, "threads", "reasoning_effort", "TEXT")
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 5:
        if _table_exists(db, "agents"):
            _ensure_column(db, "agents", "is_shared", "INTEGER NOT NULL DEFAULT 0")
            with db.connect() as conn:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agents_shared "
                    "ON agents(is_shared) WHERE is_shared = 1"
                )
        _ensure_published_experts_schema(db)
        _ensure_sso_oidc_schema(db)
        _ensure_knowledge_bases_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 6:
        if _table_exists(db, "users"):
            _ensure_column(db, "users", "permissions", "TEXT NOT NULL DEFAULT '[]'")
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 7:
        _ensure_agent_profile_columns(db)
        _backfill_agent_profile_from_config(db)
        if _table_exists(db, "threads"):
            _ensure_column(db, "threads", "artifacts", "TEXT NOT NULL DEFAULT '[]'")
        _rebuild_knowledge_identity_schema(db)
        _drop_knowledge_base_members(db)
        _ensure_skill_packages_schema(db)
        _ensure_published_experts_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 8:
        _ensure_usage_cache_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 9:
        _ensure_user_invites_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 10 and not _table_exists(db, "threads"):
        # Some very old/partial installs only contain ``users``. They still
        # need the version watermark to advance, but there is no history to
        # project and the migration's INSERT ... SELECT threads cannot run.
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 11:
        _ensure_cron_jobs_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 12:
        _ensure_trajectory_events_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 13 and not _table_exists(db, "connectors"):
        # Very old/partial installs may contain only ``users``. There are no
        # connector rows to rebuild, so only advance the migration watermark.
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 14:
        _ensure_agent_profile_columns(db)
        _ensure_user_policy_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 15:
        _ensure_sso_provider_kind_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 17:
        _ensure_org_units_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 18:
        _ensure_resource_acl_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version in _DROPPED_FEATURE_TABLE_VERSIONS:
        # 16-25 built the feature tables that v26 drops again, and nothing else.
        # Their DDL is not re-runnable here — ``ALTER TABLE ADD COLUMN`` refuses
        # a column that is already there, which is why these versions called
        # ensure helpers until v26 — and running it would only build tables this
        # same boot deletes again. The watermark is the whole of what they still
        # owe a database. PostgreSQL runs their ``.pg.sql`` files (idempotent
        # throughout) and drops them in 026.
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 20:
        _ensure_data_sources_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 21:
        _drop_legacy_share_columns(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 26:
        _ensure_agent_kind_column(db)
        _drop_feature_tables(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 27:
        _ensure_enterprise_knowledge_space(db)
        _ensure_data_sources_connection_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 28:
        _ensure_knowledge_file_index_schema(db)
        _ensure_knowledge_sync_runs_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 29:
        _ensure_knowledge_derived_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 33:
        _ensure_extract_templates_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 34:
        _ensure_extract_results_schema(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    if version == 35:
        _merge_legacy_knowledge_bases(db)
        with db.connect() as conn:
            conn.execute("UPDATE _schema_version SET version = ?", (version,))
        return
    sql = path.read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)


def run_migrations(db: DatabasePool) -> None:
    if db.dialect == "sqlite":
        _repair_legacy_schema(db)
    for version, path in _discover(db.dialect):
        if version <= _current_version(db):
            continue
        if db.dialect == "postgresql":
            sql = path.read_text(encoding="utf-8")
            with db.connect() as conn, conn.transaction():
                _apply_postgresql_migration(conn, sql)
            if version == 3:
                from octop.infra.db.repos.threads import repair_all_legacy_thread_titles

                repair_all_legacy_thread_titles(db)
            if version == 7:
                _backfill_agent_profile_from_config(db)
                _rebuild_knowledge_identity_schema(db)
                _drop_knowledge_base_members(db)
                _collapse_legacy_agent_welcome_columns(db)
                _ensure_skill_packages_schema(db)
                _ensure_published_experts_schema(db)
            if version == 10:
                _ensure_knowledge_bases_schema(db)
            if version == 27:
                _ensure_enterprise_knowledge_space(db)
                _ensure_data_sources_connection_schema(db)
            if version == 28:
                _ensure_knowledge_file_index_schema(db)
                _ensure_knowledge_sync_runs_schema(db)
            if version == 29:
                _ensure_knowledge_derived_schema(db)
            if version == 33:
                _ensure_extract_templates_schema(db)
            if version == 34:
                _ensure_extract_results_schema(db)
            if version == 35:
                _merge_legacy_knowledge_bases(db)
        else:
            _apply_sqlite_migration(db, version, path)
    _reconcile_pre_squash_schema_version(db)
    _ensure_knowledge_bases_schema(db)
    _ensure_skill_packages_schema(db)
    _ensure_published_experts_schema(db)
    _ensure_usage_cache_schema(db)
    _ensure_user_invites_schema(db)
    _ensure_thread_message_projection_schema(db)
    _ensure_cron_jobs_schema(db)
    _ensure_trajectory_events_schema(db)
    _ensure_connectors_v13_schema(db)
    _ensure_user_policy_schema(db)
    _ensure_agent_profile_columns(db)
    _ensure_sso_provider_kind_schema(db)
    _ensure_org_units_schema(db)
    _ensure_resource_acl_schema(db)
    _drop_legacy_share_columns(db)
    _ensure_data_sources_schema(db)
    _ensure_agent_kind_column(db)
    _drop_feature_tables(db)
    _ensure_enterprise_knowledge_space(db)
    _ensure_data_sources_connection_schema(db)
    _ensure_knowledge_file_index_schema(db)
    _ensure_knowledge_sync_runs_schema(db)
    _ensure_knowledge_derived_schema(db)
    _ensure_extract_templates_schema(db)
    _ensure_extract_results_schema(db)
