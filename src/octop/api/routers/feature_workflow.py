"""A feature's workflow — the definition its runs follow, and the form they ask for.

A feature *is* an agent (:mod:`octop.infra.agents.kinds`), so this surface lives on
the agent's own path like its skills, its tools and its workspace files, and holds
exactly the one document those surfaces have no equivalent of:
``.octop/workflow.json`` (:mod:`octop.infra.agents.feature_workflow`).

**Who may read, who may write.** Reading is the same answer as reading the agent:
a caller who may reach the feature may read its definition, which they need to
render the input card they run it with. Writing is the *configuration* group
(:class:`~octop.api.common.agent.AgentCapability`), which for a feature's own agent
is its author — the matrix states that rule once, and this router names the group
rather than deciding for itself who may edit a feature.

**Only a feature has one.** An expert has no declared run, so a workflow is refused
on any other kind with its own code (``WORKFLOW_NOT_A_FEATURE``) instead of writing
a document that nothing would ever read.

**Both statuses are written through here.** ``draft`` is checked for shape only —
that is what makes the definition savable while its author (or the configuration
assistant) is still working it out — and ``active`` must stand on its own. The
refusal lists every problem at once, so the editor can put each one on the section
it belongs to.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from octop.api.common.agent import AgentCapability, require_agent_row
from octop.api.common.workspace import require_agent_workspace
from octop.api.deps import current_user, get_server
from octop.infra.agents import feature_workflow as wf
from octop.infra.agents import feature_workflow_changes as wfc
from octop.infra.agents import feature_workflow_service as workflow_service
from octop.infra.agents.kinds import feature_id_of_agent, is_feature_agent
from octop.infra.db.repos.feature_workflow_changes import TARGET_DEFINITION
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer

router = APIRouter(prefix="/agents", tags=["agents"])


class WorkflowResponse(BaseModel):
    """The definition as stored, or the reason a stored one cannot be read."""

    workflow: dict[str, Any] | None = Field(
        default=None,
        description="The workflow definition, or null when the feature declares none.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Set when a definition file exists but is not a valid document "
            "(hand-edited, or written by an older build). The editor shows it so "
            "its author can fix it; a run merely goes without the block."
        ),
    )


class WorkflowPutBody(BaseModel):
    """What to store as the definition. ``null`` removes it."""

    workflow: dict[str, Any] | None = Field(
        ...,
        description=(
            "A workflow document: ``version``, optional ``status`` (draft|active), "
            "``inputs``, ``steps``, ``outputs``, ``rules``. See "
            "``octop.infra.agents.feature_workflow`` for the accepted shape."
        ),
    )


def _require_feature_row(agent_id: str, *, user: Any, server: OctopServer) -> Any:
    """The agent row, refused unless it is a feature's own agent.

    The kind is checked before anything is read or written: an expert has no run to
    declare, and answering with an empty definition would suggest it could have one.
    """
    row = require_agent_row(agent_id, user=user, as_user=None, server=server)
    if not is_feature_agent(str(getattr(row, "kind", ""))):
        raise OctopError(
            ErrorCode.WORKFLOW_NOT_A_FEATURE,
            f"agent {agent_id!r} is not a feature's own agent",
        )
    return row


@router.get(
    "/{agent_id}/workflow",
    summary="Read a feature's workflow definition",
    response_model=WorkflowResponse,
    description=(
        "The definition the feature's runs follow: input form, fixed steps, "
        "deliverables and rules. An unfinished draft is visible only to its "
        "author for training; callers see it after the author activates it. "
        "Readable while the agent is stopped, because it is a document rather "
        "than runtime state."
    ),
)
async def get_workflow(
    agent_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowResponse:
    """Read ``.octop/workflow.json`` for a feature, if it has one."""
    row = _require_feature_row(agent_id, user=user, server=server)
    workspace = await require_agent_workspace(agent_id, user=user, server=server)
    loaded = await wf.load_workflow(workspace)
    if (
        loaded.definition is not None
        and wf.workflow_status(loaded.definition) != wf.STATUS_ACTIVE
        and row.user_id != user.id
    ):
        return WorkflowResponse()
    return WorkflowResponse(workflow=loaded.definition, error=loaded.error)


@router.put(
    "/{agent_id}/workflow",
    summary="Write a feature's workflow definition",
    response_model=WorkflowResponse,
    description=(
        "Validates and stores the definition. A ``draft`` is checked for shape "
        "only; an ``active`` one must stand on its own. Every problem is reported "
        "at once (``WORKFLOW_INVALID``) and nothing is written when any exists. "
        "Writing ``workflow: null`` removes the definition."
    ),
)
async def put_workflow(
    agent_id: str,
    body: WorkflowPutBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowResponse:
    """Store a feature's workflow definition (its author writes it)."""
    _require_feature_row(agent_id, user=user, server=server)
    workspace = await require_agent_workspace(
        agent_id,
        user=user,
        server=server,
        capability=AgentCapability.CONFIGURATION,
    )
    if body.workflow is None:
        await wf.clear_workflow(workspace)
        return WorkflowResponse()
    stored = await workflow_service.save_definition(workspace, body.workflow)
    return WorkflowResponse(workflow=stored)


@router.delete(
    "/{agent_id}/workflow",
    summary="Remove a feature's workflow definition",
    response_model=WorkflowResponse,
    description=(
        "Deletes the definition, leaving the feature as a plain conversational "
        "agent. Identical to writing ``workflow: null``."
    ),
)
async def delete_workflow(
    agent_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowResponse:
    """Remove the definition outright."""
    _require_feature_row(agent_id, user=user, server=server)
    workspace = await require_agent_workspace(
        agent_id,
        user=user,
        server=server,
        capability=AgentCapability.CONFIGURATION,
    )
    await wf.clear_workflow(workspace)
    return WorkflowResponse()


class WorkflowChangeItem(BaseModel):
    """One place an improvement touches."""

    path: str = Field(
        ...,
        description=(
            "Where it applies: ``/rules/2``, ``/steps/1/gate``, "
            "``/inputs/properties/name/title/zh``. ``-`` as a list index appends."
        ),
    )
    before: Any = Field(
        default=None,
        description="What the place said before. ``null`` means nothing was there.",
    )
    after: Any = Field(..., description="What it says after.")


class WorkflowChangeBody(BaseModel):
    """An improvement to apply, as a diff."""

    target: Literal["definition", "overlay"] = Field(
        ...,
        description=(
            "``definition`` edits the feature's own workflow (its author's); "
            "``overlay`` edits the caller's own text (its owner's)."
        ),
    )
    summary: str = Field(
        ..., min_length=1, max_length=200, description="What changed, for the card."
    )
    items: list[WorkflowChangeItem] = Field(
        ..., min_length=1, description="One entry per place touched."
    )
    run_id: str | None = Field(
        default=None, description="The run this was summarised from, when there is one."
    )


class WorkflowChangeOut(BaseModel):
    """An applied improvement, as the card and the history read it."""

    id: str
    target: str
    summary: str
    items: list[dict[str, Any]]
    status: str = Field(..., description="``applied`` or ``reverted``.")
    run_id: str | None = None
    created_at: int
    reverted_at: int | None = None


class WorkflowChangesResponse(BaseModel):
    changes: list[WorkflowChangeOut] = Field(default_factory=list)


def _change_payload(row: Any) -> WorkflowChangeOut:
    return WorkflowChangeOut(
        id=row.id,
        target=row.target,
        summary=row.summary,
        items=row.items,
        status=row.status,
        run_id=row.run_id,
        created_at=row.created_at,
        reverted_at=row.reverted_at,
    )


@router.post(
    "/{agent_id}/workflow/changes",
    summary="Apply an improvement to a feature's workflow",
    response_model=WorkflowChangeOut,
    description=(
        "Applies a diff to the feature's definition (its author's) or to the "
        "caller's own overlay. An item carries the value it expected to find: if "
        "that place was edited in the meantime the whole batch is refused "
        "(`WORKFLOW_CHANGE_CONFLICT`, nothing written) rather than overwriting the "
        "newer edit. A definition change that would not validate is refused too. "
        "Every applied change is recorded, so it can be undone afterwards."
    ),
)
async def post_workflow_change(
    agent_id: str,
    body: WorkflowChangeBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowChangeOut:
    """Apply one improvement, recording how to undo it."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    items = [item.model_dump() for item in body.items]
    problems = wfc.validate_items(items)
    if problems:
        reason = "; ".join(problems)
        raise OctopError(ErrorCode.WORKFLOW_INVALID, reason, details={"reason": reason})

    services = _services(server)
    # The definition is the author's to write; an overlay is the caller's own — so
    # only the definition path asks for the configuration capability.
    workspace = None
    if body.target == TARGET_DEFINITION:
        workspace = await require_agent_workspace(
            agent_id,
            user=user,
            server=server,
            capability=AgentCapability.CONFIGURATION,
        )
    row = await workflow_service.apply_change(
        workspace=workspace,
        repos=services.repos,
        feature_id=feature_id,
        user_id=user.id,
        target=body.target,
        summary=body.summary,
        items=items,
        run_id=body.run_id,
    )
    return _change_payload(row)


@router.get(
    "/{agent_id}/workflow/changes",
    summary="List a feature's applied improvements",
    response_model=WorkflowChangesResponse,
    description=(
        "Newest first. Definition edits are visible to their author, and personal "
        "overlay edits only to the caller who made them; draft content is never "
        "exposed through improvement history."
    ),
)
async def list_workflow_changes(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100, description="How many changes to return."),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowChangesResponse:
    """List the changes this caller may see."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    rows = _services(server).feature_change_repo.list_for_feature(
        feature_id=feature_id, user_id=user.id, limit=limit
    )
    return WorkflowChangesResponse(changes=[_change_payload(row) for row in rows])


@router.post(
    "/{agent_id}/workflow/changes/{change_id}/revert",
    summary="Undo an applied improvement",
    response_model=WorkflowChangeOut,
    description=(
        "Puts back what the change replaced and drops what it added. An item whose "
        "value has since been edited elsewhere is reported instead of overwritten "
        "(`WORKFLOW_CHANGE_CONFLICT`) — the caller decides which edit they meant. "
        "Reverting an already-reverted change is a no-op, not an error."
    ),
)
async def revert_workflow_change(
    agent_id: str,
    change_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowChangeOut:
    """Undo one recorded improvement."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    services = _services(server)
    row = services.feature_change_repo.get(change_id)
    if row is None or row.feature_id != feature_id:
        raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
    # A reverted diff still contains its original before/after values. Never reveal
    # it to another caller just because there is no work left to undo.
    if row.user_id != user.id:
        reason = "a change belongs to its own author"
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    workspace = None
    if not row.is_reverted and row.target == TARGET_DEFINITION:
        workspace = await require_agent_workspace(
            agent_id,
            user=user,
            server=server,
            capability=AgentCapability.CONFIGURATION,
        )
    reverted = await workflow_service.revert_change(
        workspace=workspace,
        repos=services.repos,
        feature_id=feature_id,
        user_id=user.id,
        change_id=change_id,
    )
    return _change_payload(reverted)


class WorkflowRunItem(BaseModel):
    """One run this caller submitted."""

    id: str = Field(..., description="The run's id.")
    created_at: int = Field(..., description="Unix seconds when the run was submitted.")
    thread_id: str | None = Field(
        default=None, description="The conversation the run happened in, when known."
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="The values its input card submitted."
    )


class WorkflowRunsResponse(BaseModel):
    """This caller's runs of one feature, newest first."""

    runs: list[WorkflowRunItem] = Field(default_factory=list)


@router.get(
    "/{agent_id}/workflow/runs",
    summary="List your own runs of a feature's workflow",
    response_model=WorkflowRunsResponse,
    description=(
        "The caller's own submitted runs, newest first: what each was given and "
        "which conversation it happened in. Another caller's runs are never listed "
        "— a run carries the values somebody typed — and a feature's author sees "
        "theirs here like anybody else does theirs."
    ),
)
async def list_workflow_runs(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100, description="How many runs to return."),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> WorkflowRunsResponse:
    """List the caller's own runs of this feature."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    rows = _services(server).feature_run_repo.list_for(
        feature_id=feature_id, user_id=user.id, limit=limit
    )
    return WorkflowRunsResponse(
        runs=[
            WorkflowRunItem(
                id=row.id,
                created_at=row.created_at,
                thread_id=row.thread_id,
                inputs=row.inputs,
            )
            for row in rows
        ]
    )


class OverlayResponse(BaseModel):
    """The calling user's own overlay on this feature's workflow."""

    overlay: str | None = Field(
        default=None,
        description="Their own text, or null when they keep none.",
    )


class OverlayPutBody(BaseModel):
    """What to store as the caller's own overlay. ``null`` (or blank) removes it."""

    overlay: str | None = Field(
        ...,
        max_length=wf.MAX_OVERLAY_CHARS,
        description=(
            "Their own standing instruction for this feature's runs, injected above "
            "the definition and stated to win where the two disagree. Blank or null "
            "removes it."
        ),
    )


def _services(server: OctopServer) -> Any:
    """The server's services, refused rather than dereferenced when absent.

    The same accessor the sharing router uses: a request that arrives before the
    control plane is bound has to be told so, instead of failing with an
    ``AttributeError`` that reads like a bug in this router.
    """
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services are not initialized")
    return server.services


def _require_feature_id(agent_id: str, *, user: Any, server: OctopServer) -> str:
    """The feature's public id, refused unless the agent is a feature's own."""
    _require_feature_row(agent_id, user=user, server=server)
    feature_id = feature_id_of_agent(agent_id)
    if feature_id is None:  # unreachable for a feature row; the id is what makes it one
        raise OctopError(
            ErrorCode.WORKFLOW_NOT_A_FEATURE,
            f"agent {agent_id!r} carries no feature id",
        )
    return feature_id


@router.get(
    "/{agent_id}/workflow/overlay",
    summary="Read your own overlay on a feature's workflow",
    response_model=OverlayResponse,
    description=(
        "The personal layer above the feature's definition: text *this* caller "
        "writes for themselves, injected after the definition on their runs and "
        "stated to win where the two disagree. It is theirs alone — another "
        "caller's overlay is never returned here, and keeping one is not a way to "
        "change the feature for anybody else."
    ),
)
async def get_workflow_overlay(
    agent_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> OverlayResponse:
    """Read the caller's own overlay on this feature's workflow."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    row = _services(server).feature_overlay_repo.get(feature_id=feature_id, user_id=user.id)
    return OverlayResponse(overlay=row.content if row is not None else None)


@router.put(
    "/{agent_id}/workflow/overlay",
    summary="Write your own overlay on a feature's workflow",
    response_model=OverlayResponse,
    description=(
        "Stores the caller's own standing instruction for this feature. No "
        "configuration permission is required — it changes nobody's feature — but "
        "the caller must be able to reach it. Blank or null removes the overlay."
    ),
)
async def put_workflow_overlay(
    agent_id: str,
    body: OverlayPutBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> OverlayResponse:
    """Store (or remove) the caller's own overlay on this feature's workflow."""
    feature_id = _require_feature_id(agent_id, user=user, server=server)
    repo = _services(server).feature_overlay_repo
    content = (body.overlay or "").strip()
    if not content:
        repo.delete(feature_id=feature_id, user_id=user.id)
        return OverlayResponse()
    return OverlayResponse(
        overlay=repo.set(feature_id=feature_id, user_id=user.id, content=content).content
    )


__all__ = [
    "OverlayPutBody",
    "OverlayResponse",
    "WorkflowPutBody",
    "WorkflowResponse",
    "WorkflowRunItem",
    "WorkflowRunsResponse",
    "router",
]
