"""Unit tests for the knowledge-base read check.

The rule itself lives in ``sharing.can_access`` — the list form of it is
``sharing.allowed_resource_ids`` (covered in ``tests/unit/sharing/test_acl_rules.py``
and, end to end, in ``tests/unit/connectors/test_scope_parity.py``). What is
knowledge-base specific here is the single-entry denial: no ACL row means no
access, not open access.
"""

from __future__ import annotations

from octop.infra.knowledge.scope import may_read_knowledge_base
from octop.infra.sharing import AclEntry


def _entry(
    kb_id: str,
    *,
    owner_user_id: int | None,
    visibility: str = "private",
    unit_key: str | None = None,
    grants: tuple[tuple[str, str], ...] = (),
) -> AclEntry:
    return AclEntry(
        resource_type="knowledge_base",
        resource_id=kb_id,
        owner_user_id=owner_user_id,
        visibility=visibility,
        unit_key=unit_key,
        version=1,
        grants=grants,
    )


def test_may_read_knowledge_base_denies_a_missing_entry() -> None:
    """No ACL row means no access, not open access."""
    denied = may_read_knowledge_base(None, user_id=7, role="user", unit_key=None)
    denied_admin_less = may_read_knowledge_base(None, user_id=7, role="admin", unit_key=None)

    assert denied is False
    assert denied_admin_less is False


def test_may_read_knowledge_base_follows_the_entry() -> None:
    private = _entry("kb1", owner_user_id=7)
    published = _entry("kb1", owner_user_id=7, visibility="public")

    assert may_read_knowledge_base(private, user_id=99, role="user", unit_key=None) is False
    assert may_read_knowledge_base(published, user_id=99, role="user", unit_key=None) is True
    assert may_read_knowledge_base(private, user_id=7, role="user", unit_key=None) is True
