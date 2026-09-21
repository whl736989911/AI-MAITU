"""The connector display flag must come from ``resource_acl``.

A share applied through ``SharingService`` only ever lands in the ACL (schema
v21 removed the legacy column it used to mirror), so the display flag has to be
read from the ACL: the old column read told the dashboard that a published
connector was private.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from octop.infra.connectors.service import ConnectorService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import AclEntry
from octop.infra.sharing.service import SharingService


def _env(tmp_path: Path) -> SimpleNamespace:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = ConnectorRepo(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    instance_id = repo.create(
        instance_id="CONN1",
        user_id=owner,
        kind="qq-mail",
        display_name="Work mail",
        mcp_server_name="qq-mail",
    )
    return SimpleNamespace(
        acl=ResourceAclRepo(pool),
        repo=repo,
        sharing=SharingService(pool),
        owner=owner,
        peer=users.create(username="peer", password_hash="h", role="user"),
        admin=users.create(username="admin", password_hash="h", role="admin"),
        instance_id=instance_id,
        service=ConnectorService(
            repo=repo,
            secret_repo=SecretRepo(pool),
            settings_repo=SettingsRepo(pool),
            config=SimpleNamespace(),
        ),
    )


def _listed(env: SimpleNamespace, user_id: int) -> list[dict[str, object]]:
    return env.service.list_instances_for_api(user_id)


def test_shared_flag_reflects_the_acl(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert _listed(env, env.owner)[0]["shared"] is False

    pending = env.sharing.apply_change(
        env.owner,
        "connector",
        env.instance_id,
        AclEntry(
            resource_type="connector",
            resource_id=env.instance_id,
            owner_user_id=env.owner,
            visibility="public",
            unit_key=None,
            version=0,
        ),
    )
    env.sharing.approve(pending.change_id, env.admin)

    # Sharing only ever writes the ACL; the display follows it.
    entry = env.acl.get("connector", env.instance_id)
    assert entry is not None and entry.visibility == "public"

    assert _listed(env, env.owner)[0]["shared"] is True
    assert [row["instance_id"] for row in _listed(env, env.peer)] == [env.instance_id]
    assert _listed(env, env.peer)[0]["shared"] is True
