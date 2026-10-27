"""Explicit multilevel knowledge delegation — graph resolution and lifecycle.

Delegation shares *individual* users' existing access, one document at a time.
The graph is a per-document set of edges ``(grantor → grantee, can_delegate)``:

* Authority always bottoms out at the **direct ACL** entries of a document's
  chain (base, ancestor folders, the document itself): an individual ``user``
  grant that carries the per-grant ``can_delegate`` switch is a delegation
  *root*. Visibility alone (public / unit) never delegates, and management
  rights belong to the KB owner or a system administrator only.
* An edge conveys the grantor's **current** effective permission, so a later
  ACL narrowing automatically shrinks everything downstream — every hop is
  resolved at read time and can never exceed its source.
* ``can_delegate`` rides on edges: a recipient may only issue further edges for
  the same document when an incoming edge (or their own direct grant) says so.
  Recipients cannot self-enable it, and an edge that would close a cycle is
  rejected.
* Edges are hard-deleted when their supporting authority disappears (revocation
  cascade, computed to a fixpoint inside the caller's transaction), so a
  re-enabled root switch cannot resurrect revoked branches, and truly
  independent authorizations are preserved.
* Users whose own individual grant or delegation edge was removed receive one
  durable ``knowledge_access_notifications`` row per affected resource in the
  *same* transaction, with ``access_retained`` distinguishing real loss from an
  independent grant that keeps them reading.

Everything a read path needs (lists, downloads, previews, search, agents,
permission metadata) resolves through :meth:`derived_rights` /
:meth:`effective_rights` — there is deliberately no second, UI-only notion of
delegated access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_delegations import (
    KnowledgeAccessNotificationRepo,
    KnowledgeDelegationRepo,
)
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.relpath import path_parent
from octop.infra.knowledge.scope import document_access_ids
from octop.infra.sharing import AclEntry, can_access, can_write, grant_delegates

EVENT_AUTHORIZATION_REVOKED = "authorization_revoked"
EVENT_DELEGATION_REVOKED = "delegation_revoked"

_SOURCE_ACL_CHANGE = "acl_change"
_SOURCE_DELEGATION_DELETE = "delegation_delete"
_SOURCE_DELEGATION_UPDATE = "delegation_update"

#: Pseudo document id under which :meth:`_KbContext.direct` evaluates the base
#: entry alone — the chain of a non-document resource is just the base row.
_BASE_DOC = "__knowledge_base__"


@dataclass(frozen=True)
class DelegatedRight:
    """What one user may do with one resource. ``write`` implies ``read``;
    ``can_delegate`` is the authority to issue further edges, never a content
    permission of its own."""

    read: bool = False
    write: bool = False
    can_delegate: bool = False

    @property
    def any_access(self) -> bool:
        return self.read or self.write

    def merged(self, other: DelegatedRight) -> DelegatedRight:
        return DelegatedRight(
            read=self.read or other.read,
            write=self.write or other.write,
            can_delegate=self.can_delegate or other.can_delegate,
        )


@dataclass(frozen=True)
class _RevocationEvent:
    """One prepared notification row: who lost (or kept) what, and why."""

    recipient_user_id: int
    kb_id: str
    resource_id: str
    resource_path: str
    event_kind: str
    access_retained: bool
    before: DelegatedRight
    after: DelegatedRight
    delegation_ids: tuple[str, ...]
    grantor_user_id: int | None


class _KbContext:
    """Per-KB resolution workspace.

    When built inside a transaction, every read goes through that connection so
    cascade decisions see the not-yet-committed ACL/edge state. ``*_override``
    lets callers evaluate the *pre*-mutation world for before/after payloads.
    """

    def __init__(
        self,
        service: KnowledgeDelegationService,
        kb_id: str,
        *,
        conn: Any | None = None,
        entries_override: dict[str, AclEntry | None] | None = None,
        edges_override: dict[str, list[Any]] | None = None,
    ) -> None:
        self.service = service
        self.kb_id = kb_id
        self.conn = conn
        self.base = service._knowledge.get_base(kb_id, conn=conn)
        self.base_entry = service._acl.get("knowledge_base", kb_id, conn=conn)
        if entries_override and kb_id in entries_override:
            override = entries_override[kb_id]
            self.base_entry = override
        self.documents: dict[str, Any] = {
            row.id: row for row in service._knowledge.list_documents(kb_id, conn=conn)
        }
        self.documents_by_path = {str(row.path): row for row in self.documents.values()}
        self.entries: dict[str, AclEntry] = {
            entry.resource_id: entry
            for entry in service._acl.list_for_type(
                "knowledge_document", resource_ids=set(self.documents), conn=conn
            )
        }
        if entries_override:
            for resource_id, entry in entries_override.items():
                if entry is None:
                    self.entries.pop(resource_id, None)
                else:
                    self.entries[resource_id] = entry
        self.edges_override = edges_override
        self._scope_cache: dict[int, tuple[str, tuple[str, ...]]] = {}
        self._chain_cache: dict[str, list[AclEntry]] = {}
        self._direct_cache: dict[tuple[str, int], DelegatedRight] = {}
        self._document_scope_cache: dict[int, tuple[set[str], set[str], set[str]]] = {}

    @property
    def owner_user_id(self) -> int | None:
        return self.base.owner_user_id if self.base is not None else None

    def scope(self, user_id: int) -> tuple[str, tuple[str, ...]]:
        """``(role, unit_keys)`` for *user_id*, resolved in this context."""
        cached = self._scope_cache.get(user_id)
        if cached is None:
            cached = self.service._acl.scope_for_user(user_id, conn=self.conn)
            self._scope_cache[user_id] = cached
        return cached

    def is_manager(self, user_id: int) -> bool:
        role, _ = self.scope(user_id)
        if role == "admin":
            return True
        return self.owner_user_id is not None and self.owner_user_id == user_id

    def edges(self, doc_id: str) -> list[Any]:
        if self.edges_override is not None and doc_id in self.edges_override:
            return self.edges_override[doc_id]
        return self.service._delegations.list_for_document(self.kb_id, doc_id, conn=self.conn)

    def chain(self, doc_id: str) -> list[AclEntry]:
        """The document's ACL chain: base, ancestor folders, own entry."""
        cached = self._chain_cache.get(doc_id)
        if cached is not None:
            return cached
        chain: list[AclEntry] = []
        if self.base_entry is not None:
            chain.append(self.base_entry)
        document = self.documents.get(doc_id)
        if document is not None:
            parent = path_parent(str(document.path))
            ancestors: list[Any] = []
            while parent:
                ancestor = self.documents_by_path.get(parent)
                if ancestor is not None:
                    ancestors.append(ancestor)
                parent = path_parent(parent)
            for ancestor in reversed(ancestors):
                entry = self.entries.get(ancestor.id)
                if entry is not None:
                    chain.append(entry)
            own = self.entries.get(doc_id)
            if own is not None:
                chain.append(own)
        self._chain_cache[doc_id] = chain
        return chain

    def direct(self, doc_id: str, user_id: int) -> DelegatedRight:
        """Rights straight from the ACL chain, ignoring delegation edges.

        ``can_delegate`` here means a delegation *root*: an individual user
        grant whose switch is on, on an entry the user can actually reach.
        Managers hold it implicitly — they are the governance principals.
        """
        key = (doc_id, user_id)
        cached = self._direct_cache.get(key)
        if cached is not None:
            return cached
        user = self.service._users.get(user_id, conn=self.conn)
        role, unit_keys = self.scope(user_id)
        base = self.base_entry
        if (
            user is None
            or user.disabled
            or base is None
            or not can_access(base, user_id=user_id, role=role, unit_keys=unit_keys)
        ):
            right = DelegatedRight()
            self._direct_cache[key] = right
            return right
        if doc_id == _BASE_DOC:
            read = True
            write = can_write(base, user_id=user_id, role=role, unit_keys=unit_keys)
        else:
            scoped = self._document_scope_cache.get(user_id)
            if scoped is None:
                scoped = document_access_ids(
                    self.documents.values(),
                    self.entries,
                    user_id=user_id,
                    role=role,
                    unit_keys=unit_keys,
                )
                self._document_scope_cache[user_id] = scoped
            read = doc_id in scoped[1]
            write = (
                read
                and doc_id in scoped[2]
                and can_write(base, user_id=user_id, role=role, unit_keys=unit_keys)
            )
        can_delegate = read and (
            self.is_manager(user_id)
            or any(
                ("user", str(user_id)) in grant_delegates(entry)
                and can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys)
                for entry in self.chain(doc_id)
            )
        )
        right = DelegatedRight(read=read, write=write, can_delegate=can_delegate)
        self._direct_cache[key] = right
        return right


class KnowledgeDelegationService:
    """Resolve the delegation graph and run its lifecycle end to end."""

    def __init__(self, db: Any) -> None:
        """Take the database pool directly, mirroring ``SharingService``.

        Repositories are stateless views over the pool, so building them here
        keeps the service usable from both the request side (via the services
        bundle) and the sharing pipeline (which only holds a pool).
        """
        self._db = db
        self._knowledge = KnowledgeRepo(db)
        self._acl = ResourceAclRepo(db)
        self._delegations = KnowledgeDelegationRepo(db)
        self._notifications = KnowledgeAccessNotificationRepo(db)
        self._users = UserRepo(db)

    # ------------------------------------------------------------------
    # Resolution — the one implementation every access path shares
    # ------------------------------------------------------------------

    def _context(
        self,
        kb_id: str,
        *,
        conn: Any | None = None,
        entries_override: dict[str, AclEntry | None] | None = None,
        edges_override: dict[str, list[Any]] | None = None,
    ) -> _KbContext:
        return _KbContext(
            self,
            kb_id,
            conn=conn,
            entries_override=entries_override,
            edges_override=edges_override,
        )

    def derived_rights_for(self, ctx: _KbContext, doc_id: str, user_id: int) -> DelegatedRight:
        """Rights *user_id* reaches through the delegation edges of one doc.

        Memoized DFS over incoming edges with a visited set: pure cycles add no
        authority (there is nothing to re-root through), and a hop grants at
        most its grantor's current effective permission.
        """
        edges = ctx.edges(doc_id)
        incoming: dict[int, list[Any]] = {}
        for edge in edges:
            incoming.setdefault(edge.grantee_user_id, []).append(edge)
        memo: dict[int, DelegatedRight] = {}
        visiting: set[int] = set()

        def effective(user: int) -> DelegatedRight:
            if user in memo:
                return memo[user]
            if user in visiting:
                return DelegatedRight()
            if not ctx.direct(_BASE_DOC, user).read:
                return DelegatedRight()
            visiting.add(user)
            right = ctx.direct(doc_id, user)
            for edge in incoming.get(user, ()):
                source = effective(edge.grantor_user_id)
                if source.read and source.can_delegate:
                    right = DelegatedRight(
                        read=True,
                        write=right.write or source.write,
                        can_delegate=right.can_delegate or edge.can_delegate,
                    )
            visiting.discard(user)
            memo[user] = right
            return right

        return effective(user_id)

    def derived_rights(self, kb_id: str, *, actor_user_id: int) -> dict[str, DelegatedRight]:
        """Per-document rights *actor_user_id* holds through delegation edges."""
        ctx = self._context(kb_id)
        if ctx.base is None:
            return {}
        by_doc: dict[str, list[Any]] = {}
        for edge in self._delegations.list_for_kb(kb_id):
            by_doc.setdefault(edge.document_id, []).append(edge)
        rights: dict[str, DelegatedRight] = {}
        for doc_id in by_doc:
            if doc_id not in ctx.documents:
                continue
            right = self.derived_rights_for(ctx, doc_id, actor_user_id)
            if right.any_access or right.can_delegate:
                rights[doc_id] = right
        return rights

    def effective_rights(self, kb_id: str, doc_id: str, *, actor_user_id: int) -> DelegatedRight:
        """Direct ACL rights merged with delegation-derived rights."""
        ctx = self._context(kb_id)
        if ctx.base is None or doc_id not in ctx.documents:
            return DelegatedRight()
        return ctx.direct(doc_id, actor_user_id).merged(
            self.derived_rights_for(ctx, doc_id, actor_user_id)
        )

    # ------------------------------------------------------------------
    # Delegation listing and mutations
    # ------------------------------------------------------------------

    def _require_document(self, kb_id: str, doc_id: str) -> Any:
        base = self._knowledge.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        document = self._knowledge.get_document(doc_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            raise LookupError("knowledge document not found")
        return document

    def _principal_options(self, kb_id: str) -> list[dict[str, Any]]:
        """Active recipients with current access to the knowledge base."""
        ctx = self._context(kb_id)
        return [
            {"id": row.id, "username": row.username, "label": row.display_name or row.username}
            for row in self._users.list(include_disabled=False)
            if ctx.direct(_BASE_DOC, row.id).read
        ]

    def list_delegations(self, kb_id: str, doc_id: str, *, actor_user_id: int) -> dict[str, Any]:
        """Listing payload per the fixed HTTP contract."""
        self._require_document(kb_id, doc_id)
        rights = self.effective_rights(kb_id, doc_id, actor_user_id=actor_user_id)
        ctx = self._context(kb_id)
        manager = ctx.is_manager(actor_user_id)
        if not manager and not rights.can_delegate:
            raise OctopError(
                ErrorCode.KNOWLEDGE_DELEGATION_FORBIDDEN,
                "delegation management requires delegation authority or KB management",
            )
        edges = self._delegations.list_for_document(kb_id, doc_id)
        if not manager:
            edges = [edge for edge in edges if edge.grantor_user_id == actor_user_id]
        labels = {
            row.id: row.display_name or row.username
            for row in self._users.list(include_disabled=True)
            if row.id in {edge.grantee_user_id for edge in edges}
        }
        entry = self._acl.get("knowledge_document", doc_id)
        return {
            "version": entry.version if entry is not None else 0,
            "can_delegate": rights.can_delegate,
            "permission": "write" if rights.write else "read",
            "grants": [
                {
                    "id": edge.id,
                    "grantor_user_id": edge.grantor_user_id,
                    "grantee_user_id": edge.grantee_user_id,
                    "grantee_label": labels.get(edge.grantee_user_id, ""),
                    "can_delegate": edge.can_delegate,
                    "can_revoke": manager or edge.grantor_user_id == actor_user_id,
                }
                for edge in edges
            ],
            "principals": self._principal_options(kb_id),
        }

    def _would_cycle(
        self, kb_id: str, doc_id: str, grantor: int, grantee: int, *, conn: Any | None = None
    ) -> bool:
        """True when *grantee* already reaches *grantor* downstream."""
        if grantee == grantor:
            return True
        outgoing: dict[int, list[int]] = {}
        for edge in self._delegations.list_for_document(kb_id, doc_id, conn=conn):
            outgoing.setdefault(edge.grantor_user_id, []).append(edge.grantee_user_id)
        seen: set[int] = set()
        stack = [grantee]
        while stack:
            user = stack.pop()
            if user == grantor:
                return True
            if user in seen:
                continue
            seen.add(user)
            stack.extend(outgoing.get(user, ()))
        return False

    def upsert_delegation(
        self,
        kb_id: str,
        doc_id: str,
        *,
        actor_user_id: int,
        grantee_user_id: int,
        can_delegate: bool = False,
    ) -> dict[str, Any]:
        """Add or update only the actor's own edge to *grantee_user_id*.

        Authorization is rechecked inside the transaction; a chain toggle to
        ``can_delegate=False`` cascades the recipient's now-unsupported edges
        and notifies them, atomically with the update.
        """
        self._require_document(kb_id, doc_id)
        with self._db.transaction() as conn:
            grantee = self._users.get(grantee_user_id)
            if grantee is None or grantee.disabled:
                raise LookupError("user not found")
            ctx = self._context(kb_id, conn=conn)
            if not ctx.direct(_BASE_DOC, grantee_user_id).read:
                raise LookupError("user not found")
            target = ctx.documents.get(doc_id)
            if target is None or target.is_dir:
                raise LookupError("knowledge document not found")
            pre_edges = list(ctx.edges(doc_id))
            pre_ctx = self._context(kb_id, conn=conn, edges_override={doc_id: pre_edges})
            direct = ctx.direct(doc_id, actor_user_id)
            derived = self.derived_rights_for(ctx, doc_id, actor_user_id)
            actor_right = direct.merged(derived)
            if not actor_right.read or not actor_right.can_delegate:
                raise OctopError(
                    ErrorCode.KNOWLEDGE_DELEGATION_FORBIDDEN,
                    "delegation requires read and delegation authority on this document",
                )
            if grantee_user_id == actor_user_id:
                raise OctopError(
                    ErrorCode.KNOWLEDGE_DELEGATION_FORBIDDEN, "delegation to self is not allowed"
                )
            if self._would_cycle(kb_id, doc_id, actor_user_id, grantee_user_id, conn=conn):
                raise OctopError(
                    ErrorCode.KNOWLEDGE_DELEGATION_FORBIDDEN,
                    "delegation cycle: recipient already passes access to the grantor",
                )
            self._delegations.upsert(
                kb_id=kb_id,
                document_id=doc_id,
                grantor_user_id=actor_user_id,
                grantee_user_id=grantee_user_id,
                can_delegate=can_delegate,
                conn=conn,
            )
            post_ctx = self._context(kb_id, conn=conn)
            removed = self._strip_unsupported(post_ctx, doc_id)
            if removed:
                self._emit_events(
                    conn,
                    self._edge_events(
                        post_ctx,
                        removed,
                        source=_SOURCE_DELEGATION_UPDATE,
                        pre_ctx=pre_ctx,
                    ),
                    source=_SOURCE_DELEGATION_UPDATE,
                    change_id=None,
                )
        return self.list_delegations(kb_id, doc_id, actor_user_id=actor_user_id)

    def revoke_delegation(
        self, kb_id: str, doc_id: str, delegation_id: str, *, actor_user_id: int
    ) -> None:
        """Delete one edge (issuer or KB ACL manager) and cascade descendants.

        Cross-resource ids fail closed: a delegation_id that exists but belongs
        to another document or KB is indistinguishable from a missing one.
        """
        self._require_document(kb_id, doc_id)
        with self._db.transaction() as conn:
            edge = self._delegations.get_by_delegation_id(delegation_id, conn=conn)
            if edge is None or edge.kb_id != kb_id or edge.document_id != doc_id:
                raise LookupError("knowledge delegation not found")
            ctx = self._context(kb_id, conn=conn)
            target = ctx.documents.get(doc_id)
            if target is None or target.is_dir:
                raise LookupError("knowledge document not found")
            if not ctx.is_manager(actor_user_id) and edge.grantor_user_id != actor_user_id:
                raise OctopError(
                    ErrorCode.KNOWLEDGE_DELEGATION_FORBIDDEN,
                    "only the issuer or a KB manager may revoke this delegation",
                )
            pre_edges = list(ctx.edges(doc_id))
            pre_ctx = self._context(kb_id, conn=conn, edges_override={doc_id: pre_edges})
            self._delegations.delete(delegation_id, conn=conn)
            removed = self._strip_unsupported(ctx, doc_id)
            self._emit_events(
                conn,
                self._edge_events(
                    ctx,
                    [edge, *removed],
                    source=_SOURCE_DELEGATION_DELETE,
                    pre_ctx=pre_ctx,
                ),
                source=_SOURCE_DELEGATION_DELETE,
                change_id=None,
            )

    # ------------------------------------------------------------------
    # Cascade — shared by revocation and ACL changes
    # ------------------------------------------------------------------

    def _strip_unsupported(self, ctx: _KbContext, doc_id: str) -> list[Any]:
        """Fixpoint-delete edges whose grantor lost delegatable authority.

        An edge survives only while its grantor still holds delegation
        authority through the *remaining* roots and edges, so a revoked branch
        can never silently re-root through an unrelated right, and genuinely
        independent authorizations are preserved.
        """
        alive = {edge.id: edge for edge in ctx.edges(doc_id)}
        removed: list[Any] = []

        def supported(grantor: int, visiting: frozenset[int]) -> bool:
            if not ctx.direct(_BASE_DOC, grantor).read:
                return False
            if ctx.direct(doc_id, grantor).can_delegate:
                return True
            for edge in alive.values():
                if not edge.can_delegate or edge.grantee_user_id != grantor:
                    continue
                if edge.grantor_user_id in visiting:
                    continue
                if supported(edge.grantor_user_id, visiting | {grantor}):
                    return True
            return False

        while True:
            dead = [
                edge
                for edge in alive.values()
                if not ctx.direct(_BASE_DOC, edge.grantee_user_id).read
                or not supported(edge.grantor_user_id, frozenset())
            ]
            if not dead:
                break
            for edge in dead:
                alive.pop(edge.id, None)
                removed.append(edge)
        for edge in removed:
            self._delegations.delete(edge.id, conn=ctx.conn)
        return removed

    def cascade_after_acl_change(
        self,
        conn: Any,
        resource_type: str,
        resource_id: str,
        before: AclEntry | None,
        after: AclEntry | None,
        *,
        change_id: str | None = None,
    ) -> None:
        """Reconcile the graph after an ACL row changed, in that transaction.

        Called from :class:`~octop.infra.sharing.service.SharingService` for
        every applied knowledge ACL change (immediate, approved, or rolled
        back). Removed individual user grants notify their holders directly;
        edges that lost support cascade and notify their grantees. Identical
        re-saves produce an empty diff and therefore no events at all.
        """
        direct_removed = self._removed_user_grants(before, after)
        if resource_type == "knowledge_base":
            kb_id = resource_id
        elif resource_type == "knowledge_document":
            document = self._knowledge.get_document(resource_id, conn=conn)
            if document is None:
                return
            kb_id = document.kb_id
        else:
            return
        post_ctx = self._context(kb_id, conn=conn, entries_override={resource_id: after})
        if post_ctx.base is None:
            return
        if resource_type == "knowledge_base":
            affected = {
                edge.document_id for edge in self._delegations.list_for_kb(kb_id, conn=conn)
            }
        else:
            document = post_ctx.documents.get(resource_id)
            if document is None:
                return
            prefix = f"{document.path}/"
            affected = {
                doc_id
                for doc_id, row in post_ctx.documents.items()
                if doc_id == resource_id or str(row.path).startswith(prefix)
            }
        # Delegation edges are untouched by an ACL change, so the pre-strip
        # edge snapshot *is* the pre-mutation world for the payload's
        # before-rights; combined with the pre-change entry override it answers
        # exactly what the recipients could do before.
        pre_edges = {doc_id: list(post_ctx.edges(doc_id)) for doc_id in sorted(affected)}
        pre_ctx = self._context(
            kb_id,
            conn=conn,
            entries_override={resource_id: before},
            edges_override=pre_edges,
        )
        removed_by_doc: dict[str, list[Any]] = {}
        for doc_id in sorted(affected):
            removed_by_doc[doc_id] = self._strip_unsupported(post_ctx, doc_id)
        if not (direct_removed or any(removed_by_doc.values())):
            return
        events: list[_RevocationEvent] = []
        for removed in removed_by_doc.values():
            events.extend(
                self._edge_events(post_ctx, removed, source=_SOURCE_ACL_CHANGE, pre_ctx=pre_ctx)
            )
        for user_id in sorted(direct_removed):
            events.append(
                self._direct_grant_event(pre_ctx, post_ctx, resource_type, resource_id, user_id)
            )
        self._emit_events(conn, events, source=_SOURCE_ACL_CHANGE, change_id=change_id)

    # ------------------------------------------------------------------
    # Event construction and emission
    # ------------------------------------------------------------------

    @staticmethod
    def _removed_user_grants(before: AclEntry | None, after: AclEntry | None) -> set[int]:
        """Individual users whose *direct* grant disappeared in this change."""
        if before is None:
            return set()
        after_pairs = set(after.grants) if after is not None else set()
        removed: set[int] = set()
        for kind, grantee in set(before.grants) - after_pairs:
            if kind == "user" and grantee.isdigit():
                removed.add(int(grantee))
        return removed

    def _rights(self, ctx: _KbContext, doc_id: str, user_id: int) -> DelegatedRight:
        return ctx.direct(doc_id, user_id).merged(self.derived_rights_for(ctx, doc_id, user_id))

    def _kb_rights(self, ctx: _KbContext, user_id: int) -> DelegatedRight:
        """Base-level rights plus anything still reachable in the KB."""
        base = ctx.direct(_BASE_DOC, user_id)
        if base.any_access:
            return base
        for doc_id in ctx.documents:
            merged = self._rights(ctx, doc_id, user_id)
            if merged.any_access:
                return DelegatedRight(
                    read=True, write=merged.write, can_delegate=merged.can_delegate
                )
        return base

    def _resource_path(self, ctx: _KbContext, resource_id: str) -> str:
        if resource_id == ctx.kb_id:
            return "/"
        document = ctx.documents.get(resource_id)
        return str(document.path) if document is not None else ""

    def _edge_events(
        self,
        ctx: _KbContext,
        removed: list[Any],
        *,
        source: str,
        pre_ctx: _KbContext | None = None,
    ) -> list[_RevocationEvent]:
        events: list[_RevocationEvent] = []
        for edge in removed:
            before = (
                self._rights(pre_ctx, edge.document_id, edge.grantee_user_id)
                if pre_ctx is not None
                else DelegatedRight()
            )
            after = self._rights(ctx, edge.document_id, edge.grantee_user_id)
            events.append(
                _RevocationEvent(
                    recipient_user_id=edge.grantee_user_id,
                    kb_id=ctx.kb_id,
                    resource_id=edge.document_id,
                    resource_path=self._resource_path(ctx, edge.document_id),
                    event_kind=(
                        EVENT_AUTHORIZATION_REVOKED
                        if source == _SOURCE_ACL_CHANGE
                        else EVENT_DELEGATION_REVOKED
                    ),
                    access_retained=after.any_access,
                    before=before,
                    after=after,
                    delegation_ids=(edge.id,),
                    grantor_user_id=edge.grantor_user_id,
                )
            )
        return events

    def _direct_grant_event(
        self,
        pre_ctx: _KbContext,
        post_ctx: _KbContext,
        resource_type: str,
        resource_id: str,
        user_id: int,
    ) -> _RevocationEvent:
        if resource_type == "knowledge_base":
            before = pre_ctx.direct(_BASE_DOC, user_id)
            after = self._kb_rights(post_ctx, user_id)
        else:
            before = self._rights(pre_ctx, resource_id, user_id)
            after = self._rights(post_ctx, resource_id, user_id)
        return _RevocationEvent(
            recipient_user_id=user_id,
            kb_id=post_ctx.kb_id,
            resource_id=resource_id,
            resource_path=self._resource_path(post_ctx, resource_id),
            event_kind=EVENT_AUTHORIZATION_REVOKED,
            access_retained=after.any_access,
            before=before,
            after=after,
            delegation_ids=(),
            grantor_user_id=None,
        )

    def _emit_events(
        self,
        conn: Any,
        events: list[_RevocationEvent],
        *,
        source: str,
        change_id: str | None,
    ) -> None:
        """Persist one row per (resource, recipient), ACL causes winning ties.

        Rows are written inside the caller's transaction: if the mutation rolls
        back, no notification survives; if it commits, the inbox shows exactly
        the authorities that were removed. Rejected mutations never reach this
        point, so they never emit.
        """
        merged: dict[tuple[str, int], _RevocationEvent] = {}
        for event in events:
            key = (event.resource_id, event.recipient_user_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = event
                continue
            kept = existing
            if (
                existing.event_kind != EVENT_AUTHORIZATION_REVOKED
                and event.event_kind == EVENT_AUTHORIZATION_REVOKED
            ):
                kept = _RevocationEvent(
                    recipient_user_id=existing.recipient_user_id,
                    kb_id=existing.kb_id,
                    resource_id=existing.resource_id,
                    resource_path=existing.resource_path,
                    event_kind=EVENT_AUTHORIZATION_REVOKED,
                    access_retained=existing.access_retained,
                    before=existing.before,
                    after=existing.after,
                    delegation_ids=tuple(
                        dict.fromkeys(existing.delegation_ids + event.delegation_ids)
                    ),
                    grantor_user_id=existing.grantor_user_id,
                )
            merged[key] = kept
        for event in merged.values():
            payload = {
                "source": source,
                "change_id": change_id,
                "delegation_ids": list(event.delegation_ids),
                "grantor_user_id": event.grantor_user_id,
                "before": {
                    "read": event.before.read,
                    "write": event.before.write,
                    "can_delegate": event.before.can_delegate,
                },
                "after": {
                    "read": event.after.read,
                    "write": event.after.write,
                    "can_delegate": event.after.can_delegate,
                },
            }
            self._notifications.insert(
                recipient_user_id=event.recipient_user_id,
                kb_id=event.kb_id,
                resource_id=event.resource_id,
                resource_path=event.resource_path,
                event_kind=event.event_kind,
                access_retained=event.access_retained,
                payload=payload,
                conn=conn,
            )

    # ------------------------------------------------------------------
    # Notification inbox — no KB/module/feature gates by design
    # ------------------------------------------------------------------

    def list_notifications(
        self,
        *,
        actor_user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        locale: str = "zh",
    ) -> dict[str, Any]:
        """The actor's own durable inbox. Authentication is the only gate."""
        rows = self._notifications.list_for_recipient(
            actor_user_id, unread_only=unread_only, limit=limit
        )
        return {
            "items": [
                {
                    "id": row.id,
                    "kb_id": row.kb_id,
                    "resource_id": row.resource_id,
                    "resource_path": row.resource_path,
                    "event_kind": row.event_kind,
                    "access_retained": row.access_retained,
                    "read_at": row.read_at,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "unread_count": self._notifications.count_unread(actor_user_id),
        }

    def mark_notification_read(self, notification_id: str, *, actor_user_id: int) -> None:
        """Mark the actor's own row read; foreign or absent ids stay not found."""
        row = self._notifications.get(notification_id)
        if row is None or row.recipient_user_id != actor_user_id:
            raise LookupError("knowledge access notification not found")
        self._notifications.mark_read(notification_id)
