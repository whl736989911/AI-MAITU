"""Runtime knowledge-base scope — one rule for "which bases may I mount?".

Cron deliveries, the agent composer defaults, and the knowledge service each
ask that question at a different moment. They answer it with
``sharing.allowed_resource_ids``, so a new access rule lands in one place
instead of in every caller's own ``list_all() if is_admin else list_visible()``
branch — the role fork was redundant (``can_access`` rule 1 is the admin
bypass) and every new rule had to be remembered at each fork.

The single-base read check that needs more than an id stays here: what is
knowledge-base specific about it is the caller's question, not the rule.

Pure and IO-free: the caller loads the ``resource_type='knowledge_base'``
entries and resolves the actor's scope with
``ResourceAclRepo.scope_for_user`` (role + unit chain).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from octop.infra.sharing import AclEntry, can_access

__all__ = ["may_read_document", "may_read_knowledge_base", "readable_documents"]


def may_read_knowledge_base(
    entry: AclEntry | None,
    *,
    user_id: int,
    role: str,
    unit_keys: Collection[str],
) -> bool:
    """Read check for a single base whose entry the caller already holds.

    ``None`` denies: access is granted by a row, never by its absence.

    ``unit_keys`` is the actor's unit chain, so a base shared with a department
    reaches the members of its sub-departments (``sharing.can_access``).
    """
    return entry is not None and can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys)


def may_read_document(
    document_id: str,
    *,
    restricted: Collection[str],
    readable: Collection[str],
) -> bool:
    """Read check for one document, given the file-level entries.

    ``restricted`` is every document that carries an entry of its own and
    ``readable`` the subset ``sharing.can_access`` allows this actor. A document
    outside ``restricted`` wears no file-level rule, so the base's entry — which
    the caller has already checked — is the whole answer.
    """
    return document_id not in restricted or document_id in readable


def readable_documents(
    documents: Iterable[Any], *, restricted: Collection[str], readable: Collection[str]
) -> list[Any]:
    """The documents of *documents* this actor may read (design §14).

    The list form of :func:`may_read_document`, so a listing, a search result,
    and a chat citation cannot answer differently about the same file.
    """
    return [
        document
        for document in documents
        if may_read_document(document.id, restricted=restricted, readable=readable)
    ]
