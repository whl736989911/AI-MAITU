"""Render explicit deployment-identity tokens in platform-owned prompts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from octop.branding import company_name, platform_name, platform_short_name
from octop.infra.utils.locale import normalize_locale

_IDENTITY_TOKENS = {
    "{{platform_name}}": platform_name,
    "{{platform_short_name}}": platform_short_name,
    "{{company_name}}": company_name,
}


def render_platform_identity(text: str, locale: str | None = None) -> str:
    """Render known identity tokens without rewriting unmarked prompt or command text."""
    for token, resolver in _IDENTITY_TOKENS.items():
        if token not in text:
            continue
        replacement = f"{resolver('zh')} ({resolver('en')})" if locale is None else resolver(locale)
        text = text.replace(token, replacement)
    return text


def _render_content(content: Any, locale: str) -> Any:
    if isinstance(content, str):
        return render_platform_identity(content, locale)
    if isinstance(content, list):
        return [_render_content(item, locale) for item in content]
    if isinstance(content, dict):
        # Only textual content blocks are prompt prose; metadata and identifiers
        # remain unchanged.
        rendered = dict(content)
        if isinstance(rendered.get("text"), str):
            rendered["text"] = render_platform_identity(rendered["text"], locale)
        return rendered
    return content


def _identity_instruction(locale: str) -> str:
    if locale == "zh":
        return (
            "平台身份：你运行于「"
            f"{platform_name(locale)}」（{company_name(locale)}）"
            "平台。回答平台自身身份问题时使用此名称；真实 CLI 命令、包名、环境变量与系统路径保持原样。"
        )
    return (
        "Platform identity: you run on "
        f"“{platform_name(locale)}” ({company_name(locale)}). "
        "Use that identity for platform-owned descriptions; preserve actual CLI commands, "
        "package names, environment variables, and system paths exactly."
    )


def _render_request(request: ModelRequest[Any], locale: str) -> ModelRequest[Any]:
    system_message = request.system_message
    content = getattr(system_message, "content", "") if system_message is not None else ""
    rendered = _render_content(content, locale)
    instruction = _identity_instruction(locale)
    if isinstance(rendered, str):
        if instruction not in rendered:
            rendered = f"{rendered.rstrip()}\n\n{instruction}".strip()
    elif isinstance(rendered, list):
        if not any(isinstance(item, dict) and item.get("text") == instruction for item in rendered):
            rendered = [*rendered, {"type": "text", "text": instruction}]
    else:
        rendered = instruction
    if system_message is not None and rendered == content:
        return request
    rendered_system_message = (
        system_message.model_copy(update={"content": rendered})
        if system_message is not None
        else SystemMessage(content=rendered)
    )
    return request.override(system_message=rendered_system_message)


def _configured_locale() -> str:
    configurable = dict(get_config().get("configurable") or {})
    locale = (
        configurable.get("octop_feature_locale")
        or configurable.get("user_locale")
        or configurable.get("locale")
        or configurable.get("lang")
    )
    return normalize_locale(str(locale) if locale else None)


class PlatformIdentityMiddleware(AgentMiddleware[Any, Any]):
    """Resolve platform-owned identity at model-call time without rewriting prompt data."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_render_request(request, _configured_locale()))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_render_request(request, _configured_locale()))


__all__ = ["PlatformIdentityMiddleware", "render_platform_identity"]
