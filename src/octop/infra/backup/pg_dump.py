"""PostgreSQL dump/restore via client tools on PATH."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from octop.infra.db.pool import DatabasePool
from octop.infra.errors import ErrorCode, OctopError

# Rollback cleanup covers every object class the supported Octop PostgreSQL
# migrations can create: relations (tables, views, sequences, indexes,
# composite types), user-defined types, schemas, and routines. Extensions are
# DBA-managed (see 001_initial.pg.sql) but newly introduced ones are still
# dropped. Other DBA-only classes (operators, collations, publications, roles,
# tablespaces) are not created by any supported migration and are untouched.
_SCHEMA_FILTER = "n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'"

_SCHEMAS_SQL = f"""
SELECT n.nspname
FROM pg_namespace n
WHERE {_SCHEMA_FILTER}
"""

_RELATIONS_SQL = f"""
SELECT n.nspname, c.relname, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S', 'i', 'I', 'c')
  AND {_SCHEMA_FILTER}
"""

_TYPES_SQL = f"""
SELECT n.nspname, t.typname, t.typtype
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_depend d
  ON d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e'
WHERE t.typrelid = 0
  AND t.typelem = 0
  AND t.typtype IN ('d', 'e', 'r', 'm')
  AND d.objid IS NULL
  AND {_SCHEMA_FILTER}
"""

_EXTENSIONS_SQL = "SELECT e.extname FROM pg_extension e"

_ROUTINES_SQL = f"""
SELECT n.nspname, p.proname, p.prokind,
       pg_get_function_identity_arguments(p.oid)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
LEFT JOIN pg_depend d
  ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
WHERE d.objid IS NULL
  AND p.prokind IN ('f', 'p', 'w')
  AND {_SCHEMA_FILTER}
"""

# Dependency-safe drop order: views first, then table-shaped relations, then
# sequences/indexes, then standalone composite types.
_RELATION_DROP_ORDER = {
    "v": 0,
    "m": 1,
    "f": 2,
    "p": 3,
    "r": 4,
    "S": 5,
    "i": 6,
    "I": 6,
    "c": 7,
}
_RELATION_DROP_VERBS = {
    "v": "VIEW",
    "m": "MATERIALIZED VIEW",
    "f": "FOREIGN TABLE",
    "p": "TABLE",
    "r": "TABLE",
    "S": "SEQUENCE",
    "i": "INDEX",
    "I": "INDEX",
    "c": "TYPE",
}
_TYPE_DROP_VERBS = {"d": "DOMAIN", "e": "TYPE", "r": "TYPE", "m": "TYPE"}


@dataclass(frozen=True)
class PostgresRelation:
    schema: str
    name: str
    kind: str


@dataclass(frozen=True)
class PostgresType:
    schema: str
    name: str
    kind: str


@dataclass(frozen=True)
class PostgresRoutine:
    schema: str
    name: str
    kind: str
    identity_arguments: str


@dataclass(frozen=True)
class PostgresObjectInventory:
    """Objects that existed before a restore replaced the live database."""

    schemas: frozenset[str]
    relations: frozenset[PostgresRelation]
    types: frozenset[PostgresType]
    routines: frozenset[PostgresRoutine]
    extensions: frozenset[str]


def capture_postgres_inventory(pool: DatabasePool) -> PostgresObjectInventory:
    """Read every supported schema object from the live PostgreSQL database."""
    with pool.connect() as conn:
        schema_rows = conn.execute(_SCHEMAS_SQL).fetchall()
        relation_rows = conn.execute(_RELATIONS_SQL).fetchall()
        type_rows = conn.execute(_TYPES_SQL).fetchall()
        routine_rows = conn.execute(_ROUTINES_SQL).fetchall()
        extension_rows = conn.execute(_EXTENSIONS_SQL).fetchall()
    return PostgresObjectInventory(
        schemas=frozenset(str(row[0]) for row in schema_rows),
        relations=frozenset(
            PostgresRelation(str(row[0]), str(row[1]), str(row[2])) for row in relation_rows
        ),
        types=frozenset(PostgresType(str(row[0]), str(row[1]), str(row[2])) for row in type_rows),
        routines=frozenset(
            PostgresRoutine(str(row[0]), str(row[1]), str(row[2]), str(row[3] or ""))
            for row in routine_rows
        ),
        extensions=frozenset(str(row[0]) for row in extension_rows),
    )


def drop_postgres_objects_added_since(pool: DatabasePool, before: PostgresObjectInventory) -> int:
    """Drop schema objects that appeared after *before* was captured.

    ``pg_restore --clean`` can only drop objects described by the dump it is
    replaying. Objects introduced only by a failed archive restore therefore
    survive a plain pre-restore dump replay; this removes them first, in one
    transaction, using identifiers supplied by PostgreSQL catalogs.
    """
    current = capture_postgres_inventory(pool)
    new_relations = current.relations - before.relations
    new_types = current.types - before.types
    new_routines = current.routines - before.routines
    new_extensions = current.extensions - before.extensions
    new_schemas = current.schemas - before.schemas
    if not (new_relations or new_types or new_routines or new_extensions or new_schemas):
        return 0

    # Imported lazily: SQLite-only installs never exercise this path.
    from psycopg import sql as pg_sql

    dropped = 0
    with pool.transaction() as conn, conn.cursor() as cursor:

        def drop(verb: str, schema: str, name: str) -> None:
            statement = pg_sql.SQL("DROP {} IF EXISTS {}.{} CASCADE").format(
                pg_sql.SQL(verb),
                pg_sql.Identifier(schema),
                pg_sql.Identifier(name),
            )
            cursor.execute(statement)

        for extension in sorted(new_extensions):
            # Extension member objects cannot be dropped individually.
            cursor.execute(
                pg_sql.SQL("DROP EXTENSION IF EXISTS {} CASCADE").format(
                    pg_sql.Identifier(extension)
                )
            )
            dropped += 1
        for routine in sorted(
            new_routines,
            key=lambda item: (item.schema, item.name, item.identity_arguments),
        ):
            routine_verb = "PROCEDURE" if routine.kind == "p" else "FUNCTION"
            cursor.execute(
                pg_sql.SQL("DROP {} IF EXISTS {}.{}({}) CASCADE").format(
                    pg_sql.SQL(routine_verb),
                    pg_sql.Identifier(routine.schema),
                    pg_sql.Identifier(routine.name),
                    pg_sql.SQL(routine.identity_arguments),
                )
            )
            dropped += 1
        for relation in sorted(
            new_relations,
            key=lambda item: (
                _RELATION_DROP_ORDER.get(item.kind, len(_RELATION_DROP_ORDER)),
                item.schema,
                item.name,
            ),
        ):
            verb = _RELATION_DROP_VERBS.get(relation.kind)
            if verb is None:
                continue
            drop(verb, relation.schema, relation.name)
            dropped += 1
        for type_ in sorted(new_types, key=lambda item: (item.schema, item.name)):
            verb = _TYPE_DROP_VERBS.get(type_.kind)
            if verb is None:
                continue
            drop(verb, type_.schema, type_.name)
            dropped += 1
        for schema in sorted(new_schemas):
            cursor.execute(
                pg_sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(pg_sql.Identifier(schema))
            )
            dropped += 1
    return dropped


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{name} not found on PATH; required for PostgreSQL backup/restore",
        )
    return path


def dump_postgres(
    conninfo: str,
    dest: Path,
    *,
    exclude_table_data: Sequence[str] = (),
) -> None:
    pg_dump = _require_tool("pg_dump")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [pg_dump, "-Fc", "-f", str(dest), "--dbname", conninfo]
    for table in exclude_table_data:
        cmd.extend(["--exclude-table-data", table])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"pg_dump failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )


def restore_postgres(conninfo: str, dump_file: Path, *, single_transaction: bool = False) -> None:
    """Replay a dump over the live database.

    ``single_transaction`` makes rollback replay atomic. Any non-zero exit is
    a failure: pg_restore returns 1 when it ignored SQL errors during restore.
    """
    pg_restore = _require_tool("pg_restore")
    command = [pg_restore, "--clean", "--if-exists", "--no-owner"]
    if single_transaction:
        command.append("--single-transaction")
    command.extend(["--dbname", conninfo, str(dump_file)])
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"pg_restore failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )
