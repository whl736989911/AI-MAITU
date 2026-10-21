from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.agents.manager import AgentManager
from octop.infra.agents.plugins.manager import PluginManager
from octop.infra.cron.job import CronJob
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import service as licensing_service
from tests.support.licensing import provision_license


@pytest.fixture
def locked_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service = provision_license(tmp_path / "home", status="revoked")
    monkeypatch.setattr(licensing_service, "_CURRENT", service)
    return service


@pytest.mark.asyncio
async def test_cron_execution_is_denied_when_instance_license_is_revoked(
    locked_instance,
) -> None:
    delivery = MagicMock()
    delivery.deliver = AsyncMock()
    cron_repo = MagicMock()
    audit_repo = MagicMock()
    job = CronJob(
        cron_id="cron-1",
        name="daily task",
        agent_id="agent-1",
        prompt="perform task",
        fresh_thread=False,
        session_key="session-1",
        model=None,
        task_type="agent",
        mcp_servers=[],
        user_id=1,
        delivery_service=delivery,
        cron_repo=cron_repo,
        audit_repo=audit_repo,
        date_trigger=False,
    )

    with pytest.raises(OctopError) as exc_info:
        await job.run()

    assert exc_info.value.code is ErrorCode.INSTANCE_LICENSE_LOCKED

    delivery.deliver.assert_not_awaited()
    cron_repo.set_run_status.assert_not_called()
    audit_repo.write.assert_not_called()


def test_locked_instance_cannot_load_untrusted_plugin_code(tmp_path: Path, locked_instance) -> None:
    plugin_dir = tmp_path / "plugins" / "untrusted"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: untrusted\nname: untrusted\nversion: '1'\n", encoding="utf-8"
    )
    (plugin_dir / "main.py").write_text(
        "raise RuntimeError('plugin code executed')\n", encoding="utf-8"
    )
    manager = PluginManager(
        plugins_dir=tmp_path / "plugins",
        config_path=tmp_path / "config.json",
    )

    with pytest.raises(OctopError) as exc_info:
        manager.load_installed()

    assert exc_info.value.code is ErrorCode.INSTANCE_LICENSE_LOCKED


def test_active_instance_still_rejects_untrusted_plugin_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = provision_license(tmp_path / "home")
    monkeypatch.setattr(licensing_service, "_CURRENT", service)
    plugin_dir = tmp_path / "plugins" / "untrusted"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: untrusted\\nname: untrusted\\nversion: '1'\\n", encoding="utf-8"
    )
    (plugin_dir / "main.py").write_text(
        "raise RuntimeError('plugin code executed')\\n", encoding="utf-8"
    )
    manager = PluginManager(
        plugins_dir=tmp_path / "plugins",
        config_path=tmp_path / "config.json",
    )

    assert manager.load_installed() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["call", "stream", "resume_hitl"])
async def test_internal_agent_invocation_is_denied_before_harness_call(
    locked_instance, entry: str
) -> None:
    manager = AgentManager.__new__(AgentManager)
    manager._harness_manager = MagicMock()

    with pytest.raises(OctopError) as exc_info:
        if entry == "call":
            await manager.call("agent-1", {"thread_id": "thread-1"})
        else:
            invocation = (
                manager.stream("agent-1", {"thread_id": "thread-1"})
                if entry == "stream"
                else manager.resume_hitl("agent-1", "thread-1", [])
            )
            await anext(invocation)

    assert exc_info.value.code is ErrorCode.INSTANCE_LICENSE_LOCKED
    getattr(manager._harness_manager, entry).assert_not_called()


@pytest.mark.asyncio
async def test_tls_maintenance_remains_scheduled_while_business_is_locked(
    locked_instance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from octop.infra.setup.tls import renewal

    paths = SimpleNamespace(config=Path("config.json"))
    monkeypatch.setattr(renewal, "_scheduler", None)
    monkeypatch.setattr(renewal, "load_config", lambda _path: object())
    monkeypatch.setattr(renewal, "is_renewal_mode", lambda _config: True)

    try:
        renewal.install_auto_renewal_job(paths=paths)

        assert renewal._scheduler is not None
        assert renewal._scheduler.running
        assert renewal._scheduler.get_job("octop_tls_auto_renew") is not None
    finally:
        renewal.shutdown_auto_renewal_scheduler()


def test_managed_shell_policy_protects_license_paths_and_preserves_workspace_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.backend import managed_shell

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    license_dir = tmp_path / "license"
    license_dir.mkdir()
    secret = license_dir / "state.json"
    secret.write_text("private license data", encoding="utf-8")
    trust_dir = tmp_path / "trust"
    trust_dir.mkdir()
    monkeypatch.setenv(
        "MAITU_LICENSE_TRUSTED_KEYS_FILE",
        str(trust_dir / "license-trusted-keys.json"),
    )
    monkeypatch.setenv("MAITU_LICENSE_STATE_DIR", str(license_dir))
    monkeypatch.setattr(managed_shell.sys, "platform", "linux")
    monkeypatch.setattr(managed_shell.shutil, "which", lambda *_args, **_kwargs: "/usr/bin/bwrap")

    spec = {"type": "local_shell", "root_dir": str(tmp_path), "virtual_mode": True}
    with pytest.raises(OctopError) as exc_info:
        managed_shell.managed_shell_spec(spec, workspace_dir=workspace, system_files_path=".octop")
    assert exc_info.value.code is ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED

    safe_backend = managed_shell.managed_shell_spec(
        {"type": "local_shell", "root_dir": str(workspace), "virtual_mode": True},
        workspace_dir=workspace,
        system_files_path=".octop",
    )
    write = safe_backend.write("/allowed.txt", "workspace data")
    read = safe_backend.read("/allowed.txt")
    with pytest.raises(ValueError):
        safe_backend.read("/../../license/state.json")
    with pytest.raises(ValueError):
        safe_backend.write("/../../license/overwritten.txt", "no")

    assert write.error is None
    assert read.error is None
    assert "workspace data" in read.file_data["content"]
    assert secret.read_text(encoding="utf-8") == "private license data"

    link = workspace / "license-link"
    try:
        link.symlink_to(license_dir, target_is_directory=True)
    except OSError:
        return
    symlink_read = safe_backend.read("/license-link/state.json")
    assert symlink_read.error is not None or symlink_read.file_data is None
