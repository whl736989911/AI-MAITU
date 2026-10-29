"""Dispatch due feature-data reminders into durable in-app notifications."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from octop.i18n import tr
from octop.infra.agents import feature_data as fd
from octop.infra.agents.feature_data_responsibility import resolve_reminder_recipients
from octop.infra.utils.locale import resolve_user_locale

logger = logging.getLogger(__name__)

FEATURE_REMINDER_JOB = "feature-data-reminders"
FEATURE_REMINDER_INTERVAL_SECONDS = 60


def _timezone(server: Any) -> ZoneInfo:
    config = getattr(server.services, "config", None)
    name = str(getattr(config, "default_timezone", None) or "UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.error("Invalid configured server timezone %r for feature reminders", name)
        return ZoneInfo("UTC")


def _local_midnight_timestamp(day: date, zone: ZoneInfo) -> int:
    return int(datetime.combine(day, time.min, tzinfo=zone).timestamp())


async def dispatch_due_feature_reminders(server: Any) -> int:
    """Create one currently authorized notification per recipient and threshold.

    Due dates are business-calendar dates in the configured server timezone.
    Recipients are resolved immediately before delivery so current account status,
    feature ACL, record visibility, unit membership, and role membership apply.
    """
    services = server.services
    repos = services.repos
    repo = repos.feature_data_repo
    zone = _timezone(server)
    now_dt = datetime.now(zone)
    now = int(now_dt.timestamp())
    dispatched = 0
    for reminder in repo.due_reminders(now):
        record = repo.get_record(reminder.record_id)
        agent = repos.agent_repo.get(reminder.agent_id)
        if record is None or record.deleted_at is not None or agent is None:
            repo.set_reminder_schedule(reminder.id, next_notify_at=None, last_notified_at=None)
            continue
        workspace = server.app_runtime.agent_registry.workspace_for_agent(reminder.agent_id)
        thresholds: list[int] = []
        obj: dict[str, Any] | None = None
        if workspace is not None:
            loaded = await fd.load_schema(workspace)
            if loaded.schema is not None:
                try:
                    obj = fd.object_definition(loaded.schema, reminder.object_key)
                    field = fd.field_definition(obj, reminder.field_key)
                    thresholds = sorted(
                        {int(day) for day in field.get("reminder_days", [])}, reverse=True
                    )
                except Exception:
                    thresholds = []
        try:
            due_day = date.fromisoformat(reminder.due_at)
        except ValueError:
            logger.warning("Invalid due date on feature reminder %s", reminder.id)
            repo.set_reminder_schedule(reminder.id, next_notify_at=None, last_notified_at=None)
            continue
        due_ts = _local_midnight_timestamp(due_day, zone)
        threshold = None
        if reminder.next_notify_at is not None:
            for candidate in thresholds:
                if reminder.next_notify_at == _local_midnight_timestamp(
                    due_day - timedelta(days=candidate), zone
                ):
                    threshold = candidate
                    break
        cycle = f"{reminder.due_at}:{threshold if threshold is not None else 'due'}"
        if all(hasattr(repos, name) for name in ("user_repo", "resource_acl_repo")):
            try:
                recipient_obj = obj or {"reminder_recipients": {"creator": True, "owner": True}}
                recipients = resolve_reminder_recipients(
                    record=record,
                    obj=recipient_obj,
                    agent=agent,
                    user_repo=repos.user_repo,
                    acl_repo=repos.resource_acl_repo,
                    unit_repo=getattr(repos, "org_unit_repo", None),
                )
            except Exception:
                logger.exception(
                    "Could not resolve authorized recipients for reminder %s", reminder.id
                )
                recipients = set()
        else:
            # Missing identity or ACL infrastructure is fail-closed.
            recipients = set()
        title = reminder.title or record.title
        for user_id in recipients:
            body = tr(
                "feature_data.reminder_due",
                resolve_user_locale(user_repo=repos.user_repo, user_id=user_id),
                date=reminder.due_at,
            )
            if repo.add_notification(
                agent_id=reminder.agent_id,
                user_id=user_id,
                reminder_id=reminder.id,
                cycle_key=cycle,
                title=title,
                body=body,
            ):
                dispatched += 1
        future: list[int] = []
        for day in thresholds:
            scheduled_at = _local_midnight_timestamp(due_day - timedelta(days=day), zone)
            if scheduled_at > now:
                future.append(scheduled_at)
        next_notify = min(future) if future else (due_ts if due_ts > now else None)
        repo.set_reminder_schedule(
            reminder.id,
            next_notify_at=next_notify,
            last_notified_at=now,
        )
    return dispatched


__all__ = [
    "FEATURE_REMINDER_INTERVAL_SECONDS",
    "FEATURE_REMINDER_JOB",
    "dispatch_due_feature_reminders",
]
