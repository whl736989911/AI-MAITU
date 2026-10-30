"""One-shot LLM text assist for dashboard forms and chat prompt editing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, get_args

from langchain_core.messages import HumanMessage, SystemMessage

from octop.i18n import tr
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.llm_text import ainvoke_text
from octop.infra.utils.locale import Locale, normalize_locale

logger = logging.getLogger(__name__)

AssistKind = Literal[
    "chat.prompt",
    "expert.description",
    "expert.welcome_message",
    "team.description",
    "team.welcome_message",
    "skill_package.description",
    "skill.description",
    "skill.label_zh",
    "skill.label_en",
    "skill.summary_zh",
    "skill.summary_en",
    "skill.body",
    "knowledge_base.description",
    "feature.description",
]

ASSIST_KINDS: frozenset[str] = frozenset(get_args(AssistKind))
_FORM_KINDS: frozenset[str] = ASSIST_KINDS - {"chat.prompt"}
_TIMEOUT_SECONDS = 30.0


def _resolve_model(
    server: Any, *, default_model: str | None, agent_id: str | None
) -> tuple[Any, str]:
    runtime = getattr(server, "app_runtime", None)
    registry = getattr(runtime, "agent_registry", None)
    if registry is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "runtime unavailable")

    requested = (default_model or "").strip()
    if agent_id:
        harness = registry.get_agent(agent_id)
        model_ref = requested or harness.config.pick_default_model_ref()
    else:
        model_ref = requested or registry.resolve_fallback_model_ref()

    providers = registry.providers
    if not model_ref or not providers.is_model_ref_usable(model_ref):
        raise OctopError(ErrorCode.INTERNAL_ERROR, "text assist model unavailable")
    manager = registry.harness_manager
    factory = getattr(manager, "shared_factory", None) if manager is not None else None
    if factory is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "text assist model factory unavailable")
    try:
        return factory.get_chat_model(model_ref), model_ref
    except Exception:
        logger.exception("text assist model resolution failed model=%s", model_ref)
        raise OctopError(ErrorCode.INTERNAL_ERROR, "text assist model unavailable") from None


def _prompt_pair(*, kind: str, text: str, name: str, locale: Locale) -> tuple[str, str]:
    system = tr(f"text_assist.kinds.{kind}.system", locale)
    if kind == "chat.prompt":
        user = tr(f"text_assist.kinds.{kind}.user", locale, text=text)
    elif text:
        user = tr(f"text_assist.kinds.{kind}.user_polish", locale, name=name, text=text)
    else:
        user = tr(f"text_assist.kinds.{kind}.user_generate", locale, name=name)
    return system, user


async def run_text_assist(
    *,
    server: Any,
    kind: str,
    text: str = "",
    context: dict[str, Any] | None = None,
    locale: str | Locale = "en",
    default_model: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Polish or generate short copy for a known assist kind without persistence."""
    if kind not in ASSIST_KINDS:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"unknown assist kind: {kind}")
    draft = (text or "").strip()
    raw_name = (context or {}).get("name")
    name = str(raw_name).strip() if raw_name is not None else ""
    loc = normalize_locale(str(locale))
    if kind == "chat.prompt":
        if not draft:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "text is required")
    elif kind in _FORM_KINDS and not name:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "context.name is required")

    llm, model_ref = _resolve_model(server, default_model=default_model, agent_id=agent_id)
    system, user = _prompt_pair(kind=kind, text=draft, name=name, locale=loc)
    try:
        result = await asyncio.wait_for(
            ainvoke_text(
                llm,
                [SystemMessage(content=system), HumanMessage(content=user)],
                timeout=_TIMEOUT_SECONDS,
            ),
            timeout=_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "text assist timed out") from None
    except Exception:
        logger.exception("text assist failed kind=%s model=%s", kind, model_ref)
        raise OctopError(ErrorCode.INTERNAL_ERROR, "text assist failed") from None
    output = (result or "").strip()
    if not output:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "model returned empty text assist result")
    return output


__all__ = ["ASSIST_KINDS", "AssistKind", "run_text_assist"]
