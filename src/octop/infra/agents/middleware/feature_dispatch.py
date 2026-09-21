"""One step's subagent ceiling, and its record — applied to the ``task`` tool.

Design 7.7 splits a step in two: **the model decides** whether to decompose it,
into how many pieces, and in what order; **the platform keeps** a ceiling on how
many of those pieces may run at once (``max_parallel``) and the record of what
actually ran (7.8's 分解留痕). Nothing here schedules anything — the model already
holds the tool that dispatches subagents (deepagents' ``task``, the name
:data:`octop.infra.agents.middleware.feature_scope.TASK_TOOL_NAME` resolves), and
this middleware is the layer that bounds that tool and writes the record:

- the run opens one :class:`DispatchLedger` for a step's turn
  (:func:`open_ledger`) and stamps its token onto that turn's ``configurable``
  (:func:`stamp_dispatch`) — the same per-run channel
  :mod:`octop.infra.agents.middleware.feature_scope` uses, and a token rather than
  the ledger because ``configurable`` crosses into the harness as JSON;
- :meth:`FeatureDispatchMiddleware.awrap_tool_call` runs every ``task`` call of
  such a turn through the ledger, which waits for a slot under the ceiling and
  records the call's outcome;
- :meth:`DispatchLedger.entries` is that record, read back after the turn and
  persisted by the run.

Everything else passes through untouched: a turn carrying no token (chat, cron
delivery, IM messages, rule extraction) keeps the tool surface its agent has, and
so does every call that is not a subagent dispatch. The middleware's seat in the
chain matters — it sits inside ``FeatureScopeMiddleware``, so a subagent the run
may not use is refused before the boundary ever sees the call, and a refused
dispatch is never recorded as one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from octop.infra.agents.middleware.feature_scope import TASK_TOOL_NAME
from octop.infra.db.repos._base import now_ts
from octop.infra.features.dispatch import MAX_DISPATCH_RESULT_CHARS, DispatchEntry

logger = logging.getLogger(__name__)

_CallResult = TypeVar("_CallResult")
"""What one gated call returns — handed back untouched, so the caller's own type."""

CONFIG_KEY = "octop_feature_dispatch"
"""``configurable`` entry carrying one step turn's ledger token."""

_LEDGERS: dict[str, DispatchLedger] = {}
"""Open ledgers by token — the run's handle on a turn it opened and will close."""

_DISPATCHING: ContextVar[bool] = ContextVar("octop_feature_dispatch_active", default=False)
"""True while a dispatch this boundary made is running.

A subagent that dispatches a subagent of its own calls ``task`` from inside its
parent's dispatch, and at ``max_parallel == 1`` the parent already holds the only
slot — gating that call would deadlock the turn against its own ceiling. Such a
call passes straight through instead: the record is the *step's* dispatch list, so
what a subagent does with its own helpers is not another row in it.
"""


@dataclass(frozen=True)
class _Slot:
    """What the ceiling measured about one dispatch, before its outcome is known."""

    waited: bool
    waited_ms: int
    slots: int
    started_at: int


@dataclass(frozen=True)
class _Outcome:
    """How one dispatch's call ended, in :class:`DispatchEntry`'s own terms."""

    status: str
    error: str | None
    result: str | None
    truncated: bool


def _content_text(content: Any) -> str:
    """A tool message's content as text.

    The subagent's answer is usually a string, but a block list or a JSON payload
    reaches here whenever the subagent's own tools put one there, and the record
    exists to answer "这个子 agent 产出什么" — so the payload is kept as it was
    rather than approximated by a repr.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        return "".join(parts)
    return json.dumps(content, ensure_ascii=False, default=str)


def _capped(text: str) -> tuple[str, bool]:
    """*text*, cut at :data:`MAX_DISPATCH_RESULT_CHARS`, and whether it was cut."""
    if len(text) <= MAX_DISPATCH_RESULT_CHARS:
        return text, False
    return text[:MAX_DISPATCH_RESULT_CHARS], True


def _outcome(value: Any) -> _Outcome:
    """Read a ``task`` call's return value the way the record keeps it.

    ``ToolNode`` hands back a ``ToolMessage`` for the usual case and a ``Command``
    when an interceptor rerouted the turn, carrying the message as the last of its
    updates. A ``Command`` with no message behind it is a success whose answer the
    boundary never saw — recorded as such, not filled in with a guess.
    """
    if isinstance(value, Command):
        messages = value.update.get("messages") if isinstance(value.update, dict) else None
        last = messages[-1] if isinstance(messages, list) and messages else None
        value = last if isinstance(last, ToolMessage) else None
    if not isinstance(value, ToolMessage):
        return _Outcome(status="succeeded", error=None, result=None, truncated=False)
    text = _content_text(value.content)
    if value.status == "error":
        return _Outcome(status="failed", error=text, result=None, truncated=False)
    result, truncated = _capped(text)
    return _Outcome(status="succeeded", error=None, result=result, truncated=truncated)


def _entry(role: str, task: str, slot: _Slot, outcome: _Outcome) -> DispatchEntry:
    return DispatchEntry(
        role=role,
        task=task,
        status=outcome.status,
        error=outcome.error,
        result=outcome.result,
        truncated=outcome.truncated,
        waited=slot.waited,
        waited_ms=slot.waited_ms,
        slots=slot.slots,
        started_at=slot.started_at,
        ended_at=now_ts(),
    )


class DispatchLedger:
    """One step turn's dispatches: the ceiling they ran under, and the record.

    The ledger belongs to a single turn, because the ceiling is 7.7's property of
    *that step's* decomposition rather than of the run. It is the only thing that
    measures — :attr:`peak` and :attr:`waited` come from calls that really ran, so
    "模型想开很多时被上限截住" is answerable without trusting the model's own account
    of what it did.
    """

    def __init__(self, *, ceiling: int) -> None:
        if ceiling < 1:
            # A zero-slot ledger is not a tighter ceiling, it is a turn that waits
            # for a slot that can never exist — refused here, where the caller can
            # still see why.
            raise ValueError(f"a dispatch ceiling must be at least 1, got {ceiling}")
        self._ceiling = ceiling
        self._slots_free = asyncio.Semaphore(ceiling)
        self._running = 0
        self._peak = 0
        self._waited = 0
        self._entries: list[DispatchEntry] = []

    @property
    def ceiling(self) -> int:
        """How many dispatches of this step may run at once."""
        return self._ceiling

    @property
    def peak(self) -> int:
        """The high-water mark of concurrent dispatches — what the ceiling allowed."""
        return self._peak

    @property
    def waited(self) -> int:
        """How many dispatches found no free slot and queued for one."""
        return self._waited

    def entries(self) -> tuple[DispatchEntry, ...]:
        """This turn's dispatches, in the order they finished."""
        return tuple(self._entries)

    async def dispatch(
        self,
        *,
        role: str,
        task: str,
        call: Callable[[], Awaitable[_CallResult]],
    ) -> _CallResult:
        """Await one subagent call under the ceiling, recording how it went.

        The call's own return value is handed back unchanged (the ``task`` tool's
        message or state update), so gating a call never changes what the caller
        receives.
        The call is awaited *while holding a slot*, so the ceiling is what the
        model actually got rather than what the definition declared, and the slot
        is released in a ``finally`` — a call that raises or a turn that is
        cancelled still gives its slot back. ``waited`` is decided by the state the
        call found on arrival (nothing is awaited between that check and the
        acquisition when a slot is free), and the answer is capped at
        :data:`MAX_DISPATCH_RESULT_CHARS` with ``truncated`` saying it was cut.
        """
        queued_at = time.monotonic()
        # Decided on the state the call found, before anything is awaited: a free
        # slot is acquired without yielding, so a dispatch that saw one here really
        # did start then. A dispatch that queues and never starts (the turn was
        # cancelled) is counted nowhere — it left no entry to be counted beside.
        waited = self._running >= self._ceiling
        await self._slots_free.acquire()
        waited_ms = 0
        if waited:
            waited_ms = int((time.monotonic() - queued_at) * 1000)
            self._waited += 1
            logger.info(
                "feature dispatch: %s waited %dms for one of %d slots",
                role,
                waited_ms,
                self._ceiling,
            )
        self._running += 1
        self._peak = max(self._peak, self._running)
        slot = _Slot(
            waited=waited,
            waited_ms=waited_ms,
            slots=self._running,
            started_at=now_ts(),
        )
        active = _DISPATCHING.set(True)
        try:
            value = await call()
        except BaseException as exc:
            # Cancellation is recorded too: a turn stopped mid-dispatch must still
            # leave the evidence of what it had started.
            failed = _Outcome(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                result=None,
                truncated=False,
            )
            self._entries.append(_entry(role, task, slot, failed))
            raise
        finally:
            _DISPATCHING.reset(active)
            self._running -= 1
            self._slots_free.release()
        self._entries.append(_entry(role, task, slot, _outcome(value)))
        return value


def open_ledger(*, ceiling: int) -> tuple[str, DispatchLedger]:
    """Register a fresh ledger for one step turn and return ``(token, ledger)``."""
    token = uuid.uuid4().hex
    ledger = DispatchLedger(ceiling=ceiling)
    _LEDGERS[token] = ledger
    return token, ledger


def close_ledger(token: str) -> DispatchLedger | None:
    """Unregister and return it; ``None`` when it was never registered.

    Written to be called from a ``finally``: the second call — a turn that ends
    twice — returns ``None`` instead of dropping a ledger somebody else still owns.
    """
    return _LEDGERS.pop(token, None)


def ledger_for(token: str) -> DispatchLedger | None:
    """The open ledger *token* names, or ``None`` when nothing is registered for it."""
    return _LEDGERS.get(token)


def stamp_dispatch(request: dict[str, Any], token: str) -> None:
    """Write *token* onto one harness request, in place.

    The token is a string, never the ledger: ``configurable`` travels into the
    harness as JSON, and the ledger is process-local state this module owns.
    """
    configurable = dict(request.get("configurable") or {})
    configurable[CONFIG_KEY] = {"token": token}
    request["configurable"] = configurable


def dispatch_token(configurable: Mapping[str, Any]) -> str | None:
    """Read a stamped token back; ``None`` when this run carries none."""
    raw = configurable.get(CONFIG_KEY)
    if not isinstance(raw, Mapping):
        return None
    token = raw.get("token")
    return token if isinstance(token, str) else None


def _run_configurable() -> dict[str, Any]:
    """This run's ``configurable``, or ``{}`` outside a run (unit tests, CLI)."""
    try:
        config = get_config()
    except RuntimeError:
        return {}
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable")
    return dict(configurable) if isinstance(configurable, dict) else {}


class FeatureDispatchMiddleware(AgentMiddleware[Any, Any]):
    """Cap and record this turn's subagent dispatches; never gated when no ledger is stamped."""

    def _ledger(self) -> DispatchLedger | None:
        token = dispatch_token(_run_configurable())
        return ledger_for(token) if token is not None else None

    def _gated(self, request: ToolCallRequest) -> DispatchLedger | None:
        """The ledger this call must run under, or ``None`` to leave it alone."""
        if _DISPATCHING.get():
            return None
        if str(request.tool_call.get("name") or "") != TASK_TOOL_NAME:
            return None
        return self._ledger()

    @staticmethod
    def _message(request: ToolCallRequest, reason: str) -> ToolMessage:
        return ToolMessage(
            content=reason,
            tool_call_id=str(request.tool_call.get("id") or ""),
            status="error",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        ledger = self._gated(request)
        if ledger is None:
            return await handler(request)
        args = request.tool_call.get("args")
        args = args if isinstance(args, Mapping) else {}
        return await ledger.dispatch(
            role=str(args.get("subagent_type") or ""),
            task=str(args.get("description") or ""),
            call=lambda: handler(request),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Refuse a dispatch a ledger would have gated — the ceiling is not optional.

        A ledger measures by awaiting, which this path cannot do, so running the
        call here would hand back a subagent result that was neither capped nor
        recorded. That is not a degraded step, it is a different one, so the call is
        refused by name of the reason instead.
        """
        if self._gated(request) is None:
            return handler(request)
        logger.info("feature dispatch: refused a synchronous %s call", TASK_TOOL_NAME)
        return self._message(
            request,
            f"Subagent dispatch is unavailable on this path: a feature run dispatches "
            f"subagents on the async path only, so {TASK_TOOL_NAME!r} cannot be called here.",
        )


__all__ = [
    "CONFIG_KEY",
    "DispatchLedger",
    "FeatureDispatchMiddleware",
    "close_ledger",
    "dispatch_token",
    "ledger_for",
    "open_ledger",
    "stamp_dispatch",
]
