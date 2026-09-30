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

from octop.infra.agents.kinds import feature_agent_id_for, is_feature_agent
from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
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
from octop.infra.users.identity import Role
from octop.infra.users.scope import scope_for
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
        self._agents = AgentRepo(db)
        self._units = OrgUnitRepo(db)

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

        ``actor`` is the acting user id. An owner or system administrator may
        change access; published features additionally belong to their scoped
        enterprise administrators. Ownership cannot be changed by a share.
        """
        self._require_resource_type(resource_type)
        actor_role = self._role_of(actor)
        before = self._repo.get(resource_type, resource_id)
        if before is None:
            before = AclEntry(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_user_id=self._first_owner(
                    resource_type, resource_id, new_entry.owner_user_id
                ),
                visibility=VISIBILITY_PRIVATE,
                unit_key=None,
                version=0,
            )
        if (
            actor_role != _ADMIN_ROLE
            and before.owner_user_id != actor
            and not self._manages_published_feature(actor, resource_type, resource_id)
        ):
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
        if current is not None:
            after = replace(after, owner_user_id=current.owner_user_id)
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
        if actor_role != _ADMIN_ROLE and (
            (
                self._is_published_feature(change.resource_type, change.resource_id)
                and not self._manages_published_feature(
                    actor, change.resource_type, change.resource_id
                )
            )
            or (
                not self._is_published_feature(change.resource_type, change.resource_id)
                and change.actor_user_id != actor
            )
        ):
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
        restored = replace(restored, owner_user_id=current.owner_user_id)
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

    def _first_owner(
        self, resource_type: str, resource_id: str, requested: int | None
    ) -> int | None:
        """Who owns a resource that has no ACL row of its own yet.

        Every resource the ACL can hold is given its row when it is created, so
        this is the first-share case: the request's own owner is the answer, which
        is how a resource with no entry at all gets its first one.

        ``feature`` is the exception, and the reason this is a method. A feature's
        entry is filed under the *feature* id while a feature *is* its agent
        (``feat-<feature_id>``), so its owner is on that agent's row and nowhere
        the ACL table can see. Without reading it there, whoever asked first would
        own the entry — and because the entry decides the feature's agent, that is
        a grant of somebody else's feature to the asker. A feature with no agent
        row (or one whose author is gone) resolves to no owner, which only an
        admin may then change.
        """
        if resource_type == "feature":
            row = self._agents.get(feature_agent_id_for(resource_id))
            return None if row is None else row.user_id
        if resource_type == "agent":
            row = self._agents.get(resource_id)
            if row is not None and is_feature_agent(row.kind):
                return row.user_id
        return requested

    def _is_published_feature(self, resource_type: str, resource_id: str) -> bool:
        if resource_type == "feature":
            agent_id = feature_agent_id_for(resource_id)
        elif resource_type == "agent":
            agent_id = resource_id
        else:
            return False
        row = self._agents.get(agent_id)
        return (
            row is not None and is_feature_agent(row.kind) and row.enterprise_unit_key is not None
        )

    def _manages_published_feature(self, actor: int, resource_type: str, resource_id: str) -> bool:
        if not self._is_published_feature(resource_type, resource_id):
            return False
        user = self._users.get(actor)
        if user is None or user.role != Role.ENTERPRISE_ADMIN.value:
            return False
        agent_id = feature_agent_id_for(resource_id) if resource_type == "feature" else resource_id
        row = self._agents.get(agent_id)
        return row is not None and scope_for(user, self._units).covers_unit(row.enterprise_unit_key)

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
