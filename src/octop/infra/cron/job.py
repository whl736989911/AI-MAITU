"""CronJob — APScheduler callable with status and audit bookkeeping."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from octop.infra.cron.delivery import CronDeliveryCommand
from octop.infra.cron.task_type import normalize_cron_task_type
from octop.infra.db.repos.audit import ACTOR_SYSTEM

if TYPE_CHECKING:
    from octop.infra.cron.delivery import CronDeliveryService
    from octop.infra.db.repos.audit import AuditRepo
    from octop.infra.db.repos.cron import CronJobRepo, CronJobRow
    from octop.infra.users.capabilities import UserCapabilityPolicy

logger = logging.getLogger(__name__)


class CronJob:
    def __init__(
        self,
        *,
        cron_id: str,
        name: str,
        agent_id: str,
        prompt: str,
        fresh_thread: bool,
        session_key: str,
        model: str | None,
        task_type: str,
        mcp_servers: list[str] | None,
        user_id: int,
        delivery_service: CronDeliveryService,
        trigger: str,
        cron_repo: CronJobRepo,
        audit_repo: AuditRepo,
        user_policy: UserCapabilityPolicy,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        self._cron_id = cron_id
        self._name = name
        self._agent_id = agent_id
        self._prompt = prompt
        self._fresh_thread = fresh_thread
        self._session_key = session_key
        self._model = model
        self._task_type = normalize_cron_task_type(task_type)
        self._mcp_servers = list(mcp_servers or [])
        self._user_id = user_id
        self._delivery_service = delivery_service
        self._date_trigger = trigger.startswith("date:")
        self._trigger = trigger
        self._cron_repo = cron_repo
        self._audit_repo = audit_repo
        self._user_policy = user_policy
        self._is_current = is_current or (lambda: True)

    @classmethod
    def from_row(
        cls,
        row: CronJobRow,
        *,
        delivery_service: CronDeliveryService,
        cron_repo: CronJobRepo,
        audit_repo: AuditRepo,
        user_policy: UserCapabilityPolicy,
        is_current: Callable[[], bool] | None = None,
    ) -> CronJob:
        return cls(
            cron_id=row.cron_id,
            name=row.name,
            agent_id=row.agent_id,
            prompt=row.prompt,
            fresh_thread=bool(row.fresh_thread),
            session_key=row.session_key,
            model=row.model,
            task_type=row.task_type,
            mcp_servers=row.mcp_servers,
            user_id=row.user_id,
            trigger=row.trigger,
            delivery_service=delivery_service,
            cron_repo=cron_repo,
            audit_repo=audit_repo,
            user_policy=user_policy,
            is_current=is_current,
        )

    async def run(self, *, scheduled: bool = True) -> None:
        # Cron execution is business work. Check at the final execution boundary
        # so queued/manual invocations cannot deliver or record runs after lock.
        from octop.infra.licensing import current_license

        current_license().require_business_access()
        self._user_policy.require_permissions(self._user_id, ())
        if not self._is_current():
            return
        if scheduled:
            current = self._cron_repo.get(self._cron_id)
            if current is None or not current.enabled:
                # Removing a scheduler entry cannot retract an already-queued callback.
                return

        from octop.infra.metrics import METRICS  # noqa: PLC0415

        ts = int(time.time())
        if self._date_trigger and not self._cron_repo.claim_date_run(self._cron_id, ts=ts):
            # A scheduled callback, manual run, or replacement schedule owns it.
            self._cron_repo.update(self._cron_id, enabled=False)
            return
        try:
            await self._delivery_service.deliver(
                CronDeliveryCommand(
                    cron_id=self._cron_id,
                    cron_name=self._name,
                    agent_id=self._agent_id,
                    user_id=self._user_id,
                    session_key=self._session_key,
                    prompt=self._prompt,
                    fresh_thread=self._fresh_thread,
                    task_type=self._task_type,
                    model=self._model,
                    mcp_servers=tuple(self._mcp_servers),
                )
            )
        except (Exception, asyncio.CancelledError) as exc:
            METRICS.inc("cron_errors_total")
            err = str(exc)
            self._record_run_status(ts=ts, status="error", error=err)
            self._audit_repo.write(
                actor=ACTOR_SYSTEM,
                action="cron.run_failed",
                target=self._cron_id,
                payload=err,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return

        self._record_run_status(ts=ts, status="ok", error=None)
        self._audit_repo.write(
            actor=ACTOR_SYSTEM,
            action="cron.run_ok",
            target=self._cron_id,
        )

    def _run_state_is_current(self) -> bool:
        current = self._cron_repo.get(self._cron_id)
        return current is not None and current.trigger == self._trigger and self._is_current()

    def _record_run_status(self, *, ts: int, status: str, error: str | None) -> None:
        if not self._run_state_is_current():
            return
        self._cron_repo.set_run_status(self._cron_id, ts=ts, status=status, error=error)
        if self._date_trigger:
            self._cron_repo.update(self._cron_id, enabled=False)
