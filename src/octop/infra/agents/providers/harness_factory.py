"""Hot-sync provider configs through Octop's model-aware harness factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from harness_agent.config import ModelConfig, ProviderConfig
from harness_agent.llm.factory import ChatModelFactory
from harness_agent.manager import HarnessAgentManager
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    BaseCallbackHandler,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import RunnableConfig, merge_configs
from pydantic import PrivateAttr

from octop.infra.agents.providers.model_config import ProviderModelConfig

if TYPE_CHECKING:
    from harness_agent.manager import HarnessAgentManager as HarnessAgentManagerType

logger = logging.getLogger(__name__)


def _is_responses_compatibility_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in {400, 404, 405, 501}:
        return False
    detail = " ".join(
        str(value)
        for value in (
            exc,
            getattr(exc, "code", None),
            getattr(exc, "param", None),
            getattr(exc, "body", None),
        )
        if value is not None
    ).lower()
    if any(
        code in detail
        for code in (
            "insufficient_quota",
            "quota_exceeded",
            "insufficient_balance",
            "model_not_found",
        )
    ):
        return False
    return any(
        marker in detail
        for marker in (
            "responses api",
            "responses endpoint",
            "/responses",
            "unknown parameter: 'input'",
            'unknown parameter: "input"',
            "unsupported endpoint",
            "endpoint is not supported",
            "not supported with this model",
            "unrecognized request argument",
        )
    )


def _mark_fallback_output(output: Any) -> Any:
    if isinstance(output, (AIMessage, AIMessageChunk)):
        output.response_metadata["octop_api_fallback"] = "chat_completions"
    return output


class _OutputTracker(BaseCallbackHandler):
    """Track output delivered through callbacks even during an invoke call."""

    run_inline = True

    def __init__(self) -> None:
        self.started = False

    def on_llm_new_token(self, token: str | list[str | dict[str, Any]], **kwargs: Any) -> None:
        self.started = True


class _ProtocolFallbackRunnable(Runnable[Any, Any]):
    """Keep bound tools and structured output on the same first-output boundary."""

    def __init__(self, primary: Runnable[Any, Any], fallback: Runnable[Any, Any]) -> None:
        self._primary = primary
        self._fallback = fallback

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        tracker = _OutputTracker()
        try:
            return self._primary.invoke(
                input, config=merge_configs(config, {"callbacks": [tracker]}), **kwargs
            )
        except Exception as exc:
            if tracker.started or not _is_responses_compatibility_error(exc):
                raise
        return _mark_fallback_output(self._fallback.invoke(input, config=config, **kwargs))

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        tracker = _OutputTracker()
        try:
            return await self._primary.ainvoke(
                input, config=merge_configs(config, {"callbacks": [tracker]}), **kwargs
            )
        except Exception as exc:
            if tracker.started or not _is_responses_compatibility_error(exc):
                raise
        return _mark_fallback_output(await self._fallback.ainvoke(input, config=config, **kwargs))

    def stream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        tracker = _OutputTracker()
        try:
            chunks = iter(
                self._primary.stream(
                    input, config=merge_configs(config, {"callbacks": [tracker]}), **kwargs
                )
            )
            first = next(chunks)
        except StopIteration:
            return
        except Exception as exc:
            if tracker.started or not _is_responses_compatibility_error(exc):
                raise
            for chunk in self._fallback.stream(input, config=config, **kwargs):
                yield _mark_fallback_output(chunk)
            return
        yield first
        yield from chunks

    async def astream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        tracker = _OutputTracker()
        try:
            chunks = self._primary.astream(
                input, config=merge_configs(config, {"callbacks": [tracker]}), **kwargs
            ).__aiter__()
            first = await chunks.__anext__()
        except StopAsyncIteration:
            return
        except Exception as exc:
            if tracker.started or not _is_responses_compatibility_error(exc):
                raise
            async for chunk in self._fallback.astream(input, config=config, **kwargs):
                yield _mark_fallback_output(chunk)
            return
        yield first
        async for chunk in chunks:
            yield chunk


class _ResponsesFallbackChatModel(BaseChatModel):
    """A chat model that retries protocol rejection only before output begins."""

    _primary: BaseChatModel = PrivateAttr()
    _fallback: BaseChatModel = PrivateAttr()

    def __init__(self, primary: BaseChatModel, fallback: BaseChatModel) -> None:
        super().__init__()
        self._primary = primary
        self._fallback = fallback
        profile = getattr(primary, "profile", None)
        if isinstance(profile, dict):
            # LangChain consumers read this profile directly; isolate the
            # wrapper's request-time budget adjustments from the primary.
            object.__setattr__(self, "profile", dict(profile))
        for name in (
            "_harness_supported_input",
            "_harness_max_input_tokens",
            "_harness_context_window",
            "_harness_max_output_tokens",
            "_harness_native_tool_search",
            "_harness_tool_search_provider",
        ):
            if hasattr(primary, name):
                object.__setattr__(self, name, getattr(primary, name))

    @property
    def _llm_type(self) -> str:
        return "responses_with_chat_completions_fallback"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        return _ProtocolFallbackRunnable(
            self._primary.bind_tools(tools, **kwargs),
            self._fallback.bind_tools(tools, **kwargs),
        )

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return _ProtocolFallbackRunnable(
            self._primary.with_structured_output(schema, **kwargs),
            self._fallback.with_structured_output(schema, **kwargs),
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return self._primary._generate(messages, stop, run_manager, **kwargs)
        except Exception as exc:
            if not _is_responses_compatibility_error(exc):
                raise
            result = self._fallback._generate(messages, stop, run_manager, **kwargs)
            for generation in result.generations:
                generation.message.response_metadata["octop_api_fallback"] = "chat_completions"
            return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return await self._primary._agenerate(messages, stop, run_manager, **kwargs)
        except Exception as exc:
            if not _is_responses_compatibility_error(exc):
                raise
            result = await self._fallback._agenerate(messages, stop, run_manager, **kwargs)
            for generation in result.generations:
                generation.message.response_metadata["octop_api_fallback"] = "chat_completions"
            return result

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        try:
            chunks = iter(self._primary._stream(messages, stop, run_manager, **kwargs))
            first = next(chunks)
        except StopIteration:
            return
        except Exception as exc:
            if not _is_responses_compatibility_error(exc):
                raise
            chunks = iter(self._fallback._stream(messages, stop, run_manager, **kwargs))
            for chunk in chunks:
                chunk.message.response_metadata["octop_api_fallback"] = "chat_completions"
                yield chunk
            return
        yield first
        yield from chunks

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        try:
            chunks = self._primary._astream(messages, stop, run_manager, **kwargs).__aiter__()
            first = await chunks.__anext__()
        except StopAsyncIteration:
            return
        except Exception as exc:
            if not _is_responses_compatibility_error(exc):
                raise
            chunks = self._fallback._astream(messages, stop, run_manager, **kwargs)
            async for chunk in chunks:
                chunk.message.response_metadata["octop_api_fallback"] = "chat_completions"
                yield chunk
            return
        yield first
        async for chunk in chunks:
            yield chunk


class OctopChatModelFactory(ChatModelFactory):
    """Build Responses API models only when the model config explicitly asks."""

    def _build(self, provider: ProviderConfig, model: ModelConfig) -> Any:
        if provider.protocol != "openai" or not isinstance(model, ProviderModelConfig):
            return super()._build(provider, model)
        if model.api_mode == "chat_completions":
            return super()._build(provider, model)
        from harness_agent.llm.factory import (
            _get_reasoning_aware_chat_openai,
            _inject_model_token_limits,
            _stamp_native_tool_search_capability,
        )
        from harness_agent.llm.session_header import (
            attach_session_http_clients,
            session_http_clients,
        )
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": model.id,
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "stream_usage": provider.stream_usage,
            "use_responses_api": True,
            "output_version": "responses/v1",
            "store": False,
        }
        if provider.headers:
            kwargs["default_headers"] = dict(provider.headers)
        session_clients = None
        if provider.session_header:
            session_clients = session_http_clients(provider.session_header)
            kwargs["http_client"], kwargs["http_async_client"] = session_clients
        instance = _get_reasoning_aware_chat_openai(ChatOpenAI)(**kwargs)
        if session_clients is not None:
            attach_session_http_clients(instance, session_clients[0], session_clients[1])
        instance._harness_supported_input = set(model.input)
        _stamp_native_tool_search_capability(instance, provider.protocol, model.native_tool_search)
        _inject_model_token_limits(instance, model)
        if model.api_fallback == "chat_completions":
            from harness_agent.llm.factory import _build_openai

            if model.native_tool_search:
                raise ValueError("Responses fallback is incompatible with native_tool_search")
            return _ResponsesFallbackChatModel(instance, _build_openai(provider, model))
        return instance

    @staticmethod
    def _close_fallback_clients(model: Any) -> None:
        if not isinstance(model, _ResponsesFallbackChatModel):
            return
        from harness_agent.llm.session_header import close_session_http_clients

        close_session_http_clients(model._primary)
        close_session_http_clients(model._fallback)

    @staticmethod
    async def _aclose_fallback_clients(model: Any) -> None:
        if not isinstance(model, _ResponsesFallbackChatModel):
            return
        from harness_agent.llm.session_header import aclose_session_http_clients

        await aclose_session_http_clients(model._primary)
        await aclose_session_http_clients(model._fallback)

    def pop_cached(self, model_ref: str) -> None:
        model = self._cache.get(model_ref)
        super().pop_cached(model_ref)
        self._close_fallback_clients(model)

    def close(self) -> None:
        models = list(self._cache.values())
        super().close()
        for model in models:
            self._close_fallback_clients(model)

    async def aclose(self) -> None:
        models = list(self._cache.values())
        await super().aclose()
        for model in models:
            await self._aclose_fallback_clients(model)


def build_configured_chat_model(provider: ProviderConfig, model: ModelConfig) -> Any:
    """Build an explicit model, including unsaved probe drafts, without registry lookup."""
    factory = OctopChatModelFactory([])
    try:
        return factory._build(provider, model)
    except BaseException:
        factory.close()
        raise


def sync_providers_to_harness(
    harness_manager: HarnessAgentManagerType,
    providers: list[ProviderConfig],
    *,
    shared_factory: Any | None,
) -> None:
    """Apply *providers* to the harness manager's shared model factory."""
    if shared_factory is None:
        if not providers:
            return
        for provider in providers:
            try:
                harness_manager.add_provider(provider)
            except Exception:
                logger.exception("add_provider %s failed", provider.id)
        return

    new_ids = {provider.id for provider in providers}
    existing_ids = set(getattr(shared_factory, "_providers", {}).keys())
    for provider_id in existing_ids - new_ids:
        try:
            harness_manager.remove_provider(provider_id)
        except Exception:
            logger.exception("remove_provider %s failed", provider_id)
    for provider in providers:
        try:
            harness_manager.add_provider(provider)
        except Exception:
            logger.exception("add_provider %s failed", provider.id)


class OctopHarnessAgentManager(HarnessAgentManager):
    """Harness manager variant with Octop's explicit per-model API selection."""

    def __init__(self, providers: list[ProviderConfig] | None = None, **kwargs: Any) -> None:
        super().__init__(providers=None, **kwargs)
        if providers:
            self._shared_factory = OctopChatModelFactory(providers)

    def add_provider(self, provider: ProviderConfig) -> None:
        if self._shared_factory is None:
            self._shared_factory = OctopChatModelFactory([provider])
            return
        super().add_provider(provider)

    def _register_agent(
        self,
        config: Any,
        *,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        agent_id: str,
        init_workspace: bool = True,
    ) -> Any:
        """Register capability-aware agents without replacing the patchable factory."""
        from dataclasses import replace
        from datetime import UTC, datetime

        from harness_agent.mcp import merge_mcp_server_configs
        from harness_agent.registry import AgentEntry

        from octop.infra.agents.middleware.capabilities import CapabilityHarnessAgent

        effective_config = replace(
            config,
            mcp_server_configs=merge_mcp_server_configs(
                self._shared_mcp_configs, config.mcp_server_configs
            ),
        )
        effective_config = self._apply_security_policy(effective_config)
        agent = CapabilityHarnessAgent(
            effective_config,
            model_factory=self._shared_factory,
            team_manager=self._team if effective_config.team_enabled else None,
            agent_id=agent_id,
        )
        if init_workspace:
            seeded = agent.init_workspace()
            if (
                seeded.templates_created
                or seeded.templates_overwritten
                or seeded.skills_synced
                or seeded.agents_created
                or seeded.agents_overwritten
            ):
                agent._init_graph()
        if self._langfuse is not None:
            agent.set_langfuse_callbacks(self._langfuse.callbacks)
        entry = AgentEntry(
            agent_id=agent_id,
            agent=agent,
            config=effective_config,
            metadata=dict(metadata or {}),
            tags=list(tags or []),
            created_at=datetime.now(tz=UTC),
        )
        self._registry.add(entry)
        return entry


__all__ = [
    "OctopChatModelFactory",
    "OctopHarnessAgentManager",
    "build_configured_chat_model",
    "sync_providers_to_harness",
]
