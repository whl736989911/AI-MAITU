"""tests/unit/gateway/test_stream_project_tools.py"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage

from octop.infra.gateway.process.stream_project import (
    dashboard_tool_results,
    enrich_tool_stream_chunk,
    full_tool_call_chunks,
)


def test_enrich_tool_stream_chunk_adds_display_name():
    chunk = {"type": "tool_call_chunk", "name": "read_file", "args": "{"}
    out = enrich_tool_stream_chunk(chunk, "zh")
    assert out["display_name"] == "读取文件"
    assert out["name"] == "read_file"


def test_enrich_tool_stream_chunk_ignores_non_tool():
    chunk = {"type": "token", "content": "hi"}
    assert enrich_tool_stream_chunk(chunk, "zh") is chunk


def test_full_ai_tool_calls_project_to_serializable_chunks() -> None:
    chunk = {
        "type": "state_update",
        "node": "agent",
        "data": {
            "messages": [
                {
                    "type": "ai",
                    "data": {
                        "tool_calls": [
                            {"id": "call-1", "name": "read_file", "args": {"path": "a.py"}},
                            {"id": "call-2", "name": "search", "args": {"query": "b.py"}},
                        ]
                    },
                }
            ]
        },
    }

    projected = full_tool_call_chunks(chunk)

    assert projected == [
        {
            "type": "tool_call_chunk",
            "id": "call-1",
            "name": "read_file",
            "args": '{"path":"a.py"}',
            "index": 0,
            "node": "agent",
        },
        {
            "type": "tool_call_chunk",
            "id": "call-2",
            "name": "search",
            "args": '{"query":"b.py"}',
            "index": 1,
            "node": "agent",
        },
    ]


def test_full_tool_call_projection_ignores_non_state_updates() -> None:
    assert full_tool_call_chunks({"type": "token", "content": "hello"}) == []


def test_dashboard_does_not_replay_tool_output_from_a_previous_turn() -> None:
    old_result = ToolMessage("previous output", tool_call_id="old")
    current = ToolMessage("current output", tool_call_id="new")
    replay = {
        "type": "tool_result",
        "messages": [old_result, HumanMessage("new request"), current],
    }

    projected = dashboard_tool_results(replay)

    assert projected is not None
    assert projected["messages"] == [current]
    assert dashboard_tool_results({**replay, "messages": replay["messages"][:2]}) is None


def test_tool_start_event_carries_localized_hint():
    from octop_gateway.models import MessageEvent

    from octop.i18n import channel_tool_hint_start

    label = "读取文件"
    event = MessageEvent.tool_start(
        label,
        tool_hint_text=channel_tool_hint_start(label, "zh"),
    )
    assert event.metadata["tool_hint_text"] == channel_tool_hint_start(label, "zh")
    assert "正在调用工具" in event.metadata["tool_hint_text"]
