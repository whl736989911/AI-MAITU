from __future__ import annotations

import pytest
from harness_agent.config import ModelConfig, ProviderConfig

from octop.infra.agents.providers.protocol import alternate_protocol_config


@pytest.mark.parametrize(
    ("protocol", "base_url", "expected_protocol", "expected_url"),
    [
        (
            "openai",
            "https://gateway.example/custom/tenant/v1/",
            "anthropic",
            "https://gateway.example/custom/tenant",
        ),
        (
            "anthropic",
            "https://gateway.example/custom/tenant",
            "openai",
            "https://gateway.example/custom/tenant/v1",
        ),
        (
            "openai",
            "https://api.deepseek.com/v1",
            "anthropic",
            "https://api.deepseek.com/anthropic",
        ),
        (
            "openai",
            "https://api.deepseek.com",
            "anthropic",
            "https://api.deepseek.com/anthropic",
        ),
        (
            "anthropic",
            "https://api.deepseek.com/anthropic",
            "openai",
            "https://api.deepseek.com/v1",
        ),
    ],
)
def test_alternate_protocol_config_converts_endpoint_and_preserves_model_settings(
    protocol: str,
    base_url: str,
    expected_protocol: str,
    expected_url: str,
) -> None:
    model = ModelConfig(
        id="reasoning-vision-v2",
        name="Reasoning Vision",
        input=["text", "image"],
        thinking=True,
        max_input_tokens=88_000,
        context_window=96_000,
        max_output_tokens=12_000,
        capabilities=("reasoning", "vision"),
    )
    headers = {"X-Gateway-Key": "gateway-secret", "X-Tenant": "tenant-7"}
    primary = ProviderConfig(
        id="custom-provider",
        name="Custom provider",
        base_url=base_url,
        api_key="provider-secret",
        protocol=protocol,  # type: ignore[arg-type]
        headers=headers,
        session_header="X-Session-ID",
        models=[model],
        stream_usage=False,
    )

    alternate = alternate_protocol_config(primary)

    assert alternate is not primary
    assert alternate.id == primary.id
    assert alternate.name == primary.name
    assert alternate.api_key == "provider-secret"
    assert alternate.protocol == expected_protocol
    assert alternate.base_url == expected_url
    assert alternate.headers == headers
    assert alternate.headers is not primary.headers
    assert alternate.session_header == "X-Session-ID"
    assert alternate.stream_usage is False
    assert len(alternate.models) == 1
    assert alternate.models[0].to_dict() == model.to_dict()
    assert alternate.models[0] is not model
    assert alternate.models is not primary.models

    # Mutating a returned config must never rewrite the primary cached config.
    alternate.headers["X-Tenant"] = "changed"
    alternate.models[0].max_output_tokens = 1
    assert primary.headers == headers
    assert primary.models[0].max_output_tokens == 12_000


def test_alternate_protocol_rejects_non_generic_protocols() -> None:
    provider = ProviderConfig(
        id="bedrock",
        base_url="https://bedrock.example",
        api_key="provider-secret",
        protocol="bedrock",
        models=[ModelConfig(id="model")],
    )

    with pytest.raises(ValueError):
        alternate_protocol_config(provider)


def test_alternate_protocol_rejects_malformed_base_url() -> None:
    provider = ProviderConfig(
        id="bad",
        base_url="not-a-url",
        api_key="provider-secret",
        protocol="openai",
        models=[ModelConfig(id="model")],
    )

    with pytest.raises(ValueError):
        alternate_protocol_config(provider)
