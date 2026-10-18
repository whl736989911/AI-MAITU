"""Automatic TLS certificate renewal scheduling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from octop.config import load_config
from octop.infra.setup.tls.manager import get_tls_manager
from octop.infra.setup.tls.modes import is_renewal_mode
from octop.infra.utils.paths import PathLayout

logger = logging.getLogger(__name__)

RENEWAL_WINDOW_DAYS = 30
_AUTO_RENEW_JOB_ID = "octop_tls_auto_renew"
_scheduler: AsyncIOScheduler | None = None


def _parse_expiry(expires_at: str) -> datetime | None:
    if not expires_at:
        return None
    try:
        dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def cert_expires_within_days(expires_at: str, *, days: int = RENEWAL_WINDOW_DAYS) -> bool:
    expiry = _parse_expiry(expires_at)
    if expiry is None:
        return False
    remaining = expiry - datetime.now(UTC)
    return remaining.total_seconds() <= days * 86400


async def _auto_renew_job(paths: PathLayout) -> None:
    config = load_config(paths.config)
    if not is_renewal_mode(config):
        return
    if not config.tls.domains:
        return
    if not cert_expires_within_days(config.tls.expires_at):
        return

    mgr = get_tls_manager()
    if mgr.is_busy():
        logger.info("TLS auto-renew skipped: issuance already in progress")
        return

    domain = config.tls.domains[0]
    logger.info("TLS auto-renew starting for %s", domain)
    try:
        result = await mgr.start_issue(
            domain=domain,
            config=config,
            paths=paths,
            staging=config.tls.acme_staging,
            locale="en",
        )
    except RuntimeError as exc:
        logger.warning("TLS auto-renew could not start: %s", exc)
        return
    if not result.ok:
        logger.warning("TLS auto-renew preflight failed")


def install_auto_renewal_job(*, paths: PathLayout, timezone: str = "Asia/Shanghai") -> None:
    """Run trusted TLS maintenance independently of the licensed business scheduler."""
    global _scheduler

    config = load_config(paths.config)
    if not is_renewal_mode(config):
        if _scheduler is not None and _scheduler.get_job(_AUTO_RENEW_JOB_ID) is not None:
            _scheduler.remove_job(_AUTO_RENEW_JOB_ID)
        return

    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=timezone)

    async def _run() -> None:
        await _auto_renew_job(paths)

    _scheduler.add_job(
        _run,
        trigger=CronTrigger(hour=3, minute=0, timezone=timezone),
        id=_AUTO_RENEW_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
    )
    if not _scheduler.running:
        _scheduler.start()
    logger.info("TLS auto-renewal job scheduled (daily 03:00)")


def shutdown_auto_renewal_scheduler() -> None:
    """Stop process-owned TLS maintenance during server shutdown."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
