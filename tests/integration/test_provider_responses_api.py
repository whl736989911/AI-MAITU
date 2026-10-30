"""Local HTTP regressions for per-model Responses API construction."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from harness_agent.config import ProviderConfig
from openai import APIStatusError

from octop.infra.agents.providers.harness_factory import OctopChatModelFactory
from octop.infra.agents.providers.model_config import ProviderModelConfig


class _ProviderServer(ThreadingHTTPServer):
    requests: list[tuple[str, dict[str, Any]]]
    responses_status: int
    responses_error_code: str


@pytest.fixture
def provider_http_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append((self.path, body))  # type: ignore[attr-defined]
            if self.path.endswith("/responses"):
                status = self.server.responses_status  # type: ignore[attr-defined]
                if status != 200:
                    payload = {
                        "error": {
                            "message": "Responses API is not supported by this endpoint",
                            "type": "invalid_request_error",
                            "param": None,
                            "code": self.server.responses_error_code,  # type: ignore[attr-defined]
                        }
                    }
                else:
                    payload = {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": 1,
                        "status": "completed",
                        "model": "test-model",
                        "output": [
                            {
                                "id": "msg_test",
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {"type": "output_text", "text": "ok", "annotations": []}
                                ],
                            }
                        ],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    }
            else:
                if body.get("stream"):
                    self._chat_stream()
                    return
                payload = {
                    "id": "chat_test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
                status = 200
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _chat_stream(self) -> None:
            events = []
            for text, finish in (("first", None), ("second", None), ("", "stop")):
                payload = {
                    "id": "chat_stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
                }
                events.append(f"data: {json.dumps(payload)}\n\n")
            encoded = ("".join(events) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            pass

    server = _ProviderServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.responses_status = 200
    server.responses_error_code = "unsupported_endpoint"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _build(base_url: str, *, fallback: str = "none"):
    provider = ProviderConfig(
        id="test-provider",
        name="test-provider",
        protocol="openai",
        base_url=base_url,
        api_key="test-key",
    )
    model = ProviderModelConfig(
        id="test-model",
        api_mode="responses",
        api_fallback=fallback,
    )
    provider.models = [model]
    return OctopChatModelFactory([provider]), model


@pytest.mark.asyncio
async def test_responses_endpoint_uses_store_false(provider_http_server) -> None:
    base_url, server = provider_http_server
    factory, model = _build(base_url)
    try:
        chat = factory.get_chat_model(f"test-provider/{model.id}")
        result = await chat.ainvoke("ping")
        assert result.text == "ok"
        assert len(server.requests) == 1
        path, body = server.requests[0]
        assert path.endswith("/responses")
        assert body["store"] is False
    finally:
        await factory.aclose()


@pytest.mark.asyncio
async def test_responses_protocol_rejection_falls_back_once(provider_http_server) -> None:
    base_url, server = provider_http_server
    server.responses_status = 404
    factory, model = _build(base_url, fallback="chat_completions")
    try:
        chat = factory.get_chat_model(f"test-provider/{model.id}").bind_tools(
            [
                {
                    "type": "function",
                    "function": {"name": "ping_tool", "parameters": {"type": "object"}},
                }
            ]
        )
        result = await chat.ainvoke("ping")
        assert result.text == "ok"
        assert result.response_metadata["octop_api_fallback"] == "chat_completions"
        assert [path.split("/v1/", 1)[-1] for path, _ in server.requests] == [
            "responses",
            "chat/completions",
        ]
        assert len(server.requests) == 2
    finally:
        await factory.aclose()


@pytest.mark.asyncio
async def test_tool_bound_fallback_preserves_actual_streaming(provider_http_server) -> None:
    base_url, server = provider_http_server
    server.responses_status = 404
    factory, model = _build(base_url, fallback="chat_completions")
    try:
        chat = factory.get_chat_model(f"test-provider/{model.id}").bind_tools(
            [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        )
        texts = [chunk.text async for chunk in chat.astream("ping") if chunk.text]
        assert texts == ["first", "second"]
        assert [path for path, _ in server.requests] == ["/v1/responses", "/v1/chat/completions"]
        assert all(body.get("stream") is True for _, body in server.requests)
    finally:
        await factory.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["insufficient_quota", "model_not_found"])
async def test_provider_error_code_overrides_protocol_error_wording(
    provider_http_server, code: str
) -> None:
    base_url, server = provider_http_server
    server.responses_status = 400
    server.responses_error_code = code
    factory, model = _build(base_url, fallback="chat_completions")
    try:
        with pytest.raises(APIStatusError):
            await factory.get_chat_model(f"test-provider/{model.id}").ainvoke("ping")
        assert [path for path, _ in server.requests] == ["/v1/responses"]
    finally:
        await factory.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_auth_quota_and_server_errors_do_not_fall_back(
    provider_http_server, status: int
) -> None:
    base_url, server = provider_http_server
    server.responses_status = status
    factory, model = _build(base_url, fallback="chat_completions")
    try:
        chat = factory.get_chat_model(f"test-provider/{model.id}")
        with pytest.raises(APIStatusError) as error:
            await chat.ainvoke("ping")
        assert error.value.status_code == status
        assert {path for path, _ in server.requests} == {"/v1/responses"}
    finally:
        await factory.aclose()


@pytest.mark.asyncio
async def test_legacy_model_uses_chat_completions_endpoint(provider_http_server) -> None:
    base_url, server = provider_http_server
    model = ProviderModelConfig.from_dict({"id": "test-model"})
    provider = ProviderConfig(
        id="legacy", protocol="openai", base_url=base_url, api_key="test-key", models=[model]
    )
    factory = OctopChatModelFactory([provider])
    try:
        result = await factory.get_chat_model("legacy/test-model").ainvoke("ping")
        assert result.text == "ok"
        assert [path for path, _ in server.requests] == ["/v1/chat/completions"]
    finally:
        await factory.aclose()
