"""Unit tests for default-agent bootstrap helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.agents.default_agent import (
    BUILTIN_GENERAL_ASSISTANT_FEATURE_AGENT_ID,
    BUILTIN_GENERAL_ASSISTANT_FEATURE_ID,
    DEFAULT_EXPERT_ID,
    SETUP_DEFAULT_AGENT_ID,
    bootstrap_default_agent,
    ensure_builtin_general_assistant_feature,
)
from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root
from octop.infra.errors import OctopError


@pytest.fixture(scope="module")
def catalog() -> ExpertCatalog:
    cat = ExpertCatalog(default_library_root())
    cat.refresh()
    return cat


async def test_ensure_builtin_feature_creates_public_plain_assistant(
    catalog: ExpertCatalog,
) -> None:
    registry = MagicMock()
    registry.get_row.return_value = None
    registry.create = AsyncMock(return_value=object())
    acl = MagicMock()

    await ensure_builtin_general_assistant_feature(
        registry, catalog, acl, owner_user_id=4, locale="zh"
    )

    registry.create.assert_awaited_once()
    spec = registry.create.await_args.args[0]
    assert spec.agent_id == BUILTIN_GENERAL_ASSISTANT_FEATURE_AGENT_ID
    assert spec.kind == "feature"
    assert spec.name == "通用助手"
    assert spec.template_name == DEFAULT_EXPERT_ID
    assert spec.welcome_message == catalog.get(DEFAULT_EXPERT_ID).summary.welcome_message_zh
    acl.set_visibility.assert_called_once_with(
        "feature",
        BUILTIN_GENERAL_ASSISTANT_FEATURE_ID,
        "public",
        owner_user_id=4,
    )


async def test_ensure_builtin_feature_is_idempotent(catalog: ExpertCatalog) -> None:
    registry = MagicMock()
    registry.get_row.return_value = object()
    registry.create = AsyncMock()
    acl = MagicMock()

    result = await ensure_builtin_general_assistant_feature(registry, catalog, acl, owner_user_id=4)

    assert result is None
    registry.create.assert_not_called()
    acl.set_visibility.assert_not_called()


async def test_bootstrap_skips_when_user_has_agents(catalog: ExpertCatalog) -> None:
    registry = MagicMock()
    registry.list_agents.return_value = [object()]
    registry.get_row.return_value = None
    registry.create = AsyncMock()
    result = await bootstrap_default_agent(registry, catalog, user_id=2, locale="zh", agent_id=None)
    assert result is None
    registry.create.assert_not_called()


async def test_bootstrap_skips_when_main_exists(catalog: ExpertCatalog) -> None:
    registry = MagicMock()
    registry.get_row.return_value = object()
    registry.list_agents.return_value = []
    registry.create = AsyncMock()
    result = await bootstrap_default_agent(
        registry,
        catalog,
        user_id=1,
        locale="zh",
        agent_id=SETUP_DEFAULT_AGENT_ID,
    )
    assert result is None
    registry.create.assert_not_called()


async def test_bootstrap_requires_catalog() -> None:
    registry = MagicMock()
    registry.get_row.return_value = None
    registry.list_agents.return_value = []
    with pytest.raises(OctopError):
        await bootstrap_default_agent(registry, None, user_id=1, locale="zh")
