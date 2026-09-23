"""Tools a feature's own agent uses to write its own workflow, in conversation.

The author of a feature talks to it. These tools are what let that conversation
*change* the feature instead of only describing it: read the current document, save a
whole new one, apply a small diff (and hand back the change so it can be undone), list
the runs it has produced, and read back the author's own overlay.

**Only the author, only their own feature.** Each tool resolves the feature from the
turn's own agent id (or an id the model passed in) and refuses everything else — the
same rule the HTTP surface applies as "the author writes it", expressed here without
the API layer, which `infra/` may not import. The *change* logic is not duplicated:
apply and undo go through :mod:`octop.infra.agents.feature_workflow_service`, the one
implementation both entry points share, so a change applied here is recorded and
reversible exactly like one applied from the dashboard.

**Diffs, not rewrites.** ``feature_workflow_change`` is the tool to reach for while
working something out with a person: it names the places it touches, refuses the whole
batch when one of them has moved since, and can be undone item by item. Saving the
whole document is for the first draft, or for a deliberate rewrite.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import BaseModel, Field

from octop.infra.agents import feature_workflow as wf
from octop.infra.agents import feature_workflow_changes as wf_changes
from octop.infra.agents import feature_workflow_service as service
from octop.infra.agents.kinds import feature_agent_id_for, feature_id_of_agent, is_feature_agent
from octop.infra.db.repos.feature_workflow_changes import TARGET_DEFINITION, TARGET_OVERLAY


class WorkflowChangeItemArg(BaseModel):
    """One place a change touches, as the model writes it."""

    path: str = Field(
        ...,
        description=(
            "Where it applies: '/rules/2', '/steps/1/gate', "
            "'/inputs/properties/customer_name/title/zh'. '-' as a list index appends."
        ),
    )
    before: Any = Field(
        default=None,
        description="What that place says now. Use null when nothing is there yet.",
    )
    after: Any = Field(..., description="What it should say afterwards.")


def _turn_identity() -> tuple[str, int]:
    """``(agent_id, user_id)`` of the turn this tool is called in."""
    try:
        config = get_config()
    except RuntimeError:  # outside a run
        return "", 0
    configurable = (config.get("configurable") or {}) if isinstance(config, dict) else {}
    agent_id = str(configurable.get("agent_id") or "")
    raw = configurable.get("user")
    user_id = int(raw) if isinstance(raw, int | str) and str(raw).isdigit() else 0
    return agent_id, user_id


def _resolve_owned_feature(registry: Any, requested: str) -> tuple[str, str, int]:
    """``(agent_id, feature_id, user_id)`` for a feature the caller authored.

    Resolution accepts either id — the feature's public id ("quote-helper") or its
    agent's ("feat-quote-helper") — because a person says the first and the platform
    passes the second, and making the model translate between them is a way to get it
    wrong for no benefit.
    """
    agent_id, user_id = _turn_identity()
    if not agent_id or not user_id:
        raise ValueError("this tool needs the turn's agent and user")
    raw = (requested or "").strip()
    target = (
        agent_id if not raw else (raw if raw.startswith("feat-") else feature_agent_id_for(raw))
    )
    row = registry.get_row(target)
    if row is None or not is_feature_agent(str(getattr(row, "kind", ""))):
        raise ValueError(f"{target!r} is not a feature's own agent")
    if getattr(row, "user_id", None) != user_id:
        raise ValueError("only the feature's author can change it")
    feature_id = feature_id_of_agent(target)
    if feature_id is None:
        raise ValueError(f"{target!r} carries no feature id")
    return target, feature_id, user_id


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _err(exc: Exception) -> str:
    payload: dict[str, Any] = {"error": str(exc)}
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        payload["details"] = details
    return json.dumps(payload, ensure_ascii=False, indent=2)


_SAVE_DESC = (
    "Replace this feature's whole workflow document. Use it for the first draft, or "
    "for a deliberate rewrite. The document is validated first and the refusal lists "
    "every problem at once; nothing is written when anything is wrong. Keep "
    "'status': 'draft' while you are still working it out with the author — an "
    "'active' document must have every step's id, name and prompt. The shape is: "
    "{version: 1, status, inputs: {type: 'object', properties: {...}, required: [...]}, "
    "steps: [{id, name, prompt, depends_on: [], skills: [], subagents: [], tools: [], "
    "gate: 'auto'|'confirm'}], outputs: [{name, form, path}], rules: [str]}. Field "
    "titles are {'zh': ..., 'en': ...} pairs."
)

_CHANGE_DESC = (
    "Apply a small change to this feature's workflow as a diff — the tool to use while "
    "working something out with the author, because it is reversible. Each item names "
    "one place (path) and what it says before and after; the whole batch is refused if "
    "anything moved since (nothing is written), so read the document first "
    "(feature_workflow_get). target='definition' edits the workflow everybody runs; "
    "target='overlay' edits only this author's own extra instructions, where path is "
    "'/overlay'. The applied change is returned with its id — tell the author what "
    "changed, and that they can undo it with feature_workflow_revert."
)


def build_feature_workflow_tools(*, registry: Any, repos: Any) -> list[StructuredTool]:
    """The workflow tool set for a feature's own agent (its author's turn)."""

    async def feature_workflow_get(feature_id: str = "") -> str:
        """Read this feature's workflow: the document, any validation problems, and your own overlay text."""
        try:
            target, public_id, user_id = _resolve_owned_feature(registry, feature_id)
            workspace = registry.workspace_for_agent(target)
            if workspace is None:
                raise ValueError("this feature's workspace is not reachable")
            loaded = await wf.load_workflow(workspace)
            references: list[str] = []
            if loaded.definition is not None:
                references = wf.validate_references(
                    loaded.definition, await wf.read_capabilities(workspace)
                )
            return _ok(
                {
                    "feature_id": public_id,
                    "workflow": loaded.definition,
                    "reference_problems": references,
                    "problems": wf.validate_workflow(loaded.definition)
                    if loaded.definition is not None
                    else [],
                    "unreadable": loaded.error,
                    "your_overlay": repos.feature_overlay_repo.content(
                        feature_id=public_id, user_id=user_id
                    )
                    or None,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def feature_workflow_save(workflow: dict[str, Any]) -> str:
        """Replace this feature's workflow document (validated; nothing is written when invalid)."""
        try:
            target, _public_id, _user_id = _resolve_owned_feature(registry, "")
            workspace = registry.workspace_for_agent(target)
            if workspace is None:
                raise ValueError("this feature's workspace is not reachable")
            # The model is not asked to invent identifiers: a step that has none
            # gets one derived from its name, uniquely, before anything is validated.
            stored = await service.save_definition(workspace, wf.fill_step_ids(workflow))
            return _ok({"saved": True, "workflow": stored})
        except Exception as exc:
            return _err(exc)

    async def feature_workflow_change(
        target: str,
        summary: str,
        items: list[WorkflowChangeItemArg],
    ) -> str:
        """Apply a diff to this feature's workflow (or to your own overlay) and return the recorded change."""
        try:
            if target not in (TARGET_DEFINITION, TARGET_OVERLAY):
                raise ValueError("target must be 'definition' or 'overlay'")
            agent_id, public_id, user_id = _resolve_owned_feature(registry, "")
            payload = [item.model_dump() for item in items]
            problems = wf_changes.validate_items(payload)
            if problems:
                raise ValueError("; ".join(problems))
            workspace = None
            if target == TARGET_DEFINITION:
                workspace = registry.workspace_for_agent(agent_id)
                if workspace is None:
                    raise ValueError("this feature's workspace is not reachable")
            row = await service.apply_change(
                workspace=workspace,
                repos=repos,
                feature_id=public_id,
                user_id=user_id,
                target=target,
                summary=summary,
                items=payload,
            )
            return _ok(
                {
                    "change_id": row.id,
                    "target": row.target,
                    "summary": row.summary,
                    "items": row.items,
                    "status": row.status,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def feature_workflow_revert(change_id: str) -> str:
        """Undo a change this feature's workflow received (idempotent: undoing twice is fine)."""
        try:
            agent_id, public_id, user_id = _resolve_owned_feature(registry, "")
            row = repos.feature_change_repo.get(change_id)
            workspace = None
            needs_workspace = (
                row is not None and not row.is_reverted and row.target == TARGET_DEFINITION
            )
            if needs_workspace:
                workspace = registry.workspace_for_agent(agent_id)
                if workspace is None:
                    raise ValueError("this feature's workspace is not reachable")
            reverted = await service.revert_change(
                workspace=workspace,
                repos=repos,
                feature_id=public_id,
                user_id=user_id,
                change_id=change_id,
            )
            return _ok(
                {
                    "change_id": reverted.id,
                    "status": reverted.status,
                    "summary": reverted.summary,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def feature_workflow_runs(limit: int = 5) -> str:
        """List this author's recent runs of the feature: what each was given, and when."""
        try:
            _agent, public_id, user_id = _resolve_owned_feature(registry, "")
            rows = repos.feature_run_repo.list_for(
                feature_id=public_id, user_id=user_id, limit=max(1, min(int(limit), 50))
            )
            return _ok(
                {
                    "runs": [
                        {
                            "id": row.id,
                            "created_at": row.created_at,
                            "thread_id": row.thread_id,
                            "inputs": row.inputs,
                        }
                        for row in rows
                    ]
                }
            )
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=feature_workflow_get,
            name="feature_workflow_get",
            description=(
                "Read this feature's workflow: the document its runs follow, every "
                "validation problem in it, and the author's own overlay text. Read it "
                "before changing anything, so a change can quote what is there."
            ),
        ),
        StructuredTool.from_function(
            coroutine=feature_workflow_save,
            name="feature_workflow_save",
            description=_SAVE_DESC,
        ),
        StructuredTool.from_function(
            coroutine=feature_workflow_change,
            name="feature_workflow_change",
            description=_CHANGE_DESC,
        ),
        StructuredTool.from_function(
            coroutine=feature_workflow_revert,
            name="feature_workflow_revert",
            description=(
                "Undo a recorded change to this feature's workflow, by its change id. "
                "Use it when the author says a change was wrong; a place that was "
                "edited afterwards is reported back instead of being overwritten, so "
                "say what could not be undone and why."
            ),
        ),
        StructuredTool.from_function(
            coroutine=feature_workflow_runs,
            name="feature_workflow_runs",
            description=(
                "List this author's recent runs of the feature, with the values each "
                "run was given. The evidence behind a suggestion: what people actually "
                "submit, and what they had to correct afterwards."
            ),
        ),
    ]


__all__ = ["build_feature_workflow_tools"]
