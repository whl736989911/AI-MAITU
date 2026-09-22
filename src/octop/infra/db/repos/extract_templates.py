"""Extraction templates, their versions, and their bindings (design §7).

Three rows because the design gives them three lifetimes: a template is the
identity an administrator manages, a version is what an edit produced (never an
overwrite — §7.3), and a binding is where the template applies (§7.4).

The repo keeps the three in step in one place: creating a template writes its
first version and bumps ``current_version`` in the same transaction, so a
template can never be read with a version number that has no version row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, partial_updates
from octop.infra.knowledge.template_match import BindingCandidate
from octop.infra.utils.ulid import new_ulid

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUSES = (STATUS_ACTIVE, STATUS_DISABLED)


@dataclass(frozen=True)
class ExtractTemplateRow:
    id: str
    name: str
    description: str
    status: str
    current_version: int
    created_by: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ExtractTemplateRow:
        return cls(
            id=str(r["id"]),
            name=str(r["name"]),
            description=str(r["description"] or ""),
            status=str(r["status"]),
            current_version=int(r["current_version"] or 0),
            created_by=int(r["created_by"]) if r["created_by"] is not None else None,
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )

    @property
    def enabled(self) -> bool:
        return self.status == STATUS_ACTIVE


@dataclass(frozen=True)
class TemplateVersionRow:
    id: str
    template_id: str
    version: int
    fields_json: str
    instruction: str
    applies_to: str
    note: str
    created_by: int | None
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> TemplateVersionRow:
        return cls(
            id=str(r["id"]),
            template_id=str(r["template_id"]),
            version=int(r["version"]),
            fields_json=str(r["fields_json"] or "[]"),
            instruction=str(r["instruction"] or ""),
            applies_to=str(r["applies_to"] or ""),
            note=str(r["note"] or ""),
            created_by=int(r["created_by"]) if r["created_by"] is not None else None,
            created_at=int(r["created_at"]),
        )

    @property
    def fields(self) -> list[dict[str, Any]]:
        """The field list as stored.

        An unreadable payload degrades to ``[]`` like every other JSON column in
        the repos; a version whose fields cannot be read is a version nobody can
        carry out, and pretending otherwise would fail per file later.
        """
        try:
            decoded = json.loads(self.fields_json or "[]")
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []


@dataclass(frozen=True)
class ExtractBindingRow:
    id: str
    template_id: str
    data_source_id: str
    path: str
    extension: str
    mime_type: str
    name_pattern: str
    match_regex: str
    created_by: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ExtractBindingRow:
        return cls(
            id=str(r["id"]),
            template_id=str(r["template_id"]),
            data_source_id=str(r["data_source_id"]),
            path=str(r["path"] or ""),
            extension=str(r["extension"] or ""),
            mime_type=str(r["mime_type"] or ""),
            name_pattern=str(r["name_pattern"] or ""),
            match_regex=str(r["match_regex"] or ""),
            created_by=int(r["created_by"]) if r["created_by"] is not None else None,
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class ExtractTemplateRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        description: str,
        fields_json: str,
        instruction: str,
        applies_to: str,
        note: str,
        created_by: int | None,
    ) -> ExtractTemplateRow:
        """Create a template together with its first version (§7.3).

        One transaction: a template whose ``current_version`` points at a row
        that does not exist would be unreadable, and nothing outside can repair
        that afterwards.
        """
        template_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_extract_templates("
                "id, name, description, status, current_version, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (template_id, name, description, STATUS_ACTIVE, created_by, ts, ts),
            )
            conn.execute(
                "INSERT INTO knowledge_extract_template_versions("
                "id, template_id, version, fields_json, instruction, applies_to, "
                "note, created_by, created_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    new_ulid(),
                    template_id,
                    fields_json,
                    instruction,
                    applies_to,
                    note,
                    created_by,
                    ts,
                ),
            )
        row = self.get(template_id)
        if row is None:
            raise RuntimeError(f"extract template insert failed: {template_id}")
        return row

    def get(self, template_id: str) -> ExtractTemplateRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM knowledge_extract_templates WHERE id = ?", (template_id,)
            ).fetchone()
        return ExtractTemplateRow.from_row(r) if r else None

    def list_all(self) -> list[ExtractTemplateRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_extract_templates ORDER BY name, id"
            ).fetchall()
        return map_rows(rows, ExtractTemplateRow)

    def update(
        self,
        template_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> ExtractTemplateRow | None:
        fields, params = partial_updates(
            [("name", name), ("description", description), ("status", status)]
        )
        if not fields:
            return self.get(template_id)
        fields.append("updated_at = ?")
        params.extend([now_ts(), template_id])
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE knowledge_extract_templates SET {', '.join(fields)} WHERE id = ?",
                params,
            )
        return self.get(template_id)

    def add_version(
        self,
        template_id: str,
        *,
        fields_json: str,
        instruction: str,
        applies_to: str,
        note: str,
        created_by: int | None,
    ) -> TemplateVersionRow | None:
        """Write the next version and point the template at it (§7.3).

        The previous version stays exactly as it was, which is what lets an
        already-produced result keep naming the version that produced it.
        """
        template = self.get(template_id)
        if template is None:
            return None
        version = template.current_version + 1
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_extract_template_versions("
                "id, template_id, version, fields_json, instruction, applies_to, "
                "note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_ulid(),
                    template_id,
                    version,
                    fields_json,
                    instruction,
                    applies_to,
                    note,
                    created_by,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE knowledge_extract_templates SET current_version = ?, updated_at = ? "
                "WHERE id = ?",
                (version, ts, template_id),
            )
        return self.get_version(template_id, version)

    def get_version(
        self, template_id: str, version: int | None = None
    ) -> TemplateVersionRow | None:
        """One version, or the current one when *version* is omitted."""
        if version is None:
            template = self.get(template_id)
            if template is None:
                return None
            version = template.current_version
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM knowledge_extract_template_versions "
                "WHERE template_id = ? AND version = ?",
                (template_id, int(version)),
            ).fetchone()
        return TemplateVersionRow.from_row(r) if r else None

    def list_versions(self, template_id: str) -> list[TemplateVersionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_extract_template_versions WHERE template_id = ? "
                "ORDER BY version DESC",
                (template_id,),
            ).fetchall()
        return map_rows(rows, TemplateVersionRow)

    def current_versions(self) -> dict[str, TemplateVersionRow]:
        """Every template's current version, in one query.

        The list view needs each template's fields, and asking per template would
        be one query per row for a list that is read whenever the page opens.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT v.* FROM knowledge_extract_template_versions v "
                "JOIN knowledge_extract_templates t "
                "  ON t.id = v.template_id AND t.current_version = v.version"
            ).fetchall()
        return {row.template_id: row for row in map_rows(rows, TemplateVersionRow)}

    def binding_counts(self) -> dict[str, int]:
        """How many bindings each template has, in one query."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT template_id, COUNT(*) AS n FROM knowledge_extract_bindings "
                "GROUP BY template_id"
            ).fetchall()
        return {str(r["template_id"]): int(r["n"]) for r in rows}

    def delete(self, template_id: str) -> None:
        """Remove a template; its versions and bindings cascade with it.

        The caller decides whether a deletion is allowed at all — design §7.2
        refuses to remove a template that is in use, and that check needs to see
        the bindings this statement would cascade away.
        """
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM knowledge_extract_templates WHERE id = ?", (template_id,))

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def create_binding(
        self,
        *,
        template_id: str,
        data_source_id: str,
        path: str,
        extension: str,
        mime_type: str,
        name_pattern: str,
        match_regex: str,
        created_by: int | None,
    ) -> ExtractBindingRow:
        binding_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_extract_bindings("
                "id, template_id, data_source_id, path, extension, mime_type, "
                "name_pattern, match_regex, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding_id,
                    template_id,
                    data_source_id,
                    path,
                    extension,
                    mime_type,
                    name_pattern,
                    match_regex,
                    created_by,
                    ts,
                    ts,
                ),
            )
        row = self.get_binding(binding_id)
        if row is None:
            raise RuntimeError(f"extract binding insert failed: {binding_id}")
        return row

    def get_binding(self, binding_id: str) -> ExtractBindingRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM knowledge_extract_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
        return ExtractBindingRow.from_row(r) if r else None

    def list_bindings(self, *, template_id: str | None = None) -> list[ExtractBindingRow]:
        if template_id is None:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM knowledge_extract_bindings ORDER BY data_source_id, path, id"
                ).fetchall()
        else:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM knowledge_extract_bindings WHERE template_id = ? "
                    "ORDER BY data_source_id, path, id",
                    (template_id,),
                ).fetchall()
        return map_rows(rows, ExtractBindingRow)

    def delete_binding(self, binding_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_extract_bindings WHERE id = ?", (binding_id,)
            )
            return bool(cursor.rowcount)

    def count_bindings(self, template_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge_extract_bindings WHERE template_id = ?",
                (template_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def candidates_for_matching(self) -> list[BindingCandidate]:
        """Every enabled binding with its template's declared file types.

        Joined rather than assembled in Python: the matcher needs the template's
        ``applies_to`` and asking for it per binding would be one query per
        candidate. Disabled templates are excluded here because that is the whole
        point of disabling one (design §7.2).
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT b.id AS binding_id, b.template_id, b.data_source_id, b.path, "
                "b.extension, b.mime_type, b.name_pattern, b.match_regex, "
                "v.applies_to AS applies_to "
                "FROM knowledge_extract_bindings b "
                "JOIN knowledge_extract_templates t ON t.id = b.template_id "
                "LEFT JOIN knowledge_extract_template_versions v "
                "  ON v.template_id = t.id AND v.version = t.current_version "
                "WHERE t.status = ? "
                "ORDER BY b.data_source_id, b.path, b.id",
                (STATUS_ACTIVE,),
            ).fetchall()
        from octop.infra.knowledge.template_match import normalize_types

        return [
            BindingCandidate(
                binding_id=str(r["binding_id"]),
                template_id=str(r["template_id"]),
                data_source_id=str(r["data_source_id"]),
                path=str(r["path"] or ""),
                extension=str(r["extension"] or ""),
                mime_type=str(r["mime_type"] or ""),
                name_pattern=str(r["name_pattern"] or ""),
                match_regex=str(r["match_regex"] or ""),
                applies_to=normalize_types(str(r["applies_to"] or "")),
            )
            for r in rows
        ]
