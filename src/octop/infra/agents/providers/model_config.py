"""Persisted per-model OpenAI protocol preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from octop_harness.config import ModelConfig


@dataclass
class ProviderModelConfig(ModelConfig):
    """Harness model config, keeping the legacy native tool-search inference.

    ``api_mode`` / ``api_fallback`` are part of the runtime config since
    octop-harness 1.0.6, so only the historical rule that a stored
    ``native_tool_search`` entry implies the Responses API needs to live here.
    """

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderModelConfig:
        payload = dict(data)
        if payload.get("native_tool_search") and not payload.get("api_mode"):
            payload["api_mode"] = "responses"
        return cast(ProviderModelConfig, super().from_dict(payload))


__all__ = ["ProviderModelConfig"]
