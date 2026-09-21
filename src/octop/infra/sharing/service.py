"""Sharing governance — apply ACL changes, gate org-wide ones, roll them back.

Every state change goes through here so it is classified by impact scope and
written to ``resource_acl_changes`` in the same transaction as the ACL row:

- ``self`` / ``unit`` — applied immediately, recorded in the change log
- ``org`` (widening to ``public``) — recorded as ``pending_approval`` and *not*
  applied until an admin approves it

Approval is deliberately narrow: only a change that reaches the whole
organization waits, because that is the one that cannot be walked back by
looking at a bounded group of people.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.resource_acl import (
    CHANGE_APPLIED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    CHANGE_ROLLED_BACK,
    AclChangeRow,
    ResourceAclRepo,
    decode_acl_state,
    encode_acl_state,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.sharing import (
    RESOURCE_TYPES,
    VISIBILITY_PRIVATE,
    AclEntry,
    impact_scope,
    requires_approval,
)
from octop.infra.utils.ulid import new_ulid

_ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of :meth:`SharingService.apply_change`."""

    change_id: str
    status: str
    impact_scope: str
    entry: AclEntry  # the entry in force after the call

    @property
    def applied(self) -> bool:
        return self.status == CHANGE_APPLIED


class SharingService:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self._repo = ResourceAclRepo(db)
        self._users = UserRepo(db)

    # ------------------------------------------------------------------
    # Change pipeline
    # ------------------------------------------------------------------

    def apply_change(
        self,
        actor: int,
        resource_type: str,
        resource_id: str,
        new_entry: AclEntry,
        reason: str | None = None,
    ) -> ChangeResult:
        """Apply ``new_entry`` now, or park it for approval when it goes org-wide.

        ``actor`` is the acting user id. Only the resource's owner or an admin
        may change its access; the owner is never taken from ``new_entry`` for a
        resource that already has an ACL row (ownership is not a shareable
        property). A resource with no row yet is treated as private to the entry's
        owner, so the impact of its first change is classified correctly.
        """
        self._require_resource_type(resource_type)
        actor_role = self._role_of(actor)
        before = self._repo.get(resource_type, resource_id)
        if before is None:
            before = AclEntry(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_user_id=new_entry.owner_user_id,
                visibility=VISIBILITY_PRIVATE,
                unit_key=None,
                version=0,
            )
        if actor_role != _ADMIN_ROLE and before.owner_user_id != actor:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                f"user {actor} may not change access for {resource_type} {resource_id!r}",
            )
        after = replace(
            new_entry,
            resource_type=resource_type,
            resource_id=resource_id,
            owner_user_id=before.owner_user_id,
            version=before.version + 1,
        )
        impact = impact_scope(before, after)
        status = CHANGE_PENDING if requires_approval(before, after) else CHANGE_APPLIED
        change = AclChangeRow(
            id=new_ulid(),
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=actor,
            from_version=before.version,
            to_version=after.version,
            before_json=encode_acl_state(before),
            after_json=encode_acl_state(after),
            impact_scope=impact,
            status=status,
            reason=reason,
            created_at=now_ts(),
        )
        with self._db.transaction() as conn:
            self._repo.insert_change(change, conn=conn)
            if status == CHANGE_APPLIED:
                self._repo.upsert(after, conn=conn)
        return ChangeResult(
            change_id=change.id,
            status=status,
            impact_scope=impact,
            entry=after if status == CHANGE_APPLIED else before,
        )

    def approve(self, change_id: str, approver: int) -> None:
        """Apply a pending change (admins only).

        The recorded ``after`` state is re-applied at ``current version + 1`` so
        a change parked for a while cannot rewind numbers written since; the
        change row keeps the version it actually landed as.
        """
        change = self._require_change(change_id)
        self._require_admin(approver)
        if change.status != CHANGE_PENDING:
            raise ValueError(f"change {change_id!r} is {change.status!r}, not {CHANGE_PENDING!r}")
        current = self._repo.get(change.resource_type, change.resource_id)
        version = (current.version if current is not None else 0) + 1
        after = decode_acl_state(
            change.after_json,
            resource_type=change.resource_type,
            resource_id=change.resource_id,
            version=version,
        )
        with self._db.transaction() as conn:
            self._repo.upsert(after, conn=conn)
            self._repo.set_change_status(change_id, CHANGE_APPLIED, to_version=version, conn=conn)

    def reject(self, change_id: str, approver: int, reason: str | None = None) -> None:
        """Refuse a pending change (admins only); nothing is applied."""
        change = self._require_change(change_id)
        self._require_admin(approver)
        if change.status != CHANGE_PENDING:
            raise ValueError(f"change {change_id!r} is {change.status!r}, not {CHANGE_PENDING!r}")
        self._repo.set_change_status(change_id, CHANGE_REJECTED, reason=reason)

    def rollback(self, change_id: str, actor: int) -> None:
        """Restore the state a change replaced, at ``current version + 1``.

        The old version number is never reused, and the restored state is itself
        logged as a fresh applied change — so the state being discarded survives
        in the log and can be re-applied by rolling that change back.
        """
        change = self._require_change(change_id)
        actor_role = self._role_of(actor)
        if actor_role != _ADMIN_ROLE and change.actor_user_id != actor:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                f"user {actor} may not roll back change {change_id!r}",
            )
        if change.status != CHANGE_APPLIED:
            raise ValueError(f"change {change_id!r} is {change.status!r}, not {CHANGE_APPLIED!r}")
        current = self._repo.get(change.resource_type, change.resource_id)
        if current is None:
            raise OctopError(
                ErrorCode.NOT_FOUND,
                f"{change.resource_type} {change.resource_id!r} has no ACL entry to roll back",
            )
        version = current.version + 1
        restored = decode_acl_state(
            change.before_json,
            resource_type=change.resource_type,
            resource_id=change.resource_id,
            version=version,
        )
        rollback = AclChangeRow(
            id=new_ulid(),
            resource_type=change.resource_type,
            resource_id=change.resource_id,
            actor_user_id=actor,
            from_version=current.version,
            to_version=version,
            before_json=encode_acl_state(current),
            after_json=encode_acl_state(restored),
            impact_scope=impact_scope(current, restored),
            status=CHANGE_APPLIED,
            reason=f"rollback of {change.id}",
            created_at=now_ts(),
        )
        with self._db.transaction() as conn:
            self._repo.upsert(restored, conn=conn)
            self._repo.insert_change(rollback, conn=conn)
            self._repo.set_change_status(change_id, CHANGE_ROLLED_BACK, conn=conn)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_resource_type(self, resource_type: str) -> None:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(f"unknown resource_type {resource_type!r}")

    def _require_change(self, change_id: str) -> AclChangeRow:
        change = self._repo.get_change(change_id)
        if change is None:
            raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
        return change

    def _require_admin(self, user_id: int) -> None:
        if self._role_of(user_id) != _ADMIN_ROLE:
            raise OctopError(
                ErrorCode.FORBIDDEN, f"user {user_id} is not allowed to approve sharing changes"
            )

    def _role_of(self, user_id: int) -> str:
        user = self._users.get(user_id)
        return user.role if user is not None else ""
