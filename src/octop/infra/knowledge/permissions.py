"""Knowledge-base authorization metadata and native ACL changes."""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos.resource_acl import CHANGE_PENDING, AclChangeRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.scope import document_access_ids
from octop.infra.sharing import (
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_UNIT,
    AclEntry,
    can_access,
    can_write,
)
from octop.infra.sharing.service import SharingService


class StalePermissionVersion(Exception):
    """A caller attempted to replace an ACL they have not seen."""


class KnowledgePermissionService:
    """Present effective knowledge ACLs and apply changes through SharingService."""

    def __init__(self, services: Any) -> None:
        if services is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "knowledge services are not initialized")
        self._services = services
        self._repos = services.repos
        self._knowledge = self._repos.knowledge_repo
        self._acl = self._repos.resource_acl_repo
        self._units = self._repos.org_unit_repo
        self._sharing = SharingService(services.db)

    def list_permissions(
        self, kb_id: str, *, actor_user_id: int, locale: str = "zh"
    ) -> dict[str, Any]:
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        role, unit_keys = self._acl.scope_for_user(actor_user_id)
        manageable = role == "admin" or actor_user_id == base.owner_user_id
        units = {unit.key: unit for unit in self._units.list_all()}
        base_entry = self._base_entry(base)
        self._require_read(base_entry, actor_user_id, role, unit_keys)

        all_documents = self._knowledge.list_documents(kb_id)
        all_entries = self._document_entries(all_documents)
        _restricted, doc_reads, doc_writes = document_access_ids(
            all_documents,
            all_entries,
            user_id=actor_user_id,
            role=role,
            unit_keys=unit_keys,
        )
        # Filenames and paths of denied documents are deliberately omitted.
        documents = [row for row in all_documents if row.id in doc_reads]
        pending = self._pending_changes(
            [("knowledge_base", kb_id)] + [("knowledge_document", row.id) for row in documents]
        )
        base_meta = self._metadata(
            resource_type="knowledge_base",
            resource_id=kb_id,
            path="/",
            entries=[base_entry],
            own_entry=self._acl.get("knowledge_base", kb_id),
            effective_read=True,
            effective_write=can_write(
                base_entry, user_id=actor_user_id, role=role, unit_keys=unit_keys
            ),
            manageable=manageable,
            units=units,
            pending=pending.get(("knowledge_base", kb_id)),
            locale=locale,
        )
        doc_meta = []
        by_path = {str(row.path): row for row in all_documents}
        for row in documents:
            chain = self._document_chain(row, by_path, all_entries, base_entry)
            own_entry = all_entries.get(row.id)
            explicit_file_grant = self._has_explicit_file_grant(
                own_entry, actor_user_id, role, unit_keys
            )
            effective_entries = (
                [base_entry, own_entry]
                if explicit_file_grant and not row.is_dir and own_entry is not None
                else chain
            )
            doc_meta.append(
                self._metadata(
                    resource_type="knowledge_document",
                    resource_id=row.id,
                    path=row.path,
                    entries=effective_entries,
                    own_entry=own_entry,
                    effective_read=True,
                    effective_write=(
                        row.id in doc_writes
                        and can_write(
                            base_entry, user_id=actor_user_id, role=role, unit_keys=unit_keys
                        )
                    ),
                    manageable=manageable,
                    units=units,
                    pending=pending.get(("knowledge_document", row.id)),
                    locale=locale,
                    inherited_from=self._inherited_from(
                        row,
                        by_path,
                        all_entries,
                        base_entry,
                        explicit_file_grant and not row.is_dir,
                    ),
                )
            )
        return {"knowledge_base": base_meta, "documents": doc_meta}

    def list_principals(
        self, kb_id: str, *, actor_user_id: int, locale: str = "zh"
    ) -> dict[str, Any]:
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        if not self._is_manager(actor_user_id, base.owner_user_id):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "only the knowledge-base owner or system admin may list ACL principals",
            )
        users = [
            {
                "id": row.id,
                "username": row.username,
                "label": row.display_name or row.username,
            }
            for row in self._repos.user_repo.list(include_disabled=False)
        ]
        departments = [
            {
                "key": unit.key,
                "label": (unit.label_en if locale.startswith("en") else unit.label_zh)
                or unit.label_zh
                or unit.label_en
                or unit.key,
            }
            for unit in self._units.list_all()
        ]
        return {
            "users": users,
            "departments": departments,
            "roles": [
                {"id": role, "label": role}
                for role in ("admin", "enterprise_admin", "unit_admin", "user")
            ],
        }

    def get_document_permissions(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, locale: str = "zh"
    ) -> dict[str, Any]:
        listing = self.list_permissions(kb_id, actor_user_id=actor_user_id, locale=locale)
        documents: list[dict[str, Any]] = listing["documents"]
        for item in documents:
            if item["resource_id"] == doc_id:
                return item
        # Same not-found result for absent and denied rows, avoiding filename leakage.
        raise LookupError("knowledge document not found")

    def put_permissions(
        self,
        kb_id: str,
        resource_type: str,
        resource_id: str,
        *,
        actor_user_id: int,
        visibility: str,
        unit_key: str | None,
        permission: str,
        grants: list[tuple[str, str]],
        reason: str | None,
        expected_version: int | None,
        locale: str = "zh",
    ) -> dict[str, Any]:
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        if not self._is_manager(actor_user_id, base.owner_user_id):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "only the knowledge-base owner or system admin may change access",
            )
        if resource_type == "knowledge_base":
            if resource_id != kb_id:
                raise LookupError("knowledge base not found")
            if expected_version is not None and expected_version != self._current_version(
                resource_type, resource_id
            ):
                raise StalePermissionVersion
        elif resource_type == "knowledge_document":
            target = self._knowledge.get_document(resource_id)
            if target is None or target.kb_id != kb_id:
                raise LookupError("knowledge document not found")
            # Require read access before resolving/returning document identity.
            self.get_document_permissions(kb_id, resource_id, actor_user_id=actor_user_id)
            if expected_version is not None and expected_version != self._current_version(
                resource_type, resource_id
            ):
                raise StalePermissionVersion
        else:
            raise ValueError("unsupported knowledge ACL resource")
        current_version = self._current_version(resource_type, resource_id)
        if visibility == VISIBILITY_UNIT:
            if not unit_key:
                actor = self._repos.user_repo.get(actor_user_id)
                unit_key = getattr(actor, "org_unit", None)
                if not unit_key:
                    raise ValueError("unit_key is required for department access")
            if self._units.get(unit_key) is None:
                raise ValueError("unknown unit_key")
        updated = AclEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_user_id=base.owner_user_id,
            visibility=visibility,
            unit_key=unit_key if visibility == VISIBILITY_UNIT else None,
            permission=permission,
            grants=tuple(grants),
            version=current_version,
        )
        result = self._sharing.apply_change(
            actor_user_id, resource_type, resource_id, updated, reason=reason
        )
        if resource_type == "knowledge_base":
            return {
                "permission": self.list_permissions(
                    kb_id, actor_user_id=actor_user_id, locale=locale
                )["knowledge_base"],
                "change": self._change_payload(
                    result.change_id, result.status, result.impact_scope
                ),
            }
        return {
            "permission": self.get_document_permissions(
                kb_id, resource_id, actor_user_id=actor_user_id, locale=locale
            ),
            "change": self._change_payload(result.change_id, result.status, result.impact_scope),
        }

    def _base_entry(self, base: Any) -> AclEntry:
        return self._acl.get("knowledge_base", base.id) or AclEntry(
            resource_type="knowledge_base",
            resource_id=base.id,
            owner_user_id=base.owner_user_id,
            visibility=VISIBILITY_PRIVATE,
            unit_key=None,
            version=0,
        )

    def _current_version(self, resource_type: str, resource_id: str) -> int:
        entry = self._acl.get(resource_type, resource_id)
        version = entry.version if entry is not None else 0
        with self._services.db.connect() as conn:
            pending = conn.execute(
                "SELECT to_version FROM resource_acl_changes "
                "WHERE resource_type = ? AND resource_id = ? AND status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (resource_type, resource_id, CHANGE_PENDING),
            ).fetchone()
        return max(version, int(pending["to_version"]) if pending is not None else 0)

    def _document_entries(self, documents: list[Any]) -> dict[str, AclEntry]:
        if not documents:
            return {}
        wanted = {row.id for row in documents}
        return {
            entry.resource_id: entry
            for entry in self._acl.list_for_type("knowledge_document")
            if entry.resource_id in wanted
        }

    def _document_chain(
        self,
        row: Any,
        by_path: dict[str, Any],
        entries: dict[str, AclEntry],
        base_entry: AclEntry,
    ) -> list[AclEntry]:
        chain = [base_entry]
        parent = row.path.rpartition("/")[0]
        ancestors: list[Any] = []
        while parent:
            ancestor = by_path.get(parent)
            if ancestor is not None:
                ancestors.append(ancestor)
            parent = parent.rpartition("/")[0]
        for ancestor in reversed(ancestors):
            entry = entries.get(ancestor.id)
            if entry is not None:
                chain.append(entry)
        own = entries.get(row.id)
        if own is not None:
            chain.append(own)
        return chain

    def _inherited_from(
        self,
        row: Any,
        by_path: dict[str, Any],
        entries: dict[str, AclEntry],
        base_entry: AclEntry,
        bypass_ancestors: bool,
    ) -> dict[str, str] | None:
        if not bypass_ancestors:
            parent = row.path.rpartition("/")[0]
            while parent:
                ancestor = by_path.get(parent)
                if ancestor is not None and ancestor.id in entries:
                    return {
                        "resource_type": "knowledge_document",
                        "resource_id": ancestor.id,
                        "path": ancestor.path,
                    }
                parent = parent.rpartition("/")[0]
        return {
            "resource_type": "knowledge_base",
            "resource_id": base_entry.resource_id,
            "path": "/",
        }

    @staticmethod
    def _has_explicit_file_grant(
        entry: AclEntry | None, actor_user_id: int, role: str, unit_keys: tuple[str, ...]
    ) -> bool:
        if entry is None:
            return False
        grants = set(entry.grants)
        return (
            ("user", str(actor_user_id)) in grants
            or ("role", role) in grants
            or any(("unit", unit) in grants for unit in unit_keys)
        )

    def _metadata(
        self,
        *,
        resource_type: str,
        resource_id: str,
        path: str,
        entries: list[AclEntry],
        own_entry: AclEntry | None,
        effective_read: bool,
        effective_write: bool,
        manageable: bool,
        units: dict[str, Any],
        pending: AclChangeRow | None,
        locale: str,
        inherited_from: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        scope, unit_key = self._scope(entries)
        unit = units.get(unit_key) if unit_key else None
        department_name = None
        if unit is not None:
            department_name = (
                (unit.label_en if locale.startswith("en") else unit.label_zh)
                or unit.label_zh
                or unit.label_en
            )
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "path": path,
            "version": max(
                own_entry.version if own_entry is not None else 0,
                pending.to_version if pending is not None else 0,
            ),
            "access_scope": scope,
            "department_name": department_name,
            "inherited_from": inherited_from,
            "permission": "write" if effective_write else "read",
            "effective": {"read": effective_read, "write": effective_write},
            "can_manage": manageable,
            "entry": self._entry_payload(own_entry) if manageable and own_entry else None,
            "pending": self._pending_payload(pending) if manageable else None,
        }

    def _is_manager(self, actor_user_id: int, owner_user_id: int) -> bool:
        role, _ = self._acl.scope_for_user(actor_user_id)
        return role == "admin" or actor_user_id == owner_user_id

    def _scope(self, entries: list[AclEntry]) -> tuple[str, str | None]:
        # Public ancestors do not narrow a child's audience. An ungranted
        # private restriction owned by the same owner narrows it to that owner,
        # even when another ancestor carries a department or explicit grants.
        restrictions = [entry for entry in entries if entry.visibility != VISIBILITY_PUBLIC]
        if not restrictions:
            return "company", None
        if (
            any(
                entry.visibility == VISIBILITY_PRIVATE and not entry.grants
                for entry in restrictions
            )
            and len({entry.owner_user_id for entry in restrictions}) == 1
        ):
            return "personal", None
        if any(entry.grants for entry in restrictions):
            return "custom", None
        unit_keys = {
            entry.unit_key for entry in restrictions if entry.visibility == VISIBILITY_UNIT
        }
        if (
            all(entry.visibility == VISIBILITY_UNIT for entry in restrictions)
            and len(unit_keys) == 1
            and None not in unit_keys
        ):
            return "department", next(iter(unit_keys))
        return "custom", None

    @staticmethod
    def _entry_payload(entry: AclEntry) -> dict[str, Any]:
        return {
            "visibility": entry.visibility,
            "unit_key": entry.unit_key,
            "permission": entry.permission,
            "grants": [
                {"grantee_type": kind, "grantee_id": grantee} for kind, grantee in entry.grants
            ],
            "version": entry.version,
        }

    @staticmethod
    def _pending_payload(change: AclChangeRow | None) -> dict[str, Any] | None:
        if change is None:
            return None
        return {
            "change_id": change.id,
            "status": change.status,
            "impact_scope": change.impact_scope,
            "reason": change.reason,
            "created_at": change.created_at,
        }

    def _pending_changes(
        self, resources: list[tuple[str, str]]
    ) -> dict[tuple[str, str], AclChangeRow]:
        if not resources:
            return {}
        base_id = resources[0][1]
        wanted = set(resources)
        with self._services.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_acl_changes WHERE status = ? AND ("
                "(resource_type = 'knowledge_base' AND resource_id = ?) OR "
                "(resource_type = 'knowledge_document' AND resource_id IN "
                "(SELECT document_id FROM knowledge_documents WHERE kb_id = ?))) "
                "ORDER BY created_at DESC, id DESC",
                (CHANGE_PENDING, base_id, base_id),
            ).fetchall()
        found: dict[tuple[str, str], AclChangeRow] = {}
        for row in rows:
            item = AclChangeRow.from_row(row)
            key = (item.resource_type, item.resource_id)
            if key in wanted and key not in found:
                found[key] = item
        return found

    @staticmethod
    def _change_payload(change_id: str, status: str, impact_scope: str) -> dict[str, str]:
        return {"change_id": change_id, "status": status, "impact_scope": impact_scope}

    @staticmethod
    def _require_read(
        entry: AclEntry, actor_user_id: int, role: str, unit_keys: tuple[str, ...]
    ) -> None:
        if not can_access(entry, user_id=actor_user_id, role=role, unit_keys=unit_keys):
            raise OctopError(
                ErrorCode.KNOWLEDGE_FORBIDDEN, "knowledge base read access is required"
            )
