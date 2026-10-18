"""Keep market operations inside existing HITL policy across harness rebuilds."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from harness_agent.security.models import SecurityPolicy

from octop.infra.agents.security.hitl_session import HitlSessionPolicyStore, apply_session_bypass

_MARKET_TOOLS = frozenset({"inspect_market_skill", "install_market_skill"})


def inherit_market_approvals(
    interrupt_on: dict[str, Any] | None, allowed_decisions: Sequence[str]
) -> dict[str, Any] | None:
    if not interrupt_on:
        return interrupt_on
    rules = dict(interrupt_on)
    approval = {"allowed_decisions": list(allowed_decisions)}
    if any(name in rules for name in ("execute", "bash")):
        rules.setdefault("inspect_market_skill", dict(approval))
    if any(name in rules for name in ("write_file", "edit_file", "execute", "bash")):
        rules.setdefault("install_market_skill", dict(approval))
    return rules


@dataclass
class _MarketAwareSecurityPolicy(SecurityPolicy):
    session_store: Callable[[], HitlSessionPolicyStore | None] = field(
        default=lambda: None, repr=False, compare=False
    )

    def resolve_interrupt_on(self) -> dict[str, Any] | None:
        rules = inherit_market_approvals(
            super().resolve_interrupt_on(), self.hitl.allowed_decisions
        )
        if not rules:
            return rules
        # Harness reapplies its global policy during registration and rebuilds.
        # Wrap only the new operations here; other tools retain existing behavior.
        market_rules = {name: rule for name, rule in rules.items() if name in _MARKET_TOOLS}
        wrapped = apply_session_bypass(market_rules, self.session_store())
        if wrapped:
            rules.update(wrapped)
        return rules


def market_aware_security_policy(
    policy: SecurityPolicy, session_store: Callable[[], HitlSessionPolicyStore | None]
) -> SecurityPolicy:
    return _MarketAwareSecurityPolicy(
        **{item.name: getattr(policy, item.name) for item in fields(SecurityPolicy)},
        session_store=session_store,
    )
