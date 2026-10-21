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
from octop.infra.db.repos.knowledge import KnowledgeRepo
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


def _acl_state_key(entry: AclEntry) -> tuple[object, ...]:
    """Compare governed ACL semantics independent of JSON/grant ordering."""
    return (
        entry.owner_user_id,
        entry.visibility,
        entry.unit_key,
        entry.permission,
        tuple(sorted(entry.grants)),
    )


def _logged_acl_state_key(change: AclChangeRow, payload: str) -> tuple[object, ...]:
    return _acl_state_key(
        decode_acl_state(
            payload,
            resource_type=change.resource_type,
            resource_id=change.resource_id,
            version=0,
        )
    )


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
        self._knowledge = KnowledgeRepo(db)

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
        """Apply a pending change only if its recorded base is still current."""
        self._require_admin(approver)
        with self._db.transaction() as conn:
            change = self._repo.get_change(change_id, conn=conn)
            if change is None:
                raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
            if change.status != CHANGE_PENDING:
                raise ValueError(
                    f"change {change_id!r} is {change.status!r}, not {CHANGE_PENDING!r}"
                )
            current = self._repo.get(change.resource_type, change.resource_id, conn=conn)
            current_version = current.version if current is not None else 0
            if current is not None:
                state_matches = _acl_state_key(current) == _logged_acl_state_key(
                    change, change.before_json
                )
            else:
                baseline = decode_acl_state(
                    change.before_json,
                    resource_type=change.resource_type,
                    resource_id=change.resource_id,
                    version=0,
                )
                state_matches = (
                    baseline.visibility == VISIBILITY_PRIVATE
                    and baseline.unit_key is None
                    and baseline.permission == "read"
                    and not baseline.grants
                    and _acl_state_key(baseline)
                    == _logged_acl_state_key(change, change.before_json)
                )
            if current_version != change.from_version or not state_matches:
                raise ValueError(f"change {change_id!r} is stale; its base ACL has changed")
            version = current_version + 1
            after = decode_acl_state(
                change.after_json,
                resource_type=change.resource_type,
                resource_id=change.resource_id,
                version=version,
            )
            if current is not None:
                after = replace(after, owner_user_id=current.owner_user_id)
            self._repo.upsert(after, conn=conn)
            self._repo.set_change_status(
                change_id,
                CHANGE_APPLIED,
                to_version=version,
                expected_status=CHANGE_PENDING,
                conn=conn,
            )
            if change.rollback_of is not None:
                self._repo.set_change_status(
                    change.rollback_of,
                    CHANGE_ROLLED_BACK,
                    expected_status=CHANGE_APPLIED,
                    conn=conn,
                )

    def reject(self, change_id: str, approver: int, reason: str | None = None) -> None:
        """Refuse a pending change (admins only); nothing is applied."""
        change = self._require_change(change_id)
        self._require_admin(approver)
        if change.status != CHANGE_PENDING:
            raise ValueError(f"change {change_id!r} is {change.status!r}, not {CHANGE_PENDING!r}")
        self._repo.set_change_status(change_id, CHANGE_REJECTED, reason=reason)

    def rollback(self, change_id: str, actor: int) -> str:
        """Request restoration through the normal ACL approval policy.

        Returns the newly created reversal change id. The original change is
        marked rolled back only when that reversal actually takes effect.
        """
        actor_role = self._role_of(actor)
        initial = self._require_change(change_id)
        if actor_role != _ADMIN_ROLE and (
            (
                self._is_published_feature(initial.resource_type, initial.resource_id)
                and not self._manages_published_feature(
                    actor, initial.resource_type, initial.resource_id
                )
            )
            or (
                not self._is_published_feature(initial.resource_type, initial.resource_id)
                and initial.actor_user_id != actor
            )
        ):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                f"user {actor} may not roll back change {change_id!r}",
            )
        with self._db.transaction() as conn:
            change = self._repo.get_change(change_id, conn=conn)
            if change is None:
                raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
            if change.status != CHANGE_APPLIED:
                raise ValueError(
                    f"change {change_id!r} is {change.status!r}, not {CHANGE_APPLIED!r}"
                )
            if self._repo.has_pending_rollback(change_id, conn=conn):
                raise ValueError(f"change {change_id!r} already has a pending rollback")
            current = self._repo.get(change.resource_type, change.resource_id, conn=conn)
            if current is None:
                raise OctopError(
                    ErrorCode.NOT_FOUND,
                    f"{change.resource_type} {change.resource_id!r} has no ACL entry to roll back",
                )
            if current.version != change.to_version or _acl_state_key(
                current
            ) != _logged_acl_state_key(change, change.after_json):
                raise ValueError(f"change {change_id!r} is stale and cannot be rolled back")
            version = current.version + 1
            restored = decode_acl_state(
                change.before_json,
                resource_type=change.resource_type,
                resource_id=change.resource_id,
                version=version,
            )
            restored = replace(restored, owner_user_id=current.owner_user_id)
            status = CHANGE_PENDING if requires_approval(current, restored) else CHANGE_APPLIED
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
                status=status,
                reason=f"rollback of {change.id}",
                created_at=now_ts(),
                rollback_of=change.id,
            )
            self._repo.insert_change(rollback, conn=conn)
            if status == CHANGE_APPLIED:
                self._repo.upsert(restored, conn=conn)
                self._repo.set_change_status(
                    change_id,
                    CHANGE_ROLLED_BACK,
                    expected_status=CHANGE_APPLIED,
                    conn=conn,
                )
        return rollback.id

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _first_owner(
        self, resource_type: str, resource_id: str, requested: int | None
    ) -> int | None:
        """Who owns a resource that has no ACL row of its own yet.

        Most resources use the owner supplied by their creator for their first
        share. Knowledge documents inherit the knowledge base owner's governance
        identity, and ``feature`` entries are filed under a feature id while a
        feature is its agent (``feat-<feature_id>``). Resolve those owners from
        their authoritative rows instead of letting the first ACL requester
        claim ownership.
        """
        if resource_type == "knowledge_document":
            document = self._knowledge.get_document(resource_id)
            if document is None:
                raise OctopError(
                    ErrorCode.NOT_FOUND, f"knowledge document {resource_id!r} not found"
                )
            base = self._knowledge.get_base(document.kb_id)
            return None if base is None else base.owner_user_id
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
