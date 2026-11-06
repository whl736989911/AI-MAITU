"""Per-user preferences (locale, etc.)."""

from __future__ import annotations

import json
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.preferences import (
    MAX_REMOTE_BROWSER_BOOKMARKS,
    PREFERENCES_KEY_TIMEZONE,
    ModelReasoningPreference,
    get_model_reasoning_from_json,
    get_preferred_model_from_json,
    get_remote_browser_bookmarks_from_json,
    get_sidebar_preferences_from_json,
    merge_model_preferences_json,
    merge_preferences_json,
    merge_sidebar_preferences_json,
    normalize_model_ref,
    parse_preferences_json,
    validate_remote_browser_bookmarks,
)
from octop.infra.utils.locale import normalize_locale

router = APIRouter()


class RemoteBrowserBookmarkModel(BaseModel):
    url: str = Field(description="Bookmark URL (`http://` or `https://`).")
    title: str = Field(description="Short display label.")


class ModelReasoningPreferenceModel(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|enabled|disabled)$")
    effort: str | None = None


class PreferencesResponse(BaseModel):
    locale: str = Field(description="UI locale: `zh` or `en`.")
    remote_browser_bookmarks: list[RemoteBrowserBookmarkModel] = Field(
        default_factory=list,
        description="Saved URLs for the remote-browser page.",
    )
    preferred_model: str | None = Field(
        default=None,
        description="Per-user preferred model ref. The global active model is only a fallback.",
    )
    model_reasoning: dict[str, ModelReasoningPreferenceModel] = Field(
        default_factory=dict,
        description="Per-model reasoning defaults for this user.",
    )
    timezone: str | None = Field(default=None, description="Preferred IANA timezone.")
    sidebar: dict[str, list[str]] = Field(
        default_factory=lambda: dict[str, list[str]](order=[], hidden=[])
    )


def _validate_sidebar_preferences(value: dict[str, list[str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in ("order", "hidden"):
        items = value.get(key, [])
        if len(items) > 64 or any(not item or len(item) > 64 for item in items):
            raise ValueError(f"sidebar.{key} entries must contain 1-64 characters (max 64 items)")
        result[key] = list(dict.fromkeys(items))
    if set(value).difference({"order", "hidden"}):
        raise ValueError("sidebar accepts only order and hidden")
    return result


class PatchPreferencesBody(BaseModel):
    locale: str | None = Field(default=None, description="UI locale: `zh` or `en`.")
    remote_browser_bookmarks: list[RemoteBrowserBookmarkModel] | None = Field(
        default=None,
        description="Replace remote-browser bookmarks (max 12).",
    )
    preferred_model: str | None = None
    model_reasoning: dict[str, ModelReasoningPreferenceModel] | None = None
    sidebar: dict[str, list[str]] | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set.intersection(
            {
                "locale",
                "remote_browser_bookmarks",
                "preferred_model",
                "model_reasoning",
                "timezone",
                "sidebar",
            }
        ):
            raise ValueError("at least one preference field is required")

        if (
            self.remote_browser_bookmarks is not None
            and len(self.remote_browser_bookmarks) > MAX_REMOTE_BROWSER_BOOKMARKS
        ):
            raise ValueError(
                f"remote_browser_bookmarks must have at most {MAX_REMOTE_BROWSER_BOOKMARKS} items"
            )
        if self.sidebar is not None:
            self.sidebar = _validate_sidebar_preferences(self.sidebar)

        return self


def _bookmarks_response(row: Any) -> list[RemoteBrowserBookmarkModel]:
    raw = row.preferences_json if row else None
    return [
        RemoteBrowserBookmarkModel(url=b.url, title=b.title)
        for b in get_remote_browser_bookmarks_from_json(raw)
    ]


def _response(row: Any) -> PreferencesResponse:
    raw = row.preferences_json if row else None
    return PreferencesResponse(
        locale=normalize_locale(row.locale if row else None),
        remote_browser_bookmarks=_bookmarks_response(row),
        preferred_model=get_preferred_model_from_json(raw),
        model_reasoning={
            ref: ModelReasoningPreferenceModel(mode=pref.mode, effort=pref.effort)
            for ref, pref in get_model_reasoning_from_json(raw).items()
        },
        timezone=parse_preferences_json(raw).get(PREFERENCES_KEY_TIMEZONE),
        sidebar=get_sidebar_preferences_from_json(raw),
    )


@router.get("/preferences", summary="Current user preferences", response_model=PreferencesResponse)
async def get_preferences(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> PreferencesResponse:
    row = server.services.user_repo.get(user.id)
    return _response(row)


@router.patch("/preferences", summary="Update user preferences", response_model=PreferencesResponse)
async def patch_preferences(
    body: PatchPreferencesBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> PreferencesResponse:
    bookmarks = None
    if body.remote_browser_bookmarks is not None:
        bookmarks = validate_remote_browser_bookmarks(
            [item.model_dump() for item in body.remote_browser_bookmarks]
        )

    preferred: str | None | object = ...
    if "preferred_model" in body.model_fields_set:
        preferred = body.preferred_model
        if (
            preferred is not None
            and not server.app_runtime.agent_registry.providers.is_model_ref_usable(preferred)
        ):
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                "preferred_model must reference an enabled model",
            )
    reasoning = None
    if body.model_reasoning is not None:
        reasoning = {
            ref: ModelReasoningPreference(mode=value.mode, effort=value.effort)
            for ref, value in body.model_reasoning.items()
        }
        if any(normalize_model_ref(ref) is None for ref in reasoning):
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                "model_reasoning keys must be model references",
            )

    timezone = body.timezone
    if "timezone" in body.model_fields_set and timezone is not None:
        timezone = timezone.strip()
        if not timezone:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "timezone cannot be empty")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                "timezone must be a valid IANA timezone",
            ) from None

    def merge(raw: str) -> str:
        merged = raw
        if bookmarks is not None:
            merged = merge_preferences_json(merged, bookmarks)
        if "preferred_model" in body.model_fields_set or body.model_reasoning is not None:
            merged = merge_model_preferences_json(
                merged,
                preferred_model=preferred,
                model_reasoning=reasoning,
            )
        if "timezone" in body.model_fields_set:
            data = parse_preferences_json(merged)
            if timezone is not None:
                data[PREFERENCES_KEY_TIMEZONE] = timezone
            else:
                data.pop(PREFERENCES_KEY_TIMEZONE, None)
            merged = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if body.sidebar is not None:
            merged = merge_sidebar_preferences_json(merged, body.sidebar)
        return merged

    await server.user_manager.update_preferences(
        user.username,
        transform=merge,
        locale=body.locale,
    )
    row = server.services.user_repo.get(user.id)
    return _response(row)
