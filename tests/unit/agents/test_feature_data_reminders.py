"""Feature reminder dispatch keeps notifications durable and deduplicated."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from octop.infra.agents.feature_data_reminders import dispatch_due_feature_reminders
from octop.infra.db.repos.feature_data import (
    FeatureDataRecordRow,
    FeatureDataReminderRow,
)


class _Repo:
    def __init__(self) -> None:
        self.reminder = FeatureDataReminderRow(
            id="rem-1",
            agent_id="feat-x",
            record_id="rec-1",
            object_key="supplier",
            field_key="expires_at",
            due_at="2030-01-01",
            title="ACME expires",
            status="open",
            next_notify_at=1,
            last_notified_at=None,
            created_by=2,
            created_at=1,
            updated_at=1,
        )
        self.notifications: list[tuple[int, str]] = []
        self.schedule: tuple[int | None, int | None] | None = None

    def due_reminders(self, _now: int) -> list[FeatureDataReminderRow]:
        return [self.reminder]

    def get_record(self, _record_id: str) -> FeatureDataRecordRow:
        return FeatureDataRecordRow(
            id="rec-1",
            agent_id="feat-x",
            object_key="supplier",
            title="ACME",
            values={},
            visibility="feature",
            revision=1,
            created_by=2,
            created_at=1,
            updated_at=1,
            deleted_at=None,
        )

    def add_notification(self, **kwargs: Any) -> bool:
        key = (int(kwargs["user_id"]), str(kwargs["cycle_key"]))
        if key in self.notifications:
            return False
        self.notifications.append(key)
        return True

    def set_reminder_schedule(
        self, _reminder_id: str, *, next_notify_at: int | None, last_notified_at: int | None
    ) -> None:
        self.schedule = (next_notify_at, last_notified_at)


async def test_dispatch_notifies_record_creator_and_owner_once() -> None:
    repo = _Repo()
    agent = SimpleNamespace(user_id=1, agent_id="feat-x", kind="feature", enterprise_unit_key=None)
    users = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(id=1, role="member", disabled=0),
            SimpleNamespace(id=2, role="member", disabled=0),
        ]
    )
    users.get = lambda user_id: next((user for user in users.list() if user.id == user_id), None)

    class ACL:
        def get(self, _kind: str, _resource_id: str) -> Any:
            return SimpleNamespace(visibility="public", owner_user_id=1, grants=())

        def scope_for_user(self, _user_id: int) -> tuple[str, tuple[str, ...]]:
            return "member", ()

    server = SimpleNamespace(
        services=SimpleNamespace(
            repos=SimpleNamespace(
                feature_data_repo=repo,
                agent_repo=SimpleNamespace(get=lambda _id: agent),
                user_repo=users,
                resource_acl_repo=ACL(),
            )
        ),
        app_runtime=SimpleNamespace(
            agent_registry=SimpleNamespace(workspace_for_agent=lambda _id: None)
        ),
    )
    assert await dispatch_due_feature_reminders(server) == 2
    assert {user for user, _cycle in repo.notifications} == {1, 2}
    assert repo.schedule is not None
