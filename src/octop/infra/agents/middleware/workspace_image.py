"""Ephemeral workspace-image rematerialization for model calls (plan B).

Checkpoint / history keep path-only ``image_url`` refs (``workspace://…``).
This middleware expands them to ``data:`` URIs on the model request only so
base64 never lands in LangGraph state or the dashboard history payload.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import anthropic
import openai
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_config

from octop.i18n import tr
from octop.i18n.domains.stream import exception_display_message
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.media.attachment_hints import (
    content_blocks_need_vision,
    expand_workspace_image_ref,
    expand_workspace_image_ref_sync,
    is_workspace_image_ref,
)

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

ModelForImages = Callable[[ModelRequest[Any], bool], BaseChatModel]
_TOOL_IMAGE_TYPES = frozenset({"image_url", "image", "input_image"})
_PROTOCOL_FAILURES = (
    openai.APIStatusError,
    openai.APIConnectionError,
    anthropic.APIStatusError,
    anthropic.APIConnectionError,
)


def _request_locale() -> str:
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return "en"
    if not isinstance(configurable, dict):
        return "en"
    return str(configurable.get("octop_feature_locale") or configurable.get("locale") or "en")


def _protocols_failed(first_error: BaseException, second_error: BaseException) -> OctopError:
    locale = _request_locale()
    return OctopError(
        ErrorCode.AGENT_FAILED,
        tr(
            "agent_errors.image_protocols_failed",
            locale,
            first_error=exception_display_message(first_error),
            second_error=exception_display_message(second_error),
        ),
    )


class WorkspaceImageMaterializeMiddleware(AgentMiddleware[Any, Any]):
    """Materialize workspace images and route image-bearing requests to vision models."""

    def __init__(
        self,
        *,
        workspace: BackendWorkspace,
        model_for_images: ModelForImages,
    ) -> None:
        self._workspace = workspace
        self._model_for_images = model_for_images

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        messages = self._expand_messages_sync(request.messages)
        overrides: dict[str, Any] = {"messages": messages}
        if not self._messages_need_vision(messages):
            return handler(request.override(**overrides))

        # Constructing the selected model is intentionally outside the retry block:
        # a missing/broken primary configuration must retain its original error.
        overrides["model"] = self._model_for_images(request, False)
        prepared_request = request.override(**overrides)
        try:
            return handler(prepared_request)
        except _PROTOCOL_FAILURES as first_error:
            try:
                alternate_model = self._model_for_images(request, True)
            except Exception as second_error:
                raise _protocols_failed(first_error, second_error) from second_error
            alternate_request = request.override(messages=messages, model=alternate_model)
            try:
                return handler(alternate_request)
            except _PROTOCOL_FAILURES as second_error:
                raise _protocols_failed(first_error, second_error) from second_error

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        messages = await self._expand_messages(request.messages)
        overrides: dict[str, Any] = {"messages": messages}
        if not self._messages_need_vision(messages):
            return await handler(request.override(**overrides))

        overrides["model"] = self._model_for_images(request, False)
        prepared_request = request.override(**overrides)
        try:
            return await handler(prepared_request)
        except _PROTOCOL_FAILURES as first_error:
            try:
                alternate_model = self._model_for_images(request, True)
            except Exception as second_error:
                raise _protocols_failed(first_error, second_error) from second_error
            alternate_request = request.override(messages=messages, model=alternate_model)
            try:
                return await handler(alternate_request)
            except _PROTOCOL_FAILURES as second_error:
                raise _protocols_failed(first_error, second_error) from second_error

    @staticmethod
    def _prepare_model_messages(messages: Sequence[Any]) -> list[Any]:
        """Move tool-returned images after their complete tool-response batch."""
        prepared: list[Any] = []
        index = 0
        while index < len(messages):
            if not isinstance(messages[index], ToolMessage):
                prepared.append(messages[index])
                index += 1
                continue

            batch: list[Any] = []
            images: list[str | dict[str, Any]] = []
            while index < len(messages) and isinstance(messages[index], ToolMessage):
                message = messages[index]
                content = message.content
                if isinstance(content, list):
                    text_blocks = [
                        block
                        for block in content
                        if not (isinstance(block, dict) and block.get("type") in _TOOL_IMAGE_TYPES)
                    ]
                    images.extend(
                        block
                        for block in content
                        if isinstance(block, dict) and block.get("type") in _TOOL_IMAGE_TYPES
                    )
                    if len(text_blocks) != len(content):
                        content = text_blocks or ""
                        message = message.model_copy(update={"content": content})
                batch.append(message)
                index += 1

            prepared.extend(batch)
            if images:
                prepared.append(HumanMessage(content=images))
        return prepared

    @staticmethod
    def _messages_need_vision(messages: Sequence[Any]) -> bool:
        return any(content_blocks_need_vision(getattr(msg, "content", None)) for msg in messages)

    def _expand_messages_sync(self, messages: Sequence[Any]) -> list[Any]:
        out: list[Any] = []
        for msg in messages:
            content = getattr(msg, "content", None)
            if not isinstance(content, list) or not any(
                is_workspace_image_ref(block) for block in content if isinstance(block, dict)
            ):
                out.append(msg)
                continue
            new_blocks: list[Any] = [
                (
                    expand_workspace_image_ref_sync(block, workspace=self._workspace)
                    if isinstance(block, dict) and is_workspace_image_ref(block)
                    else block
                )
                for block in content
            ]
            out.append(msg.model_copy(update={"content": new_blocks}))
        return self._prepare_model_messages(out)

    async def _expand_messages(self, messages: Sequence[Any]) -> list[Any]:
        out: list[Any] = []
        for msg in messages:
            content = getattr(msg, "content", None)
            if not isinstance(content, list) or not any(
                is_workspace_image_ref(block) for block in content if isinstance(block, dict)
            ):
                out.append(msg)
                continue
            new_blocks: list[Any] = []
            for block in content:
                if isinstance(block, dict) and is_workspace_image_ref(block):
                    new_blocks.append(
                        await expand_workspace_image_ref(block, workspace=self._workspace)
                    )
                else:
                    new_blocks.append(block)
            out.append(msg.model_copy(update={"content": new_blocks}))
        return self._prepare_model_messages(out)


__all__ = ["WorkspaceImageMaterializeMiddleware"]
