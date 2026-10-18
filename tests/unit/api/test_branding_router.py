"""Behavioral tests for the partial-update runtime branding contract."""

from __future__ import annotations

from types import SimpleNamespace

from octop.api.routers.branding import BRAND_SETTINGS_KEY, _merge, _state
from octop.branding import branding_defaults, configure_branding
from octop.i18n.domains.skills import skill_display_name
from octop.i18n.loader import tr


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
    assert initial["is_custom"] is False

    updated = _merge(
        server,
        {"name": {"zh": "新品牌"}, "colors": {"accent": "#123abc"}},
    )
    assert updated["name"] == {"zh": "新品牌", "en": initial["name"]["en"]}
    assert updated["full_name"] == initial["full_name"]
    assert updated["colors"] == {"brand": initial["colors"]["brand"], "accent": "#123ABC"}
    assert updated["is_custom"] is True
    assert BRAND_SETTINGS_KEY in repo.values

    reset_name = _merge(server, {"name": {"zh": ""}})
    assert reset_name["name"] == initial["name"]
    assert reset_name["colors"]["accent"] == "#123ABC"
    assert reset_name["is_custom"] is True


def test_deployment_and_database_branding_changes_localized_runtime_output(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "brand.config.json"
    config_path.write_text(
        '{"name":{"en":"Northstar","zh":"北辰"},"full_name":{"en":"Northstar Labs","zh":"北辰实验室"},'
        '"short_name":{"en":"NS","zh":"北辰"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAITU_BRAND_CONFIG_FILE", str(config_path))
    branding_defaults.cache_clear()
    repo = SettingsRepo()
    server = SimpleNamespace(services=SimpleNamespace(settings_repo=repo))

    try:
        state = _state(server)
        assert state["name"] == {"en": "Northstar", "zh": "北辰"}
        assert tr("slash.fields.octop", "en") == "Northstar"
        assert tr("slash.fields.octop", "zh") == "北辰"
        assert "Northstar" in skill_display_name("octop-assistant", "en")
        assert "Northstar" in tr("errors.INSTANCE_LICENSE_LOCKED", "en")
        assert "Northstar Labs" in tr("errors.SSO_IDENTITY_TAKEN", "en")

        updated = _merge(server, {"name": {"en": "Aurora", "zh": "极光"}})
        assert updated["name"] == {"en": "Aurora", "zh": "极光"}
        assert tr("slash.fields.octop", "en") == "Aurora"
        assert tr("slash.fields.octop", "zh") == "极光"
        assert "极光" in skill_display_name("octop-assistant", "zh")

        reset = _merge(server, {"name": None})
        assert reset["name"] == {"en": "Northstar", "zh": "北辰"}
        assert tr("slash.fields.octop", "en") == "Northstar"

        alternate_path = tmp_path / "alternate-brand.config.json"
        alternate_path.write_text(
            '{"name":{"en":"Cedar","zh":"杉木"},"full_name":{"en":"Cedar Works","zh":"杉木工坊"},'
            '"short_name":{"en":"Cedar","zh":"杉木"}}',
            encoding="utf-8",
        )
        monkeypatch.setenv("MAITU_BRAND_CONFIG_FILE", str(alternate_path))
        branding_defaults.cache_clear()
        alternate = _state(server)
        assert alternate["name"] == {"en": "Cedar", "zh": "杉木"}
        assert tr("slash.fields.octop", "en") == "Cedar"
        assert "Cedar Works" in tr("errors.SSO_IDENTITY_TAKEN", "en")
    finally:
        monkeypatch.undo()
        branding_defaults.cache_clear()
        configure_branding(None)
