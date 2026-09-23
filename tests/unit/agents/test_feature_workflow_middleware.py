"""A feature's workflow block reaches the turn's system message — and only there.

What this pins is the shape of the injection, because it is where a mistake is
silent: a block that replaced the system message would drop the feature's persona,
one appended after the *messages* would be a second user turn the model could
argue with, and one injected into a turn without a context would apply a workflow
to an agent that never declared a run.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage

from octop.infra.agents import feature_workflow as wf
from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.middleware import feature_workflow as mw


def _definition() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "active",
        "steps": [
            {"id": "extract", "name": "提取要点", "prompt": "读附件", "gate": "confirm"},
            {"id": "draft", "name": "起草", "prompt": "写", "depends_on": ["extract"]},
        ],
        "rules": ["金额逐行核对"],
    }


def _context(**overrides: Any) -> wf.WorkflowRunContext:
    values: dict[str, Any] = {
        "definition": _definition(),
        "name": "报价助手",
        "locale": "zh",
    }
    values.update(overrides)
    return wf.WorkflowRunContext(**values)


def _configure(monkeypatch: Any, context: Any) -> None:
    monkeypatch.setattr(
        mw, "get_config", lambda: {"configurable": {wf.CONFIGURABLE_WORKFLOW_KEY: context}}
    )


def _request(system: SystemMessage | None = None) -> ModelRequest[Any]:
    return ModelRequest(model=object(), messages=[], system_message=system)


def _inject(request: ModelRequest[Any]) -> ModelRequest[Any] | None:
    """Run the middleware with a handler that records what it was given."""
    seen: dict[str, Any] = {}

    def handler(req: ModelRequest[Any]) -> str:
        seen["request"] = req
        return "model-response"

    assert mw.FeatureWorkflowMiddleware(agent_id="feat-quote").wrap_model_call(
        request, handler
    ) == ("model-response")
    return seen["request"]


def test_the_block_reaches_a_turn_that_had_no_system_message(monkeypatch: Any) -> None:
    _configure(monkeypatch, _context())

    injected = _inject(_request())

    assert injected is not None
    content = injected.system_message.content
    assert isinstance(content, str)
    assert "功能工作流：报价助手" in content
    assert "第 1/2 步：提取要点" in content
    assert "等用户确认后再继续" in content
    assert "金额逐行核对" in content


def test_the_block_is_appended_and_never_replaces_the_persona(monkeypatch: Any) -> None:
    _configure(monkeypatch, _context())

    injected = _inject(_request(SystemMessage(content="你是报价助手，先看 SOUL.md。")))

    assert injected is not None
    content = injected.system_message.content
    assert isinstance(content, str)
    assert content.startswith("你是报价助手，先看 SOUL.md。")
    assert content.index("你是报价助手") < content.index("功能工作流")


def test_block_list_content_keeps_its_blocks(monkeypatch: Any) -> None:
    """A block list may carry provider cache markers — the block is added, not merged."""
    _configure(monkeypatch, _context())
    original = [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]

    injected = _inject(_request(SystemMessage(content=original)))

    assert injected is not None
    content = injected.system_message.content
    assert isinstance(content, list)
    assert content[0] == original[0]
    assert content[1]["type"] == "text"
    assert "功能工作流" in content[1]["text"]


def test_a_turn_without_a_context_is_left_exactly_as_it_was(monkeypatch: Any) -> None:
    _configure(monkeypatch, None)
    request = _request(SystemMessage(content="persona"))

    injected = _inject(request)

    assert injected is request


def test_a_context_that_is_not_one_is_ignored(monkeypatch: Any) -> None:
    """A ``configurable`` entry another build wrote is not read as a definition."""
    _configure(monkeypatch, {"definition": {"version": 1}})
    request = _request()

    assert _inject(request) is request


def test_the_overlay_is_rendered_last_and_says_it_wins(monkeypatch: Any) -> None:
    _configure(monkeypatch, _context(overlay="我们的报价含税，不要写不含税价。"))

    injected = _inject(_request())

    assert injected is not None
    content = injected.system_message.content
    assert isinstance(content, str)
    assert content.index("金额逐行核对") < content.index("我们的报价含税")
    assert "最高优先级" in content


def test_a_malformed_definition_never_takes_the_turn_down(monkeypatch: Any) -> None:
    _configure(monkeypatch, _context())

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise ValueError("bad definition")

    monkeypatch.setattr(mw, "render_run_context", boom)
    request = _request(SystemMessage(content="persona"))

    assert _inject(request) is request


def test_run_values_are_rendered_into_the_input_section(monkeypatch: Any) -> None:
    _configure(
        monkeypatch,
        _context(
            definition={
                "version": 1,
                "status": "active",
                "inputs": {
                    "type": "object",
                    "properties": {
                        "customer_name": {
                            "type": "string",
                            "title": {"zh": "客户名称", "en": "Customer"},
                        }
                    },
                },
                "steps": [{"id": "draft", "name": "起草", "prompt": "写"}],
            },
            values={"customer_name": "ACME"},
            attachments=("inbound/quote.pdf",),
        ),
    )

    injected = _inject(_request())

    assert injected is not None
    content = injected.system_message.content
    assert isinstance(content, str)
    assert "客户名称: ACME" in content
    assert "inbound/quote.pdf" in content


@pytest.mark.asyncio
async def test_the_async_hook_injects_the_same_block(monkeypatch: Any) -> None:
    _configure(monkeypatch, _context())
    seen: dict[str, Any] = {}

    async def handler(req: ModelRequest[Any]) -> str:
        seen["request"] = req
        return "model-response"

    result = await mw.FeatureWorkflowMiddleware(agent_id="feat-quote").awrap_model_call(
        _request(), handler
    )

    assert result == "model-response"
    assert "功能工作流" in str(seen["request"].system_message.content)


def test_the_chain_is_a_features_alone() -> None:
    assert mw.feature_workflow_chain(agent_id="feat-x", kind=KIND_AGENT) == []
    chain = mw.feature_workflow_chain(agent_id="feat-quote", kind=KIND_FEATURE)
    assert len(chain) == 1
    assert isinstance(chain[0], mw.FeatureWorkflowMiddleware)
