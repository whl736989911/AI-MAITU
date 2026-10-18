import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from octop.branding import configure_branding
from octop.infra.agents.middleware import platform_identity


@pytest.fixture(autouse=True)
def reset_branding() -> None:
    yield
    configure_branding(None)


def test_identity_middleware_renders_owned_tokens_and_appends_identity(monkeypatch) -> None:
    configure_branding(
        {
            "name": {"zh": "甲方平台", "en": "Alpha Platform"},
            "full_name": {"zh": "甲方科技有限公司", "en": "Alpha Company"},
            "short_name": {"zh": "甲方", "en": "Alpha"},
        }
    )
    monkeypatch.setattr(
        platform_identity,
        "get_config",
        lambda: {"configurable": {"locale": "en-US"}},
    )
    original_text = "Use {{platform_name}} with `octop run` and ~/.octop/config.json."
    original_message = SystemMessage(content=original_text)
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-4o-mini", api_key="test"),
        messages=[],
        system_message=original_message,
    )
    observed = {}

    platform_identity.PlatformIdentityMiddleware().wrap_model_call(
        request, lambda current: observed.setdefault("request", current)
    )

    content = observed["request"].system_message.content
    assert "Alpha Platform" in content
    assert "Alpha Company" in content
    assert "octop run" in content
    assert "~/.octop/config.json" in content
    assert "{{platform_name}}" not in content
    assert original_message.content == original_text


def test_identity_middleware_defaults_locale_and_preserves_unmarked_user_prompt(
    monkeypatch,
) -> None:
    configure_branding(
        {
            "name": {"zh": "乙方平台", "en": "Beta Platform"},
            "full_name": {"zh": "乙方科技有限公司", "en": "Beta Company"},
            "short_name": {"zh": "乙方", "en": "Beta"},
        }
    )
    monkeypatch.setattr(platform_identity, "get_config", lambda: {"configurable": {}})
    original_text = "Custom user SOUL: say exactly `octop --agent demo status`."
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-4o-mini", api_key="test"),
        messages=[],
        system_message=SystemMessage(content=original_text),
    )
    observed = {}

    platform_identity.PlatformIdentityMiddleware().wrap_model_call(
        request, lambda current: observed.setdefault("request", current)
    )

    content = observed["request"].system_message.content
    assert "乙方平台" in content
    assert "乙方科技有限公司" in content
    assert "Custom user SOUL: say exactly `octop --agent demo status`." in content
    assert request.system_message.content == original_text
