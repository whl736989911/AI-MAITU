"""Channels follow an authorization change onto the wire (design §2.3/§2.4).

A channel type key is not only a gate on the routes that configure a channel:
已存在的通道在类型权限被撤销后立即停止运行, and 已经配置的能力，在权限撤销后也不能继续运行.
The routes that *write* permissions are therefore the places that must tell the
gateway to re-derive what is running — the gateway owns the live channels, so
the decision stays there (:meth:`octop.infra.gateway.gateway.Gateway.reconcile_channel_permissions`)
and these two helpers only name the accounts a write can have reached.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from octop.infra.users.scope import subtree_keys


async def sync_channel_runtime(server: Any, *, user_ids: Iterable[int]) -> None:
    """Re-derive the runtime state of the channels ``user_ids`` own.

    Call this right after a write that can change an effective
    ``channel_<kind>`` — an account's permissions, denies, role or department,
    or a department's grant set. A server whose runtime is not booted has no
    channels running, so there is nothing to re-derive.
    """
    runtime = getattr(server, "app_runtime", None)
    if runtime is None:
        return
    await runtime.gateway.reconcile_channel_permissions(user_ids)


def unit_member_ids(server: Any, unit_key: str) -> list[int]:
    """Accounts whose department is ``unit_key`` or one of its sub-departments.

    A department's grants reach its sub-departments (design §2.1), so a change
    on ``unit_key`` can change the effective keys of every member below it — the
    same subtree :func:`octop.infra.users.permissions.unit_permissions` walks when
    it resolves them.
    """
    repos = server.services.repos
    units = subtree_keys(repos.org_unit_repo, unit_key)
    if not units:
        return []
    return [
        int(row.id)
        for row in repos.user_repo.list(include_disabled=True)
        if getattr(row, "org_unit", None) in units
    ]
