"""Applying and undoing an improvement — one implementation, two entry points.

A change to a feature's workflow arrives two ways: over HTTP (the dashboard's
improvement card, and whatever computes a diff for it) and through the tools a
feature's own agent is given, so its author can say "第 3 步改成确认门" in
conversation and have it actually happen. Both must apply a diff the same way —
resolve the batch, refuse it whole when the document moved, validate the result,
record exactly what was applied — because a second implementation would drift
precisely where drift is expensive: the undo would stop matching what was applied.

So the orchestration lives here and both callers pass in what they already have.

What deliberately stays with the caller is **who may do this**: the HTTP surface
answers with the capability matrix and the ACL, the tool answers with the agent's own
row and the caller's identity. This module is handed an already-authorized feature,
workspace and user, and refuses only what is wrong about the *change*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from octop.infra.agents import feature_workflow as wf
from octop.infra.agents import feature_workflow_changes as wfc
from octop.infra.db.repos.feature_workflow_changes import (
    TARGET_DEFINITION,
    TARGET_OVERLAY,
    TARGETS,
    FeatureChangeRow,
)
from octop.infra.errors import ErrorCode, OctopError


def _invalid(reason: str) -> OctopError:
    return OctopError(ErrorCode.WORKFLOW_INVALID, reason, details={"reason": reason})


def _conflict(paths: Sequence[str]) -> OctopError:
    """The document no longer says what the change expected — refuse, never force."""
    joined = ", ".join(paths)
    return OctopError(
        ErrorCode.WORKFLOW_CHANGE_CONFLICT,
        f"stale change: {joined}",
        details={"paths": joined},
    )


def _apply(
    document: Any, items: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve and apply *items* to *document*, or raise the refusal for why not."""
    try:
        resolved = wfc.resolve_items(document, items)
        updated = wfc.apply_items(document, resolved)
    except wfc.ChangeConflictError as exc:
        raise _conflict(exc.paths) from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    return resolved, updated


async def apply_change(
    *,
    workspace: Any | None,
    repos: Any,
    feature_id: str,
    user_id: int,
    target: str,
    summary: str,
    items: Sequence[Mapping[str, Any]],
    run_id: str | None = None,
) -> FeatureChangeRow:
    """Apply one improvement and record how to undo it.

    The record holds the batch *as applied* — an append under the index it landed on
    — so undoing it later deletes that element rather than guessing which one moved.
    """
    if target == TARGET_DEFINITION:
        applied = await _apply_to_definition(workspace, items)
    elif target == TARGET_OVERLAY:
        applied = _apply_to_overlay(repos, feature_id=feature_id, user_id=user_id, items=items)
    else:
        raise _invalid(f"target must be one of {', '.join(TARGETS)}")
    return cast(
        "FeatureChangeRow",
        repos.feature_change_repo.record(
            feature_id=feature_id,
            user_id=user_id,
            target=target,
            summary=summary,
            items=applied,
            run_id=run_id,
        ),
    )


async def save_definition(workspace: Any, raw: Any) -> dict[str, Any]:
    """Validate a definition, refuse dangling references when it is published, store it.

    Both write paths go through here — a person pressing save and a change computed
    from a run — so "published means runnable as declared" holds for whichever of
    them moved the document to ``active``. A draft is only checked for shape: naming a
    skill the author is about to install is what training *is*.
    """
    definition = wf.parse_workflow(raw)
    if wf.workflow_status(definition) == wf.STATUS_ACTIVE:
        capabilities = await wf.read_capabilities(workspace)
        problems = wf.validate_references(definition, capabilities)
        if problems:
            raise _invalid("; ".join(problems))
    return await wf.save_workflow(workspace, definition)


async def _apply_to_definition(
    workspace: Any | None,
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the diff to the definition and store the validated result."""
    if workspace is None:
        raise _invalid("this feature's workspace is not reachable")
    loaded = await wf.load_workflow(workspace)
    if loaded.definition is None:
        raise _invalid(loaded.error or "this feature has no workflow to change")
    resolved, updated = _apply(loaded.definition, items)
    await save_definition(workspace, updated)
    return resolved


def _apply_to_overlay(
    repos: Any,
    *,
    feature_id: str,
    user_id: int,
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the diff to one caller's own overlay text.

    The overlay is addressed as one key of a document (``/overlay``) so a change to
    it is the same shape as a change to the definition — and only that key: an item
    pointing anywhere else is refused rather than quietly landing in nothing.
    """
    repo = repos.feature_overlay_repo
    existing = repo.content(feature_id=feature_id, user_id=user_id)
    current: dict[str, Any] = {"overlay": existing} if existing else {}
    resolved, updated = _apply(current, items)
    stray = sorted(set(updated) - {"overlay"})
    if stray:
        raise _invalid(f"an overlay change may only address /overlay (got {', '.join(stray)})")
    text = str(updated.get("overlay") or "").strip()
    if len(text) > wf.MAX_OVERLAY_CHARS:
        raise _invalid(f"an overlay may hold at most {wf.MAX_OVERLAY_CHARS} characters")
    if text:
        repo.set(feature_id=feature_id, user_id=user_id, content=text)
    else:
        repo.delete(feature_id=feature_id, user_id=user_id)
    return resolved


async def revert_change(
    *,
    workspace: Any | None,
    repos: Any,
    feature_id: str,
    user_id: int,
    change_id: str,
) -> FeatureChangeRow:
    """Undo one recorded improvement; an already-undone one is returned as it is.

    Idempotent on purpose: "undo this" arriving twice is a retry, not an error, and
    a caller that cannot tell the two apart would have to read before every write.
    """
    repo = repos.feature_change_repo
    row = repo.get(change_id)
    if row is None or row.feature_id != feature_id:
        raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
    if row.is_reverted:
        return cast("FeatureChangeRow", row)
    if row.target == TARGET_DEFINITION:
        await _revert_definition(workspace, row)
    elif row.target == TARGET_OVERLAY:
        _revert_overlay(repos, row, user_id=user_id)
    else:
        raise _invalid(f"unknown change target {row.target!r}")
    repo.mark_reverted(change_id)
    refreshed = repo.get(change_id)
    assert refreshed is not None
    return cast("FeatureChangeRow", refreshed)


async def _revert_definition(workspace: Any | None, row: FeatureChangeRow) -> None:
    if workspace is None:
        raise _invalid("this feature's workspace is not reachable")
    loaded = await wf.load_workflow(workspace)
    if loaded.definition is None:
        raise _invalid(loaded.error or "this feature has no workflow to undo against")
    try:
        restored, conflicts = wfc.revert_items(loaded.definition, row.items)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    if conflicts:
        raise _conflict(conflicts)
    await save_definition(workspace, restored)


def _revert_overlay(repos: Any, row: FeatureChangeRow, *, user_id: int) -> None:
    """Undo an overlay change — its own author's, and nobody else's."""
    if row.user_id != user_id:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "an overlay's change belongs to its own author",
            details={"reason": "an overlay's change belongs to its own author"},
        )
    repo = repos.feature_overlay_repo
    existing = repo.content(feature_id=row.feature_id, user_id=row.user_id)
    current: dict[str, Any] = {"overlay": existing} if existing else {}
    try:
        restored, conflicts = wfc.revert_items(current, row.items)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    if conflicts:
        raise _conflict(conflicts)
    text = str(restored.get("overlay") or "").strip()
    if text:
        repo.set(feature_id=row.feature_id, user_id=row.user_id, content=text)
    else:
        repo.delete(feature_id=row.feature_id, user_id=row.user_id)


__all__ = ["apply_change", "revert_change", "save_definition"]
