"""Org unit table access — units, their hierarchy, and unit permission grants."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    UNSET,
    DbRow,
    map_rows,
    now_ts,
    optional_updates,
    sql_in_placeholders,
)


@dataclass(frozen=True)
class OrgUnitRow:
    key: str
    label_zh: str
    label_en: str
    parent_key: str | None
    sort_order: int
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> OrgUnitRow:
        return cls(
            key=str(r["key"]),
            label_zh=str(r["label_zh"] or ""),
            label_en=str(r["label_en"] or ""),
            parent_key=r["parent_key"],
            sort_order=int(r["sort_order"] or 0),
            created_at=int(r["created_at"] or 0),
        )


def _dedupe(keys: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication of permission keys."""
    return list(dict.fromkeys(str(key) for key in keys))


class OrgUnitRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_all(self) -> list[OrgUnitRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM org_units ORDER BY sort_order ASC, key ASC"
            ).fetchall()
        return map_rows(rows, OrgUnitRow)

    def get(self, key: str) -> OrgUnitRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM org_units WHERE key = ?", (key,)).fetchone()
        return OrgUnitRow.from_row(r) if r else None

    def ancestor_keys(self, key: str) -> list[str]:
        """``key`` and the units above it, nearest first (``[]`` for an unknown key).

        One query for the whole hierarchy plus a walk in Python: the tree is a
        handful of rows, and a recursive CTE would have to be written twice (the
        two dialects spell it differently) for no gain.

        The walk carries a visited set because a hierarchy an older release left
        cyclic must not turn a permission lookup into an infinite loop; the key
        that closes the cycle is reported once and then stops the walk.
        """
        with self._db.connect() as conn:
            rows = conn.execute("SELECT key, parent_key FROM org_units").fetchall()
        parents: dict[str, str | None] = {str(r["key"]): r["parent_key"] for r in rows}
        if key not in parents:
            return []
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = key
        while current is not None and current not in seen:
            seen.add(current)
            chain.append(current)
            current = parents.get(current)
        return chain

    def create(
        self,
        *,
        key: str,
        label_zh: str,
        label_en: str,
        parent_key: str | None = None,
        sort_order: int = 0,
    ) -> OrgUnitRow:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO org_units(key, label_zh, label_en, parent_key, sort_order, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (key, label_zh, label_en, parent_key, sort_order, now_ts()),
            )
        row = self.get(key)
        if row is None:
            raise RuntimeError(f"org unit {key!r} vanished right after insert")
        return row

    def update(
        self,
        key: str,
        *,
        label_zh: str | None | object = UNSET,
        label_en: str | None | object = UNSET,
        parent_key: str | None | object = UNSET,
        sort_order: int | None | object = UNSET,
    ) -> OrgUnitRow | None:
        """Patch the given fields only; ``UNSET`` leaves a field as it is.

        ``parent_key=None`` is meaningful and clears the parent (unlike the label
        fields, which are NOT NULL and therefore only ever patched with a value).
        Returns the refreshed row, or ``None`` when the unit does not exist.
        """
        fields, params = optional_updates(
            [
                ("label_zh", label_zh),
                ("label_en", label_en),
                ("parent_key", parent_key),
                ("sort_order", sort_order),
            ]
        )
        if not fields:
            return self.get(key)
        params.append(key)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE org_units SET {', '.join(fields)} WHERE key = ?", params)
        return self.get(key)

    def list_children(self, key: str) -> list[str]:
        """Keys of the units whose ``parent_key`` is ``key``."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT key FROM org_units WHERE parent_key = ? ORDER BY sort_order ASC, key ASC",
                (key,),
            ).fetchall()
        return [str(r["key"]) for r in rows]

    def list_users_in_unit(self, unit_key: str) -> list[str]:
        """Usernames scoped to ``unit_key`` — the unit-deletion reference guard.

        Disabled accounts are included on purpose: ``users.org_unit`` carries no
        foreign key, so skipping them would leave a dangling unit reference that
        no later read can resolve.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT username FROM users WHERE org_unit = ? ORDER BY username ASC",
                (unit_key,),
            ).fetchall()
        return [str(r["username"]) for r in rows]

    def delete(self, key: str) -> None:
        """Delete a unit and its grants (``org_units.parent_key`` is SET NULL)."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM org_unit_permissions WHERE unit_key = ?", (key,))
            conn.execute("DELETE FROM org_units WHERE key = ?", (key,))

    def list_unit_permissions(self, unit_key: str) -> list[str]:
        """Permission keys granted to ``unit_key`` (empty for an unknown unit)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT permission_key FROM org_unit_permissions WHERE unit_key = ? "
                "ORDER BY permission_key ASC",
                (unit_key,),
            ).fetchall()
        return [str(r["permission_key"]) for r in rows]

    def set_grants(self, unit_key: str, keys: Iterable[str]) -> None:
        """Replace the unit's granted permission keys with ``keys``."""
        unique = _dedupe(keys)
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM org_unit_permissions WHERE unit_key = ?", (unit_key,))
            for permission_key in unique:
                conn.execute(
                    "INSERT INTO org_unit_permissions(unit_key, permission_key) VALUES (?, ?)",
                    (unit_key, permission_key),
                )

    def grants_for_units(self, unit_keys: Iterable[str]) -> set[str]:
        """Union of the grants of ``unit_keys`` in one query (for permission resolution)."""
        keys = _dedupe(unit_keys)
        if not keys:
            return set()
        placeholders = sql_in_placeholders(len(keys))
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT permission_key FROM org_unit_permissions "
                f"WHERE unit_key IN ({placeholders})",
                keys,
            ).fetchall()
        return {str(r["permission_key"]) for r in rows}
