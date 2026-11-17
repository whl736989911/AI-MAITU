"""Feature reminder dispatch keeps notifications durable and deduplicated."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
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
            responsible_user_id=3,
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


async def test_dispatch_dismisses_stale_reminder_without_persisting_notification(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from octop.infra.agents import feature_data as fd

    server, db, repo, reminder = _reminder_environment(
        tmp_path, due_day="2030-01-01", next_notify_at=1
    )

    async def load_schema(_workspace: Any) -> Any:
        return _loaded_schema(reminder_days=[])

    monkeypatch.setattr(fd, "load_schema", load_schema)
    try:
        assert await dispatch_due_feature_reminders(server) == 0
        assert repo.list_notifications(agent_id="feat-x", user_id=1) == []
        assert repo.get_reminder(reminder.id).status == "dismissed"
    finally:
        db.close()


async def test_dispatch_reschedules_changed_reminder_threshold_without_notifying(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from octop.infra.agents import feature_data as fd

    due_day = datetime.now(UTC).date() + timedelta(days=20)
    old_schedule = int(
        datetime.combine(due_day - timedelta(days=30), time.min, tzinfo=UTC).timestamp()
    )
    next_schedule = int(
        datetime.combine(due_day - timedelta(days=7), time.min, tzinfo=UTC).timestamp()
    )
    server, db, repo, reminder = _reminder_environment(
        tmp_path, due_day=due_day.isoformat(), next_notify_at=old_schedule
    )
    repo.set_reminder_schedule(
        reminder.id, next_notify_at=old_schedule, last_notified_at=old_schedule
    )
    repo.add_notification(
        agent_id="feat-x",
        user_id=1,
        reminder_id=reminder.id,
        cycle_key=f"{due_day.isoformat()}:30",
        title="ACME expires",
        body="previous reminder",
    )

    async def load_schema(_workspace: Any) -> Any:
        return _loaded_schema(reminder_days=[7])

    monkeypatch.setattr(fd, "load_schema", load_schema)
    try:
        assert await dispatch_due_feature_reminders(server) == 0
        notifications = repo.list_notifications(agent_id="feat-x", user_id=1)
        assert [item.cycle_key for item in notifications] == [f"{due_day.isoformat()}:30"]
        updated = repo.get_reminder(reminder.id)
        assert updated.status == "open"
        assert updated.next_notify_at == next_schedule
        assert updated.last_notified_at == old_schedule
    finally:
        db.close()


async def test_dispatch_persists_notification_for_authorized_owner(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from octop.infra.agents import feature_data as fd

    due_day = datetime.now(UTC).date()
    server, db, repo, _reminder = _reminder_environment(
        tmp_path, due_day=due_day.isoformat(), next_notify_at=1
    )

    async def load_schema(_workspace: Any) -> Any:
        return _loaded_schema(reminder_days=[1])

    monkeypatch.setattr(fd, "load_schema", load_schema)
    try:
        assert await dispatch_due_feature_reminders(server) == 1
        notifications = repo.list_notifications(agent_id="feat-x", user_id=1)
        assert len(notifications) == 1
        assert notifications[0].cycle_key == f"{due_day.isoformat()}:due"
    finally:
        db.close()


async def test_dispatch_notifies_updated_threshold_then_due(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from octop.infra.agents import feature_data as fd
    from octop.infra.agents import feature_data_reminders as reminders

    due_day = datetime.now(UTC).date() + timedelta(days=5)
    old_schedule = int(
        datetime.combine(due_day - timedelta(days=30), time.min, tzinfo=UTC).timestamp()
    )
    server, db, repo, _reminder = _reminder_environment(
        tmp_path, due_day=due_day.isoformat(), next_notify_at=old_schedule
    )

    class FrozenDateTime(datetime):
        current = datetime.combine(due_day - timedelta(days=5), time(hour=12), tzinfo=UTC)

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    async def load_schema(_workspace: Any) -> Any:
        return _loaded_schema(reminder_days=[7])

    monkeypatch.setattr(fd, "load_schema", load_schema)
    monkeypatch.setattr(reminders, "datetime", FrozenDateTime)
    try:
        assert await dispatch_due_feature_reminders(server) == 1
        first = repo.list_notifications(agent_id="feat-x", user_id=1)
        assert [item.cycle_key for item in first] == [f"{due_day.isoformat()}:7"]

        FrozenDateTime.current = datetime.combine(due_day, time(hour=12), tzinfo=UTC)
        assert await dispatch_due_feature_reminders(server) == 1
        notifications = repo.list_notifications(agent_id="feat-x", user_id=1)
        assert {item.cycle_key for item in notifications} == {
            f"{due_day.isoformat()}:7",
            f"{due_day.isoformat()}:due",
        }
    finally:
        db.close()


def _reminder_environment(
    tmp_path: Path, *, due_day: str, next_notify_at: int
) -> tuple[Any, Any, Any, Any]:
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.repos.feature_data import FeatureDataRepo
    from octop.infra.db.services import RepoBundle

    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 1)"
        )
    repos = RepoBundle.from_pool(db)
    repos.agent_repo.create(agent_id="feat-x", user_id=1, name="Feature", kind="feature")
    repo: FeatureDataRepo = repos.feature_data_repo
    record = repo.create_record(
        agent_id="feat-x",
        object_key="supplier",
        title="ACME",
        values={"expires_at": due_day},
        visibility="feature",
        created_by=1,
    )
    reminder = repo.upsert_reminder(
        agent_id="feat-x",
        record_id=record.id,
        object_key="supplier",
        field_key="expires_at",
        due_at=due_day,
        title="ACME expires",
        next_notify_at=next_notify_at,
        created_by=1,
    )
    server = SimpleNamespace(
        services=SimpleNamespace(
            config=SimpleNamespace(default_timezone="UTC"),
            repos=repos,
        ),
        app_runtime=SimpleNamespace(
            agent_registry=SimpleNamespace(workspace_for_agent=lambda _id: object())
        ),
    )
    return server, db, repo, reminder


def _loaded_schema(*, reminder_days: list[int]) -> Any:
    from octop.infra.agents.feature_data import SchemaLoad

    return SchemaLoad(
        schema={
            "objects": [
                {
                    "key": "supplier",
                    "fields": [
                        {
                            "key": "expires_at",
                            "type": "date",
                            "reminder_days": reminder_days,
                        }
                    ],
                }
            ]
        }
    )


async def test_dispatch_notifies_record_creator_and_owner_once() -> None:
    repo = _Repo()
    agent = SimpleNamespace(user_id=1, agent_id="feat-x", kind="feature", enterprise_unit_key=None)
    users = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(id=1, role="member", disabled=0),
            SimpleNamespace(id=2, role="member", disabled=0),
            SimpleNamespace(id=3, role="member", disabled=0),
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
