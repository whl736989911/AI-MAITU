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
entries and resolves the actor's scope with ``sharing.user_scope``.
"""

from __future__ import annotations

from octop.infra.sharing import AclEntry, can_access

__all__ = ["may_read_knowledge_base"]


def may_read_knowledge_base(
    entry: AclEntry | None,
    *,
    user_id: int,
    role: str,
    unit_key: str | None,
) -> bool:
    """Read check for a single base whose entry the caller already holds.

    ``None`` denies: access is granted by a row, never by its absence.
    """
    return entry is not None and can_access(
        entry, user_id=user_id, role=role, unit_key=unit_key
    )
