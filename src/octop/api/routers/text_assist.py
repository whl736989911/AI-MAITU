"""Unified one-shot text assist for dashboard forms and chat polish."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from octop.api.common.agent import assert_agent_access
from octop.api.deps import current_user, get_server, unit_grants_for
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.text_assist import AssistKind, run_text_assist
from octop.infra.users.permissions import user_has_permission
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter()

# These are the same module gates that expose their respective form surfaces.
_KIND_PERMISSION: dict[str, str] = {
    "expert.description": "experts",
    "expert.welcome_message": "experts",
    "team.description": "teams",
    "team.welcome_message": "teams",
    "skill_package.description": "skill_packages",
    "skill.description": "skills",
    "skill.label_zh": "skills",
    "skill.label_en": "skills",
    "skill.summary_zh": "skills",
    "skill.summary_en": "skills",
    "skill.body": "skills",
    "knowledge_base.description": "knowledge_bases",
    "feature.description": "features",
}


class TextAssistBody(BaseModel):
    kind: AssistKind = Field(description="Assist scenario selecting the backend prompt.")
    text: str = Field(default="", description="Draft to polish; optional for form kinds.")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Form context; form kinds require context.name."
    )
    locale: str | None = Field(
        default=None, description="Output locale (zh / en). Defaults to the request locale."
    )
    default_model: str | None = Field(
        default=None, description="Optional model ref; omit for automatic routing."
    )
    agent_id: str | None = Field(
        default=None, description="Optional agent scope for access checks."
    )


class TextAssistResponse(BaseModel):
    text: str


@router.post(
    "/text/assist", summary="Polish or generate short copy", response_model=TextAssistResponse
)
async def text_assist(
    body: TextAssistBody,
    request: Request,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> TextAssistResponse:
    agent_id = (body.agent_id or "").strip()
    if agent_id:
        assert_agent_access(server, agent_id, user)
    permission = _KIND_PERMISSION.get(body.kind)
    if permission is not None and not user_has_permission(
        user, permission, unit_grants=unit_grants_for(server, user)
    ):
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "permission required",
            details={"permission": permission},
        )
    if body.kind == "chat.prompt" and not agent_id:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "agent_id is required for chat prompt assist")

    locale = (body.locale or "").strip() or resolve_request_locale(request)
    text = await run_text_assist(
        server=server,
        kind=body.kind,
        text=body.text,
        context=body.context,
        locale=locale,
        default_model=body.default_model,
        agent_id=agent_id or None,
    )
    return TextAssistResponse(text=text)
