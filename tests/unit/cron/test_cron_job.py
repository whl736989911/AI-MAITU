"""tests/unit/test_cron_job.py — CronJob bookkeeping around CronDeliveryService."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.cron.job import CronJob
from octop.infra.cron.manager import CronManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.cron import CronJobRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.users.capabilities import UserCapabilityPolicy


@pytest.fixture
def setup(tmp_path: Path):
    db = SqlitePool(tmp_path / "x.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    UserRepo(db).create(username="u", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="a1", user_id=1, name="bot")
    cron_repo = CronJobRepo(db)
    session_key = ThreadRegistry.make_key(
        agent_id="a1",
        channel_type="cron",
        channel_subject_id="j1",
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
    )
    SessionRepo(db).upsert(
        session_key=session_key,
        agent_id="a1",
        user_id=1,
        channel_type="cron",
        chat_type=ThreadRegistry.CHAT_TYPE_DM,
        thread_id="thr_seed",
        channel_subject_id="j1",
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
        channel_metadata={"channel_type": "cron", "user_id": 1},
    )
    cid = cron_repo.create(
        cron_id="j1",
        agent_id="a1",
        user_id=1,
        trigger="interval:60",
        prompt="say hi",
        session_key=session_key,
    )
    audit = AuditRepo(db)
    return cron_repo, audit, cid, session_key, repos


def _job(
    *,
    cron_repo: CronJobRepo,
    audit: AuditRepo,
    cid: str,
    session_key: str,
    delivery_service: MagicMock,
    user_policy: UserCapabilityPolicy,
    fresh_thread: bool = False,
    task_type: str = "agent",
    mcp_servers: list[str] | None = None,
) -> CronJob:
    return CronJob(
        cron_id=cid,
        name="say hi",
        agent_id="a1",
        prompt="hi",
        fresh_thread=fresh_thread,
        session_key=session_key,
        model=None,
        task_type=task_type,
        mcp_servers=mcp_servers,
        user_id=1,
        delivery_service=delivery_service,
        user_policy=user_policy,
        cron_repo=cron_repo,
        audit_repo=audit,
        date_trigger=False,
    )


@pytest.mark.asyncio
async def test_run_records_ok(setup) -> None:
    cron_repo, audit, cid, session_key, repos = setup
    delivery = MagicMock()
    delivery.deliver = AsyncMock()

    job = _job(
        cron_repo=cron_repo,
        audit=audit,
        cid=cid,
        session_key=session_key,
        delivery_service=delivery,
        user_policy=UserCapabilityPolicy(repos),
    )
    await job.run()
    row = cron_repo.get(cid)
    assert row.last_status == "ok"
    assert row.last_run_at is not None
    delivery.deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_records_error_on_failure(setup) -> None:
    cron_repo, audit, cid, session_key, repos = setup
    delivery = MagicMock()
    delivery.deliver = AsyncMock(side_effect=RuntimeError("nope"))

    job = _job(
        cron_repo=cron_repo,
        audit=audit,
        cid=cid,
        session_key=session_key,
        delivery_service=delivery,
        user_policy=UserCapabilityPolicy(repos),
    )
    await job.run()
    row = cron_repo.get(cid)
    assert row.last_status == "error"
    assert row.last_error == "nope"
    rows = audit.query(action="cron.run_failed")
    assert rows


@pytest.mark.asyncio
async def test_scheduled_job_rechecks_account_state_before_delivery(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("octop.infra.users.permissions.license_managed_install", lambda: False)
    cron_repo, _audit, cid, _session_key, repos = setup
    output = tmp_path / "delivered.txt"

    async def deliver(command) -> None:
        await asyncio.to_thread(output.write_text, command.prompt, encoding="utf-8")

    manager = CronManager(
        gateway=MagicMock(),
        delivery_service=SimpleNamespace(deliver=deliver),
        repos=repos,
        timezone="UTC",
    )
    job = manager._make_job(cron_repo.get(cid))
    await job.run()
    assert output.read_text(encoding="utf-8") == "say hi"
    output.unlink()
    repos.user_repo.set_disabled(1, True)

    with pytest.raises(OctopError) as error:
        await job.run()

    assert error.value.code == ErrorCode.USER_DISABLED
    assert not output.exists()
