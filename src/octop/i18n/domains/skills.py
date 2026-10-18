"""``skills.*`` — built-in skill display names (keyed by slug)."""

from __future__ import annotations

from typing import Any

from octop.i18n.loader import _load_all, tr
from octop.infra.utils.locale import Locale, normalize_locale

__all__ = ["all_skill_labels", "skill_display_name"]


def skill_display_name(slug: str | None, locale: str | Locale = "en") -> str:
    if not slug:
        return slug or ""
    try:
        return tr(f"skills.{slug}", locale)
    except KeyError:
        return slug


def all_skill_labels(locale: str | Locale = "en") -> dict[str, str]:
    loc = normalize_locale(str(locale))
    tables = _load_all()
    node: Any = tables.get(loc, {}).get("skills")
    if not isinstance(node, dict):
        node = tables.get("en", {}).get("skills")
    if not isinstance(node, dict):
        return {}
    return {str(key): tr(f"skills.{key}", loc) for key in node if isinstance(node[key], str)}
