"""Knowledge-base authorization metadata and native ACL changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.resource_acl import CHANGE_PENDING, AclChangeRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.delegations import KnowledgeDelegationService
from octop.infra.knowledge.relpath import ancestor_dirs, normalize_kb_path
from octop.infra.knowledge.scope import document_access_ids
from octop.infra.sharing import (
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_UNIT,
    AclEntry,
    can_access,
    can_write,
    grant_delegates,
)
from octop.infra.sharing.service import SharingService

#: Upload scopes of the fixed HTTP contract. ``authorized`` names the actor's
#: current inherited/custom authorization boundary — never unrestricted public.
UPLOAD_SCOPE_COMPANY = "company"
UPLOAD_SCOPE_DEPARTMENT = "department"
UPLOAD_SCOPE_PERSONAL = "personal"
UPLOAD_SCOPE_AUTHORIZED = "authorized"
UPLOAD_SCOPES = (
    UPLOAD_SCOPE_COMPANY,
    UPLOAD_SCOPE_DEPARTMENT,
    UPLOAD_SCOPE_PERSONAL,
    UPLOAD_SCOPE_AUTHORIZED,
)


class StalePermissionVersion(Exception):
    """A caller attempted to replace an ACL they have not seen."""


@dataclass(frozen=True)
class UploadAccessOption:
    """One selectable upload boundary for the target folder."""

    scope: str
    permission: str
    unit_key: str | None = None
    department_name: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "permission": self.permission,
            "unit_key": self.unit_key,
            "department_name": self.department_name,
        }


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
        derived_rights = KnowledgeDelegationService(self._services.db).derived_rights(
            kb_id, actor_user_id=actor_user_id
        )
        for doc_id, right in derived_rights.items():
            if right.read:
                doc_reads.add(doc_id)
            if right.write:
                doc_writes.add(doc_id)
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
            can_delegate=manageable
            or self._chain_root_can_delegate([base_entry], actor_user_id, role, unit_keys),
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
            derived = derived_rights.get(row.id)
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
                    can_delegate=manageable
                    or bool(derived is not None and derived.can_delegate)
                    or self._chain_root_can_delegate(chain, actor_user_id, role, unit_keys),
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
        grant_delegation: frozenset[tuple[str, str]] | None = None,
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
            grant_delegation=tuple(sorted(grant_delegation or frozenset())),
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

    @staticmethod
    def _chain_root_can_delegate(
        chain: list[AclEntry], actor_user_id: int, role: str, unit_keys: tuple[str, ...]
    ) -> bool:
        """True when an entry the actor can reach grants them delegation root
        authority (their individual grant carries the switch)."""
        for entry in chain:
            if not can_access(entry, user_id=actor_user_id, role=role, unit_keys=unit_keys):
                continue
            if ("user", str(actor_user_id)) in grant_delegates(entry):
                return True
        return False

    # ------------------------------------------------------------------
    # Upload access ceilings — the fixed /upload-permissions contract
    # ------------------------------------------------------------------

    def _folder_chain(
        self, kb_id: str, folder_rel: str, base_entry: AclEntry
    ) -> tuple[list[AclEntry], dict[str, Any]]:
        """ACL chain for a (possibly not yet existing) target folder.

        Returns the chain entries plus the documents-by-path map of the base so
        callers can classify the actor's route.
        """
        all_documents = self._knowledge.list_documents(kb_id)
        by_path = {str(row.path): row for row in all_documents}
        entries = self._document_entries(all_documents)
        chain = [base_entry]
        seen: set[str] = set()
        for ancestor in ancestor_dirs(folder_rel):
            row = by_path.get(ancestor)
            if row is None or row.id in seen:
                continue
            seen.add(row.id)
            entry = entries.get(row.id)
            if entry is not None:
                chain.append(entry)
        folder_row = by_path.get(folder_rel) if folder_rel else None
        if folder_row is not None and folder_row.id not in seen:
            entry = entries.get(folder_row.id)
            if entry is not None:
                chain.append(entry)
        return chain, by_path

    @staticmethod
    def _upload_groups(
        entry: AclEntry, role: str, unit_keys: tuple[str, ...]
    ) -> tuple[tuple[str, str], ...]:
        """Group routes the uploader actually holds, excluding personal grants."""
        groups = {
            (kind, value)
            for kind, value in entry.grants
            if (kind == "unit" and value in unit_keys) or (kind == "role" and value == role)
        }
        if entry.visibility == VISIBILITY_UNIT and entry.unit_key in unit_keys:
            groups.add(("unit", str(entry.unit_key)))
        return tuple(sorted(groups))

    def _upload_route(
        self,
        kb_id: str,
        folder_rel: str,
        *,
        actor_user_id: int,
        base: Any,
        base_entry: AclEntry,
        role: str,
        unit_keys: tuple[str, ...],
        units: dict[str, Any],
        locale: str,
    ) -> tuple[list[UploadAccessOption], UploadAccessOption]:
        """Intersect every boundary using the actor's actual authorization route."""
        chain, _ = self._folder_chain(kb_id, folder_rel, base_entry)
        for entry in chain:
            self._require_read(entry, actor_user_id, role, unit_keys)
        writer = all(
            can_write(entry, user_id=actor_user_id, role=role, unit_keys=unit_keys)
            for entry in chain
        )
        audience_permission = (
            "write" if all(entry.permission == "write" for entry in chain) else "read"
        )
        restrictions = [entry for entry in chain if entry.visibility != VISIBILITY_PUBLIC]
        routes = [self._upload_groups(entry, role, unit_keys) for entry in restrictions]
        personal_only = any(not groups for groups in routes)
        options: list[UploadAccessOption] = []

        def offer(
            scope: str, permission: str, key: str | None = None, label: str | None = None
        ) -> UploadAccessOption:
            maximum = UploadAccessOption(scope, permission, key, label)
            options.append(maximum)
            if permission == "write":
                options.append(UploadAccessOption(scope, "read", key, label))
            return maximum

        default = None
        if not restrictions:
            default = offer(UPLOAD_SCOPE_COMPANY, audience_permission)
        if not personal_only:
            # The resolved unit chain is nearest first. Offer broadest first;
            # a department is safe only when every restriction covers it in full.
            for index in reversed(range(len(unit_keys))):
                key = unit_keys[index]
                ancestors = unit_keys[index:]
                if not all(
                    any(kind == "unit" and value in ancestors for kind, value in groups)
                    for groups in routes
                ):
                    continue
                unit = units.get(key)
                label = None
                if unit is not None:
                    label = (
                        (unit.label_en if locale.startswith("en") else unit.label_zh)
                        or unit.label_zh
                        or unit.label_en
                        or key
                    )
                department = offer(UPLOAD_SCOPE_DEPARTMENT, audience_permission, key, label)
                if default is None:
                    default = department
            if restrictions and any(kind == "role" for groups in routes for kind, _ in groups):
                # A role/unit intersection or union cannot be represented by
                # one department. Persist its current audience at upload time.
                default = offer(UPLOAD_SCOPE_AUTHORIZED, audience_permission)
        personal = offer(UPLOAD_SCOPE_PERSONAL, "write" if writer else "read")
        return options, default or personal

    def _authorized_upload_acl(
        self,
        kb_id: str,
        folder_rel: str,
        *,
        base: Any,
        base_entry: AclEntry,
        role: str,
        unit_keys: tuple[str, ...],
        units: dict[str, Any],
        permission: str,
    ) -> AclEntry:
        """Freeze a custom audience instead of silently inheriting future widening."""
        chain, _ = self._folder_chain(kb_id, folder_rel, base_entry)
        routes = [
            self._upload_groups(entry, role, unit_keys)
            for entry in chain
            if entry.visibility != VISIBILITY_PUBLIC
        ]
        grants: list[tuple[str, str]] = []
        unit_scopes: dict[str | None, set[str]] = {}
        for user in self._services.user_repo.list():
            if user.org_unit not in unit_scopes:
                ancestors: set[str] = set()
                current = user.org_unit
                while current is not None and current not in ancestors:
                    ancestors.add(current)
                    unit = units.get(current)
                    current = unit.parent_key if unit is not None else None
                unit_scopes[user.org_unit] = ancestors
            if all(
                any(
                    (kind == "unit" and value in unit_scopes[user.org_unit])
                    or (kind == "role" and value == user.role)
                    for kind, value in groups
                )
                for groups in routes
            ):
                grants.append(("user", str(user.id)))
        return AclEntry(
            resource_type="knowledge_document",
            resource_id="",
            owner_user_id=base.owner_user_id,
            visibility=VISIBILITY_PRIVATE,
            unit_key=None,
            version=1,
            permission=permission,
            grants=tuple(grants),
        )

    def upload_access(
        self,
        kb_id: str,
        *,
        folder: str = "",
        data_source_id: str | None = None,
        actor_user_id: int,
        locale: str = "zh",
    ) -> dict[str, Any]:
        """GET /upload-permissions payload: safe options plus exact maximum."""
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        role, unit_keys = self._acl.scope_for_user(actor_user_id)
        base_entry = self._base_entry(base)
        self._require_read(base_entry, actor_user_id, role, unit_keys)
        folder_rel = normalize_kb_path(folder)
        if data_source_id:
            folder_rel = self._source_folder_rel(kb_id, data_source_id, folder_rel)
        units = {unit.key: unit for unit in self._units.list_all()}
        options, default = self._upload_route(
            kb_id,
            folder_rel,
            actor_user_id=actor_user_id,
            base=base,
            base_entry=base_entry,
            role=role,
            unit_keys=unit_keys,
            units=units,
            locale=locale,
        )
        return {
            "options": [option.payload() for option in options],
            "default": default.payload(),
        }

    def _source_folder_rel(self, kb_id: str, data_source_id: str, folder_rel: str) -> str:
        """Translate a source-relative folder into its KB document path."""
        source = self._repos.data_sources_repo.get(data_source_id)
        if source is None or source.knowledge_base_id != kb_id:
            raise LookupError("knowledge source not found")
        if source.read_only:
            raise OctopError.localized(ErrorCode.DATA_SOURCE_READ_ONLY)
        if source.kind not in ("local", "smb"):
            raise OctopError.localized(ErrorCode.DATA_SOURCE_UPLOAD_INVALID)
        prefix = (
            normalize_kb_path(getattr(source, "root_path", "") or "")
            if source.kind == "smb"
            else ""
        )
        root = source.id if not prefix else f"{source.id}/{prefix}"
        return f"{root}/{folder_rel}" if folder_rel else root

    def validate_upload_access(
        self,
        kb_id: str,
        *,
        folder: str,
        data_source_id: str | None,
        actor_user_id: int,
        scope: str | None,
        permission: str | None,
        unit_key: str | None,
    ) -> AclEntry:
        """Recompute the ceiling server-side and build the initial ACL entry.

        The chosen boundary is persisted, including a snapshot for custom audiences.
        Forged or stale selections raise ``KNOWLEDGE_UPLOAD_PERMISSION_FORBIDDEN``
        *before* any file write or document row exists; malformed selections are
        plain validation errors.
        """
        supplied = scope is not None or permission is not None or unit_key is not None
        if supplied and (scope is None or permission is None):
            raise ValueError("access_scope and access_permission must be supplied together")
        if supplied and scope not in UPLOAD_SCOPES:
            raise ValueError(f"unknown access_scope {scope!r}")
        if supplied and permission not in ("read", "write"):
            raise ValueError(f"unknown access_permission {permission!r}")
        if scope != UPLOAD_SCOPE_DEPARTMENT and unit_key is not None:
            raise ValueError("access_unit_key is only valid for department scope")
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        role, unit_keys = self._acl.scope_for_user(actor_user_id)
        base_entry = self._base_entry(base)
        self._require_read(base_entry, actor_user_id, role, unit_keys)
        folder_rel = normalize_kb_path(folder)
        if data_source_id:
            folder_rel = self._source_folder_rel(kb_id, data_source_id, folder_rel)
        units = {unit.key: unit for unit in self._units.list_all()}
        options, _default = self._upload_route(
            kb_id,
            folder_rel,
            actor_user_id=actor_user_id,
            base=base,
            base_entry=base_entry,
            role=role,
            unit_keys=unit_keys,
            units=units,
            locale="zh",
        )
        if not supplied:
            scope, permission, unit_key = _default.scope, _default.permission, _default.unit_key
        offered: UploadAccessOption | None = None
        for option in options:
            if option.scope != scope or option.permission != permission:
                continue
            if option.unit_key != unit_key:
                continue
            offered = option
            break
        if offered is None:
            raise OctopError(
                ErrorCode.KNOWLEDGE_UPLOAD_PERMISSION_FORBIDDEN,
                "selected upload permission exceeds the current authorization",
            )
        permission = offered.permission
        if scope == UPLOAD_SCOPE_AUTHORIZED:
            return self._authorized_upload_acl(
                kb_id,
                folder_rel,
                base=base,
                base_entry=base_entry,
                role=role,
                unit_keys=unit_keys,
                units=units,
                permission=permission,
            )
        if scope == UPLOAD_SCOPE_PERSONAL:
            return AclEntry(
                resource_type="knowledge_document",
                resource_id="",
                owner_user_id=base.owner_user_id,
                visibility=VISIBILITY_PRIVATE,
                unit_key=None,
                version=1,
                grants=(("user", str(actor_user_id)),),
                permission=permission,
            )
        if scope == UPLOAD_SCOPE_DEPARTMENT:
            return AclEntry(
                resource_type="knowledge_document",
                resource_id="",
                owner_user_id=base.owner_user_id,
                visibility=VISIBILITY_UNIT,
                unit_key=unit_key,
                version=1,
                permission=permission,
            )
        return AclEntry(
            resource_type="knowledge_document",
            resource_id="",
            owner_user_id=base.owner_user_id,
            visibility=VISIBILITY_PUBLIC,
            unit_key=None,
            version=1,
            permission=permission,
        )

    # ------------------------------------------------------------------
    # Notification inbox pass-through (no KB/module gates by design)
    # ------------------------------------------------------------------

    def list_notifications(
        self,
        *,
        actor_user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        locale: str = "zh",
    ) -> dict[str, Any]:
        return KnowledgeDelegationService(self._services.db).list_notifications(
            actor_user_id=actor_user_id,
            unread_only=unread_only,
            limit=limit,
            locale=locale,
        )

    def mark_notification_read(self, notification_id: str, *, actor_user_id: int) -> None:
        KnowledgeDelegationService(self._services.db).mark_notification_read(
            notification_id, actor_user_id=actor_user_id
        )

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
        can_delegate: bool = False,
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
            # Server-effective delegated authority: the actor may issue
            # delegation edges for this resource. Deliberately independent of
            # the content write level.
            "can_delegate": bool(can_delegate),
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
        delegated = grant_delegates(entry)
        return {
            "visibility": entry.visibility,
            "unit_key": entry.unit_key,
            "permission": entry.permission,
            "grants": [
                {
                    "grantee_type": kind,
                    "grantee_id": grantee,
                    "can_delegate": (kind, grantee) in delegated,
                }
                for kind, grantee in entry.grants
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
