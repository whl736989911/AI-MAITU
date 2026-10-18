"""Bootstrap the default expert and built-in general-assistant feature."""

from __future__ import annotations

import json
import logging
from typing import Any

from octop.infra.agents.experts.catalog import ExpertCatalog, build_create_spec_from_expert
from octop.infra.agents.kinds import KIND_FEATURE, feature_agent_id_for
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import current_license
from octop.infra.sharing import VISIBILITY_PUBLIC
from octop.infra.utils.locale import normalize_locale

logger = logging.getLogger(__name__)

DEFAULT_EXPERT_ID = "general-assistant"
SETUP_DEFAULT_AGENT_ID = "main"
BUILTIN_GENERAL_ASSISTANT_FEATURE_ID = "general-assistant"
_SETUP_PENDING_AGENTS_KEY = "setup.default_agents_pending"
BUILTIN_GENERAL_ASSISTANT_FEATURE_AGENT_ID = feature_agent_id_for(
    BUILTIN_GENERAL_ASSISTANT_FEATURE_ID
)


async def ensure_builtin_general_assistant_feature(
    registry: Any,
    catalog: ExpertCatalog | None,
    resource_acl_repo: Any,
    *,
    owner_user_id: int,
    locale: str = "zh",
) -> Any | None:
    """Create the instance-wide assistant feature once, seeded from the expert template."""
    if registry.get_row(BUILTIN_GENERAL_ASSISTANT_FEATURE_AGENT_ID) is not None:
        return None
    if catalog is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "expert catalog not available")
    expert = catalog.get(DEFAULT_EXPERT_ID)
    if expert is None:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{DEFAULT_EXPERT_ID} expert template missing",
        )

    loc = normalize_locale(locale)
    summary = expert.summary
    if loc == "zh":
        name = "通用助手"
        description = summary.description_zh or summary.label_zh
        welcome_message = summary.welcome_message_zh
    else:
        name = "General Assistant"
        description = summary.description_en or summary.label_en
        welcome_message = summary.welcome_message_en

    spec = build_create_spec_from_expert(
        expert_id=DEFAULT_EXPERT_ID,
        expert=expert,
        user_id=owner_user_id,
        name=name,
        description=description,
        locale=loc,
        agent_id=BUILTIN_GENERAL_ASSISTANT_FEATURE_AGENT_ID,
        welcome_message=welcome_message,
    )
    spec.kind = KIND_FEATURE
    created = await registry.create(spec)
    resource_acl_repo.set_visibility(
        "feature",
        BUILTIN_GENERAL_ASSISTANT_FEATURE_ID,
        VISIBILITY_PUBLIC,
        owner_user_id=owner_user_id,
    )
    return created


async def bootstrap_default_agent(
    registry: Any,
    catalog: ExpertCatalog | None,
    *,
    user_id: int,
    locale: str = "zh",
    agent_id: str | None = None,
) -> Any | None:
    """Create ``general-assistant`` for *user_id* when they have no agents yet.

    Returns the created agent row, or ``None`` when skipped (user already has
    agents, or ``main`` already exists when *agent_id* is ``main``).
    """
    if agent_id == SETUP_DEFAULT_AGENT_ID and registry.get_row(SETUP_DEFAULT_AGENT_ID) is not None:
        return None
    if registry.list_agents(user_id):
        return None
    if catalog is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "expert catalog not available")
    expert = catalog.get(DEFAULT_EXPERT_ID)
    if expert is None:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{DEFAULT_EXPERT_ID} expert template missing",
        )
    loc = normalize_locale(locale)
    spec = build_create_spec_from_expert(
        expert_id=DEFAULT_EXPERT_ID,
        expert=expert,
        user_id=user_id,
        agent_id=agent_id,
        locale=loc,
    )
    return await registry.create(spec, defer_bootstrap=True)


async def try_bootstrap_default_agent(
    server: Any,
    *,
    user_id: int,
    locale: str = "zh",
    agent_id: str | None = None,
) -> None:
    """Best-effort wrapper used by HTTP adapters (setup / invite)."""
    if server.app_runtime is None:
        logger.warning("skip default agent bootstrap: app_runtime not ready")
        return
    try:
        await bootstrap_default_agent(
            server.app_runtime.agent_registry,
            server.expert_catalog,
            user_id=user_id,
            locale=locale,
            agent_id=agent_id,
        )
    except Exception as exc:  # pragma: no cover - logged; user creation should still succeed
        logger.warning("could not auto-create default agent for user %s: %s", user_id, exc)


async def bootstrap_setup_default_agents(server: Any, *, user_id: int, locale: str = "zh") -> None:
    """Defer business initialization until activation without reopening setup."""
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "setup database not bound")
    if not current_license().business_allowed:
        server.services.settings_repo.set(
            _SETUP_PENDING_AGENTS_KEY,
            json.dumps({"user_id": user_id, "locale": normalize_locale(locale)}),
        )
        return
    current_license().require_business_access()
    if server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "app_runtime not ready")
    await bootstrap_default_agent(
        server.app_runtime.agent_registry,
        server.expert_catalog,
        user_id=user_id,
        locale=locale,
        agent_id=SETUP_DEFAULT_AGENT_ID,
    )
    await ensure_builtin_general_assistant_feature(
        server.app_runtime.agent_registry,
        server.expert_catalog,
        server.services.repos.resource_acl_repo,
        owner_user_id=user_id,
        locale=locale,
    )
    current_license().require_business_access()
    server.services.settings_repo.set(_SETUP_PENDING_AGENTS_KEY, "")


async def resume_deferred_setup_agents(server: Any) -> None:
    """Consume explicitly deferred first-run work without recreating deleted agents."""
    if server.services is None:
        return
    raw = server.services.settings_repo.get(_SETUP_PENDING_AGENTS_KEY)
    if not raw:
        return
    pending = json.loads(raw)
    await bootstrap_setup_default_agents(
        server, user_id=int(pending["user_id"]), locale=pending["locale"]
    )
