"""Record and knowledge references for feature data fields.

A ``record_ref`` field points at one public record of a (default: the same)
feature object; a ``knowledge_ref`` field pins one knowledge document —
optionally its exact content version, page and layout. Values reference other
users' data, so every read re-resolves the target through *both* the target
feature's ACL and the target record/document visibility, and every payload
describes what the current caller may actually see: an inaccessible target
never leaks its identifiers, title, path, version or locator.

Write paths (HTTP router and agent tools both funnel through
:mod:`octop.infra.agents.feature_data`) validate targets the same way and pin
a missing knowledge version to the document's current content hash, so a
reference is honest about what it pointed at. The service is built per
request/tool turn over the shared ``registry`` (agent rows and workspaces)
and ``repos`` bundle, with a small cache so one page of records costs one
resolution per distinct target.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from octop.infra.agents.feature_access import can_read_record, can_use_feature
from octop.infra.agents.kinds import is_feature_agent
from octop.infra.knowledge.delegations import KnowledgeDelegationService
from octop.infra.knowledge.scope import (
    document_access_ids,
    may_read_document,
    may_read_knowledge_base,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from octop.infra.db.repos.feature_data import FeatureDataRecordRow

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_LAYOUT_CHARS = 64
DOCUMENT_FILE_URL_TEMPLATE = "/api/knowledge-bases/{kb_id}/documents/{document_id}/file"
DOCUMENT_PREVIEW_URL_TEMPLATE = "/api/knowledge-bases/{kb_id}/documents/{document_id}/preview"


class ReferenceValueError(ValueError):
    """A reference value the schema or the caller's access disallows."""


def ref_fields(obj: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The record_ref/knowledge_ref fields of one object, by field key."""
    return {
        str(field["key"]): dict(field)
        for field in obj.get("fields", [])
        if field.get("type") in {"record_ref", "knowledge_ref"}
    }


def validate_reference_shape(field: Mapping[str, Any], value: Any) -> Any:
    """Structural validation of one reference value (no access checks)."""
    field_key = str(field.get("key"))
    if str(field.get("type")) == "record_ref":
        if not isinstance(value, str) or not value.strip():
            raise ReferenceValueError(f"{field_key} must reference a record id")
        if len(value) > 128:
            raise ReferenceValueError(f"{field_key} exceeds the reference id length")
        return value.strip()
    if not isinstance(value, Mapping):
        raise ReferenceValueError(f"{field_key} must be an object with kb_id and document_id")
    kb_id = value.get("kb_id")
    document_id = value.get("document_id")
    if not isinstance(kb_id, str) or not kb_id.strip():
        raise ReferenceValueError(f"{field_key}.kb_id must be a non-empty string")
    if not isinstance(document_id, str) or not document_id.strip():
        raise ReferenceValueError(f"{field_key}.document_id must be a non-empty string")
    normalized: dict[str, Any] = {"kb_id": kb_id.strip(), "document_id": document_id.strip()}
    version = value.get("version")
    if version is not None:
        if not isinstance(version, str) or not _SHA256_RE.fullmatch(version.strip().lower()):
            raise ReferenceValueError(f"{field_key}.version must be a SHA-256 hex digest")
        normalized["version"] = version.strip().lower()
    page = value.get("page")
    if page is not None:
        if type(page) is not int or page <= 0:
            raise ReferenceValueError(f"{field_key}.page must be a positive integer")
        normalized["page"] = page
    layout = value.get("layout")
    if layout is not None:
        if not isinstance(layout, str) or not layout.strip():
            raise ReferenceValueError(f"{field_key}.layout must be a non-empty string")
        normalized["layout"] = layout.strip()[:MAX_LAYOUT_CHARS]
    extra = set(value) - {"kb_id", "document_id", "version", "page", "layout"}
    if extra:
        raise ReferenceValueError(f"{field_key} has unsupported keys: {sorted(extra)}")
    return normalized


class FeatureReferenceService:
    """Per-request resolver: describes and validates references for one caller."""

    def __init__(self, *, registry: Any, repos: Any, user_id: int) -> None:
        self._registry = registry
        self._repos = repos
        self._user_id = int(user_id)
        self._record_cache: dict[str, FeatureDataRecordRow | None] = {}
        self._knowledge_cache: dict[str, dict[str, Any] | None] = {}
        self._feature_cache: dict[str, bool] = {}

    # -- write path ---------------------------------------------------------
    async def pin_and_validate_patch(
        self, *, agent_id: str, obj: Mapping[str, Any], patch: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate reference values the writer is sending and pin versions.

        Returns a copy of *patch* where knowledge_ref values carry an explicit
        ``version`` (the document's current content hash when the writer left
        it open). Raises :class:`ReferenceValueError` for a target that does
        not exist, is the wrong object, is deleted, or is not readable by this
        caller right now — with one generic message so probing ids cannot
        distinguish "absent" from "revoked".
        """
        refs = ref_fields(obj)
        pinned = dict(patch)
        for field_key, field in refs.items():
            if field_key not in pinned or pinned[field_key] is None:
                continue
            value = validate_reference_shape(field, pinned[field_key])
            if str(field["type"]) == "record_ref":
                self._require_record_ref(agent_id=agent_id, field=field, value=value)
            else:
                value = self._pin_knowledge_ref(value)
                self._require_knowledge_ref(value)
            pinned[field_key] = value
        return pinned

    # -- read path ----------------------------------------------------------
    async def describe(
        self, *, agent_id: str, obj: Mapping[str, Any], record: Any
    ) -> dict[str, dict[str, Any]]:
        """The ``references`` payload for one record: what this caller may see."""
        described: dict[str, dict[str, Any]] = {}
        for field_key, field in ref_fields(obj).items():
            value = record.values.get(field_key)
            if value is None or value == "":
                continue
            if str(field["type"]) == "record_ref":
                described[field_key] = self._describe_record_ref(
                    agent_id=agent_id, field=field, value=value
                )
            else:
                described[field_key] = self._describe_knowledge_ref(value)
        return described

    async def describe_with_values(
        self, *, agent_id: str, obj: Mapping[str, Any], record: Any
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """``describe`` plus the caller's view of ``record.values``.

        The values copy masks every reference field whose target is currently
        inaccessible with the ``{"redacted": true}`` marker; ordinary fields
        are untouched and stay editable.
        """
        described = await self.describe(agent_id=agent_id, obj=obj, record=record)
        values = dict(record.values)
        for field_key in ref_fields(obj):
            if field_key not in values or values[field_key] in (None, ""):
                continue
            info = described.get(field_key)
            if info is not None and not info.get("available"):
                values[field_key] = {"redacted": True}
        return described, values

    async def redact_history(
        self, *, agent_id: str, obj: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop before/after reference payloads history must not carry.

        Every distinct reference value in the items is resolved against the
        caller's *current* access; an unavailable target leaves
        ``{"redacted": true}`` instead of its raw value.
        """
        refs = ref_fields(obj)
        if not refs:
            return [dict(item) for item in items]
        redacted_items: list[dict[str, Any]] = []
        for item in items:
            field_key = str(item.get("field") or "")
            field = refs.get(field_key)
            if field is None:
                redacted_items.append(dict(item))
                continue
            entry = dict(item)
            for side in ("before", "after"):
                value = entry.get(side)
                if value is None:
                    continue
                entry[side] = self._history_value(agent_id, field, value)
            redacted_items.append(entry)
        return redacted_items

    async def selector_records(
        self,
        *,
        agent_id: str,
        target_agent_id: str | None,
        object_key: str,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Authorized record options for the reference selector endpoint.

        The caller's access to the source feature is the router's job; this
        re-checks the target feature's ACL and lists only records the caller
        may read there.
        """
        effective_target = (target_agent_id or agent_id).strip()
        target_row = self._registry.get_row(effective_target)
        if target_row is None or not is_feature_agent(str(getattr(target_row, "kind", ""))):
            raise ReferenceValueError("reference target feature not found")
        if not can_use_feature(
            target_row,
            user_id=self._user_id,
            acl_repo=self._acl_repo,
            user_repo=self._repos.user_repo,
            org_unit_repo=self._repos.org_unit_repo,
        ):
            raise PermissionError("reference target feature is not accessible")
        workspace = self._registry.workspace_for_agent(effective_target)
        if workspace is None:
            raise ReferenceValueError("reference target workspace is not reachable")
        from octop.infra.agents import feature_data as fd

        loaded = await fd.load_schema(workspace)
        if loaded.schema is None:
            raise ReferenceValueError("reference target feature has no data schema")
        fd.object_definition(loaded.schema, object_key)
        records, total = self._repos.feature_data_repo.list_records(
            agent_id=effective_target,
            object_key=object_key,
            user_id=self._user_id,
            query=query,
            limit=limit,
            offset=offset,
        )
        return (
            [
                {
                    "id": record.id,
                    "title": record.title,
                    "object_key": record.object_key,
                    "agent_id": record.agent_id,
                }
                for record in records
            ],
            total,
        )

    # -- internals ----------------------------------------------------------
    @property
    def _acl_repo(self) -> Any:
        return self._repos.resource_acl_repo

    def _feature_visible(self, agent_id: str) -> bool:
        cached = self._feature_cache.get(agent_id)
        if cached is None:
            row = self._registry.get_row(agent_id)
            cached = bool(
                row is not None
                and can_use_feature(
                    row,
                    user_id=self._user_id,
                    acl_repo=self._acl_repo,
                    user_repo=self._repos.user_repo,
                    org_unit_repo=self._repos.org_unit_repo,
                )
            )
            self._feature_cache[agent_id] = cached
        return cached

    def _require_record_ref(self, *, agent_id: str, field: Mapping[str, Any], value: str) -> None:
        target_agent_id = str(field.get("target_agent_id") or agent_id)
        target_object = str(field.get("target_object") or "")
        if not target_object:
            raise ReferenceValueError(
                f"{field.get('key')} must declare target_object for record references"
            )
        record = self._record_state(target_agent_id, target_object, value)
        if record is None:
            raise ReferenceValueError("referenced record is not available")
        if not can_read_record(record, user_id=self._user_id):
            raise ReferenceValueError("referenced record is not available")

    def _record_state(
        self, target_agent_id: str, target_object: str, record_id: str
    ) -> FeatureDataRecordRow | None:
        key = f"{target_agent_id}|{target_object}|{record_id}"
        if key in self._record_cache:
            return self._record_cache[key]
        record: FeatureDataRecordRow | None = None
        target_row = self._registry.get_row(target_agent_id)
        if (
            target_row is not None
            and is_feature_agent(str(getattr(target_row, "kind", "")))
            and self._feature_visible(target_agent_id)
        ):
            candidate: FeatureDataRecordRow | None = self._repos.feature_data_repo.get_record(
                record_id
            )
            if (
                candidate is not None
                and candidate.agent_id == target_agent_id
                and candidate.object_key == target_object
                and candidate.deleted_at is None
            ):
                record = candidate
        self._record_cache[key] = record
        return record

    def _describe_record_ref(
        self, *, agent_id: str, field: Mapping[str, Any], value: Any
    ) -> dict[str, Any]:
        target_agent_id = str(field.get("target_agent_id") or agent_id)
        target_object = str(field.get("target_object") or "")
        info: dict[str, Any] = {
            "kind": "record_ref",
            "available": False,
        }
        if not isinstance(value, str):
            return info
        record = self._record_state(target_agent_id, target_object, value)
        if record is None or not can_read_record(record, user_id=self._user_id):
            return info
        info.update(
            {
                "available": True,
                "record_id": value,
                "agent_id": target_agent_id,
                "object_key": target_object,
                "title": record.title,
            }
        )
        return info

    # knowledge ---------------------------------------------------------
    def _pin_knowledge_ref(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(value, Mapping) and value.get("version"):
            return dict(value)
        document = self._repos.knowledge_repo.get_document(value["document_id"])
        if document is None or document.kb_id != value["kb_id"] or document.is_dir:
            raise ReferenceValueError("referenced knowledge document is not available")
        pinned = dict(value)
        pinned["version"] = str(document.content_hash or "")
        if not pinned["version"]:
            raise ReferenceValueError("referenced knowledge document has no content version")
        return pinned

    def _require_knowledge_ref(self, value: Mapping[str, Any]) -> None:
        if self._knowledge_state(value["kb_id"], value["document_id"]) is None:
            raise ReferenceValueError("referenced knowledge document is not available")

    def _knowledge_state(self, kb_id: str, document_id: str) -> dict[str, Any] | None:
        key = f"{kb_id}|{document_id}"
        if key in self._knowledge_cache:
            return self._knowledge_cache[key]
        state = self._resolve_knowledge_state(kb_id, document_id)
        self._knowledge_cache[key] = state
        return state

    def _resolve_knowledge_state(self, kb_id: str, document_id: str) -> dict[str, Any] | None:
        """Available target facts, or ``None`` — the one access implementation.

        Mirrors ``KnowledgeService.document_access_scope`` for a single base:
        an unreadable base restricts every document in it, folder/file ACLs
        narrow the rest, and delegation edges re-add exactly the documents
        they currently authorize.
        """
        knowledge = self._repos.knowledge_repo
        role, unit_keys = knowledge.scope_for_user(self._user_id)
        base_entry = knowledge.acl_entry(kb_id)
        if not may_read_knowledge_base(
            base_entry, user_id=self._user_id, role=role, unit_keys=unit_keys
        ):
            return None
        base = next((item for item in knowledge.list_all() if item.id == kb_id), None)
        if base is None:
            return None
        document = knowledge.get_document(document_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            return None
        documents = knowledge.list_documents(kb_id)
        entries = knowledge.document_acl_entries()
        restricted, readable, _writable = document_access_ids(
            documents, entries, user_id=self._user_id, role=role, unit_keys=unit_keys
        )
        rights = KnowledgeDelegationService(self._repos.db).derived_rights(
            kb_id, actor_user_id=self._user_id
        )
        for doc_id, right in rights.items():
            if right.read:
                readable.add(str(doc_id))
        if not may_read_document(document_id, restricted=restricted, readable=readable):
            return None
        return {
            "title": document.title or document.display_path,
            "path": document.display_path,
            "content_hash": str(document.content_hash or ""),
            "filename": document.filename,
            "content_type": document.content_type,
            "preview_ready": document.status == "ready",
        }

    def _describe_knowledge_ref(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {"kind": "knowledge_ref", "available": False}
        info: dict[str, Any] = {
            "kind": "knowledge_ref",
            "available": False,
        }
        state = self._knowledge_state(str(value.get("kb_id")), str(value.get("document_id")))
        if state is None:
            return info
        if "version" in value:
            info["version"] = value.get("version")
        if "page" in value:
            info["page"] = value.get("page")
        if "layout" in value:
            info["layout"] = value.get("layout")
        current = state["content_hash"]
        pinned = value.get("version")
        stale = bool(pinned and current and pinned != current)
        original_version = pinned or current
        original_url = DOCUMENT_FILE_URL_TEMPLATE.format(
            kb_id=value.get("kb_id"), document_id=value.get("document_id")
        )
        if original_version:
            original_url = f"{original_url}?version={original_version}"
        current_original_url = DOCUMENT_FILE_URL_TEMPLATE.format(
            kb_id=value.get("kb_id"), document_id=value.get("document_id")
        )
        if current:
            current_original_url = f"{current_original_url}?version={current}"
        preview_url = DOCUMENT_PREVIEW_URL_TEMPLATE.format(
            kb_id=value.get("kb_id"), document_id=value.get("document_id")
        )
        info.update(
            {
                "available": True,
                "kb_id": value.get("kb_id"),
                "document_id": value.get("document_id"),
                "stale": stale,
                "current_version": current,
                "title": state["title"],
                "path": state["path"],
            }
        )
        if stale:
            # The pinned bytes may no longer exist; offer only the checked current version.
            info["current_original_url"] = current_original_url
            if state["preview_ready"]:
                info["current_preview_url"] = preview_url
        else:
            if original_version:
                info["original_url"] = original_url
            if state["preview_ready"]:
                info["preview_url"] = preview_url
        return info

    def _history_value(
        self, agent_id: str, field: Mapping[str, Any], value: Any
    ) -> str | dict[str, Any] | None:
        """One redacted-or-authorized history slot for a reference value."""
        if value is None:
            return None
        if isinstance(value, str) and not value:
            return ""
        if str(field.get("type")) == "record_ref":
            info = self._describe_record_ref(agent_id=agent_id, field=field, value=value)
        else:
            info = self._describe_knowledge_ref(value)
        if info.get("available"):
            if isinstance(value, str):
                return value
            if isinstance(value, Mapping):
                return dict(value)
        return {"redacted": True}


__all__ = [
    "FeatureReferenceService",
    "ReferenceValueError",
    "ref_fields",
    "validate_reference_shape",
]
