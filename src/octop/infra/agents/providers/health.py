"""Process-local health and cooldown state for configured chat models."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from langgraph.errors import GraphBubbleUp


@dataclass(frozen=True, slots=True)
class ModelHealth:
    failures: int
    unavailable_until: float
    last_error: str | None


class ModelHealthRegistry:
    """Track failures by public provider/model identity, never by credentials."""

    def __init__(
        self,
        *,
        failure_threshold: int = 1,
        cooldown_seconds: float = 30.0,
        max_cooldown_seconds: float = 300.0,
        clock: Any = time.monotonic,
    ) -> None:
        self._threshold = max(1, int(failure_threshold))
        self._base_cooldown = max(0.1, float(cooldown_seconds))
        self._max_cooldown = max(self._base_cooldown, float(max_cooldown_seconds))
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[str, ModelHealth] = {}

    def record_failure(self, model_ref: str, error: BaseException) -> None:
        ref = model_ref.strip()
        if not ref:
            return
        with self._lock:
            old = self._states.get(ref, ModelHealth(0, 0.0, None))
            failures = old.failures + 1
            until = old.unavailable_until
            if failures >= self._threshold:
                exponent = max(0, failures - self._threshold)
                until = self._clock() + min(self._max_cooldown, self._base_cooldown * (2**exponent))
            # Do not retain exception text: it can contain URLs, response bodies, or secrets.
            self._states[ref] = ModelHealth(failures, until, type(error).__name__)

    def record_success(self, model_ref: str) -> None:
        with self._lock:
            self._states.pop(model_ref.strip(), None)

    def is_available(self, model_ref: str) -> bool:
        with self._lock:
            state = self._states.get(model_ref.strip())
            return state is None or state.unavailable_until <= self._clock()

    def snapshot(self) -> dict[str, ModelHealth]:
        """Return an immutable-value copy keyed only by provider/model ref."""
        with self._lock:
            return dict(self._states)

    def clear_provider(self, provider_name: str) -> None:
        """Forget health for one reconfigured provider without ever storing its secret."""
        prefix = f"{provider_name.strip()}/"
        if not prefix.strip("/"):
            return
        with self._lock:
            for ref in tuple(self._states):
                if ref.startswith(prefix):
                    del self._states[ref]

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


def is_retryable_model_error(error: BaseException) -> bool:
    """Only classify upstream availability failures as model health failures."""
    if isinstance(error, GraphBubbleUp):
        return False
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status in {401, 403} or (
        status is not None and 400 <= status < 500 and status not in {408, 429}
    ):
        return False
    if status is not None:
        return status >= 500 or status in {408, 429}
    name = type(error).__name__.lower()
    module = type(error).__module__.lower()
    if any(
        token in name
        for token in (
            "authentication",
            "permission",
            "badrequest",
            "invalidrequest",
            "tool",
            "interrupt",
        )
    ):
        return False
    if any(token in module for token in ("langgraph", "hitl")):
        return False
    return any(
        token in name
        for token in ("timeout", "connection", "ratelimit", "serviceunavailable", "internalserver")
    )


__all__ = ["ModelHealth", "ModelHealthRegistry", "is_retryable_model_error"]
