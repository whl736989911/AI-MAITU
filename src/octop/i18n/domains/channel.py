"""``channel.*`` — IM channel status lines (tool hints, probe errors)."""

from __future__ import annotations

from octop.i18n.loader import lookup, tr
from octop.infra.utils.locale import Locale

__all__ = [
    "channel_permission_revoked",
    "channel_probe_field_label",
    "channel_probe_incomplete",
    "channel_runtime_reason",
    "channel_tool_hint_end",
    "channel_tool_hint_start",
]


def channel_tool_hint_start(tool_name: str, locale: str | Locale = "en") -> str:
    return tr("channel.tool_hint_start", locale, tool_name=tool_name)


def channel_tool_hint_end(tool_name: str, locale: str | Locale = "en") -> str:
    return tr("channel.tool_hint_end", locale, tool_name=tool_name)


def channel_probe_field_label(field: str, locale: str | Locale = "en") -> str:
    """Human label for a credential field; falls back to the raw field name."""
    label = lookup(f"channel.probe.field.{field}", locale)
    return label if isinstance(label, str) else field


def channel_probe_incomplete(missing: list[str], locale: str | Locale = "en") -> str:
    """Localized 'missing credentials' message from structured field names."""
    sep = "、" if str(locale).startswith("zh") else ", "
    fields = sep.join(channel_probe_field_label(name, locale) for name in missing)
    return tr("channel.probe.incomplete", locale, fields=fields)


def channel_runtime_reason(reason: str, locale: str | Locale = "en") -> str:
    """Localized label for a runtime-status reason code (``disabled``/``error``/…)."""
    label = lookup(f"channel.runtime.{reason}", locale)
    return label if isinstance(label, str) else reason


def channel_permission_revoked(locale: str | Locale = "en") -> str:
    """Line an IM user gets when the channel's owner lost its ``channel_<kind>``.

    Design §4.5: 通道权限撤销后，通道消费者停止接收消息. The message is refused —
    and the refusal is spoken, because a bot that goes silent mid-conversation
    reads as a broken bot, not as a revoked authorization.
    """
    return tr("channel.permission_revoked", locale)
