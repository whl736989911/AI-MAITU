"""Gateway — global AI interaction entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_gateway.channel import ChannelCredentialsError, MessageProcessor
from harness_gateway.channels import ChannelKind
from harness_gateway.manager import ChannelManager
from harness_gateway.models import ChannelSubject, InboundMessage, MessageEvent

from octop.i18n import (
    channel_permission_revoked,
    channel_probe_incomplete,
    channel_runtime_reason,
    tr,
)
from octop.infra.db.repos.channels import ChannelRow
from octop.infra.db.repos.sessions import SessionRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.cli import CLI_CHANNEL_ID, CliChannel, CliHub
from octop.infra.gateway.history_backfill import HistoryBackfillQueue
from octop.infra.gateway.process import media_backend_for_agent
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.process.response_mode import (
    normalize_channel_response_mode,
    processor_for_response_mode,
    qq_channel_response_mode,
)
from octop.infra.gateway.slash.dispatcher import SlashDispatcher, build_default_dispatcher
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.gateway.ws import (
    WS_CHANNEL_ID,
    WebSocketChannel,
    WebSocketHub,
)
from octop.infra.users.permissions import (
    channel_permission_key,
    unit_permissions,
    user_has_permission,
)
from octop.infra.utils.locale import DEFAULT_LOCALE, Locale, normalize_locale

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.db.services import RepoBundle

logger = logging.getLogger(__name__)

# Re-export for api/cli importers
__all__ = [
    "ChannelCreateSpec",
    "ChannelKind",
    "ChannelRuntimeStatus",
    "Gateway",
    "SlashRuntimeMeta",
]


@dataclass(frozen=True)
class SlashRuntimeMeta:
    version: str
    started_at: int


@dataclass(frozen=True)
class ChannelRuntimeStatus:
    """Live connection state for a channel.

    ``reason`` is a locale-neutral code (``disabled`` / ``unregistered`` /
    ``error``) localized at serialization time; ``detail`` carries free-form
    diagnostics (e.g. an exception message) shown alongside the reason.
    """

    connected: bool
    reason: str | None = None
    detail: str | None = None
    updated_at: int = 0


@dataclass
class ChannelCreateSpec:
    channel_id: str
    agent_id: str
    user_id: int
    kind: ChannelKind | str
    name: str
    config: dict[str, Any] = field(default_factory=dict)


async def _probe_processor(_msg: Any) -> Any:
    """Stub processor for ephemeral channel probe instances."""
    if False:  # pragma: no cover - makes this an async generator
        yield None


class _PermissionGatedProcessor:
    """``MessageProcessor`` wrapper that re-asks whether the channel may run.

    Runtime is where a revoked module key has to bite (design §4.5: 通道收发消息前
    检查对应 ``channel_<kind>``): the page that created the channel, the JWT in the
    browser and the row in the database all say what was true when they were
    written, and the message arriving now is the first chance to ask again.
    ``refusal`` returns the line the IM user gets when the channel may not run,
    or ``None`` when it may — a channel that stops answering without a word
    reads as a broken bot, not as a revoked authorization (§2.4 has no silent
    half).
    """

    def __init__(self, inner: MessageProcessor, refusal: Callable[[], str | None]) -> None:
        self._inner = inner
        self._refusal = refusal

    @property
    def inner(self) -> MessageProcessor:
        """The processor this gate wraps — the response-mode layer under it."""
        return self._inner

    async def __call__(self, message: InboundMessage) -> AsyncIterator[MessageEvent]:
        denied = self._refusal()
        if denied is not None:
            yield MessageEvent.error_event(denied)
            yield MessageEvent.completed()
            return
        async for event in self._inner(message):
            yield event


class Gateway:
    """Global AI interaction entry point.

    Owns the harness-gateway ChannelManager. Routes IM messages
    by ``InboundMessage.tenant_id`` (== agent ULID) via GlobalProcessor.
    """

    def __init__(
        self,
        *,
        agent_manager: AgentManager,
        repos: RepoBundle,
        trajectory_service: Any | None = None,
        history_archive: Any | None = None,
    ) -> None:
        self._agent_manager = agent_manager
        self._repos = repos
        self._trajectory_service = trajectory_service
        self._history_archive = history_archive
        self._thread_registry = ThreadRegistry(
            session_repo=repos.session_repo,
            thread_repo=repos.thread_repo,
        )
        self._channel_manager: ChannelManager | None = None
        self._processor: GlobalProcessor | None = None
        self._dispatcher = build_default_dispatcher()
        self._slash_meta: SlashRuntimeMeta | None = None
        self._ws_hub = WebSocketHub()
        self._cli_hub = CliHub()
        self._ws_channel: WebSocketChannel | None = None
        self._cli_channel: CliChannel | None = None
        self._runtime_status: dict[str, ChannelRuntimeStatus] = {}
        self._history_backfill = HistoryBackfillQueue()

    def replace_repos(self, repos: RepoBundle) -> None:
        """Point channel/thread persistence at a rebound control-plane pool."""
        self._repos = repos
        self._thread_registry.replace_repos(
            session_repo=repos.session_repo,
            thread_repo=repos.thread_repo,
        )
        if self._processor is not None:
            self._processor.replace_thread_message_repo(repos.thread_message_repo)

    @property
    def ws_hub(self) -> WebSocketHub:
        return self._ws_hub

    @property
    def cli_hub(self) -> CliHub:
        return self._cli_hub

    @property
    def cli_channel_id(self) -> str:
        return CLI_CHANNEL_ID

    @property
    def channel_manager(self) -> ChannelManager | None:
        return self._channel_manager

    @property
    def dashboard_channel_id(self) -> str:
        return WS_CHANNEL_ID

    @property
    def slash_dispatcher(self) -> SlashDispatcher:
        return self._dispatcher

    @property
    def processor(self) -> GlobalProcessor:
        if self._processor is None:
            raise RuntimeError("gateway not booted")
        return self._processor

    @property
    def history_backfill(self) -> HistoryBackfillQueue:
        return self._history_backfill

    @property
    def slash_meta(self) -> SlashRuntimeMeta | None:
        return self._slash_meta

    def set_slash_meta(self, *, version: str, started_at: int) -> None:
        self._slash_meta = SlashRuntimeMeta(version=version, started_at=started_at)

    @property
    def thread_registry(self) -> ThreadRegistry:
        return self._thread_registry

    def get_runtime_status(self, channel_id: str) -> ChannelRuntimeStatus | None:
        return self._runtime_status.get(channel_id)

    def runtime_status_to_dict(
        self, channel_id: str, *, locale: Locale = DEFAULT_LOCALE
    ) -> dict[str, Any] | None:
        status = self.get_runtime_status(channel_id)
        if status is None:
            return None
        error: str | None = None
        if status.reason is not None:
            error = channel_runtime_reason(status.reason, locale)
            if status.detail:
                error = f"{error}: {status.detail}"
        return {
            "connected": status.connected,
            "error": error,
            "updated_at": status.updated_at,
        }

    async def boot(self) -> None:
        self._processor = GlobalProcessor(
            agent_manager=self._agent_manager,
            thread_registry=self._thread_registry,
            audit_repo=self._repos.audit_repo,
            agent_repo=self._repos.agent_repo,
            user_repo=self._repos.user_repo,
            connector_repo=self._repos.connector_repo,
            knowledge_repo=self._repos.knowledge_repo,
            settings_repo=self._repos.settings_repo,
            provider_repo=self._repos.provider_repo,
            dispatcher=self._dispatcher,
            usage_repo=self._repos.usage_repo,
            thread_message_repo=self._repos.thread_message_repo,
            gateway=self,
            trajectory_service=self._trajectory_service,
            history_archive=self._history_archive,
            feature_overlay_repo=self._repos.feature_overlay_repo,
            feature_run_repo=self._repos.feature_run_repo,
        )

        self._channel_manager = ChannelManager(channels={})
        self._channel_manager.set_pre_lock_handler(self._preempt_cancel_on_stop)
        await self._channel_manager.start()

        self._ws_channel = WebSocketChannel(
            self._processor,
            hub=self._ws_hub,
            channel_id=WS_CHANNEL_ID,
        )
        await self._channel_manager.add_channel(self._ws_channel)

        self._cli_channel = CliChannel(
            self._processor,
            hub=self._cli_hub,
            channel_id=CLI_CHANNEL_ID,
        )
        await self._channel_manager.add_channel(self._cli_channel)

        rows = self._repos.channel_repo.list_all(include_disabled=False)
        if rows:
            await asyncio.gather(*(self._start_channel(row) for row in rows))

        logger.info("Gateway booted")

    async def refresh_media_backends(self) -> None:
        """Re-set media backends on all registered channels.

        Called after agents finish booting (gateway boots before agents) to
        resolve the startup ordering gap.
        """
        if not self._channel_manager:
            return
        rows = self._repos.channel_repo.list_all(include_disabled=False)
        for row in rows:
            channel = self._channel_manager.get_channel(row.channel_id)
            if channel is None:
                continue
            backend = media_backend_for_agent(self._agent_manager, row.agent_id)
            if backend is not None:
                channel.set_media_backend(backend)

    async def reload_channels_from_db(self) -> None:
        """Drop and re-register enabled IM channels from the DB.

        Used after backup restore so Gateway matches the replaced ``channels``
        table without a process restart. Built-in dashboard/cli channels stay
        registered. Call after agents have been rehydrated so media backends
        can resolve.
        """
        if not self._channel_manager or not self._processor:
            return

        builtin = {WS_CHANNEL_ID, CLI_CHANNEL_ID}
        live_ids = [cid for cid in self._channel_manager.channel_ids if cid not in builtin]
        for channel_id in live_ids:
            await self._unregister(channel_id)

        for channel_id in list(self._runtime_status):
            if channel_id not in builtin:
                self._runtime_status.pop(channel_id, None)

        rows = self._repos.channel_repo.list_all(include_disabled=False)
        if rows:
            await asyncio.gather(*(self._start_channel(row) for row in rows))
        await self.refresh_media_backends()
        logger.info("Gateway channels reloaded from DB (%d enabled)", len(rows))

    async def shutdown(self) -> None:
        await self._history_backfill.close()
        if self._channel_manager:
            await self._channel_manager.stop()
        self._channel_manager = None
        self._processor = None
        self._ws_channel = None
        self._cli_channel = None
        self._runtime_status.clear()

    def list_channels(self, agent_id: str) -> list[ChannelRow]:
        return self._repos.channel_repo.list_by_agent(agent_id)

    def get_channel(self, channel_id: str) -> ChannelRow | None:
        return self._repos.channel_repo.get(channel_id)

    async def create_channel(self, spec: ChannelCreateSpec) -> ChannelRow:
        config_json = json.dumps(spec.config)
        existing = self._repos.channel_repo.get_by_agent_and_name(spec.agent_id, spec.name)
        if existing is not None:
            if existing.kind != str(spec.kind):
                raise OctopError(
                    ErrorCode.CHANNEL_NAME_TAKEN,
                    f"channel name {spec.name!r} already in use by kind {existing.kind!r}",
                )
            row = await self.update_channel(
                existing.channel_id,
                kind=str(spec.kind),
                name=spec.name,
                config_json=config_json,
                enabled=1,
            )
            assert row is not None
            return row

        self._repos.channel_repo.create(
            channel_id=spec.channel_id,
            agent_id=spec.agent_id,
            user_id=spec.user_id,
            kind=str(spec.kind),
            name=spec.name,
            config_json=config_json,
        )
        row = self._repos.channel_repo.get(spec.channel_id)
        assert row is not None
        await self._start_channel(row)
        return row

    async def update_channel(
        self,
        channel_id: str,
        *,
        kind: str | None = None,
        name: str | None = None,
        config_json: str | None = None,
        enabled: int | None = None,
    ) -> ChannelRow | None:
        self._repos.channel_repo.update(
            channel_id,
            kind=kind,
            name=name,
            config_json=config_json,
            enabled=bool(enabled) if enabled is not None else None,
        )
        row = self._repos.channel_repo.get(channel_id)
        await self._unregister(channel_id)
        if row is not None:
            await self._start_channel(row)
        return row

    async def delete_channel(self, channel_id: str) -> None:
        self._repos.channel_repo.delete(channel_id)
        await self._unregister(channel_id)
        self._runtime_status.pop(channel_id, None)

    def require_session(self, agent_id: str, session_key: str) -> SessionRow:
        """Return a session after validating its owning agent."""
        session = self._thread_registry.get_session(session_key)
        if session is None:
            raise ValueError(f"session {session_key!r} not found")
        if session.agent_id != agent_id:
            raise ValueError(f"session {session_key!r} does not belong to agent {agent_id!r}")
        return session

    def _bump_virtual_session(self, session: SessionRow, text: str) -> None:
        self._thread_registry.touch_last_active(session.thread_id)
        if text:
            self._thread_registry.set_title_if_null(session.thread_id, text)
        self._thread_registry.increment_unread(session.session_key)

    async def run_in_session(
        self,
        agent_id: str,
        session_key: str,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        """Serialize a proactive operation with inbound turns for its session."""
        session = self.require_session(agent_id, session_key)
        manager = self._require_channel_manager()
        channel_id = self._channel_id_for_session(session)
        await manager.run_in_session(channel_id, session_key, operation)

    async def push_text_from_session(
        self,
        agent_id: str,
        session_key: str,
        text: str,
    ) -> None:
        """Push complete text without running the agent."""

        async def _locked() -> None:
            session = self.require_session(agent_id, session_key)
            await self.push_session_text(session, text, title_source=text)
            if session.channel_type == ThreadRegistry.CHANNEL_DASHBOARD:
                await self.notify_dashboard_push(session, agent_id, text)

        await self.run_in_session(agent_id, session_key, _locked)

    async def push_session_text(
        self,
        session: SessionRow,
        text: str,
        *,
        title_source: str | None = None,
    ) -> None:
        """Deliver one complete proactive message to a resolved session."""
        channel_id = self._channel_id_for_session(session)
        await self.push_text(
            session.channel_type,
            channel_id,
            self._resolve_push_subject(session),
            text,
        )
        if session.channel_type in (
            ThreadRegistry.CHANNEL_DASHBOARD,
            ThreadRegistry.CHANNEL_CLI,
        ):
            self._bump_virtual_session(session, title_source or text)

    @staticmethod
    def _channel_id_for_session(session: SessionRow) -> str:
        if session.channel_type == ThreadRegistry.CHANNEL_DASHBOARD:
            return WS_CHANNEL_ID
        if session.channel_type == ThreadRegistry.CHANNEL_CLI:
            return CLI_CHANNEL_ID
        if not session.channel_id:
            raise ValueError(
                f"session is IM ({session.channel_type!r}) but has no channel_id bound"
            )
        return session.channel_id

    def _resolve_push_subject(self, session: SessionRow) -> ChannelSubject:
        """Build ChannelSubject from session; IM routing enrichment is in harness-gateway."""
        subject = session.to_channel_subject()
        if session.channel_type not in (
            ThreadRegistry.CHANNEL_DASHBOARD,
            ThreadRegistry.CHANNEL_CLI,
        ):
            return subject
        metadata = dict(subject.metadata or {})
        metadata["thread_id"] = session.thread_id
        return ChannelSubject(
            subject_id=subject.subject_id,
            first_seen=subject.first_seen,
            last_seen=subject.last_seen,
            display_name=subject.display_name,
            chat_type=subject.chat_type,
            metadata=metadata,
        )

    async def notify_dashboard_push(
        self,
        session: SessionRow,
        agent_id: str,
        text: str,
    ) -> None:
        """Fan out a toast payload to the owner's dashboard notification sockets."""
        if not text.strip():
            return
        row = self._agent_manager.get_row(agent_id)
        name = getattr(row, "name", None) if row is not None else None
        agent_name = str(name).strip() if isinstance(name, str) and name.strip() else agent_id
        await self._ws_hub.push_to_user(
            session.user_id,
            {
                "type": "dashboard_push",
                "agent_id": agent_id,
                "thread_id": session.thread_id,
                "text": text,
                "agent_name": agent_name,
            },
        )

    def _require_channel_manager(self) -> ChannelManager:
        if self._channel_manager is None:
            raise RuntimeError("gateway not booted")
        return self._channel_manager

    async def _preempt_cancel_on_stop(self, _channel_id: str, message: Any) -> None:
        """Signal harness cancel for ``/stop`` / ``/cancel`` before the session lock.

        Same-session IM turns are serialized by ChannelManager. Without this hook,
        ``/stop`` waits behind the in-flight turn and never interrupts it.
        """
        from octop.infra.gateway.process.message_keys import session_key_from_message
        from octop.infra.gateway.slash.parser import parse_slash

        text = getattr(message, "text", None)
        cmd = parse_slash(text if isinstance(text, str) else None)
        if cmd is None or cmd.name not in ("stop", "cancel"):
            return

        agent_id = getattr(message, "tenant_id", None) or ""
        if not isinstance(agent_id, str) or not agent_id.strip():
            return

        session_key = session_key_from_message(message, agent_id=agent_id.strip())
        thread_id = self._thread_registry.get_bound_thread_id(session_key)
        if not thread_id:
            return

        self._agent_manager.cancel_stream(agent_id.strip(), thread_id)
        logger.info(
            "preempt cancel: agent=%s thread=%s cmd=/%s",
            agent_id,
            thread_id,
            cmd.name,
        )

    async def push_text(
        self,
        channel_type: str,
        channel_id: str,
        subject: ChannelSubject,
        text: str,
    ) -> None:
        """Proactively push text to an IM user via ChannelManager.

        The send entry refuses too (design §4.5: 发送入口也必须拒绝): a revoked type
        must not keep talking to the IM users it already reached. A ``channel_id``
        with no row is the virtual dashboard / CLI channel; those are not IM
        channels of any kind, and the surfaces that serve them carry their own
        module keys.
        """
        row = self._repos.channel_repo.get(channel_id)
        if row is not None and self.channel_refusal(row) is not None:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "channel type permission revoked",
                details={"permission": channel_permission_key(str(row.kind))},
            )
        await self._require_channel_manager().push_text(channel_id, subject, text)

    async def probe_channel(
        self, channel_id: str, *, locale: Locale = DEFAULT_LOCALE
    ) -> dict[str, Any]:
        """Start/stop an ephemeral channel instance to verify credentials."""
        row = self.get_channel(channel_id)
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "channel not found")
        return await self._probe_row(row, locale=locale)

    async def probe_config(
        self,
        *,
        agent_id: str,
        kind: str,
        config: dict[str, Any],
        locale: Locale = DEFAULT_LOCALE,
    ) -> dict[str, Any]:
        """Probe credentials without persisting a channel row."""
        ts = int(time.time())
        row = ChannelRow(
            id=0,
            channel_id="__probe__",
            agent_id=agent_id,
            user_id=0,
            kind=kind,
            name="probe",
            config_json=json.dumps(config),
            enabled=1,
            created_at=ts,
            updated_at=ts,
        )
        return await self._probe_row(row, locale=locale)

    async def _probe_row(
        self, row: ChannelRow, *, locale: Locale = DEFAULT_LOCALE
    ) -> dict[str, Any]:
        try:
            raw_cfg = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            return {"ok": False, "error": tr("errors.CHANNEL_INVALID_CREDENTIALS", locale)}
        if not isinstance(raw_cfg, dict):
            return {"ok": False, "error": tr("errors.CHANNEL_INVALID_CREDENTIALS", locale)}

        manager = self._channel_manager
        if manager is None:
            raise RuntimeError("gateway not booted")

        try:
            await manager.probe_channel(
                row.kind,
                raw_cfg,
                tenant_id=row.agent_id,
                channel_id=row.channel_id,
                processor=_probe_processor,
            )
            return {"ok": True}
        except ChannelCredentialsError as exc:
            return {"ok": False, "error": channel_probe_incomplete(exc.missing, locale)}
        except OctopError as exc:
            return {"ok": False, "error": exc.localized_message(locale)}
        except Exception as exc:
            logger.exception("Channel probe failed for %s", row.channel_id)
            return {"ok": False, "error": self._format_probe_error(exc, locale)}

    @staticmethod
    def _format_probe_error(exc: Exception, locale: Locale) -> str:
        msg = str(exc)
        lower = msg.lower()
        if "invalid appid or secret" in lower or "100016" in msg:
            return tr("channel.probe.invalid_credentials", locale)
        if "feishu token refresh failed" in lower:
            return tr(
                "channel.probe.feishu_token_failed", locale, detail=msg.split(":", 1)[-1].strip()
            )
        return msg

    def _set_runtime_status(
        self,
        channel_id: str,
        *,
        connected: bool,
        reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._runtime_status[channel_id] = ChannelRuntimeStatus(
            connected=connected,
            reason=reason,
            detail=detail,
            updated_at=int(time.time()),
        )

    async def _safe_register_channel(self, row: ChannelRow) -> None:
        try:
            await self._register_channel(row)
        except Exception as exc:
            logger.exception("Failed to register channel %s (%s)", row.channel_id, row.kind)
            self._set_runtime_status(
                row.channel_id, connected=False, reason="error", detail=str(exc)
            )

    def channel_owner_allowed(self, owner: Any | None, kind: str) -> bool:
        """Whether ``owner`` may use channel type ``kind`` (design §2.3).

        The subject of a channel-type permission is the account the channel
        belongs to (``channels.user_id``) — one and the same account at create
        time and at three in the morning when a message arrives, so what an
        administrator revokes is what the runtime stops.
        """
        if owner is None:
            return False
        grants = unit_permissions(getattr(owner, "org_unit", None), self._repos.org_unit_repo)
        return user_has_permission(owner, channel_permission_key(kind), unit_grants=grants)

    def channel_refusal(self, row: ChannelRow) -> str | None:
        """The line an IM user gets while ``row`` may not run, else ``None``."""
        owner = self._repos.user_repo.get(row.user_id)
        if self.channel_owner_allowed(owner, str(row.kind)):
            return None
        return channel_permission_revoked(normalize_locale(getattr(owner, "locale", None)))

    async def _stop_channel(self, channel_id: str, *, reason: str) -> None:
        """Take ``channel_id`` out of the manager; the reason is what the UI reads."""
        await self._unregister(channel_id)
        self._set_runtime_status(channel_id, connected=False, reason=reason)

    async def _start_channel(self, row: ChannelRow) -> None:
        """Register ``row`` if it may run at all — the one gate every path shares.

        Boot, reload, create, update and a permission change all land here, so
        已存在的通道在类型权限被撤销后立即停止运行 (design §2.3) cannot hold on one of
        them and not on another.
        """
        if not row.enabled:
            await self._stop_channel(row.channel_id, reason="disabled")
            return
        if not self.channel_owner_allowed(self._repos.user_repo.get(row.user_id), str(row.kind)):
            await self._stop_channel(row.channel_id, reason="permission")
            return
        await self._safe_register_channel(row)

    async def reconcile_channel_permissions(self, user_ids: Iterable[int]) -> None:
        """Re-derive the runtime state of the channels owned by ``user_ids``.

        Called when an account's own keys, role, department or denies change, or
        when a department's grants change (design §2.4: 已经配置的能力，在权限撤销后
        也不能继续运行). A channel that lost its type key is unregistered — its IM
        connection goes down and an in-flight turn is cancelled with it, which is
        what "立即" means for a long-running task — and one that got its key back
        is registered again, the same 通道管理服务恢复 the design leaves the recovery
        scope to.
        """
        wanted = set(user_ids)
        if not wanted:
            return
        for row in self._repos.channel_repo.list_all():
            if row.user_id not in wanted:
                continue
            if row.enabled and self.channel_owner_allowed(
                self._repos.user_repo.get(row.user_id), str(row.kind)
            ):
                if self._is_channel_live(row.channel_id):
                    continue
                await self._start_channel(row)
            else:
                await self._stop_channel(
                    row.channel_id, reason="permission" if row.enabled else "disabled"
                )

    def _is_channel_live(self, channel_id: str) -> bool:
        if self._channel_manager is None:
            return False
        return self._channel_manager.get_channel(channel_id) is not None

    def _gated_processor(self, row: ChannelRow, processor: MessageProcessor) -> MessageProcessor:
        """``processor`` wrapped so every inbound message re-asks the type key.

        Design §4.5: 运行时必须重新检查权限，不能只依赖页面进入时的结果. The
        registration is where the channel's kind and owner are known, so the
        check is bound here and re-run per message.
        """
        return _PermissionGatedProcessor(processor, lambda: self.channel_refusal(row))

    async def _register_channel(self, row: ChannelRow) -> None:
        if not self._channel_manager or not self._processor:
            return
        config = self._config_from_row(row)
        response_mode = (
            qq_channel_response_mode(config)
            if row.kind == "qq"
            else normalize_channel_response_mode(config.get("response_mode"))
        )
        processor = processor_for_response_mode(self._processor, response_mode)
        processor = self._gated_processor(row, processor)
        manager = self._require_channel_manager()
        await manager.add_channel(
            row.kind,
            config,
            tenant_id=row.agent_id,
            channel_id=row.channel_id,
            processor=processor,
        )
        registered = manager.get_channel(row.channel_id)
        if registered is not None:
            backend = media_backend_for_agent(self._agent_manager, row.agent_id)
            if backend is not None:
                registered.set_media_backend(backend)
        self._set_runtime_status(row.channel_id, connected=True)

    def _config_from_row(self, row: ChannelRow) -> dict[str, Any]:
        """Parse the stored JSON config; alias/normalize happens in ``Config.from_dict``."""
        try:
            raw = json.loads(row.config_json or "{}")
        except json.JSONDecodeError as exc:
            raise OctopError(
                ErrorCode.CHANNEL_INVALID_CREDENTIALS,
                f"channel {row.channel_id} config is not valid JSON",
            ) from exc
        if not isinstance(raw, dict):
            raise OctopError(
                ErrorCode.CHANNEL_INVALID_CREDENTIALS,
                f"channel {row.channel_id} config must be a JSON object",
            )
        return raw

    async def _unregister(self, channel_id: str) -> None:
        if not self._channel_manager:
            return
        try:
            await self._channel_manager.remove_channel(channel_id)
        except Exception:
            logger.exception("Failed to unregister channel %s", channel_id)
        self._set_runtime_status(channel_id, connected=False, reason="unregistered")
