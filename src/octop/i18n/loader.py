"""Load nested locale JSON and resolve dot-path keys."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from octop.branding import company_name, platform_name
from octop.infra.utils.locale import Locale, normalize_locale

_FALLBACK_LOCALE: Locale = "en"


class _IdentityTemplateValues(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _resolve_node(table: dict[str, Any], key: str) -> str | None:
    node: Any = table
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


@lru_cache(maxsize=1)
def _load_all() -> dict[Locale, dict[str, Any]]:
    out: dict[Locale, dict[str, Any]] = {}
    for loc in ("en", "zh"):
        raw = resources.files("octop.i18n").joinpath(f"{loc}.json").read_text(encoding="utf-8")
        out[loc] = json.loads(raw)
    return out


def lookup(key: str, locale: str | Locale) -> str | None:
    loc = normalize_locale(str(locale))
    tables = _load_all()
    text = _resolve_node(tables[loc], key)
    if text is not None:
        return text
    if loc != _FALLBACK_LOCALE:
        return _resolve_node(tables[_FALLBACK_LOCALE], key)
    return None


def tr(key: str, locale: str | Locale = "en", **kwargs: object) -> str:
    """Resolve *key* (e.g. ``slash.help.title``) for *locale*, fallback to ``en``."""
    text = lookup(key, locale)
    if text is None:
        raise KeyError(key)
    if not kwargs:
        if "{platform_name}" not in text and "{company_name}" not in text:
            return text
        identity_values = _IdentityTemplateValues(
            platform_name=platform_name(str(locale)),
            company_name=company_name(str(locale)),
        )
        return text.format_map(identity_values)
    values = {
        "platform_name": platform_name(str(locale)),
        "company_name": company_name(str(locale)),
        **kwargs,
    }
    return text.format(**values)


def flatten_keys(table: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for name, value in table.items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            keys.update(flatten_keys(value, path))
        elif isinstance(value, str):
            keys.add(path)
    return keys


def all_keys_for_locale(locale: Locale = "en") -> set[str]:
    return flatten_keys(_load_all()[locale])
