"""A feature's workflow, stated to the model for the turn it runs under.

A feature declares its run once — the form it asks the caller for, the fixed steps,
the deliverables, the rules — and this middleware is how that declaration reaches
the model: one system block per model call, appended to the system message the
harness already built, never to the conversation.

**No I/O, no state.** The block is rendered from ``configurable``
(:data:`~octop.infra.agents.feature_workflow.CONFIGURABLE_WORKFLOW_KEY`), which the
turn path stamps while it can still read the workspace asynchronously. That is what
keeps this middleware free of a per-call file read on a remote backend, and it is
why the same code serves both the synchronous and the asynchronous model hooks.

**The personal overlay rides above the definition.** A caller's own text — written
by them, or summarised for them out of their own runs — is rendered after the
definition and states that it wins where the two disagree. Enterprise-wide hard
rules are a layer above both and are not part of a feature's definition, so nothing
here can weaken them.

**A missing context is not an error.** An expert's chain never builds this
middleware at all, and a feature whose definition could not be read (or that has
none) stamps nothing: those turns run exactly as they did before the feature ever
declared a workflow.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from octop.infra.agents.feature_workflow import (
    CONFIGURABLE_WORKFLOW_KEY,
    WorkflowRunContext,
    render_run_context,
)
from octop.infra.agents.kinds import is_feature_agent

logger = logging.getLogger(__name__)


def run_workflow_context() -> WorkflowRunContext | None:
    """This run's workflow context, or ``None`` when the turn carries none."""
    try:
        config = get_config()
    except RuntimeError:  # outside a run (unit tests, CLI)
        return None
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    raw = configurable.get(CONFIGURABLE_WORKFLOW_KEY)
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
    """State the feature's declared workflow for the turn, overlay included."""

    def __init__(self, *, agent_id: str) -> None:
        self._agent_id = agent_id

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
        context = run_workflow_context()
        if context is None:
            return request
        try:
            block = render_run_context(context)
        except Exception:  # pragma: no cover - a malformed definition must not kill the turn
            logger.warning(
                "Failed to render the workflow block for %s",
                self._agent_id,
                exc_info=True,
            )
            return request
        return _with_block(request, block)


def feature_workflow_chain(*, agent_id: str, kind: str) -> list[Any]:
    """The workflow block for a feature agent's chain, or ``[]`` for any other agent.

    The same shape as the memory freeze: what a feature's agent needs is decided by
    the row's kind, in one place, and every other agent's chain stays exactly what
    it was.
    """
    if not is_feature_agent(kind):
        return []
    return [FeatureWorkflowMiddleware(agent_id=agent_id)]


__all__ = [
    "FeatureWorkflowMiddleware",
    "feature_workflow_chain",
    "run_workflow_context",
]
