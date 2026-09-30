"""Per-turn stage and identity routing for a feature's memory.

A feature's *shared* memory (``agent_{agent_id}``) is its training corpus: the
author fills it by hand while the workflow is a draft, and once the workflow is
active nobody writes to it again. Each *active*-stage caller who is a verified,
authenticated user instead gets a *private* namespace
(``agent_{agent_id}_user_{user_id}``) that only they can read or write.

The turn path (gateway processor) decides — once, per turn, from the workflow
stage and the caller's verified identity — what this turn may do, and stamps
the decision on the request's ``configurable``. This module only executes
that decision:

* recall reads shared memory, plus the caller's private memory when one applies;
* capture, extraction, candidate promotion and deposition land only in the
  private namespace of an active verified caller — never in shared memory, in
  any stage. A draft author builds the shared corpus by editing its files
  directly (file tools, dashboard memory API), never through conversation;
* ``memory_search`` / ``memory_get`` operate on exactly the namespaces this
  turn may recall from — the feature's shared memory plus the caller's own
  private partition — so a caller can never reach another user's memory;
* idle-session extraction distills each session into the namespace its turns
  were captured in.

An IM turn is never given a private namespace: its caller identity falls back
to the agent owner by design, and treating that fallback as a private-memory
identity would let a channel member read and write the owner's private
partition. Unstamped turns (HITL resume, cron) fail closed: shared reads
only, no capture.

Experts never mount any of this — their memory stays exactly as harness-agent
wires it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_agent.memory.llm_client import HarnessAgentLLMClient
from harness_agent.memory.store import MemoryIdentity, shared_memory_store
from harness_agent.middleware.memory import (
    _extract_messages,
    _filter_visible_messages,
    _find_turn_trigger_user,
    _get_configurable,
    _opt_id,
    _split_user_assistant,
    _stringify_content,
)
from harness_agent.middleware.memory_recall import (
    has_recall_snapshot,
    stamp_recall_snapshot,
)
from harness_memory import Memory, MemoryService
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_config

from octop.infra.agents.memory_backend import memory_namespace

logger = logging.getLogger(__name__)

CONFIGURABLE_FEATURE_MEMORY_KEY = "octop_feature_memory"
"""The per-turn feature-memory routing decision, stamped by the gateway."""


@dataclass(frozen=True)
class FeatureMemoryTurnContext:
    """What this turn may do with the feature's memory — decided once, upstream.

    The gateway derives it from the workflow stage and the caller's verified
    identity; this module and the freeze middleware only read it. A turn
    without a private namespace has no private memory at all, so no code path
    can address one.
    """

    stage: str | None
    """``draft`` / ``active`` from the workflow definition; ``None`` when the
    definition is unreadable — the fail-closed state that unlocks nothing."""

    shared_writable: bool
    """A verified draft author may edit the shared root files *manually* —
    the file-tool and dashboard-API path that builds the corpus. Auto capture
    never consults this flag: conversational writes never reach shared memory
    in any stage."""

    shared_namespace: str
    """The feature's shared namespace, ``agent_{agent_id}``."""

    private_namespace: str | None = None
    """The verified caller's own namespace on an active feature; ``None``
    otherwise — an IM turn never carries one."""

    def capture_namespace(self) -> str | None:
        """Where this turn's capture may land, or ``None`` for no capture.

        Only the caller's own private namespace — an active feature's verified
        caller. Shared memory is never captured into, not even by its draft
        author: their training path is manual file/API edits, so extraction,
        candidate promotion and deposition can never touch shared either.
        """
        return self.private_namespace

    def recall_namespaces(self) -> tuple[str, ...]:
        """Namespaces this turn recalls from: shared, plus own private when one applies."""
        if self.private_namespace is not None:
            return (self.shared_namespace, self.private_namespace)
        return (self.shared_namespace,)


def turn_feature_memory_context() -> FeatureMemoryTurnContext | None:
    """This turn's routing decision, or ``None`` when the turn carries none."""
    try:
        config = get_config()
    except RuntimeError:  # outside a run (unit tests, CLI)
        return None
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    raw = configurable.get(CONFIGURABLE_FEATURE_MEMORY_KEY)
    return raw if isinstance(raw, FeatureMemoryTurnContext) else None


class FeatureMemoryRuntime:
    """Per-agent holder of the feature's shared and per-user private services.

    All ``Memory`` instances come from harness-agent's process-wide refcounted
    store, keyed by namespace + backend location — acquiring the shared
    namespace returns the very instance the agent's own memory runtime uses,
    and a private namespace opens that user's partition of the same database.
    ``close`` drops this runtime's claims; the store closes a backend only on
    its last reference.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        backend_type: str,
        backend_config: dict[str, Any] | None,
        llm: HarnessAgentLLMClient | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._backend_type = backend_type
        self._backend_config = dict(backend_config) if backend_config else None
        self._llm = llm
        self._lock = threading.Lock()
        self._closed = False
        self._services: dict[str, MemoryService] = {}
        self._identities: dict[str, MemoryIdentity] = {}

    def service(self, namespace: str) -> MemoryService | None:
        """The service for *namespace*, opening the backing memory on first use."""
        with self._lock:
            if self._closed:
                return None
            existing = self._services.get(namespace)
            if existing is not None:
                return existing
        identity = self._identity(namespace)
        if identity is None:
            return None
        store = shared_memory_store()
        try:
            memory = store.acquire(identity, lambda: self._construct(namespace))
        except Exception:
            logger.warning(
                "feature memory for %s could not open namespace %s",
                self._agent_id,
                namespace,
                exc_info=True,
            )
            return None
        service = MemoryService(memory, llm=self._llm, host="harness-agent")
        with self._lock:
            if self._closed:
                store.release(identity)
                return None
            self._identities.setdefault(namespace, identity)
            self._services[namespace] = service
            return service

    def _identity(self, namespace: str) -> MemoryIdentity | None:
        """The process-wide store key: namespace + backend location."""
        cfg = self._backend_config or {}
        if self._backend_type == "sqlite":
            raw_path = str(cfg.get("db_path") or "")
            try:
                location = str(Path(raw_path).expanduser().resolve())
            except OSError:
                location = raw_path
        elif self._backend_type == "postgres":
            location = str(cfg.get("dsn") or "").strip()
        else:
            return None
        return MemoryIdentity(namespace=namespace, backend=self._backend_type, location=location)

    def _construct(self, namespace: str) -> Memory:
        return Memory(
            namespace=namespace,
            backend=self._backend_type,
            backend_config=self._backend_config,
        )

    def close(self) -> None:
        """Release every namespace this runtime opened (idempotent)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            identities = list(self._identities.values())
            self._identities.clear()
            self._services.clear()
        store = shared_memory_store()
        for identity in identities:
            try:
                store.release(identity)
            except Exception:  # pragma: no cover - defensive shutdown
                logger.warning(
                    "feature memory for %s failed to release %s",
                    self._agent_id,
                    identity.namespace,
                    exc_info=True,
                )


def _submit_daemon(fn: Callable[[], None], *, label: str) -> None:
    """Run a fire-and-forget memory write on a daemon thread, like harness does."""

    def _runner() -> None:
        try:
            fn()
        except Exception:  # pragma: no cover - never raise into the user turn
            logger.warning("feature memory background %s failed", label, exc_info=True)

    threading.Thread(target=_runner, name=f"octop-fm-{label}", daemon=True).start()


class FeatureMemoryMiddleware(AgentMiddleware[Any, Any]):
    """Capture, recall and distill a feature turn into its routed namespace.

    Mounted only on feature agents, with harness's own capture / recall
    injection / session-end extraction disabled for them (the manager sets the
    config flags), so every structured-memory write below is the only writer.
    The daily JSONL session log and store maintenance stay with the harness
    middleware, exactly as for experts.
    """

    @property
    def name(self) -> str:
        """Distinct name so the harness never confuses the two middlewares."""
        return "OctopFeatureMemoryMiddleware"

    def __init__(
        self,
        *,
        agent_id: str,
        runtime: FeatureMemoryRuntime,
        recall_limit: int = 5,
        idle_extract_seconds: float = 300.0,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._runtime = runtime
        self._recall_limit = recall_limit
        self._idle_extract_seconds = idle_extract_seconds
        # Per-thread message count at before_model time, mirroring the harness
        # middleware's cursor discipline for after_model capture.
        self._cursors: dict[int, int] = {}
        # session_id -> namespace its turns were captured in, so extraction
        # distills the partition the events actually live in, even if the
        # feature's stage changed mid-session.
        self._session_scope: dict[str, str] = {}
        self._scope_lock = threading.Lock()
        self._idle_timers: dict[str, threading.Timer] = {}
        self._idle_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Read path: merged recall snapshot
    # ------------------------------------------------------------------

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Stamp shared (+ own private) recall onto a fresh user message."""
        try:
            messages = _extract_messages(state)
            self._cursors[threading.get_ident()] = len(messages)
            if (
                messages
                and isinstance(messages[-1], HumanMessage)
                and not has_recall_snapshot(messages[-1])
            ):
                ctx = turn_feature_memory_context()
                if ctx is not None:
                    namespaces = ctx.recall_namespaces()
                else:
                    # Unstamped turn (HITL resume, cron): shared reads only.
                    namespaces = (memory_namespace(self._agent_id),)
                rendered = self._recall_rendered(namespaces, messages[-1], runtime)
                return {"messages": [stamp_recall_snapshot(messages[-1], rendered)]}
        except Exception:  # pragma: no cover - never raise into the user turn
            logger.warning("feature memory recall failed", exc_info=True)
        return None

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def _recall_rendered(
        self, namespaces: tuple[str, ...], message: HumanMessage, runtime: Any
    ) -> str:
        query = _stringify_content(message.content).strip()
        if not query:
            return ""
        configurable = _get_configurable(runtime)
        thread_id = _opt_id(configurable, "thread_id")
        session_id = _opt_id(configurable, "session_id") or thread_id
        blocks: list[str] = []
        for namespace in namespaces:
            service = self._runtime.service(namespace)
            if service is None:
                continue
            try:
                result = service.recall(
                    query, thread_id=thread_id, session_id=session_id, limit=self._recall_limit
                )
            except Exception:
                logger.warning("feature memory recall failed ns=%s", namespace, exc_info=True)
                continue
            rendered = getattr(result, "rendered", None) or ""
            if rendered:
                blocks.append(rendered)
            logger.info(
                "feature memory recall ns=%s thread=%s session=%s hit=%s",
                namespace,
                thread_id,
                session_id,
                bool(rendered),
            )
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Write path: routed capture
    # ------------------------------------------------------------------

    def after_model(self, state: Any, runtime: Any) -> None:
        """Capture this turn's visible exchange — into a private namespace only.

        A turn without a private namespace (draft author, IM, cron, HITL
        resume) captures nothing, so no conversational write can ever reach
        the shared corpus.
        """
        try:
            messages = _extract_messages(state)
            cursor = self._cursors.pop(threading.get_ident(), 0)
            new_messages: list[Any] = list(messages[cursor:]) if cursor <= len(messages) else []
            if not new_messages:
                return
            configurable = _get_configurable(runtime)
            thread_id = configurable.get("thread_id", "unknown")
            session_id = configurable.get("session_id") or thread_id
            ctx = turn_feature_memory_context()
            namespace = ctx.capture_namespace() if ctx is not None else None
            if namespace is None:
                return
            trigger_user = _find_turn_trigger_user(messages, cursor)
            visible = _filter_visible_messages(new_messages, trigger_user=trigger_user)
            if not visible:
                return
            user_text, assistant_text = _split_user_assistant(visible)
            if not user_text and not assistant_text:
                return
            self._remember_scope(session_id, namespace)
            user_id = configurable.get("user")
            namespace_key = namespace

            def _run() -> None:
                service = self._runtime.service(namespace_key)
                if service is None:
                    return
                logger.info(
                    "feature memory capture ns=%s session=%s thread=%s user=%s",
                    namespace_key,
                    session_id,
                    thread_id,
                    user_id,
                )
                service.capture_turn(
                    user=user_text,
                    assistant=assistant_text,
                    session_id=session_id,
                    thread_id=thread_id,
                    user_id=user_id if isinstance(user_id, str) else None,
                )

            _submit_daemon(_run, label="capture")
            self._arm_idle_extract(session_id)
        except Exception:  # pragma: no cover - never raise into the user turn
            logger.warning("feature memory capture failed", exc_info=True)

    async def aafter_model(self, state: Any, runtime: Any) -> None:
        self.after_model(state, runtime)

    # ------------------------------------------------------------------
    # Extraction: distill each session into the namespace it was captured in
    # ------------------------------------------------------------------

    def _remember_scope(self, session_id: str, namespace: str) -> None:
        with self._scope_lock:
            self._session_scope[session_id] = namespace

    def _arm_idle_extract(self, session_id: str) -> None:
        if self._idle_extract_seconds <= 0:
            return
        with self._idle_lock:
            existing = self._idle_timers.pop(session_id, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(
                self._idle_extract_seconds,
                self._on_idle_extract,
                args=(session_id,),
            )
            timer.daemon = True
            timer.name = f"octop-fm-idle-{session_id}"
            self._idle_timers[session_id] = timer
            timer.start()

    def _on_idle_extract(self, session_id: str) -> None:
        with self._idle_lock:
            self._idle_timers.pop(session_id, None)
        self.end_session(session_id, background=True)

    def end_session(
        self,
        session_id: str,
        *,
        background: bool = True,
        incremental: bool = True,
        promote: bool = True,
        regen_pages: bool = True,
    ) -> None:
        """Distill a finished session into the namespace its turns were captured in.

        The scope map only ever records what ``after_model`` captured — a
        private namespace — so extraction, candidate promotion and deposition
        can never reach shared memory, whatever stage the feature is in now.
        """
        with self._scope_lock:
            namespace = self._session_scope.pop(session_id, None)
        if namespace is None:
            return

        def _run() -> None:
            service = self._runtime.service(namespace)
            if service is None:
                return
            try:
                result = service.extract(
                    session_id,
                    incremental=incremental,
                    promote=promote,
                    regen_pages=regen_pages,
                )
                logger.info(
                    "feature memory extract ns=%s session=%s result=%s",
                    namespace,
                    session_id,
                    {
                        k: result.get(k)
                        for k in ("candidates", "promoted", "failure_reason")
                        if isinstance(result, dict) and k in result
                    },
                )
            except Exception:  # pragma: no cover - extract is off the user path
                logger.warning(
                    "feature memory extract ns=%s session=%s failed",
                    namespace,
                    session_id,
                    exc_info=True,
                )

        if background:
            _submit_daemon(_run, label="extract")
        else:
            _run()

    def shutdown(self) -> None:
        """Cancel idle timers without firing them. Safe to call repeatedly."""
        with self._idle_lock:
            timers = list(self._idle_timers.values())
            self._idle_timers.clear()
        for timer in timers:
            timer.cancel()

    def close(self) -> None:
        """Stop this routing and release its namespace services (idempotent)."""
        self.shutdown()
        self._runtime.close()


# ----------------------------------------------------------------------
# Scope-routed memory tools
# ----------------------------------------------------------------------

_PATH_SHARED_PREFIX = "shared:"
"""Hit-path prefix that pins a lookup to the feature's shared memory."""

_PATH_PRIVATE_PREFIX = "private:"
"""Hit-path prefix that pins a lookup to the caller's own private memory."""


def _split_memory_path(path: str) -> tuple[str | None, str]:
    """Split an optional ``shared:`` / ``private:`` prefix off a virtual path."""
    stripped = path.strip()
    lowered = stripped.lower()
    if lowered.startswith(_PATH_SHARED_PREFIX):
        return "shared", stripped[len(_PATH_SHARED_PREFIX) :].strip()
    if lowered.startswith(_PATH_PRIVATE_PREFIX):
        return "private", stripped[len(_PATH_PRIVATE_PREFIX) :].strip()
    return None, stripped


def build_feature_memory_tools(
    runtime: FeatureMemoryRuntime,
    *,
    agent_id: str,
) -> list[BaseTool]:
    """``memory_search`` / ``memory_get`` bound to this turn's own namespaces.

    Same names as harness's service tools — declared later in the agent's tool
    list, and the graph keeps the last tool per name, so on a feature these
    replace the shared-only defaults. Each call resolves only the namespaces
    the turn may recall from: the feature's shared memory, plus the active
    verified caller's own private partition. No argument a model can choose
    ever widens that scope, so one user's search can never surface another
    user's private memory.
    """

    def _namespaces_for_turn() -> tuple[str, ...]:
        ctx = turn_feature_memory_context()
        if ctx is not None:
            return ctx.recall_namespaces()
        # Unstamped turn (HITL resume, cron): shared reads only.
        return (memory_namespace(agent_id),)

    @tool
    def memory_search(query: str, max_results: int = 5) -> str:
        """Search this feature's memory for the current caller.

        Covers the feature's shared memory plus, on an active feature, the
        caller's own private memory. Use this when the user references
        something that happened in a previous turn or session, or asks about
        a fact / preference / decision they expect the agent to remember.

        Args:
            query: Natural-language search query.
            max_results: Cap on returned hits per memory (default 5, max ~20).
        """
        try:
            namespaces = _namespaces_for_turn()
            lines: list[str] = []
            empty_reason: str | None = None
            for namespace in namespaces:
                service = runtime.service(namespace)
                if service is None:
                    continue
                result = service.search(query, max_results=max_results)
                hits = result.get("hits", []) if isinstance(result, dict) else []
                # Shared is always first in the tuple; private is the caller's own.
                tag = "shared" if namespace == namespaces[0] else "private"
                for h in hits:
                    path = h.get("path", "?")
                    layer = h.get("layer") or "?"
                    snippet = (h.get("snippet") or "").strip().replace("\n", " ")
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    lines.append(f"- [{tag}] [{layer}] {tag}:{path}\n  {snippet}")
                if not hits and empty_reason is None:
                    empty_reason = result.get("empty_reason") if isinstance(result, dict) else None
            if not lines:
                return f"No matching memory entries. ({empty_reason or 'empty'})"
            header = "Memory hits (pass memory_get the path including its shared:/private: prefix):"
            return header + "\n" + "\n".join(lines)
        except Exception as exc:  # pragma: no cover - storage / ranker fault
            logger.warning("feature memory_search failed: %s", exc, exc_info=True)
            return f"memory_search failed: {exc.__class__.__name__}"

    @tool
    def memory_get(path: str, start: int | None = None, lines: int | None = None) -> str:
        """Resolve a virtual memory path returned by ``memory_search``.

        Args:
            path: The virtual path from a ``memory_search`` hit, including its
                memory prefix (``shared:atom/<id>.md`` /
                ``private:page/<entity>.md`` / ``private:raw/<date>/<id>.md``).
                A bare path resolves against the caller's own private memory
                first, then shared.
            start: Optional 1-based line number to start at.
            lines: Optional number of lines to return.
        """
        try:
            namespaces = _namespaces_for_turn()
            tag, virtual = _split_memory_path(path)
            if tag == "shared":
                candidates: tuple[str, ...] = (namespaces[0],)
            elif tag == "private":
                if len(namespaces) < 2:
                    return "memory_get error: this view has no private memory"
                candidates = (namespaces[-1],)
            else:
                # Own private first — the more specific source — then shared.
                candidates = tuple(reversed(namespaces))
            first_error = ""
            for namespace in candidates:
                service = runtime.service(namespace)
                if service is None:
                    continue
                result = service.get(virtual, start=start, lines=lines)
                if not isinstance(result, dict):
                    return str(result)
                if result.get("error"):
                    first_error = first_error or f"memory_get error: {result.get('error')}"
                    continue
                content = result.get("content")
                return content if isinstance(content, str) else str(result)
            return first_error or "memory_get error: memory is unavailable for this view"
        except Exception as exc:  # pragma: no cover - storage / IO fault
            logger.warning("feature memory_get failed: %s", exc, exc_info=True)
            return f"memory_get failed: {exc.__class__.__name__}"

    return [memory_search, memory_get]


__all__ = [
    "CONFIGURABLE_FEATURE_MEMORY_KEY",
    "FeatureMemoryMiddleware",
    "FeatureMemoryRuntime",
    "FeatureMemoryTurnContext",
    "build_feature_memory_tools",
    "turn_feature_memory_context",
]
