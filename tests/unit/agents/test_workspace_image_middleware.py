"""Unit tests for WorkspaceImageMaterializeMiddleware."""

from __future__ import annotations

import base64
import io
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from PIL import Image

from octop.api.common.attachments import save_attachment
from octop.infra import errors as infra_errors
from octop.infra.agents.middleware import workspace_image as workspace_image_middleware
from octop.infra.agents.middleware.binary_read_guard import BinaryReadGuardMiddleware
from octop.infra.agents.middleware.workspace_image import WorkspaceImageMaterializeMiddleware
from octop.infra.gateway.media.attachment_hints import make_workspace_image_ref

_PNG_BUFFER = io.BytesIO()
Image.new("RGB", (2, 2), (20, 90, 200)).save(_PNG_BUFFER, format="PNG")
_PNG = _PNG_BUFFER.getvalue()


@pytest.mark.asyncio
async def test_awrap_model_call_expands_workspace_image_ref() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(
            LocalShellBackend(root_dir=ws_dir, virtual_mode=False),
            ws_dir,
        )
        stored = await save_attachment(
            workspace,
            owner_id=1,
            filename="shot.png",
            media_type="image/png",
            data=_PNG,
        )
        ref = make_workspace_image_ref(workspace_path=stored.data_path, mime_type="image/png")
        human = HumanMessage(content=[{"type": "text", "text": "see"}, ref])
        captured: dict[str, Any] = {}
        vision_model = MagicMock()
        model_for_images = MagicMock(return_value=vision_model)

        async def handler(request: Any) -> Any:
            captured["messages"] = request.messages
            captured["model"] = request.model
            return MagicMock()

        request = MagicMock()
        request.messages = [human]
        request.override = lambda **kw: MagicMock(**kw)

        mw = WorkspaceImageMaterializeMiddleware(
            workspace=workspace,
            model_for_images=model_for_images,
        )
        await mw.awrap_model_call(request, handler)
        assert captured["model"] is vision_model
        model_for_images.assert_called_once_with(request, False)

        expanded = captured["messages"][0].content[1]
        url = expanded["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        with Image.open(io.BytesIO(decoded)) as image:
            image.load()
            assert image.size == (2, 2)
            assert image.getpixel((0, 0)) == (20, 90, 200)
        # Original checkpoint message is unchanged.
        assert human.content[1]["image_url"]["url"].startswith("workspace://")


@pytest.mark.asyncio
async def test_image_protocol_failure_retries_with_alternate_and_preserves_image_bytes() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(LocalShellBackend(root_dir=ws_dir, virtual_mode=False), ws_dir)
        stored = await save_attachment(
            workspace, owner_id=1, filename="shot.png", media_type="image/png", data=_PNG
        )
        ref = make_workspace_image_ref(workspace_path=stored.data_path, mime_type="image/png")
        request_bodies: list[bytes] = []

        def primary_transport(request: httpx.Request) -> httpx.Response:
            request_bodies.append(request.read())
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "primary rejected image",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "unsupported_image",
                    }
                },
            )

        def alternate_transport(request: httpx.Request) -> httpx.Response:
            request_bodies.append(request.read())
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "image received"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(primary_transport)) as primary_client,
            httpx.AsyncClient(
                transport=httpx.MockTransport(alternate_transport)
            ) as alternate_client,
        ):
            primary = ChatOpenAI(
                model="gpt-4o", api_key="test", http_async_client=primary_client, max_retries=0
            )
            alternate = ChatOpenAI(
                model="gpt-4o", api_key="test", http_async_client=alternate_client, max_retries=0
            )
            selected: list[bool] = []

            def model_for_images(_: Any, use_alternate: bool) -> Any:
                selected.append(use_alternate)
                return alternate if use_alternate else primary

            model_request = MagicMock()
            model_request.messages = [
                HumanMessage(content=[{"type": "text", "text": "describe"}, ref])
            ]
            model_request.override = lambda **kwargs: MagicMock(**kwargs)

            async def handler(prepared: Any) -> Any:
                return await prepared.model.ainvoke(prepared.messages)

            result = await WorkspaceImageMaterializeMiddleware(
                workspace=workspace, model_for_images=model_for_images
            ).awrap_model_call(model_request, handler)

        assert result.content == "image received"
        assert selected == [False, True]
        assert len(request_bodies) == 2
        encoded_image = base64.b64encode(_PNG)
        assert encoded_image in request_bodies[0]
        assert encoded_image in request_bodies[1]


@pytest.mark.asyncio
async def test_text_only_request_does_not_construct_alternate_model() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(LocalShellBackend(root_dir=ws_dir, virtual_mode=False), ws_dir)
        request = MagicMock()
        request.messages = [HumanMessage(content="plain text")]
        request.override = lambda **kwargs: MagicMock(**kwargs)
        model_for_images = MagicMock()

        async def handler(_: Any) -> Any:
            return "text result"

        result = await WorkspaceImageMaterializeMiddleware(
            workspace=workspace, model_for_images=model_for_images
        ).awrap_model_call(request, handler)

        assert result == "text result"
        model_for_images.assert_not_called()


@pytest.mark.asyncio
async def test_image_request_does_not_retry_arbitrary_handler_error() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(LocalShellBackend(root_dir=ws_dir, virtual_mode=False), ws_dir)
        request = MagicMock()
        request.messages = [
            HumanMessage(
                content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
            )
        ]
        request.override = lambda **kwargs: MagicMock(**kwargs)
        model_for_images = MagicMock(return_value=MagicMock())

        async def handler(_: Any) -> Any:
            raise ValueError("middleware logic failure")

        with pytest.raises(ValueError, match="middleware logic failure"):
            await WorkspaceImageMaterializeMiddleware(
                workspace=workspace, model_for_images=model_for_images
            ).awrap_model_call(request, handler)
        model_for_images.assert_called_once_with(request, False)


@pytest.mark.asyncio
async def test_image_request_does_not_retry_midstream_api_failure() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(LocalShellBackend(root_dir=ws_dir, virtual_mode=False), ws_dir)
        request = MagicMock()
        request.messages = [
            HumanMessage(
                content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
            )
        ]
        request.override = lambda **kwargs: MagicMock(**kwargs)
        model_for_images = MagicMock(return_value=MagicMock())

        async def handler(_: Any) -> Any:
            async def stream() -> Any:
                raise openai.APIConnectionError(
                    request=httpx.Request("POST", "https://api.example")
                )
                yield  # Make this an async generator.

            return stream()

        response_stream = await WorkspaceImageMaterializeMiddleware(
            workspace=workspace, model_for_images=model_for_images
        ).awrap_model_call(request, handler)
        with pytest.raises(openai.APIConnectionError):
            async for _ in response_stream:
                pass
        model_for_images.assert_called_once_with(request, False)


@pytest.mark.asyncio
async def test_both_protocol_failures_include_both_provider_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(LocalShellBackend(root_dir=ws_dir, virtual_mode=False), ws_dir)
        request = MagicMock()
        request.messages = [
            HumanMessage(
                content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
            )
        ]
        request.override = lambda **kwargs: MagicMock(**kwargs)
        selected: list[bool] = []
        localized: dict[str, str] = {}

        def localize(key: str, _locale: str, **kwargs: str) -> str:
            localized.update(kwargs)
            return key

        monkeypatch.setattr(workspace_image_middleware, "tr", localize)

        def model_for_images(_: Any, alternate: bool) -> Any:
            selected.append(alternate)
            return object()

        async def handler(_: Any) -> Any:
            if not selected[-1]:
                raise openai.APIConnectionError(
                    request=httpx.Request("POST", "https://primary.example"),
                    message="primary network unavailable",
                )
            response = httpx.Response(
                400, request=httpx.Request("POST", "https://alternate.example")
            )
            raise anthropic.APIStatusError("alternate rejected image", response=response, body={})

        with pytest.raises(infra_errors.OctopError) as raised:
            await WorkspaceImageMaterializeMiddleware(
                workspace=workspace, model_for_images=model_for_images
            ).awrap_model_call(request, handler)

        assert raised.value.code is infra_errors.ErrorCode.AGENT_FAILED
        assert selected == [False, True]
        assert "primary network unavailable" in localized["first_error"]
        assert "alternate rejected image" in localized["second_error"]


@pytest.mark.asyncio
async def test_tool_image_moves_after_complete_batch_into_vision_user_message() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(
            LocalShellBackend(root_dir=ws_dir, virtual_mode=False),
            ws_dir,
        )
        media_path = "unpacked/word/media/image1.png"
        image_file = Path(ws_dir) / Path(*media_path.split("/"))
        image_file.parent.mkdir(parents=True)
        image_file.write_bytes(_PNG)
        tool_request = MagicMock()
        tool_request.tool_call = {
            "name": "read_file",
            "args": {"file_path": media_path},
            "id": "read-image",
        }

        async def unexpected_handler(_: Any) -> Any:
            raise AssertionError("image reads must not reach the text-only read_file tool")

        image_result = await BinaryReadGuardMiddleware(workspace=workspace).awrap_tool_call(
            tool_request, unexpected_handler
        )
        original_ref = image_result.content[1]
        image_result = image_result.model_copy(
            update={
                "content": [
                    image_result.content[0],
                    original_ref,
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "AQ=="},
                    },
                    {"type": "input_image", "image_url": "data:image/png;base64,AQ=="},
                ]
            }
        )
        text_result = ToolMessage(content="plain result", tool_call_id="read-text")
        preceding_assistant = AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file", "args": {}, "id": "read-image", "type": "tool_call"},
                {"name": "read_file", "args": {}, "id": "read-text", "type": "tool_call"},
            ],
        )
        captured: dict[str, Any] = {}
        vision_model = MagicMock()
        vision_requests: list[Any] = []

        async def model_handler(request: Any) -> Any:
            captured["messages"] = request.messages
            captured["model"] = request.model
            return MagicMock()

        model_request = MagicMock()
        model_request.messages = [preceding_assistant, image_result, text_result]
        model_request.override = lambda **kwargs: MagicMock(**kwargs)
        await WorkspaceImageMaterializeMiddleware(
            workspace=workspace,
            model_for_images=lambda request, alternate: (
                vision_requests.append((request, alternate)) or vision_model
            ),
        ).awrap_model_call(model_request, model_handler)

        outgoing = captured["messages"]
        assert captured["model"] is vision_model
        assert vision_requests == [(model_request, False)]
        assert [
            message.tool_call_id for message in outgoing if isinstance(message, ToolMessage)
        ] == [
            "read-image",
            "read-text",
        ]
        assert isinstance(outgoing[1], ToolMessage)
        assert isinstance(outgoing[2], ToolMessage)
        assert all(
            not (
                isinstance(block, dict)
                and block.get("type") in {"image_url", "image", "input_image"}
            )
            for message in outgoing
            if isinstance(message, ToolMessage)
            for block in (message.content if isinstance(message.content, list) else [])
        )
        assert outgoing[2].content == "plain result"
        image_message = outgoing[3]
        assert isinstance(image_message, HumanMessage)
        image_block = image_message.content[0]
        assert [block["type"] for block in image_message.content] == [
            "image_url",
            "image",
            "input_image",
        ]
        image_url = image_block["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
        image_data = base64.b64decode(image_url.split(",", 1)[1])
        with Image.open(io.BytesIO(image_data)) as image:
            image.load()
            assert image.size == (2, 2)
            assert image.getpixel((0, 0)) == (20, 90, 200)

        assert image_result.content[1] is original_ref
        assert original_ref["image_url"]["url"] == f"workspace://{media_path}"


@pytest.mark.asyncio
async def test_missing_vision_model_does_not_call_downstream_model() -> None:
    with tempfile.TemporaryDirectory() as ws_dir:
        workspace = BackendWorkspace(
            LocalShellBackend(root_dir=ws_dir, virtual_mode=False),
            ws_dir,
        )
        request = MagicMock()
        request.messages = [
            HumanMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AA=="},
                    }
                ]
            )
        ]
        request.override = MagicMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
        model_for_images = MagicMock(side_effect=LookupError("vision unavailable"))
        downstream_called = False

        async def handler(_: Any) -> Any:
            nonlocal downstream_called
            downstream_called = True
            return MagicMock()

        with pytest.raises(LookupError):
            await WorkspaceImageMaterializeMiddleware(
                workspace=workspace,
                model_for_images=model_for_images,
            ).awrap_model_call(request, handler)

        assert not downstream_called
