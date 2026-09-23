"""Feature configuration guidance and the declared workflow in model calls.

Every feature turn gets the product's configuration menu and shared-workspace
boundary, even before a workflow exists. The author's turn also learns how to
change a workflow in conversation; callers only learn where the author configures
it. The locale and user come from the turn's configurable context, not a workspace
template that can be stale or shared between callers.

When a workflow exists, its fixed steps and caller overlay follow that guidance
in the system message. No workspace I/O occurs per model call: the turn path
loads the workflow asynchronously and stamps the context once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from octop.i18n import tr
from octop.infra.agents.feature_workflow import (
    CONFIGURABLE_WORKFLOW_KEY,
    WorkflowRunContext,
    render_run_context,
)
from octop.infra.agents.kinds import is_feature_agent

logger = logging.getLogger(__name__)

CONFIGURABLE_FEATURE_LOCALE_KEY = "octop_feature_locale"
"""The locale of this feature turn, stamped by both dashboard and IM paths."""


def _turn_configurable() -> Mapping[str, Any]:
    try:
        config = get_config()
    except RuntimeError:  # outside a run (unit tests, CLI)
        return {}
    if not isinstance(config, Mapping):
        return {}
    configurable = config.get("configurable")
    return configurable if isinstance(configurable, Mapping) else {}


def run_workflow_context() -> WorkflowRunContext | None:
    """This run's workflow context, or ``None`` when the turn carries none."""
    raw = _turn_configurable().get(CONFIGURABLE_WORKFLOW_KEY)
    return raw if isinstance(raw, WorkflowRunContext) else None


def _with_block(request: ModelRequest[Any], block: str) -> ModelRequest[Any]:
    """*request* with *block* appended to its system message.

    Appending rather than replacing is deliberate: the system message already
    carries the agent's own prompt and memory, and a block that replaced it would
    silently drop the feature's persona. A message whose content is a block list
    keeps its blocks — they may carry provider cache markers — and gains one more.
    """
    if not block:
        return request
    current = request.system_message
    if current is None:
        return request.override(system_message=SystemMessage(content=block))
    content = getattr(current, "content", "")
    if isinstance(content, str):
        text = f"{content}\n\n{block}" if content.strip() else block
        return request.override(system_message=SystemMessage(content=text))
    if isinstance(content, list):
        return request.override(
            system_message=SystemMessage(content=[*content, {"type": "text", "text": block}])
        )
    return request.override(system_message=SystemMessage(content=block))


class FeatureWorkflowMiddleware(AgentMiddleware[Any, Any]):
    """State a feature's configuration choices and any declared workflow."""

    def __init__(self, *, agent_id: str, author_user_id: int | None) -> None:
        self._agent_id = agent_id
        self._author_user_id = author_user_id

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._inject(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._inject(request))

    def _inject(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        configurable = _turn_configurable()
        raw = configurable.get(CONFIGURABLE_WORKFLOW_KEY)
        context = raw if isinstance(raw, WorkflowRunContext) else None
        locale = str(
            configurable.get(CONFIGURABLE_FEATURE_LOCALE_KEY)
            or (context.locale if context is not None else "en")
        )
        is_author = self._author_user_id is not None and str(configurable.get("user") or "") == str(
            self._author_user_id
        )
        guide = tr(
            "feature_training.author" if is_author else "feature_training.caller",
            locale,
            catalog=tr("feature_training.catalog", locale),
        )
        guided = _with_block(request, guide)
        if context is None:
            return guided
        try:
            block = render_run_context(context)
        except Exception:  # pragma: no cover - a malformed definition must not kill the turn
            logger.warning(
                "Failed to render the workflow block for %s",
                self._agent_id,
                exc_info=True,
            )
            return guided
        return _with_block(guided, block)


def feature_workflow_chain(*, agent_id: str, kind: str, author_user_id: int | None) -> list[Any]:
    """Feature-only guidance and workflow block; experts keep their own chain."""
    if not is_feature_agent(kind):
        return []
    return [FeatureWorkflowMiddleware(agent_id=agent_id, author_user_id=author_user_id)]


__all__ = [
    "CONFIGURABLE_FEATURE_LOCALE_KEY",
    "FeatureWorkflowMiddleware",
    "feature_workflow_chain",
    "run_workflow_context",
]
