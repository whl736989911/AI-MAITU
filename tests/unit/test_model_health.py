"""Deterministic cooldown and failure-classification coverage."""

from __future__ import annotations

from octop.infra.agents.providers.health import ModelHealthRegistry, is_retryable_model_error


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _UpstreamError(Exception):
    status_code = 503


class _AuthError(Exception):
    status_code = 401


class _InputError(Exception):
    status_code = 400


def test_model_health_cooldown_exponential_backoff_and_recovery() -> None:
    clock = _Clock()
    health = ModelHealthRegistry(cooldown_seconds=10, max_cooldown_seconds=25, clock=clock)
    health.record_failure("alpha/chat", _UpstreamError("secret must not be retained"))
    assert not health.is_available("alpha/chat")
    assert health.snapshot()["alpha/chat"].unavailable_until == 110.0
    clock.now = 110.0
    assert health.is_available("alpha/chat")

    health.record_failure("alpha/chat", _UpstreamError())
    assert health.snapshot()["alpha/chat"].unavailable_until == 130.0
    health.record_success("alpha/chat")
    assert health.snapshot() == {}


def test_health_registry_isolated_by_model_and_error_class() -> None:
    health = ModelHealthRegistry(cooldown_seconds=10)
    assert is_retryable_model_error(_UpstreamError())
    assert not is_retryable_model_error(_AuthError())
    assert not is_retryable_model_error(_InputError())
    health.record_failure("tenant-a/chat", _UpstreamError())
    assert not health.is_available("tenant-a/chat")
    assert health.is_available("tenant-b/chat")
    assert "secret" not in str(health.snapshot())
