"""Behavioral tests for the partial-update runtime branding contract."""

from __future__ import annotations

from types import SimpleNamespace

from octop.api.routers.branding import BRAND_SETTINGS_KEY, DEFAULTS, _merge, _state


class SettingsRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_branding_partial_updates_preserve_omitted_fields_and_reset_empty_values() -> None:
    repo = SettingsRepo()
    server = SimpleNamespace(services=SimpleNamespace(settings_repo=repo))

    initial = _state(server)
    assert initial["name"] == DEFAULTS["name"]
    assert initial["logos"]["mark"] == "/brand/mark-512.png"
    assert initial["is_custom"] is False

    updated = _merge(
        server,
        {"name": {"zh": "新品牌"}, "colors": {"accent": "#123abc"}},
    )
    assert updated["name"] == {"zh": "新品牌", "en": DEFAULTS["name"]["en"]}
    assert updated["full_name"] == DEFAULTS["full_name"]
    assert updated["colors"] == {"brand": DEFAULTS["colors"]["brand"], "accent": "#123ABC"}
    assert updated["is_custom"] is True
    assert BRAND_SETTINGS_KEY in repo.values

    reset_name = _merge(server, {"name": {"zh": ""}})
    assert reset_name["name"] == DEFAULTS["name"]
    assert reset_name["colors"]["accent"] == "#123ABC"
    assert reset_name["is_custom"] is True


def test_branding_without_database_uses_public_factory_defaults() -> None:
    server = SimpleNamespace(services=None)
    state = _state(server)
    assert state["name"] == DEFAULTS["name"]
    assert state["is_custom"] is False
    assert state["version"]
