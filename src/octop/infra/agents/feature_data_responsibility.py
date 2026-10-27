"""Recipient resolution and authorized reassignment for feature data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from octop.infra.agents.feature_access import can_read_record, can_use_feature, is_feature_manager
from octop.infra.users.identity import resolved_role


def resolve_reminder_recipients(
    *,
    record: Any,
    obj: Mapping[str, Any],
    agent: Any,
    user_repo: Any,
    acl_repo: Any,
    unit_repo: Any | None = None,
) -> set[int]:
    """Resolve eligible recipients at dispatch time, never trusting stale IDs.

    ACL and record visibility are re-evaluated for every candidate. ``unit_repo``
    may expose ``list_users_in_unit`` (usernames); user rows are resolved through
    the active user repository, so disabled/deleted accounts are excluded.
    """
    users = user_repo.list()
    by_id = {int(user.id): user for user in users if not int(getattr(user, "disabled", 0))}
    policy = obj.get("reminder_recipients")
    if not isinstance(policy, Mapping):
        policy = {"creator": True, "owner": True}
    responsible_id = getattr(record, "responsible_user_id", None)
    creator_id = int(record.created_by)
    if responsible_id is None:
        responsible_id = creator_id
    recipients: set[int] = set()
    if policy.get("creator", False):
        recipients.add(int(responsible_id))
    if policy.get("owner", False):
        owner_id = getattr(agent, "user_id", None)
        if owner_id is not None:
            recipients.add(int(owner_id))
    if policy.get("responsible", False):
        recipients.add(int(responsible_id))
    values = _record_values(record)
    for field_key in policy.get("user_fields", []):
        value = values.get(field_key)
        if type(value) is int:
            recipients.add(value)
    if unit_repo is not None:
        for field_key in policy.get("unit_fields", []):
            unit_key = values.get(field_key)
            if not isinstance(unit_key, str) or not unit_key:
                continue
            for username in _unit_member_usernames(unit_repo, unit_key):
                user = user_repo.get_by_username(username)
                if user is not None and not int(getattr(user, "disabled", 0)):
                    recipients.add(int(user.id))
    roles = set(policy.get("role_keys", []))
    if roles:
        recipients.update(int(user.id) for user in users if resolved_role(user) in roles)
    eligible: set[int] = set()
    for user_id in recipients:
        if user_id not in by_id:
            continue
        if not can_read_record(record, user_id=user_id):
            continue
        if not can_use_feature(
            agent,
            user_id=user_id,
            acl_repo=acl_repo,
            user_repo=user_repo,
            org_unit_repo=unit_repo,
        ):
            continue
        eligible.add(user_id)
    return eligible


def handover_feature_records(
    *,
    repo: Any,
    feature_row: Any,
    agent_id: str,
    from_user_id: int,
    to_user_id: int,
    actor_user_id: int,
    user_repo: Any,
    org_unit_repo: Any,
    acl_repo: Any,
    object_key: str | None = None,
) -> dict[str, Any]:
    """Authorize and hand over shared records using the atomic repository operation."""
    if actor_user_id <= 0 or from_user_id <= 0 or to_user_id <= 0 or from_user_id == to_user_id:
        raise ValueError("handover requires positive user ids and distinct transfer users")
    actor = user_repo.get(actor_user_id)
    if actor is None or int(getattr(actor, "disabled", 0)):
        raise PermissionError("handover actor must be active")
    if not is_feature_manager(
        feature_row,
        user_id=actor_user_id,
        user_repo=user_repo,
        org_unit_repo=org_unit_repo,
    ):
        raise PermissionError("only feature managers may hand over records")
    target = user_repo.get(to_user_id)
    if target is None or int(getattr(target, "disabled", 0)):
        raise ValueError("handover target must be active")
    if not can_use_feature(
        feature_row,
        user_id=to_user_id,
        acl_repo=acl_repo,
        user_repo=user_repo,
        org_unit_repo=org_unit_repo,
    ):
        raise PermissionError("handover target cannot access this feature")
    transfer = getattr(repo, "handover_records", None)
    if not callable(transfer):
        raise RuntimeError("feature data repository does not support atomic handover")
    record_ids = transfer(
        agent_id=agent_id,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        actor_user_id=actor_user_id,
        object_key=object_key,
    )
    record_ids = list(record_ids)
    return {"transferred_count": len(record_ids), "record_ids": record_ids}


def _unit_member_usernames(unit_repo: Any, unit_key: str) -> set[str]:
    """Resolve a selected unit and its descendants using the organization tree."""
    list_children = getattr(unit_repo, "list_children", None)
    if not callable(list_children):
        return set(unit_repo.list_users_in_unit(unit_key))
    pending = [unit_key]
    visited: set[str] = set()
    usernames: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        usernames.update(str(name) for name in unit_repo.list_users_in_unit(current))
        pending.extend(str(child) for child in list_children(current) if str(child) not in visited)
    return usernames


def _record_values(record: Any) -> dict[str, Any]:
    values = getattr(record, "values", None)
    return dict(values) if isinstance(values, Mapping) else {}


__all__ = ["handover_feature_records", "resolve_reminder_recipients"]
