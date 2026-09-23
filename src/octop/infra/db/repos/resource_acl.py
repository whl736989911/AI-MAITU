"""Unified resource ACL — visibility rows, extra grants, and the change log.

``resource_acl`` is authoritative: every shareable resource has a row (the v18
backfill covers all of them) and visibility is decided there alone. A NULL
``owner_user_id`` means "system-owned" — ``can_access`` rule 2 can never match
it, so such a row stays admin-only unless its visibility publishes it.

The three legacy booleans (``agents.is_shared``, ``connectors.shared``,
``knowledge_bases.shared``) were dropped in schema v21: visibility is stored
here alone, and the migration backfilled every legacy flag before the column
disappeared.

The access rules live in ``octop.infra.sharing`` as pure functions, and that is
their only implementation: this repo resolves the actor's scope and hands the
entries to ``sharing.allowed_resource_ids``, so a list can never decide access
with a second copy of the rules.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import UNSET, DbRow, map_rows, now_ts, sql_in_placeholders
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.sharing import (
    PERMISSION_READ,
    PERMISSIONS,
    RESOURCE_TYPES,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_UNIT,
    AclEntry,
    allowed_resource_ids,
)

VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_UNIT, VISIBILITY_PUBLIC)
GRANTEE_TYPES = ("user", "unit", "role")

CHANGE_APPLIED = "applied"
CHANGE_PENDING = "pending_approval"
CHANGE_REJECTED = "rejected"
CHANGE_ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class AclChangeRow:
    id: str
    resource_type: str
    resource_id: str
    actor_user_id: int
    from_version: int
    to_version: int
    before_json: str
    after_json: str
    impact_scope: str
    status: str
    reason: str | None
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> AclChangeRow:
        return cls(
            id=str(r["id"]),
            resource_type=str(r["resource_type"]),
            resource_id=str(r["resource_id"]),
            actor_user_id=int(r["actor_user_id"]),
            from_version=int(r["from_version"]),
            to_version=int(r["to_version"]),
            before_json=str(r["before_json"]),
            after_json=str(r["after_json"]),
            impact_scope=str(r["impact_scope"]),
            status=str(r["status"]),
            reason=r["reason"],
            created_at=int(r["created_at"]),
        )


def encode_acl_state(entry: AclEntry) -> str:
    """Serialize the governed fields of ``entry`` for a change-log column.

    ``owner_user_id`` rides along so a state can be reconstructed from the log
    alone (approving a parked change, or rolling back into a row that is gone).
    A change never mutates ownership — the field is a snapshot, not a diff.
    """
    return json.dumps(
        {
            "visibility": entry.visibility,
            "unit_key": entry.unit_key,
            "permission": entry.permission,
            "grants": [[kind, grantee] for kind, grantee in entry.grants],
            "owner_user_id": entry.owner_user_id,
        },
        ensure_ascii=False,
    )


def decode_acl_state(
    payload: str,
    *,
    resource_type: str,
    resource_id: str,
    version: int,
) -> AclEntry:
    """Rebuild an :class:`AclEntry` from a change-log payload.

    A payload written before the level existed carries no ``permission``: it
    decodes as ``read``, which is what that state meant.
    """
    data = json.loads(payload)
    owner = data.get("owner_user_id")
    return AclEntry(
        resource_type=resource_type,
        resource_id=resource_id,
        owner_user_id=None if owner is None else int(owner),
        visibility=str(data["visibility"]),
        unit_key=data["unit_key"],
        version=version,
        permission=str(data.get("permission") or PERMISSION_READ),
        grants=tuple((str(kind), str(grantee)) for kind, grantee in data.get("grants", ())),
    )


def owner_unit_key(conn: Any, user_id: int) -> str | None:
    """Owner's org unit *now* — the ``unit_key`` snapshot taken when sharing.

    Read inside the caller's transaction so the snapshot matches the row being
    written.
    """
    row = conn.execute("SELECT org_unit FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return str(row["org_unit"]) if row["org_unit"] else None


def _validate_visibility(visibility: str) -> None:
    if visibility not in VISIBILITIES:
        raise ValueError(f"unknown visibility {visibility!r}; expected one of {VISIBILITIES}")


def _validate_permission(permission: str) -> None:
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}; expected one of {PERMISSIONS}")


def _validate_grants(grants: Iterable[tuple[str, str]]) -> None:
    for kind, grantee in grants:
        if kind not in GRANTEE_TYPES:
            raise ValueError(f"unknown grantee_type {kind!r}; expected one of {GRANTEE_TYPES}")
        if not grantee:
            raise ValueError(f"empty grantee_id for grantee_type {kind!r}")


def _entry_from_row(row: DbRow, grants: Sequence[tuple[str, str]]) -> AclEntry:
    owner = row["owner_user_id"]
    permission = row["permission"]
    return AclEntry(
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        owner_user_id=None if owner is None else int(owner),
        visibility=str(row["visibility"]),
        unit_key=row["unit_key"],
        version=int(row["version"]),
        permission=str(permission) if permission else PERMISSION_READ,
        grants=tuple(grants),
    )


def _entries_from_join(rows: Sequence[DbRow]) -> list[AclEntry]:
    """Fold ``resource_acl LEFT JOIN resource_acl_grants`` rows into entries."""
    order: list[tuple[str, str]] = []
    heads: dict[tuple[str, str], DbRow] = {}
    grants: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in rows:
        key = (str(row["resource_type"]), str(row["resource_id"]))
        if key not in heads:
            heads[key] = row
            grants[key] = []
            order.append(key)
        kind = row["grantee_type"]
        if kind is not None:
            grants[key].append((str(kind), str(row["grantee_id"])))
    return [_entry_from_row(heads[key], grants[key]) for key in order]


class ResourceAclRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        # The org tree is what turns a unit scope into the chain a grant matches
        # against, exactly as it does for module permissions
        # (``users.permissions.unit_permissions``).
        self._units = OrgUnitRepo(db)

    @contextmanager
    def _tx(self, conn: Any | None) -> Iterator[Any]:
        """Run in an open transaction when given, else open one.

        ``conn`` lets a sibling repo write the legacy column and the ACL row in
        the same transaction instead of leaving two half-committed mirrors.
        """
        if conn is not None:
            yield conn
            return
        with self._db.transaction() as owned:
            yield owned

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, resource_type: str, resource_id: str) -> AclEntry | None:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT acl.*, g.grantee_type, g.grantee_id "
                "FROM resource_acl AS acl "
                "LEFT JOIN resource_acl_grants AS g "
                "  ON g.resource_type = acl.resource_type AND g.resource_id = acl.resource_id "
                "WHERE acl.resource_type = ? AND acl.resource_id = ? "
                "ORDER BY g.grantee_type, g.grantee_id",
                (resource_type, resource_id),
            ).fetchall()
        entries = _entries_from_join(rows)
        return entries[0] if entries else None

    def list_for_owner(self, user_id: int) -> list[AclEntry]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT acl.*, g.grantee_type, g.grantee_id "
                "FROM resource_acl AS acl "
                "LEFT JOIN resource_acl_grants AS g "
                "  ON g.resource_type = acl.resource_type AND g.resource_id = acl.resource_id "
                "WHERE acl.owner_user_id = ? "
                "ORDER BY acl.resource_type, acl.resource_id, g.grantee_type, g.grantee_id",
                (user_id,),
            ).fetchall()
        return _entries_from_join(rows)

    def list_for_type(
        self, resource_type: str, *, resource_ids: Collection[str] | None = None
    ) -> list[AclEntry]:
        """Every entry of one type, optionally narrowed to ``resource_ids``.

        Callers that decide access with the pure rules (``sharing.can_access``)
        need the entries themselves, not ids pre-filtered by a second SQL copy
        of those rules. There is deliberately no LIMIT: a truncated list would
        silently turn accessible resources into inaccessible ones.
        """
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(f"unknown resource_type {resource_type!r}")
        sql = (
            "SELECT acl.*, g.grantee_type, g.grantee_id "
            "FROM resource_acl AS acl "
            "LEFT JOIN resource_acl_grants AS g "
            "  ON g.resource_type = acl.resource_type AND g.resource_id = acl.resource_id "
            "WHERE acl.resource_type = ?"
        )
        params: list[object] = [resource_type]
        if resource_ids is not None:
            wanted = [str(rid) for rid in resource_ids]
            if not wanted:
                return []
            sql += f" AND acl.resource_id IN ({sql_in_placeholders(len(wanted))})"
            params.extend(wanted)
        sql += " ORDER BY acl.resource_id, g.grantee_type, g.grantee_id"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return _entries_from_join(rows)

    def public_resource_ids(
        self, resource_type: str, *, resource_ids: Collection[str] | None = None
    ) -> set[str]:
        """Ids of ``resource_type`` published to everyone (``visibility='public'``).

        The display counterpart of the legacy ``shared`` booleans: those columns
        are a write-only mirror, so a share applied through
        :class:`~octop.infra.sharing.service.SharingService` never lands there.
        """
        return {
            entry.resource_id
            for entry in self.list_for_type(resource_type, resource_ids=resource_ids)
            if entry.visibility == VISIBILITY_PUBLIC
        }

    def list_grants(self, resource_type: str, resource_id: str) -> tuple[tuple[str, str], ...]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT grantee_type, grantee_id FROM resource_acl_grants "
                "WHERE resource_type = ? AND resource_id = ? "
                "ORDER BY grantee_type, grantee_id",
                (resource_type, resource_id),
            ).fetchall()
        return tuple((str(r["grantee_type"]), str(r["grantee_id"])) for r in rows)

    def list_visible_resource_ids(
        self,
        resource_type: str,
        *,
        user_id: int,
        role: str,
        unit_keys: Collection[str],
    ) -> set[str]:
        """Ids of ``resource_type`` visible to this user, per the access rules.

        ``role``/``unit_keys`` are resolved by the caller (``can_access`` does no
        IO). The entries decide through ``sharing.allowed_resource_ids`` — this
        is the list entry point for every resource type, not a second statement
        of the rules, so it cannot disagree with ``can_access``.

        The entries of one resource type are loaded and filtered in memory (a
        per-deployment count of resources, not of content): a SQL copy of the
        rules is a second implementation that can drift.
        """
        return allowed_resource_ids(
            self.list_for_type(resource_type),
            user_id=user_id,
            role=role,
            unit_keys=unit_keys,
        )

    def unit_chain(self, unit_key: str | None) -> tuple[str, ...]:
        """``unit_key`` and the units above it, nearest first.

        The actor's org scope for ``sharing.can_access``, and the same chain
        ``users.permissions.unit_permissions`` unions module grants over: a
        grant to a parent unit reaches its sub-departments by naming a unit the
        viewer's chain contains. An unassigned account, or one whose unit is
        gone (``ancestor_keys`` answers ``[]`` for an unknown key), resolves to
        ``()`` and matches no unit scope at all.
        """
        if not unit_key:
            return ()
        return tuple(self._units.ancestor_keys(unit_key))

    def scope_for_user(self, user_id: int) -> tuple[str, tuple[str, ...]]:
        """``(role, unit_keys)`` for the pure rules, from the ``users`` row.

        ``sharing.can_access`` does no IO, so every caller that decides access
        itself resolves the actor's scope through here — including
        :meth:`list_visible_resource_ids_for_user` — so two list paths cannot
        read two different callers. An unknown user resolves to no role and no
        unit, which the rules then answer with "public only".
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT role, org_unit FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return ("", ())
        return (str(row["role"]), self.unit_chain(row["org_unit"]))

    def list_visible_resource_ids_for_user(self, resource_type: str, user_id: int) -> set[str]:
        """Visible ids with the caller's scope resolved from ``users``.

        Convenience for the resource repos, which hold a user id rather than a
        resolved role/unit pair. An unknown user sees only what is public.
        """
        role, unit_keys = self.scope_for_user(user_id)
        return self.list_visible_resource_ids(
            resource_type,
            user_id=user_id,
            role=role,
            unit_keys=unit_keys,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, entry: AclEntry, *, conn: Any | None = None) -> None:
        """Write the whole entry (visibility, unit_key, permission, version, grants)."""
        _validate_visibility(entry.visibility)
        _validate_permission(entry.permission)
        _validate_grants(entry.grants)
        if entry.visibility == VISIBILITY_UNIT and not entry.unit_key:
            raise ValueError("unit visibility requires a unit_key snapshot")
        with self._tx(conn) as c:
            c.execute(
                "INSERT INTO resource_acl("
                "resource_type, resource_id, owner_user_id, visibility, unit_key, permission, "
                "version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(resource_type, resource_id) DO UPDATE SET "
                "owner_user_id = excluded.owner_user_id, visibility = excluded.visibility, "
                "unit_key = excluded.unit_key, permission = excluded.permission, "
                "version = excluded.version, "
                "updated_at = excluded.updated_at",
                (
                    entry.resource_type,
                    entry.resource_id,
                    entry.owner_user_id,
                    entry.visibility,
                    entry.unit_key,
                    entry.permission,
                    entry.version,
                    now_ts(),
                ),
            )
            self.set_grants(entry.resource_type, entry.resource_id, entry.grants, conn=c)

    def set_visibility(
        self,
        resource_type: str,
        resource_id: str,
        visibility: str,
        *,
        owner_user_id: int | None,
        unit_key: str | None = None,
        conn: Any | None = None,
    ) -> None:
        """Set visibility and bump the version (creates the row when missing).

        ``unit_key`` must be the *share-time* snapshot; it is never read back
        from the resource's current owner, so a later department change cannot
        widen an already-shared resource.

        ``permission`` is deliberately untouched: sharing a resource and
        choosing what a share lets people *do* are two decisions, and a new row
        takes the column's ``read`` default — the level an enterprise space is
        published at.
        """
        _validate_visibility(visibility)
        if visibility == VISIBILITY_UNIT and not unit_key:
            raise ValueError("unit visibility requires a unit_key snapshot")
        with self._tx(conn) as c:
            c.execute(
                "INSERT INTO resource_acl("
                "resource_type, resource_id, owner_user_id, visibility, unit_key, version, "
                "updated_at) VALUES (?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(resource_type, resource_id) DO UPDATE SET "
                "visibility = excluded.visibility, unit_key = excluded.unit_key, "
                "version = resource_acl.version + 1, updated_at = excluded.updated_at",
                (resource_type, resource_id, owner_user_id, visibility, unit_key, now_ts()),
            )

    def set_grants(
        self,
        resource_type: str,
        resource_id: str,
        grants: Iterable[tuple[str, str]],
        *,
        conn: Any | None = None,
    ) -> None:
        """Replace this resource's grants (grants only ever widen access)."""
        pairs = list(dict.fromkeys((str(kind), str(grantee)) for kind, grantee in grants))
        _validate_grants(pairs)
        with self._tx(conn) as c:
            c.execute(
                "DELETE FROM resource_acl_grants WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            )
            for kind, grantee in pairs:
                c.execute(
                    "INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, "
                    "grantee_id) VALUES (?, ?, ?, ?)",
                    (resource_type, resource_id, kind, grantee),
                )

    def delete(self, resource_type: str, resource_id: str, *, conn: Any | None = None) -> None:
        """Drop a deleted resource's ACL row and grants.

        Stale rows would otherwise grant access to a later resource that reuses
        the id (``resource_acl_grants`` has no FK to cascade).
        """
        with self._tx(conn) as c:
            c.execute(
                "DELETE FROM resource_acl_grants WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            )
            c.execute(
                "DELETE FROM resource_acl WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            )

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    def insert_change(self, change: AclChangeRow, *, conn: Any | None = None) -> None:
        with self._tx(conn) as c:
            c.execute(
                "INSERT INTO resource_acl_changes("
                "id, resource_type, resource_id, actor_user_id, from_version, to_version, "
                "before_json, after_json, impact_scope, status, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    change.id,
                    change.resource_type,
                    change.resource_id,
                    change.actor_user_id,
                    change.from_version,
                    change.to_version,
                    change.before_json,
                    change.after_json,
                    change.impact_scope,
                    change.status,
                    change.reason,
                    change.created_at,
                ),
            )

    def get_change(self, change_id: str) -> AclChangeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_acl_changes WHERE id = ?", (change_id,)
            ).fetchone()
        return AclChangeRow.from_row(row) if row else None

    def list_changes(
        self,
        resource_type: str,
        resource_id: str,
        *,
        limit: int = 50,
    ) -> list[AclChangeRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_acl_changes "
                "WHERE resource_type = ? AND resource_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (resource_type, resource_id, limit),
            ).fetchall()
        return map_rows(rows, AclChangeRow)

    def list_pending_changes(
        self, *, status: str = CHANGE_PENDING, limit: int = 50
    ) -> list[AclChangeRow]:
        """Newest changes in ``status`` — the approval queue, one statement.

        A whole-table listing would be unbounded and useless to a reviewer, so
        the caller passes a window and gets the most recent first. ``status``
        rides ``idx_resource_acl_changes_status``.
        """
        if status not in (CHANGE_APPLIED, CHANGE_PENDING, CHANGE_REJECTED, CHANGE_ROLLED_BACK):
            raise ValueError(f"unknown change status {status!r}")
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_acl_changes WHERE status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return map_rows(rows, AclChangeRow)

    def set_change_status(
        self,
        change_id: str,
        status: str,
        *,
        reason: str | None | object = UNSET,
        to_version: int | None | object = UNSET,
        conn: Any | None = None,
    ) -> None:
        if status not in (CHANGE_APPLIED, CHANGE_PENDING, CHANGE_REJECTED, CHANGE_ROLLED_BACK):
            raise ValueError(f"unknown change status {status!r}")
        fields = ["status = ?"]
        params: list[object] = [status]
        if reason is not UNSET:
            fields.append("reason = ?")
            params.append(reason)
        if to_version is not UNSET:
            fields.append("to_version = ?")
            params.append(to_version)
        params.append(change_id)
        with self._tx(conn) as c:
            c.execute(f"UPDATE resource_acl_changes SET {', '.join(fields)} WHERE id = ?", params)
