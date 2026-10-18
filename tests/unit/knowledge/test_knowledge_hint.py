"""Unit tests for source-tool visibility and attached knowledge-base catalogues."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain.agents.middleware import ModelRequest
from langchain_core.tools import StructuredTool
from langgraph.config import var_child_runnable_config

from octop.infra.knowledge.hint import (
    KnowledgeToolsHintMiddleware,
    catalog_for_selected_bases,
)


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _dummy_tool(name: str, description: str = "base") -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda: "x",
        name=name,
        description=description,
    )


def test_catalog_for_selected_bases_includes_visible_names() -> None:
    bases = [
        SimpleNamespace(id="kb-1", name="Refund policy", description="Retail refund rules"),
        SimpleNamespace(id="kb-2", name="", description=""),
    ]
    assert catalog_for_selected_bases(bases, ["kb-1", "kb-2", "missing"]) == [
        {"id": "kb-1", "name": "Refund policy", "description": "Retail refund rules"},
        {"id": "kb-2", "name": "kb-2", "description": ""},
    ]


def test_middleware_keeps_source_tools_with_selected_visible_base() -> None:
    request = ModelRequest(
        model=MagicMock(),
        messages=[],
        tools=[_dummy_tool("browse_knowledge"), _dummy_tool("other", "keep")],
    )
    captured: list[ModelRequest] = []

    with _configurable(
        knowledge_base_ids=["kb-1"],
        knowledge_base_catalog=[{"id": "kb-1", "name": "Policy"}],
    ):
        KnowledgeToolsHintMiddleware().wrap_model_call(
            request, lambda req: captured.append(req) or object()
        )

    assert [tool.name for tool in captured[0].tools] == ["browse_knowledge", "other"]


def test_middleware_hides_source_tools_when_no_base_selected() -> None:
    request = ModelRequest(
        model=MagicMock(),
        messages=[],
        tools=[
            _dummy_tool("browse_knowledge"),
            _dummy_tool("read_knowledge_document"),
            _dummy_tool("other", "keep"),
        ],
    )
    captured: list[ModelRequest] = []

    with _configurable(knowledge_base_ids=[], knowledge_base_catalog=[]):
        KnowledgeToolsHintMiddleware().wrap_model_call(
            request, lambda req: captured.append(req) or object()
        )

    assert [tool.name for tool in captured[0].tools] == ["other"]
