"""Dispatch due feature-data reminders into durable in-app notifications."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from octop.infra.agents import feature_data as fd

logger = logging.getLogger(__name__)

FEATURE_REMINDER_JOB = "feature-data-reminders"
FEATURE_REMINDER_INTERVAL_SECONDS = 60


async def dispatch_due_feature_reminders(server: Any) -> int:
    """Create one notification per recipient per threshold crossing.

    The reminder row is the source of truth; the notifications are derived.  A
    missing workspace or an unreadable schema only stops that reminder from
    advancing its threshold, never the other reminders.
    """
    repo = server.services.repos.feature_data_repo
    now = int(datetime.now(UTC).timestamp())
    dispatched = 0
    for reminder in repo.due_reminders(now):
        record = repo.get_record(reminder.record_id)
        agent = server.services.repos.agent_repo.get(reminder.agent_id)
        if record is None or record.deleted_at is not None or agent is None:
            repo.set_reminder_schedule(reminder.id, next_notify_at=None, last_notified_at=None)
            continue
        workspace = server.app_runtime.agent_registry.workspace_for_agent(reminder.agent_id)
        thresholds: list[int] = []
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
        due_ts = int(
            datetime(
                date.fromisoformat(reminder.due_at).year,
                date.fromisoformat(reminder.due_at).month,
                date.fromisoformat(reminder.due_at).day,
                tzinfo=UTC,
            ).timestamp()
        )
        threshold = None
        if reminder.next_notify_at is not None:
            offset = due_ts - reminder.next_notify_at
            if offset >= 0 and offset % 86400 == 0 and offset // 86400 in thresholds:
                threshold = offset // 86400
        cycle = f"{reminder.due_at}:{threshold if threshold is not None else 'due'}"
        recipients = {record.created_by}
        if agent.user_id is not None:
            recipients.add(int(agent.user_id))
        title = reminder.title or record.title
        body = f"到期日 {reminder.due_at}"
        for user_id in recipients:
            if repo.add_notification(
                agent_id=reminder.agent_id,
                user_id=user_id,
                reminder_id=reminder.id,
                cycle_key=cycle,
                title=title,
                body=body,
            ):
                dispatched += 1
        future = [due_ts - day * 86400 for day in thresholds if due_ts - day * 86400 > now]
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
