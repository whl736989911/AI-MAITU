"""Central domain access policy for features and their business records.

One module answers every "may this caller ...?" question about a feature:

* reaching the feature at all (the agent/feature ``resource_acl`` union),
* managing it (system administrator, its enterprise administrator, or the
  draft author before publication),
* reading or writing one business record (private stays creator-only, shared
  records belong to their responsible user and the feature's managers).

The API routers and the agent tools both call these functions, so an
enterprise manager has exactly the same reach over published-feature records
through either surface — and never over a private draft, which only its
creator can read. Nothing here imports from ``octop.api``; the HTTP layer is a
caller like any other.
"""

from __future__ import annotations

from typing import Any

from octop.infra.agents.kinds import feature_id_of_agent, is_feature_agent
from octop.infra.sharing import AclEntry, can_access
from octop.infra.users.identity import Role, resolved_role
from octop.infra.users.scope import scope_for


def feature_access_entries(row: Any, *, acl_repo: Any) -> list[AclEntry]:
    """Every ACL entry that decides this feature: its agent and feature rows.

    A feature *is* its agent (``feat-<feature_id>``), and grants may be filed
    under either resource type; grants only widen access, so the verdict is the
    union of both entries.
    """
    entries = [entry for entry in (acl_repo.get("agent", row.agent_id),) if entry is not None]
    if is_feature_agent(row.kind):
        feature_id = feature_id_of_agent(row.agent_id)
        if feature_id is not None:
            feature_entry = acl_repo.get("feature", feature_id)
            if feature_entry is not None:
                entries.append(feature_entry)
    return entries


def can_use_feature(
    row: Any, *, user_id: int, acl_repo: Any, user_repo: Any, org_unit_repo: Any
) -> bool:
    """Use the same active-caller, manager and explicit-ACL policy on every surface."""
    if user_id <= 0 or row is None:
        return False
    user = user_repo.get(user_id)
    if user is None or int(getattr(user, "disabled", 0)):
        return False
    if is_feature_manager(
        row, user_id=user_id, user_repo=user_repo, org_unit_repo=org_unit_repo
    ) or (
        is_feature_agent(row.kind)
        and getattr(row, "enterprise_unit_key", None) is None
        and feature_in_enterprise_scope(
            row, user_id=user_id, user_repo=user_repo, org_unit_repo=org_unit_repo
        )
    ):
        return True
    entries = feature_access_entries(row, acl_repo=acl_repo)
    if not entries:
        return False
    role, unit_keys = acl_repo.scope_for_user(user_id)
    return any(
        can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys) for entry in entries
    )


def feature_in_enterprise_scope(
    row: Any, *, user_id: int, user_repo: Any, org_unit_repo: Any
) -> bool:
    """Whether *user_id* is an enterprise administrator over the feature's enterprise.

    Published features carry ``enterprise_unit_key``; a feature whose owner has
    no enterprise falls back to the owner's current org unit. An unbound
    enterprise administrator reaches every unit.
    """
    user = user_repo.get(user_id) if user_id > 0 else None
    if user is None or resolved_role(user) != Role.ENTERPRISE_ADMIN.value:
        return False
    unit_key = getattr(row, "enterprise_unit_key", None)
    if unit_key is None:
        owner = user_repo.get(int(row.user_id)) if row.user_id is not None else None
        if owner is None:
            return False
        unit_key = owner.org_unit
    return scope_for(user, org_unit_repo).covers_unit(unit_key)


def is_feature_manager(row: Any, *, user_id: int, user_repo: Any, org_unit_repo: Any) -> bool:
    """Whether *user_id* manages the feature: admin, enterprise admin, or draft owner.

    Non-feature agents keep the plain owner/admin rule. Published features
    (``enterprise_unit_key`` set) are managed by their enterprise's administrators,
    not by authorship alone; an unpublished draft stays with its author. This
    policy governs API ``can_manage`` and every record write check.
    """
    user = user_repo.get(user_id) if user_id > 0 else None
    if user is None:
        return False
    owns = row.user_id is not None and int(row.user_id) == user_id
    if not is_feature_agent(row.kind):
        return resolved_role(user) == Role.ADMIN.value or owns
    if resolved_role(user) == Role.ADMIN.value:
        return True
    if getattr(row, "enterprise_unit_key", None) is not None:
        return feature_in_enterprise_scope(
            row, user_id=user_id, user_repo=user_repo, org_unit_repo=org_unit_repo
        )
    return owns


def can_read_record(record: Any, *, user_id: int) -> bool:
    """Private records remain creator-only, including for enterprise managers."""
    if user_id <= 0 or record is None:
        return False
    if record.visibility == "private":
        return bool(user_id == record.created_by)
    return True


def can_write_record(
    record: Any, row: Any, *, user_id: int, user_repo: Any, org_unit_repo: Any
) -> bool:
    """Whether *user_id* may change this record.

    A private record stays creator-only. A shared record belongs to its
    effective writer — the responsible user once handed over, else the creator
    fallback for records that predate the column — and to the feature's
    managers. Handover deliberately *removes* the original creator's write
    when they are neither the new responsible user nor a manager:
    ``created_by`` is audit history, not a standing authorization.
    """
    if record is None or user_id <= 0:
        return False
    if record.visibility == "private":
        return bool(record.created_by == user_id)
    if effective_writer(record) == user_id:
        return True
    return is_feature_manager(
        row, user_id=user_id, user_repo=user_repo, org_unit_repo=org_unit_repo
    )


def effective_writer(record: Any) -> int:
    """The account a shared record currently belongs to for writes/notifications.

    ``responsible_user_id`` is set at creation and moved only by an authorized
    handover; records from before the column existed fall back to their
    creator, which keeps pre-existing behavior bit for bit.
    """
    responsible = getattr(record, "responsible_user_id", None)
    return int(responsible) if responsible else int(record.created_by)


__all__ = [
    "can_read_record",
    "can_use_feature",
    "can_write_record",
    "effective_writer",
    "feature_access_entries",
    "feature_in_enterprise_scope",
    "is_feature_manager",
]
