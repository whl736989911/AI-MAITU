"""Retry automatic chat routes on a different healthy configured model."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager
from langchain_core.exceptions import ModelError
from langchain_core.language_models import BaseChatModel
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp

from octop.i18n import tr
from octop.infra.agents.providers.health import ModelHealthRegistry, is_retryable_model_error
from octop.infra.errors import ErrorCode, OctopError


class _NonRetryableOctopError(OctopError, ModelError):
    """Keep Octop's error envelope while stopping harness's outer retry layer."""

    is_retryable = False


class _NonRetryableModelFailure(ModelError):
    """Prevent built-in retries of model-adjacent control/input failures."""

    is_retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)


class _TokenEmissionTracker(BaseCallbackHandler):
    """Observe streaming callbacks without changing emitted tokens."""

    def __init__(self) -> None:
        self._emitted = threading.Event()

    @property
    def emitted(self) -> bool:
        return self._emitted.is_set()

    def on_llm_new_token(self, token: str | list[str | dict[str, Any]], **kwargs: Any) -> None:
        if token:
            self._emitted.set()


_CURRENT_ATTEMPT_TRACKER: ContextVar[_TokenEmissionTracker | None] = ContextVar(
    "octop_current_model_attempt_tracker", default=None
)


def track_current_failover_model(model: BaseChatModel) -> BaseChatModel:
    """Attach the active attempt tracker to a model selected by nested middleware."""
    tracker = _CURRENT_ATTEMPT_TRACKER.get()
    return _with_token_tracker(model, tracker) if tracker is not None else model


def _with_token_tracker(model: BaseChatModel, tracker: _TokenEmissionTracker) -> BaseChatModel:
    callbacks = getattr(model, "callbacks", None)
    existing = callbacks.handlers if isinstance(callbacks, BaseCallbackManager) else callbacks
    if isinstance(existing, (list, tuple)) and tracker in existing:
        return model
    merged: BaseCallbackManager | list[BaseCallbackHandler]
    if isinstance(callbacks, BaseCallbackManager):
        merged = callbacks.copy()
        merged.add_handler(tracker)
    elif callbacks is None:
        merged = [tracker]
    elif isinstance(callbacks, (list, tuple)):
        merged = [*callbacks, tracker]
    else:
        merged = [callbacks, tracker]
    return model.model_copy(update={"callbacks": merged})


_CURRENT_FAILOVER_MODEL: ContextVar[str | None] = ContextVar(
    "octop_current_failover_model", default=None
)


def current_failover_model_ref() -> str | None:
    """Return the model being invoked by failover middleware in this task context."""
    return _CURRENT_FAILOVER_MODEL.get()


AUTO_FAILOVER_CONFIG_KEY = "octop_model_failover_auto"
AUTO_FAILOVER_VISION_CONFIG_KEY = "octop_model_failover_requires_vision"


def _run_configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except RuntimeError:
        return {}
    configurable = config.get("configurable") if isinstance(config, dict) else None
    return dict(configurable) if isinstance(configurable, dict) else {}


def _availability_error(key: str, cause: Exception | None = None) -> OctopError:
    configurable = _run_configurable()
    locale = str(configurable.get("octop_feature_locale") or configurable.get("locale") or "en")
    details: dict[str, Any] = {"reason": key}
    if cause is not None:
        details["cause_type"] = type(cause).__name__
        status = getattr(cause, "status_code", None)
        if isinstance(status, int):
            details["upstream_status"] = status
    return _NonRetryableOctopError(
        ErrorCode.MODEL_UNAVAILABLE,
        tr(f"agent_errors.{key}", locale),
        status=503,
        details=details,
    )


def _nonretryable_boundary_error(error: Exception) -> Exception:
    if isinstance(error, OctopError):
        return _NonRetryableOctopError(
            error.code,
            error.message,
            status=error.status,
            details=dict(error.details),
        )
    if isinstance(error, ModelError) and not error.is_retryable:
        return error
    from octop.i18n.domains.stream import exception_display_message

    return _NonRetryableModelFailure(exception_display_message(error))


class AutoModelFailoverMiddleware(AgentMiddleware[Any, Any]):
    """Bounded request-level failover; only the caller marks genuinely automatic turns."""

    def __init__(
        self,
        *,
        factory: Any,
        health: ModelHealthRegistry,
        candidate_refs: Callable[[bool], Sequence[str]],
        primary_ref: Callable[[ModelRequest[Any]], str | None] | None = None,
    ) -> None:
        self._factory = factory
        self._health = health
        self._candidate_refs = candidate_refs
        self._primary_ref = primary_ref or (lambda _request: None)
        self._selected: ContextVar[str | None] = ContextVar(
            f"octop_failover_selected_{id(self)}", default=None
        )

    @property
    def last_selected_model_ref(self) -> str | None:
        """Selected public ref in the current async/task context."""
        return self._selected.get()

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        attempts = self._attempts(request)
        if attempts is None:
            return handler(request)
        return self._try_sync(request, handler, attempts)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        attempts = self._attempts(request)
        if attempts is None:
            return await handler(request)
        last_error: Exception | None = None
        for ref in attempts:
            if not self._health.is_available(ref):
                continue
            tracker = _TokenEmissionTracker()
            try:
                model = _with_token_tracker(self._model(ref), tracker)
                model_token = _CURRENT_FAILOVER_MODEL.set(ref)
                tracker_token = _CURRENT_ATTEMPT_TRACKER.set(tracker)
                try:
                    response = await handler(request.override(model=model))
                finally:
                    _CURRENT_ATTEMPT_TRACKER.reset(tracker_token)
                    _CURRENT_FAILOVER_MODEL.reset(model_token)
            except Exception as exc:
                if is_retryable_model_error(exc):
                    self._health.record_failure(ref, exc)
                    if tracker.emitted:
                        raise _availability_error("model_failed_after_stream", exc) from exc
                    last_error = exc
                    continue
                if isinstance(exc, GraphBubbleUp):
                    raise
                boundary_error = _nonretryable_boundary_error(exc)
                if boundary_error is exc:
                    raise
                raise boundary_error from exc
            self._health.record_success(ref)
            self._selected.set(ref)
            return _stamp_selected_model(response, ref)
        if last_error is not None:
            raise _availability_error("models_cooling_down", last_error) from last_error
        raise _availability_error("models_cooling_down")

    def _attempts(self, request: ModelRequest[Any]) -> list[str] | None:
        configurable = _run_configurable()
        if configurable.get(AUTO_FAILOVER_CONFIG_KEY) is not True:
            return None
        vision = configurable.get(AUTO_FAILOVER_VISION_CONFIG_KEY) is True
        refs = list(dict.fromkeys(self._candidate_refs(vision)))
        primary = self._primary_ref(request)
        # Image capability hints constrain fallback choices but never veto the
        # selected configured chat model for an actual image-bearing request.
        scoped_chat_refs = refs if not vision else list(dict.fromkeys(self._candidate_refs(False)))
        if primary not in scoped_chat_refs:
            primary = None
        attempts = list(dict.fromkeys(([primary] if primary else []) + refs))
        if not attempts:
            raise _availability_error("no_eligible_models")
        return attempts

    def _try_sync(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
        attempts: Sequence[str],
    ) -> ModelResponse[Any]:
        last_error: Exception | None = None
        for ref in attempts:
            if not self._health.is_available(ref):
                continue
            tracker = _TokenEmissionTracker()
            try:
                model = _with_token_tracker(self._model(ref), tracker)
                model_token = _CURRENT_FAILOVER_MODEL.set(ref)
                tracker_token = _CURRENT_ATTEMPT_TRACKER.set(tracker)
                try:
                    response = handler(request.override(model=model))
                finally:
                    _CURRENT_ATTEMPT_TRACKER.reset(tracker_token)
                    _CURRENT_FAILOVER_MODEL.reset(model_token)
            except Exception as exc:
                if is_retryable_model_error(exc):
                    self._health.record_failure(ref, exc)
                    if tracker.emitted:
                        raise _availability_error("model_failed_after_stream", exc) from exc
                    last_error = exc
                    continue
                if isinstance(exc, GraphBubbleUp):
                    raise
                boundary_error = _nonretryable_boundary_error(exc)
                if boundary_error is exc:
                    raise
                raise boundary_error from exc
            self._health.record_success(ref)
            self._selected.set(ref)
            return _stamp_selected_model(response, ref)
        if last_error is not None:
            raise _availability_error("models_cooling_down", last_error) from last_error
        raise _availability_error("models_cooling_down")

    def _model(self, model_ref: str) -> Any:
        get_chat_model = getattr(self._factory, "get_chat_model", None)
        if callable(get_chat_model):
            return get_chat_model(model_ref)
        return self._factory.get(model_ref).chat_model


def _stamp_selected_model(response: ModelResponse[Any], model_ref: str) -> ModelResponse[Any]:
    """Make the final selected ref available to usage accounting and stream consumers."""
    for index in range(len(response.result) - 1, -1, -1):
        message = response.result[index]
        if getattr(message, "type", None) != "ai":
            continue
        metadata = dict(getattr(message, "response_metadata", {}) or {})
        metadata["octop_model_ref"] = model_ref
        response.result[index] = message.model_copy(update={"response_metadata": metadata})
        break
    return response


__all__ = [
    "AUTO_FAILOVER_CONFIG_KEY",
    "AUTO_FAILOVER_VISION_CONFIG_KEY",
    "AutoModelFailoverMiddleware",
    "current_failover_model_ref",
    "track_current_failover_model",
]
