from pathlib import Path

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.cron import CronJobRepo
from octop.infra.db.repos.users import UserRepo


def test_date_job_claim_and_terminal_status_are_persisted_once(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "cron.db")
    run_migrations(db)
    UserRepo(db).create(username="u", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="agent-one", user_id=1, name="One Shot")
    repo = CronJobRepo(db)
    repo.create(
        cron_id="cron-one-shot",
        agent_id="agent-one",
        user_id=1,
        trigger="date:2030-01-02T03:04:05",
        prompt="run once",
        session_key="cron:agent-one:cron-one-shot",
    )

    assert repo.claim_date_run("cron-one-shot", ts=1_800_000_000)
    assert not repo.claim_date_run("cron-one-shot", ts=1_800_000_001)
    assert repo.get("cron-one-shot").last_status == "running"

    repo.set_run_status(
        "cron-one-shot",
        ts=1_800_000_000,
        status="error",
        error="delivery failed",
    )
    repo.update("cron-one-shot", enabled=False)

    row = repo.get("cron-one-shot")
    assert row is not None
    assert not row.enabled
    assert row.last_run_at == 1_800_000_000
    assert row.last_status == "error"
    assert row.last_error == "delivery failed"
    assert not repo.claim_date_run("cron-one-shot", ts=1_800_000_002)
