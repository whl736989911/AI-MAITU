"""Runtime knowledge-base and document permission scope."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from octop.infra.sharing import AclEntry, can_access, can_write

__all__ = [
    "document_access_ids",
    "may_read_document",
    "may_read_knowledge_base",
    "readable_documents",
]


def may_read_knowledge_base(
    entry: AclEntry | None,
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
) -> bool:
    """Read check for a single base whose entry the caller already holds."""
    return entry is not None and can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys)


def document_access_ids(
    documents: Iterable[Any],
    entries: Mapping[str, AclEntry],
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
) -> tuple[set[str], set[str], set[str]]:
    """Return restricted, readable and writable document ids for one base.

    ACLs on explicit ancestor folders narrow descendants. A matching explicit
    user/unit/role grant on a file may bypass those folder ACLs, but never the
    file's own ACL or the knowledge-base ACL (checked by the caller).
    """
    rows = list(documents)
    by_path = {str(row.path): row for row in rows}
    restricted: set[str] = set()
    readable: set[str] = set()
    writable: set[str] = set()

    for row in rows:
        doc_id = str(row.id)
        path = str(row.path)
        own = entries.get(doc_id)
        ancestors: list[AclEntry] = []
        parent = path.rpartition("/")[0]
        while parent:
            ancestor = by_path.get(parent)
            if ancestor is not None:
                acl = entries.get(str(ancestor.id))
                if acl is not None:
                    ancestors.append(acl)
            parent = parent.rpartition("/")[0]

        if own is not None or ancestors:
            restricted.add(doc_id)
        own_read = own is None or can_access(own, user_id=user_id, role=role, unit_keys=unit_keys)
        explicit_file_grant = bool(
            not row.is_dir
            and own is not None
            and (
                ("user", str(user_id)) in own.grants
                or any(("unit", unit) in own.grants for unit in unit_keys)
                or ("role", role) in own.grants
            )
        )
        ancestors_read = explicit_file_grant or all(
            can_access(acl, user_id=user_id, role=role, unit_keys=unit_keys) for acl in ancestors
        )
        if own_read and ancestors_read:
            readable.add(doc_id)

        own_write = own is None or can_write(own, user_id=user_id, role=role, unit_keys=unit_keys)
        ancestors_write = explicit_file_grant or all(
            can_write(acl, user_id=user_id, role=role, unit_keys=unit_keys) for acl in ancestors
        )
        if own_write and ancestors_write:
            writable.add(doc_id)

    return restricted, readable, writable


def may_read_document(
    document_id: str,
    *,
    restricted: Collection[str],
    readable: Collection[str],
) -> bool:
    """Read check for one document, given its effective ACL scope."""
    return document_id not in restricted or document_id in readable


def readable_documents(
    documents: Iterable[Any], *, restricted: Collection[str], readable: Collection[str]
) -> list[Any]:
    """The documents of *documents* this actor may read."""
    return [
        document
        for document in documents
        if may_read_document(document.id, restricted=restricted, readable=readable)
    ]
