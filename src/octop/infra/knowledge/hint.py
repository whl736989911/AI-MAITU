"""Per-turn visibility and descriptions for source-scoped knowledge tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools.base import BaseTool
from langgraph.config import get_config

_KNOWLEDGE_TOOLS = frozenset(
    {
        "browse_knowledge",
        "search_knowledge_paths",
        "read_knowledge_collection",
        "read_knowledge_document",
        "export_knowledge_original",
        "record_knowledge_observation",
    }
)


def catalog_for_selected_bases(
    bases: Sequence[Any],
    selected_ids: Sequence[str],
) -> list[dict[str, str]]:
    """Build compact title/description rows for visible selected bases."""
    by_id = {str(getattr(base, "id", "")): base for base in bases}
    catalog = []
    for raw_id in selected_ids:
        kb_id = str(raw_id).strip()
        base = by_id.get(kb_id)
        if not kb_id or base is None:
            continue
        name = getattr(base, "name", "")
        description = getattr(base, "description", "")
        catalog.append(
            {
                "id": kb_id,
                "name": name.strip() if isinstance(name, str) and name.strip() else kb_id,
                "description": description.strip() if isinstance(description, str) else "",
            }
        )
    return catalog


def _catalog_from_config() -> list[dict[str, str]]:
    cfg = get_config().get("configurable") or {}
    raw = cfg.get("knowledge_base_catalog")
    if not isinstance(raw, list):
        return []
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or item.get("id") or "").strip(),
            "description": str(item.get("description") or "").strip(),
        }
        for item in raw
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    ]


def _with_knowledge_tools(request: ModelRequest[Any]) -> ModelRequest[Any]:
    cfg = get_config().get("configurable") or {}
    selected = cfg.get("knowledge_base_ids")
    selected_ids = (
        {str(item).strip() for item in selected if str(item).strip()}
        if isinstance(selected, list)
        else set()
    )
    catalog = [item for item in _catalog_from_config() if item["id"] in selected_ids]
    available = bool(selected_ids and catalog)
    tools_in = list(request.tools or [])
    tools_out: list[Any] = []
    changed = False
    catalog_lines = "\n".join(
        f"- {item['id']}: {item['name']}"
        + (f" — {item['description']}" if item["description"] else "")
        for item in catalog
    )
    for tool in tools_in:
        if isinstance(tool, BaseTool) and tool.name in _KNOWLEDGE_TOOLS:
            if not available:
                changed = True
                continue
            if tool.name == "browse_knowledge":
                description = f"{tool.description}\nAttached this turn:\n{catalog_lines}"
                if description != tool.description:
                    tools_out.append(tool.model_copy(update={"description": description}))
                    changed = True
                    continue
        tools_out.append(tool)
    if changed:
        return request.override(tools=tools_out)
    return request


class KnowledgeToolsHintMiddleware(AgentMiddleware[Any, Any]):
    """Expose the source tools only when this turn has selected, visible bases."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_with_knowledge_tools(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_with_knowledge_tools(request))


__all__ = ["KnowledgeToolsHintMiddleware", "catalog_for_selected_bases"]
