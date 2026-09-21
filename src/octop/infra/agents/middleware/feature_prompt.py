"""Carry a feature run's system prompt into that run's model calls — nothing else.

A feature definition ships ``prompt.system_file`` next to its user template: the
text that governs *how* the output is written (meeting-notes structure, quote
tone). It belongs to the run, not to the user's agent, so it never reaches
``agents.system_prompt`` and never lands in the thread checkpoint. The router
stamps it onto the harness request's ``configurable`` and this middleware merges
it into the system message of that model call, exactly like
``KnowledgeSearchHintMiddleware`` rewrites the tool description from a
turn-scoped config entry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.config import get_config

CONFIG_KEY = "octop_feature_system_prompt"
"""``configurable`` entry holding one feature run's system prompt."""


def stamp_feature_system_prompt(request: dict[str, Any], system_prompt: str | None) -> None:
    """Write *system_prompt* onto one harness request, in place.

    A blank or missing prompt leaves the request untouched, so the run's messages
    stay byte-identical to a run of a feature that declares no system file.
    """
    text = (system_prompt or "").strip()
    if not text:
        return
    configurable = dict(request.get("configurable") or {})
    configurable[CONFIG_KEY] = text
    request["configurable"] = configurable


def _with_feature_prompt(request: ModelRequest[Any]) -> ModelRequest[Any]:
    configurable = get_config().get("configurable") or {}
    text = configurable.get(CONFIG_KEY)
    if not isinstance(text, str) or not text.strip():
        return request
    # Same helper the harness uses to append to ``request.system_message``:
    # it keeps the agent's own prompt and adds this run's text after it.
    from deepagents.middleware._utils import append_to_system_message  # noqa: PLC0415

    return request.override(
        system_message=append_to_system_message(request.system_message, text),
    )


class FeatureSystemPromptMiddleware(AgentMiddleware[Any, Any]):
    """Apply :data:`CONFIG_KEY` to the system message of the current model call.

    Scoped to the model call on purpose: middleware never write the text into
    ``request.messages``, which is what the checkpointer persists.
    """

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_with_feature_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_with_feature_prompt(request))


__all__ = ["CONFIG_KEY", "FeatureSystemPromptMiddleware", "stamp_feature_system_prompt"]
