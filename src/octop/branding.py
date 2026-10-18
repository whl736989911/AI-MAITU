"""Platform presentation identity, independent of package and protocol identifiers."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from octop.branding_defaults import DEFAULTS

BRAND_SETTINGS_KEY = "runtime_branding"
_runtime_names: dict[str, dict[str, str]] = {}


@lru_cache(maxsize=1)
def branding_defaults() -> dict[str, dict[str, str]]:
    """Load deployment defaults once; an explicitly configured bad file is an error."""
    values = {group: fields.copy() for group, fields in DEFAULTS.items()}
    filename = os.environ.get("MAITU_BRAND_CONFIG_FILE", "").strip()
    if not filename:
        return values
    raw: Any = json.loads(Path(filename).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deployment brand configuration must be a JSON object")
    for group in values:
        source = raw.get({"colors": "color", "logos": "logo"}.get(group, group), raw.get(group))
        if source is None:
            continue
        if not isinstance(source, dict):
            raise ValueError(f"invalid deployment brand group: {group}")
        for field in values[group]:
            if field not in source:
                continue
            value = source[field]
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError(f"invalid deployment brand field: {group}.{field}")
            if group in {"name", "full_name", "short_name"} and (
                not value.strip() or any(ord(char) < 32 for char in value)
            ):
                raise ValueError(f"invalid deployment brand name: {group}.{field}")
            values[group][field] = value.strip()
    return values


def configure_branding(values: dict[str, Any] | None) -> None:
    """Apply the existing runtime-branding setting to subsequent runtime messages."""
    merged: dict[str, dict[str, str]] = {}
    defaults = branding_defaults()
    for group in ("name", "full_name", "short_name"):
        merged[group] = defaults[group].copy()
        custom = (values or {}).get(group, {})
        if isinstance(custom, dict):
            for locale in ("zh", "en"):
                value = custom.get(locale)
                if isinstance(value, str) and value.strip():
                    merged[group][locale] = value.strip()
    global _runtime_names
    _runtime_names = merged


def configure_branding_from_json(raw: str | None) -> None:
    """Apply stored branding JSON; malformed settings retain API fallback semantics."""
    try:
        values = json.loads(raw or "{}")
    except (TypeError, ValueError):
        values = {}
    configure_branding(values if isinstance(values, dict) else {})


def _name(group: str, locale: str | None) -> str:
    language = "zh" if (locale or "en").lower().startswith("zh") else "en"
    return (_runtime_names or branding_defaults())[group][language]


def platform_name(locale: str | None = None) -> str:
    return _name("name", locale)


def company_name(locale: str | None = None) -> str:
    return _name("full_name", locale)


def platform_short_name(locale: str | None = None) -> str:
    return _name("short_name", locale)
