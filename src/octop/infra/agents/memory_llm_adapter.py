"""Configured LLM runtime for memory extraction (experts and features).

The dashboard's ``memory`` config section carries user-approved budgets:
``extract_light_timeout_s`` / ``extract_heavy_timeout_s`` / ``extract_max_tokens``
/ ``extract_extra_body``. The stock :class:`HarnessAgentLLMClient` supports the
two timeouts on its constructor and ``max_tokens`` only when its caller passes
one — harness-memory pipeline calls never do — and has no ``extra_body``
support at all. This module supplies the real adapter plus the single helper
the agent manager calls after an agent start so the budgets reach the *actual*
memory services (native expert runtime and feature routing alike), not just
unused dataclass fields.
"""

from __future__ import annotations

import logging
from typing import Any

from harness_agent.memory.llm_client import HarnessAgentLLMClient
from harness_memory.ports.llm import LLMTier

logger = logging.getLogger(__name__)


def coerce_aux_timeout(value: Any) -> float | None:
    """Normalize a timeout patch value; ``None``/non-positive → upstream default."""
    if value is None:
        return None
    seconds = float(value)
    if seconds <= 0:
        return None
    return min(3600.0, max(30.0, seconds))


def coerce_aux_max_tokens(value: Any) -> int | None:
    """Normalize a max-tokens patch value; ``None``/non-positive → upstream default."""
    if value is None:
        return None
    tokens = int(value)
    return None if tokens <= 0 else max(128, min(tokens, 65536))


def coerce_aux_extra_body(value: Any) -> dict[str, Any] | None:
    """Normalize an extra-body patch value; must be a non-empty JSON object."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("extract_extra_body must be a JSON object")
    return dict(value) or None


class OctopusMemoryLLMClient(HarnessAgentLLMClient):
    """Harness memory LLM client carrying the user's extraction budgets.

    * light/heavy timeouts come straight from the parent constructor;
    * ``max_tokens`` is applied to every call that does not pass one — the
      harness-memory extract/promotion pipeline never passes one, so without
      this the configured cap would silently never reach a request;
    * ``extra_body`` is merged into the bound call kwargs, which OpenAI-style
      LangChain adapters forward as the SDK's ``extra_body`` argument. A
      provider that cannot honor it fails the call loudly (logged, extraction
      degrades) — it is never silently dropped.
    """

    def __init__(
        self,
        factory: Any,
        *,
        aux_model: str | None = None,
        default_model: str | None = None,
        light_timeout_s: float | None = None,
        heavy_timeout_s: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            factory,
            aux_model=aux_model,
            default_model=default_model,
            light_timeout_s=light_timeout_s,
            heavy_timeout_s=heavy_timeout_s,
        )
        self._max_tokens = coerce_aux_max_tokens(max_tokens)
        self._extra_body = coerce_aux_extra_body(extra_body)

    def call_llm(
        self,
        prompt: str,
        *,
        tier: LLMTier = "light",
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: str = "text",
    ) -> str:
        effective_max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        return super().call_llm(
            prompt,
            tier=tier,
            system=system,
            max_tokens=effective_max_tokens,
            temperature=temperature,
            response_format=response_format,  # type: ignore[arg-type]
        )

    def _apply_call_options(
        self,
        model: object,
        *,
        max_tokens: int | None,
        temperature: float | None,
        response_format: str,
        timeout_s: float | None = None,
    ) -> Any:
        bound = super()._apply_call_options(
            model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,  # type: ignore[arg-type]
            timeout_s=timeout_s,
        )
        if self._extra_body is None:
            return bound
        merged = dict(self._extra_body)
        if bound is model:
            # The parent returned the model unbound (no other options).
            bind = getattr(model, "bind", None)
            if bind is None:  # pragma: no cover - LangChain models always bind
                return model
            return bind(extra_body=merged)
        # The parent bound other options; extend the same binding.
        return bound.bind(extra_body=merged)


def memory_llm_budget(mem_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the user-approved budget fields from a ``memory`` config section."""
    mem = mem_cfg if isinstance(mem_cfg, dict) else {}
    budget: dict[str, Any] = {}
    if "extract_light_timeout_s" in mem:
        budget["light_timeout_s"] = coerce_aux_timeout(mem.get("extract_light_timeout_s"))
    if "extract_heavy_timeout_s" in mem:
        budget["heavy_timeout_s"] = coerce_aux_timeout(mem.get("extract_heavy_timeout_s"))
    if "extract_max_tokens" in mem:
        budget["max_tokens"] = coerce_aux_max_tokens(mem.get("extract_max_tokens"))
    if "extract_extra_body" in mem:
        budget["extra_body"] = coerce_aux_extra_body(mem.get("extract_extra_body"))
    return budget


def build_configured_memory_llm(
    factory: Any,
    cfg: dict[str, Any],
    *,
    default_model: str | None = None,
    is_ref_usable: Any = None,
) -> OctopusMemoryLLMClient | None:
    """Build the budget-carrying memory LLM client for one agent config.

    ``None`` when no model ref resolves — the caller degrades softly exactly
    like the stock wiring. A stale aux ref falls back to the default model.
    """
    mem = cfg.get("memory")
    aux_model: str | None = None
    if isinstance(mem, dict):
        raw = mem.get("aux_model")
        if isinstance(raw, str) and raw.strip():
            ref = raw.strip()
            if is_ref_usable is None or is_ref_usable(ref):
                aux_model = ref
            else:
                logger.warning(
                    "memory aux_model %r no longer usable; extraction falls back to the default model",
                    ref,
                )
    resolved_default = default_model or (cfg.get("default_model") or None)
    if aux_model is None and resolved_default is None:
        return None
    try:
        return OctopusMemoryLLMClient(
            factory,
            aux_model=aux_model,
            default_model=resolved_default,
            **memory_llm_budget(mem if isinstance(mem, dict) else None),
        )
    except Exception:
        logger.warning("configured memory LLM client could not be built", exc_info=True)
        return None


def _swap_service_llm(service: Any, client: HarnessAgentLLMClient) -> bool:
    """Point a live ``MemoryService`` at *client*; returns True when swapped."""
    runtime = getattr(service, "_runtime", None)
    if runtime is None:
        return False
    try:
        runtime._llm = client
        configured = getattr(runtime, "_llm_configured", None)
        if configured is not None:
            runtime._llm_configured = True
        return True
    except Exception:
        logger.warning("memory service LLM swap failed", exc_info=True)
        return False


def configure_agent_memory_runtime(
    agent: Any,
    cfg: dict[str, Any],
    *,
    feature_middleware: Any = None,
) -> Any:
    """Apply the agent's extraction budgets to its *live* memory services.

    Called by the manager right after an agent start (and after any hot
    config reload that rebuilds memory clients). Swaps one
    :class:`OctopusMemoryLLMClient` into:

    * the native expert runtime (``agent._memory_runtime``) — both the runtime's
      own client and its already-built ``MemoryService``;
    * the feature routing middleware's runtime, when one is mounted.

    The native harness ``HarnessAgentConfig`` has no fields for these budgets
    (older harness releases), so config-dict plumbing alone would silently
    drop them — this helper is what makes the settings real.
    """
    model_factory = getattr(agent, "model_factory", None)
    if model_factory is None:
        logger.info("agent has no model factory; memory budgets not applied")
        return None
    default_model = getattr(getattr(agent, "config", None), "default_model", None)
    client = build_configured_memory_llm(
        model_factory,
        cfg,
        default_model=default_model,
    )
    if client is None:
        logger.info("no memory budgets/model to apply for this agent")
        return None
    runtime = getattr(agent, "_memory_runtime", None)
    if runtime is not None:
        try:
            runtime._llm_client = client
        except Exception:  # pragma: no cover - defensive
            logger.warning("native memory runtime LLM swap failed", exc_info=True)
        service = getattr(runtime, "service", None)
        if service is not None:
            _swap_service_llm(service, client)
    middleware = feature_middleware
    if middleware is None:
        middleware = _find_feature_memory_middleware(agent)
    feature_runtime = getattr(middleware, "_runtime", None)
    set_llm = getattr(feature_runtime, "set_llm", None)
    if callable(set_llm):
        set_llm(client)
    return client


def _find_feature_memory_middleware(agent: Any) -> Any | None:
    """Locate the feature routing middleware mounted on a live agent, if any."""
    config = getattr(agent, "config", None) or getattr(agent, "_config", None)
    middlewares = getattr(config, "middleware", None)
    if not middlewares:
        return None
    for item in middlewares:
        if type(item).__name__ == "FeatureMemoryMiddleware":
            return item
    return None


__all__ = [
    "OctopusMemoryLLMClient",
    "build_configured_memory_llm",
    "configure_agent_memory_runtime",
    "coerce_aux_extra_body",
    "coerce_aux_max_tokens",
    "coerce_aux_timeout",
    "memory_llm_budget",
]
