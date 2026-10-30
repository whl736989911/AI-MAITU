"""Persisted per-model OpenAI protocol preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness_agent.config import ModelConfig


@dataclass
class ProviderModelConfig(ModelConfig):
    """Harness model config with OpenAI API selection kept through deserialization."""

    api_mode: str = "chat_completions"
    api_fallback: str = "none"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.api_mode not in {"chat_completions", "responses"}:
            raise ValueError("api_mode must be 'chat_completions' or 'responses'")
        if self.api_fallback not in {"none", "chat_completions"}:
            raise ValueError("api_fallback must be 'none' or 'chat_completions'")
        if self.api_fallback != "none" and self.api_mode != "responses":
            raise ValueError("api_fallback requires api_mode='responses'")
        if self.native_tool_search and self.api_mode != "responses":
            raise ValueError("native_tool_search requires api_mode='responses'")
        if self.api_fallback != "none" and self.native_tool_search:
            raise ValueError("api_fallback is incompatible with native_tool_search")

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(api_mode=self.api_mode, api_fallback=self.api_fallback)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderModelConfig:
        base = ModelConfig.from_dict(data)
        return cls(
            id=base.id,
            name=base.name,
            enabled=base.enabled,
            input=base.input,
            thinking=base.thinking,
            max_input_tokens=base.max_input_tokens,
            context_window=base.context_window,
            max_output_tokens=base.max_output_tokens,
            native_tool_search=base.native_tool_search,
            capabilities=base.capabilities,
            api_mode=str(
                data.get("api_mode")
                or ("responses" if data.get("native_tool_search") else "chat_completions")
            ),
            api_fallback=str(data.get("api_fallback") or "none"),
        )


__all__ = ["ProviderModelConfig"]
