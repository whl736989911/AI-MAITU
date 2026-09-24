"""tests/integration/test_users_api.py"""

from __future__ import annotations

from tests.support.auth import ensure_test_org_unit


async def test_create_list_get_delete(env):
    c, srv, auth = env
    await ensure_test_org_unit(c, auth)
    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "alice",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
        },
    )
    assert r.status_code == 201
    uid = r.json()["id"]

    r = await c.get("/api/users", headers=auth)
    usernames = [u["username"] for u in r.json()]
    assert "alice" in usernames

    r = await c.get(f"/api/users/{uid}", headers=auth)
    assert r.json()["username"] == "alice"
    assert r.json()["email"] is None
    assert r.json()["has_password"] is True
    assert r.json()["sso_linked"] is False
    assert r.json()["login_locked"] is False
    assert r.json()["login_retry_after_seconds"] == 0
    assert isinstance(r.json()["created_at"], int)
    assert r.json()["created_at"] > 0

    r = await c.delete(f"/api/users/{uid}", headers=auth)
    assert r.status_code == 204
    r = await c.get(f"/api/users/{uid}", headers=auth)
    assert r.status_code == 404


async def test_non_admin_gets_403(env):
    c, srv, _ = env
    admin_auth = env[2]
    await ensure_test_org_unit(c, admin_auth)
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "bob", "password": "TestPass12", "role": "user", "org_unit": "test-unit"},
    )
    tok = (
        await c.post("/api/auth/login", json={"username": "bob", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {tok}"}
    r = await c.get("/api/users", headers=user_auth)
    assert r.status_code == 403


async def test_admin_cannot_delete_self(env):
    c, srv, auth = env
    me = (await c.get("/api/auth/me", headers=auth)).json()
    r = await c.delete(f"/api/users/{me['id']}", headers=auth)
    assert r.status_code == 403


async def test_admin_cannot_demote_self(env):
    c, srv, auth = env
    me = (await c.get("/api/auth/me", headers=auth)).json()
    r = await c.patch(f"/api/users/{me['id']}", headers=auth, json={"role": "user"})
    assert r.status_code == 403


async def test_admin_can_enable_disabled_user(env):
    c, srv, auth = env
    await ensure_test_org_unit(c, auth)
    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "disabled_user",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
        },
    )
    assert r.status_code == 201
    uid = r.json()["id"]

    r = await c.patch(f"/api/users/{uid}", headers=auth, json={"disabled": True})
    assert r.status_code == 200
    assert r.json()["disabled"] is True

    r = await c.patch(f"/api/users/{uid}", headers=auth, json={"disabled": False})
    assert r.status_code == 200
    assert r.json()["disabled"] is False


async def test_admin_can_unlock_login(env):
    c, srv, auth = env
    await ensure_test_org_unit(c, auth)
    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "lock_user",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
        },
    )
    uid = r.json()["id"]
    max_attempts = srv.services.config.login_max_attempts
    for _ in range(max_attempts):
        await c.post("/api/auth/login", json={"username": "lock_user", "password": "bad"})
    r = await c.get(f"/api/users/{uid}", headers=auth)
    assert r.json()["login_locked"] is True

    r = await c.post(f"/api/users/{uid}/unlock-login", headers=auth)
    assert r.status_code == 204
    r = await c.get(f"/api/users/{uid}", headers=auth)
    assert r.json()["login_locked"] is False

    r = await c.post("/api/auth/login", json={"username": "lock_user", "password": "TestPass12"})
    assert r.status_code == 200


async def test_admin_can_create_user_with_resource_policy(env, tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_IN_CONTAINER", "0")
    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    jail = tmp_path / "jail"
    jail.mkdir()

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "policy_create",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
            "workspace_root_dir": jail.as_posix(),
            "token_quota": 2000,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workspace_root_dir"] == jail.resolve().as_posix()
    assert body["token_quota"] == 2000

    listed = (await c.get("/api/users", headers=auth)).json()
    row = next(u for u in listed if u["username"] == "policy_create")
    assert row["workspace_root_dir"] == jail.resolve().as_posix()
    assert row["token_quota"] == 2000


async def test_create_user_rejects_workspace_root_in_container(env, tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_IN_CONTAINER", "1")
    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    jail = tmp_path / "jail"
    jail.mkdir()

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "policy_container",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
            "workspace_root_dir": jail.as_posix(),
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "WORKSPACE_ROOT_CONTAINER_UNSUPPORTED"
    listed = (await c.get("/api/users", headers=auth)).json()
    assert "policy_container" not in [u["username"] for u in listed]


async def test_create_user_rejects_invalid_workspace_root(env, tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_IN_CONTAINER", "0")
    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    missing = tmp_path / "no-such-dir"

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "bad_root",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
            "workspace_root_dir": missing.as_posix(),
        },
    )
    assert r.status_code == 400, r.text
    listed = (await c.get("/api/users", headers=auth)).json()
    assert "bad_root" not in [u["username"] for u in listed]


async def test_admin_can_set_resource_policy(env, tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_IN_CONTAINER", "0")
    from tests.support.auth import TEST_PASSWORD, create_user

    c, _srv, auth = env
    jail = tmp_path / "jail"
    nested = jail / "ok"
    outside = tmp_path / "outside"
    jail.mkdir()
    nested.mkdir()
    outside.mkdir()

    user_auth = await create_user(c, auth, username="policy_user", password=TEST_PASSWORD)
    listed = (await c.get("/api/users", headers=auth)).json()
    row = next(u for u in listed if u["username"] == "policy_user")
    uid = row["id"]
    assert row["workspace_root_dir"] is None
    assert row["token_quota"] is None

    r = await c.patch(
        f"/api/users/{uid}",
        headers=auth,
        json={"workspace_root_dir": jail.as_posix(), "token_quota": 1000},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_root_dir"] == jail.resolve().as_posix()
    assert body["token_quota"] == 1000

    ok = await c.post(
        "/api/agents",
        headers=user_auth,
        json={
            "name": "inside-root",
            "config": {
                "backend": {
                    "type": "local_shell",
                    "virtual_mode": True,
                    "root_dir": nested.as_posix(),
                }
            },
        },
    )
    assert ok.status_code == 201, ok.text

    denied = await c.post(
        "/api/agents",
        headers=user_auth,
        json={
            "name": "outside-root",
            "config": {
                "backend": {
                    "type": "local_shell",
                    "virtual_mode": True,
                    "root_dir": outside.as_posix(),
                }
            },
        },
    )
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "WORKSPACE_ROOT_RESTRICTED"

    r = await c.patch(
        f"/api/users/{uid}",
        headers=auth,
        json={"workspace_root_dir": None, "token_quota": None},
    )
    assert r.status_code == 200
    assert r.json()["workspace_root_dir"] is None
    assert r.json()["token_quota"] is None


async def test_user_org_unit_roundtrip(env):
    """Save -> store -> read back -> echo: the editor's org_unit must survive."""
    c, _srv, auth = env
    created = await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "sales", "label_zh": "销售部", "label_en": "Sales"},
    )
    assert created.status_code == 201, created.text
    await ensure_test_org_unit(c, auth)
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": "alice",
                "password": "TestPass12",
                "role": "user",
                "org_unit": "test-unit",
            },
        )
    ).json()["id"]

    r = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "sales"})
    assert r.status_code == 200, r.text
    assert r.json()["org_unit"] == "sales"

    listed = (await c.get("/api/users", headers=auth)).json()
    assert next(u for u in listed if u["id"] == uid)["org_unit"] == "sales"
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["org_unit"] == "sales"

    # An omitted field keeps the binding; employees cannot be unbound.
    kept = await c.patch(f"/api/users/{uid}", headers=auth, json={"display_name": "Alice"})
    assert kept.json()["org_unit"] == "sales"
    cleared = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": None})
    assert cleared.status_code == 403
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["org_unit"] == "sales"


async def test_create_user_with_org_unit_and_unknown_unit_refused(env):
    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "ops", "label_zh": "运维", "label_en": "Operations"},
    )

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "bob",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "ops",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["org_unit"] == "ops"

    # An unknown unit is refused instead of stored as a binding that resolves to
    # no grants at all.
    ghost = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "carol",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "ghost",
        },
    )
    assert ghost.status_code == 404, ghost.text
    assert "carol" not in [u["username"] for u in (await c.get("/api/users", headers=auth)).json()]

    uid = next(
        u for u in (await c.get("/api/users", headers=auth)).json() if u["username"] == "bob"
    )["id"]
    moved = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "ghost"})
    assert moved.status_code == 404, moved.text
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["org_unit"] == "ops"


async def test_user_write_rejects_unknown_body_field(env):
    """A field the API does not write must fail, not disappear."""
    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": "dave",
                "password": "TestPass12",
                "role": "user",
                "org_unit": "test-unit",
            },
        )
    ).json()["id"]

    r = await c.patch(f"/api/users/{uid}", headers=auth, json={"not_a_field": 1})
    assert r.status_code == 422, r.text

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "erin",
            "password": "TestPass12",
            "org_unit": "test-unit",
            "role": "user",
            "not_a_field": 1,
        },
    )
    assert r.status_code == 422, r.text


async def test_department_grant_reaches_members(env):
    """The 'unit' leg: a department grant reaches every member of the unit."""
    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "sales", "label_zh": "销售部", "label_en": "Sales"},
    )
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": "alice",
                "password": "TestPass12",
                "role": "user",
                "org_unit": "sales",
            },
        )
    ).json()["id"]
    await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "sales"})

    granted = await c.put(
        "/api/org-units/sales/permissions",
        headers=auth,
        json={"permissions": ["users"]},
    )
    assert granted.status_code == 200, granted.text

    tok = (
        await c.post("/api/auth/login", json={"username": "alice", "password": "TestPass12"})
    ).json()["access_token"]
    alice = {"Authorization": f"Bearer {tok}"}
    me = await c.get("/api/auth/me", headers=alice)
    assert "users" in me.json()["permissions"]
    # The module really opens up for the member, not just on the profile payload.
    assert (await c.get("/api/users", headers=alice)).status_code == 200

    # Revoking the department grant removes it from every bound member.
    revoked = await c.put(
        "/api/org-units/sales/permissions", headers=auth, json={"permissions": []}
    )
    assert revoked.status_code == 200, revoked.text
    assert "users" not in (await c.get("/api/auth/me", headers=alice)).json()["permissions"]
    assert (await c.get("/api/users", headers=alice)).status_code == 403


async def test_users_key_alone_cannot_move_the_authorization_boundary(env):
    """``users`` opens the page; it does not hand over roles or accounts.

    The account below is an *employee*: design §2.1 says 企业员工 不能创建账号或授权
    and 员工's 数据管理范围 is 仅自身可用资源, so the module key reaches no other
    account at all — the capability a delegated operator needs is the scoped
    administrator role, covered by the tests below this one.
    """
    from tests.support.auth import TEST_PASSWORD, create_user

    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "ops", "label_zh": "运维", "label_en": "Operations"},
    )
    support = await create_user(c, auth, username="helpdesk", permissions=["users"])
    await create_user(c, auth, username="victim", org_unit="ops")
    rows = (await c.get("/api/users", headers=auth)).json()
    victim_id = next(u["id"] for u in rows if u["username"] == "victim")
    me = (await c.get("/api/auth/me", headers=support)).json()

    # The module key still opens what it names: the account list, which for an
    # employee is its own account, and its own profile.
    listed = await c.get("/api/users", headers=support)
    assert listed.status_code == 200
    assert [u["username"] for u in listed.json()] == ["helpdesk"]
    assert (await c.get(f"/api/users/{me['id']}", headers=support)).status_code == 200
    renamed_self = await c.patch(
        f"/api/users/{me['id']}", headers=support, json={"display_name": "Desk"}
    )
    assert renamed_self.status_code == 200, renamed_self.text

    # Somebody else's account is out of reach in every direction — including the
    # profile edit the module key used to carry.
    assert (
        await c.patch(f"/api/users/{victim_id}", headers=support, json={"display_name": "x"})
    ).status_code == 403
    assert (await c.get(f"/api/users/{victim_id}", headers=support)).status_code == 403

    # Everything that moves the authorization boundary is out of reach too —
    # refused by the scope (an employee administers no account but its own) and
    # by the role level (a peer and a senior are nobody's to edit).
    assert (
        await c.patch(f"/api/users/{victim_id}", headers=support, json={"role": "admin"})
    ).status_code == 403
    assert (
        await c.patch(f"/api/users/{me['id']}", headers=support, json={"role": "admin"})
    ).status_code == 403
    assert (
        await c.post(
            f"/api/users/{victim_id}/reset-password",
            headers=support,
            json={"new_password": "Taken12345"},
        )
    ).status_code == 403
    assert (
        await c.patch(f"/api/users/{victim_id}", headers=support, json={"disabled": True})
    ).status_code == 403
    assert (await c.delete(f"/api/users/{victim_id}", headers=support)).status_code == 403
    assert (
        await c.patch(f"/api/users/{victim_id}", headers=support, json={"org_unit": "ops"})
    ).status_code == 403
    # A deny subtracts over the department grant, and can take the ``users`` key
    # itself away from a colleague — same tier as the binding above.
    assert (
        await c.patch(
            f"/api/users/{victim_id}", headers=support, json={"denied_permissions": ["browser"]}
        )
    ).status_code == 403
    assert (
        await c.patch(
            f"/api/users/{me['id']}", headers=support, json={"denied_permissions": ["browser"]}
        )
    ).status_code == 403
    assert (
        await c.patch(f"/api/users/{victim_id}", headers=support, json={"denied_permissions": None})
    ).status_code == 403
    # A department carries module grants, so binding *yourself* is escalation too.
    assert (
        await c.patch(f"/api/users/{me['id']}", headers=support, json={"org_unit": "ops"})
    ).status_code == 403
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={
                "username": "planted",
                "password": "TestPass12",
                "role": "user",
                "org_unit": "ops",
            },
        )
    ).status_code == 403
    # The same grant spelled differently: a role named at creation. Refusing the
    # edit above is worthless if ``users`` can mint the admin account instead —
    # and this victimless route comes with a password the caller chose.
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={
                "username": "planted_admin",
                "password": TEST_PASSWORD,
                "role": "admin",
            },
        )
    ).status_code == 403
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={
                "username": "planted_unit",
                "password": TEST_PASSWORD,
                "role": "unit_admin",
            },
        )
    ).status_code == 403
    # A deny planted at creation is the same write as the one refused above.
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={
                "username": "planted_denied",
                "password": TEST_PASSWORD,
                "role": "user",
                "denied_permissions": ["users"],
            },
        )
    ).status_code == 403
    # Creating an account is refused for an employee in every shape: it
    # administers no department, so there is nowhere it could put one (design
    # §2.1: 企业员工 不能创建账号或授权). An empty deny list changes nothing about
    # that — the department check comes first.
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={
                "username": "planted_empty",
                "password": TEST_PASSWORD,
                "role": "user",
                "denied_permissions": [],
            },
        )
    ).status_code == 403
    assert (
        await c.post(
            "/api/users",
            headers=support,
            json={"username": "plain", "password": TEST_PASSWORD, "role": "user"},
        )
    ).status_code == 403

    # Nothing leaked through the refusals.
    victim = (await c.get(f"/api/users/{victim_id}", headers=auth)).json()
    assert victim["role"] == "user"
    assert victim["disabled"] is False
    assert victim["org_unit"] == "ops"
    usernames = [u["username"] for u in (await c.get("/api/users", headers=auth)).json()]
    assert "planted" not in usernames
    assert "planted_admin" not in usernames
    assert "planted_unit" not in usernames
    assert "planted_denied" not in usernames
    assert (
        await c.post("/api/auth/login", json={"username": "victim", "password": TEST_PASSWORD})
    ).status_code == 200


async def test_admin_can_still_administer_accounts(env):
    """The same operations keep working for an admin — including on others."""
    from tests.support.auth import TEST_PASSWORD, create_user

    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "ops", "label_zh": "运维", "label_en": "Operations"},
    )
    await create_user(c, auth, username="target")
    uid = next(
        u["id"]
        for u in (await c.get("/api/users", headers=auth)).json()
        if u["username"] == "target"
    )

    promoted = await c.patch(f"/api/users/{uid}", headers=auth, json={"role": "unit_admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "unit_admin"

    reset = await c.post(
        f"/api/users/{uid}/reset-password", headers=auth, json={"new_password": "Rotated12345"}
    )
    assert reset.status_code == 204, reset.text
    assert (
        await c.post("/api/auth/login", json={"username": "target", "password": "Rotated12345"})
    ).status_code == 200

    assert (await c.patch(f"/api/users/{uid}", headers=auth, json={"disabled": True})).json()[
        "disabled"
    ] is True
    assert (await c.patch(f"/api/users/{uid}", headers=auth, json={"disabled": False})).json()[
        "disabled"
    ] is False

    bound = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "ops"})
    assert bound.status_code == 200 and bound.json()["org_unit"] == "ops"

    # Denies are the admin's write too, at creation and by later edit.
    assert (
        await c.patch(f"/api/users/{uid}", headers=auth, json={"denied_permissions": ["browser"]})
    ).json()["denied_permissions"] == ["browser"]

    resident = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "resident",
            "password": TEST_PASSWORD,
            "role": "user",
            "org_unit": "ops",
        },
    )
    assert resident.status_code == 201 and resident.json()["org_unit"] == "ops"

    # A role named at creation is the admin's to grant too: the gate above
    # refuses the ``users`` key, it does not refuse everybody.
    for role in ("admin", "unit_admin"):
        made = await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": f"made_{role}",
                "password": TEST_PASSWORD,
                "role": role,
                "org_unit": "ops" if role == "unit_admin" else None,
            },
        )
        assert made.status_code == 201, made.text
        assert made.json()["role"] == role

    me = (await c.get("/api/auth/me", headers=auth)).json()
    assert (await c.delete(f"/api/users/{me['id']}", headers=auth)).status_code == 403
    assert (
        await c.patch(f"/api/users/{me['id']}", headers=auth, json={"role": "user"})
    ).status_code == 403

    assert (await c.delete(f"/api/users/{uid}", headers=auth)).status_code == 204
    assert (await c.get(f"/api/users/{uid}", headers=auth)).status_code == 404


async def test_denied_permissions_roundtrip_and_tristate(env):
    """Save → store → read back → echo, with omitted / null / list as three states."""
    from tests.support.auth import TEST_PASSWORD

    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    created = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "alice",
            "password": TEST_PASSWORD,
            "role": "user",
            "org_unit": "test-unit",
            "permissions": ["browser"],
            "denied_permissions": ["browser"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["denied_permissions"] == ["browser"]
    uid = created.json()["id"]

    listed = (await c.get("/api/users", headers=auth)).json()
    assert next(u for u in listed if u["id"] == uid)["denied_permissions"] == ["browser"]
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["denied_permissions"] == [
        "browser"
    ]

    # Omitted field: kept (an unrelated edit does not hand the key back).
    kept = await c.patch(f"/api/users/{uid}", headers=auth, json={"display_name": "Alice"})
    assert kept.json()["display_name"] == "Alice"
    assert kept.json()["denied_permissions"] == ["browser"]

    # Explicit ``null`` and an empty list both clear.
    assert (
        await c.patch(f"/api/users/{uid}", headers=auth, json={"denied_permissions": None})
    ).json()["denied_permissions"] == []
    set_again = await c.patch(
        f"/api/users/{uid}", headers=auth, json={"denied_permissions": ["channels"]}
    )
    assert set_again.json()["denied_permissions"] == ["channels"]
    assert (
        await c.patch(f"/api/users/{uid}", headers=auth, json={"denied_permissions": []})
    ).json()["denied_permissions"] == []


async def test_deny_outranks_department_and_grant(env):
    """The third leg: ``role ∪ department ∪ grant − deny``, with deny last."""
    from tests.support.auth import create_user, resolve_user_id

    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "sales", "label_zh": "销售部", "label_en": "Sales"},
    )
    alice = await create_user(c, auth, username="alice", permissions=["browser", "channels"])
    uid = await resolve_user_id(c, auth, "alice")

    # Department leg: the unit grants ``users``; alice carries two direct grants.
    assert (
        await c.put(
            "/api/org-units/sales/permissions", headers=auth, json={"permissions": ["users"]}
        )
    ).status_code == 200
    assert (
        await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "sales"})
    ).status_code == 200
    before = set((await c.get("/api/auth/me", headers=alice)).json()["permissions"])
    assert {"browser", "channels", "users"} <= before
    assert (await c.get("/api/users", headers=alice)).status_code == 200

    denied = await c.patch(
        f"/api/users/{uid}", headers=auth, json={"denied_permissions": ["users", "browser"]}
    )
    assert denied.status_code == 200, denied.text

    after = set((await c.get("/api/auth/me", headers=alice)).json()["permissions"])
    assert "users" not in after  # outranks the department grant
    assert "browser" not in after  # outranks the direct grant
    assert "channels" in after  # a key the deny does not name is untouched
    # The module really closes, not just the profile payload.
    assert (await c.get("/api/users", headers=alice)).status_code == 403

    # The deny is what holds the keys down, not a stripped grant: clearing it
    # hands both back, department grant included.
    await c.patch(f"/api/users/{uid}", headers=auth, json={"denied_permissions": None})
    restored = set((await c.get("/api/auth/me", headers=alice)).json()["permissions"])
    assert {"browser", "channels", "users"} <= restored
    assert (await c.get("/api/users", headers=alice)).status_code == 200


async def test_deny_does_not_hold_down_an_admin(env):
    """``admin`` bypasses everything — including the deny, which cannot lock it out."""
    c, _srv, auth = env
    admin_id = (await c.get("/api/auth/me", headers=auth)).json()["id"]

    r = await c.patch(
        f"/api/users/{admin_id}", headers=auth, json={"denied_permissions": ["users"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["denied_permissions"] == ["users"]

    me = await c.get("/api/auth/me", headers=auth)
    assert "users" in me.json()["permissions"]
    assert (await c.get("/api/users", headers=auth)).status_code == 200


async def test_deny_rejects_unknown_key_and_stores_nothing(env):
    from tests.support.auth import TEST_PASSWORD

    c, _srv, auth = env
    await ensure_test_org_unit(c, auth)
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": "alice",
                "password": TEST_PASSWORD,
                "role": "user",
                "org_unit": "test-unit",
                "denied_permissions": ["browser"],
            },
        )
    ).json()["id"]

    bad = await c.patch(
        f"/api/users/{uid}", headers=auth, json={"denied_permissions": ["not_a_key"]}
    )
    assert bad.status_code == 400, bad.text
    # The refused write left the stored denies exactly as they were.
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["denied_permissions"] == [
        "browser"
    ]

    refused = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "bob",
            "password": TEST_PASSWORD,
            "role": "user",
            "org_unit": "test-unit",
            "denied_permissions": ["not_a_key"],
        },
    )
    assert refused.status_code == 400, refused.text
    usernames = [u["username"] for u in (await c.get("/api/users", headers=auth)).json()]
    assert "bob" not in usernames


async def test_department_granted_key_is_assignable(env):
    """A key the department carries is offered by the picker, so saving it must work.

    Regression: the guard compared the *stored* column, while ``/auth/me`` (and
    therefore the checkbox set) resolves the department grants too — so a key
    the editor displayed as assignable came back 403 on submit.
    """
    from tests.support.auth import TEST_PASSWORD, create_user, resolve_user_id

    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "sales", "label_zh": "销售部", "label_en": "Sales"},
    )
    granted = await c.put(
        "/api/org-units/sales/permissions",
        headers=auth,
        json={"permissions": ["terminal"]},
    )
    assert granted.status_code == 200, granted.text

    lead = await create_user(c, auth, username="lead", role="unit_admin", permissions=["users"])
    lead_id = await resolve_user_id(c, auth, "lead")
    await create_user(c, auth, username="member")
    member_id = await resolve_user_id(c, auth, "member")
    bound = await c.patch(f"/api/users/{lead_id}", headers=auth, json={"org_unit": "sales"})
    assert bound.status_code == 200, bound.text
    # The delegate administers *its* department, so the account it writes has to
    # be in one (design §2.1: 部门管理员 只能创建本管理范围内的员工).
    assert (
        await c.patch(f"/api/users/{member_id}", headers=auth, json={"org_unit": "sales"})
    ).status_code == 200

    # Exactly the set the editor renders as checkboxes: department included.
    assert "terminal" in (await c.get("/api/auth/me", headers=lead)).json()["permissions"]

    patched = await c.patch(
        f"/api/users/{member_id}", headers=lead, json={"permissions": ["terminal"]}
    )
    assert patched.status_code == 200, patched.text
    assert "terminal" in patched.json()["permissions"]

    # The same grant through the other call site (create).
    created = await c.post(
        "/api/users",
        headers=lead,
        json={
            "username": "handover",
            "password": TEST_PASSWORD,
            "role": "user",
            "permissions": ["terminal"],
            "org_unit": "sales",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["permissions"] == ["terminal"]

    # Held keys only, still: the department grant does not widen the guard into
    # "anything goes".
    refused = await c.patch(
        f"/api/users/{member_id}", headers=lead, json={"permissions": ["terminal", "security"]}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["security"]
    assert (await c.get(f"/api/users/{member_id}", headers=auth)).json()["permissions"] == [
        "terminal"
    ]

    # Both grant surfaces resolve the same set for this actor now: the editor's
    # PATCH accepts ``terminal``, and the department's own PUT re-submits it.
    re_granted = await c.put(
        "/api/org-units/sales/permissions", headers=lead, json={"permissions": ["terminal"]}
    )
    assert re_granted.status_code == 200, re_granted.text


async def test_denied_key_cannot_be_passed_on(env):
    """A deny subtracts the key, so its holder must not be able to grant it.

    Regression: the guard read the stored grants, so a key that was granted
    *and* denied — absent from the effective set ``/auth/me`` reports — could
    still be handed to somebody else.
    """
    from tests.support.auth import create_user, resolve_user_id

    c, _srv, auth = env
    # A department administrator is the account that hands keys to somebody
    # else in the four-level model (design §2.1: 部门管理员 只能创建本管理范围内的员
    # 工), so the delegation rule below is exercised through it.
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "ops2", "label_zh": "运维二组", "label_en": "Ops Two"},
    )
    operator = await create_user(
        c, auth, username="operator", role="unit_admin", permissions=["users", "terminal"]
    )
    operator_id = await resolve_user_id(c, auth, "operator")
    await create_user(c, auth, username="member")
    member_id = await resolve_user_id(c, auth, "member")
    assert (
        await c.patch(f"/api/users/{operator_id}", headers=auth, json={"org_unit": "ops2"})
    ).status_code == 200
    assert (
        await c.patch(f"/api/users/{member_id}", headers=auth, json={"org_unit": "ops2"})
    ).status_code == 200
    denied = await c.patch(
        f"/api/users/{operator_id}", headers=auth, json={"denied_permissions": ["terminal"]}
    )
    assert denied.status_code == 200, denied.text

    held = (await c.get("/api/auth/me", headers=operator)).json()["permissions"]
    assert "terminal" not in held
    assert "users" in held
    before = (await c.get(f"/api/users/{member_id}", headers=auth)).json()["permissions"]

    refused = await c.patch(
        f"/api/users/{member_id}", headers=operator, json={"permissions": ["terminal"]}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["terminal"]
    # Refused means nothing was written.
    assert (await c.get(f"/api/users/{member_id}", headers=auth)).json()["permissions"] == before

    # The deny is what refused, not a blanket lock: a key it does hold still goes
    # through.
    allowed = await c.patch(
        f"/api/users/{member_id}", headers=operator, json={"permissions": ["users"]}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["permissions"] == ["users"]


async def _seed_two_enterprises(c, auth) -> None:
    """``acme`` with a department tree, and a second enterprise ``globex``."""
    for key, parent in (
        ("acme", None),
        ("acme-ops", "acme"),
        ("acme-ops-night", "acme-ops"),
        ("acme-sales", "acme"),
        ("globex", None),
        ("globex-ops", "globex"),
    ):
        body: dict[str, object] = {"key": key, "label_zh": key, "label_en": key}
        if parent is not None:
            body["parent_key"] = parent
        assert (await c.post("/api/org-units", headers=auth, json=body)).status_code == 201


async def _bind(c, auth, username: str, org_unit: str) -> int:
    from tests.support.auth import resolve_user_id

    uid = await resolve_user_id(c, auth, username)
    bound = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": org_unit})
    assert bound.status_code == 200, bound.text
    return uid


async def test_enterprise_admin_is_bounded_to_its_enterprise(env):
    """企业管理员: 系统管理员授予的权限, 本企业全部部门和用户 (design §2.1)."""
    from tests.support.auth import TEST_PASSWORD, create_user, resolve_user_id

    c, _srv, auth = env
    await _seed_two_enterprises(c, auth)
    # Granted ``users`` and nothing else: the role carries reach, never keys.
    leader = await create_user(
        c, auth, username="acme-lead", role="enterprise_admin", permissions=["users"]
    )
    await _bind(c, auth, "acme-lead", "acme-ops")
    await create_user(c, auth, username="acme-staff", permissions=[])
    await _bind(c, auth, "acme-staff", "acme-ops-night")
    await create_user(c, auth, username="globex-staff", permissions=[])
    await _bind(c, auth, "globex-staff", "globex-ops")
    globex_staff = await resolve_user_id(c, auth, "globex-staff")

    # No implicit catalog: the enterprise administrator holds what it was granted.
    held = set((await c.get("/api/auth/me", headers=leader)).json()["permissions"])
    assert "users" in held
    assert "providers" not in held and "security" not in held

    # The account list is its own enterprise — 本企业.
    listed = await c.get("/api/users", headers=leader)
    assert listed.status_code == 200
    assert {u["username"] for u in listed.json()} == {"acme-lead", "acme-staff"}

    # Creating inside the enterprise works, including a department administrator.
    made = await c.post(
        "/api/users",
        headers=leader,
        json={
            "username": "acme-ops-admin",
            "password": TEST_PASSWORD,
            "role": "unit_admin",
            "org_unit": "acme-ops",
            "permissions": ["users"],
        },
    )
    assert made.status_code == 201, made.text
    assert made.json()["role"] == "unit_admin"

    # Another enterprise is out of reach, in every direction.
    assert (
        await c.post(
            "/api/users",
            headers=leader,
            json={
                "username": "globex-planted",
                "password": TEST_PASSWORD,
                "role": "user",
                "org_unit": "globex-ops",
            },
        )
    ).status_code == 403
    assert (await c.get(f"/api/users/{globex_staff}", headers=leader)).status_code == 403
    assert (
        await c.patch(f"/api/users/{globex_staff}", headers=leader, json={"disabled": True})
    ).status_code == 403
    assert (
        await c.patch(f"/api/users/{globex_staff}", headers=leader, json={"org_unit": "acme-ops"})
    ).status_code == 403
    assert (await c.delete(f"/api/users/{globex_staff}", headers=leader)).status_code == 403
    # ...and so is the department tree of another enterprise.
    assert (
        await c.put(
            "/api/org-units/globex-ops/permissions",
            headers=leader,
            json={"permissions": []},
        )
    ).status_code == 403
    assert {u["key"] for u in (await c.get("/api/org-units", headers=leader)).json()["units"]} == {
        "acme",
        "acme-ops",
        "acme-ops-night",
        "acme-sales",
    }
    # A department inside its enterprise is its to configure.
    assert (
        await c.put(
            "/api/org-units/acme-ops-night/permissions",
            headers=leader,
            json={"permissions": ["users"]},
        )
    ).status_code == 200

    # It cannot mint a system administrator or another enterprise administrator
    # (design §2.1: 企业管理员和部门管理员不能创建系统管理员或企业管理员).
    for role in ("admin", "enterprise_admin"):
        refused = await c.post(
            "/api/users",
            headers=leader,
            json={
                "username": f"acme-{role}",
                "password": TEST_PASSWORD,
                "role": role,
                "org_unit": "acme-ops",
            },
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["details"]["target_role"] == role

    # Denies stay a system administrator's write.
    staff = await resolve_user_id(c, auth, "acme-staff")
    assert (
        await c.patch(
            f"/api/users/{staff}", headers=leader, json={"denied_permissions": ["browser"]}
        )
    ).status_code == 403


async def test_unit_admin_manages_its_department_and_sub_departments_only(env):
    """部门管理员: 本部门及所有子部门, never a sibling or the level above."""
    from tests.support.auth import create_user

    c, _srv, auth = env
    await _seed_two_enterprises(c, auth)
    await c.put(
        "/api/org-units/acme-ops/permissions", headers=auth, json={"permissions": ["users"]}
    )
    lead = await create_user(c, auth, username="ops-lead", role="unit_admin", permissions=["users"])
    await _bind(c, auth, "ops-lead", "acme-ops")
    await create_user(c, auth, username="night-staff", permissions=[])
    night_staff = await _bind(c, auth, "night-staff", "acme-ops-night")
    await create_user(c, auth, username="sales-staff", permissions=[])
    sales_staff = await _bind(c, auth, "sales-staff", "acme-sales")
    await create_user(c, auth, username="ops-peer", role="unit_admin", permissions=["users"])
    ops_peer = await _bind(c, auth, "ops-peer", "acme-ops-night")

    # The list is the same check as every write below it (design §4.2: 列表查询、
    # 详情查询、修改和删除操作都使用相同的范围校验): the sub-department's employee is
    # in scope, a peer administrator is not — nothing administers a peer, so
    # nothing lists one either.
    listed = await c.get("/api/users", headers=lead)
    assert {u["username"] for u in listed.json()} == {"ops-lead", "night-staff"}

    # A sub-department is inside the scope, and so is administering it.
    assert (
        await c.patch(f"/api/users/{night_staff}", headers=lead, json={"disabled": True})
    ).status_code == 200
    assert (
        await c.patch(f"/api/users/{night_staff}", headers=lead, json={"disabled": False})
    ).status_code == 200
    assert (
        await c.post(
            f"/api/users/{night_staff}/reset-password",
            headers=lead,
            json={"new_password": "Rotated12345"},
        )
    ).status_code == 204
    sub = await c.post(
        "/api/users",
        headers=lead,
        json={
            "username": "night-hire",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "acme-ops-night",
        },
    )
    assert sub.status_code == 201, sub.text

    # A sibling department, the enterprise root and another enterprise are not.
    for blocked in (sales_staff, ops_peer):
        assert (await c.get(f"/api/users/{blocked}", headers=lead)).status_code == 403
        assert (
            await c.patch(f"/api/users/{blocked}", headers=lead, json={"disabled": True})
        ).status_code == 403
    assert (
        await c.post(
            "/api/users",
            headers=lead,
            json={
                "username": "sales-hire",
                "password": "TestPass12",
                "role": "user",
                "org_unit": "acme-sales",
            },
        )
    ).status_code == 403
    # An account with no department is outside every department tree: only a
    # system administrator creates one of those.
    assert (
        await c.post(
            "/api/users",
            headers=lead,
            json={"username": "floating", "password": "TestPass12", "role": "user"},
        )
    ).status_code == 403
    assert (
        await c.put("/api/org-units/acme-sales/permissions", headers=lead, json={"permissions": []})
    ).status_code == 403
    assert (
        await c.put(
            "/api/org-units/acme-ops-night/permissions", headers=lead, json={"permissions": []}
        )
    ).status_code == 200

    # A peer is not below the actor, so it cannot be edited even inside the scope.
    assert (
        await c.patch(f"/api/users/{ops_peer}", headers=lead, json={"role": "user"})
    ).status_code == 403
    # Nor may it hand out a peer role — 只能创建本管理范围内的员工.
    assert (
        await c.post(
            "/api/users",
            headers=lead,
            json={
                "username": "peer-hire",
                "password": "TestPass12",
                "role": "unit_admin",
                "org_unit": "acme-ops",
            },
        )
    ).status_code == 403


async def test_admin_scope_is_unchanged_and_it_creates_unbound_accounts(env):
    """The system administrator's boundary stays what it was: everything."""
    from tests.support.auth import TEST_PASSWORD

    c, _srv, auth = env
    await _seed_two_enterprises(c, auth)
    made = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "floating-admin-made",
            "password": TEST_PASSWORD,
            "role": "enterprise_admin",
        },
    )
    assert made.status_code == 201, made.text
    assert made.json()["org_unit"] is None

    # Every unit, every account, every role — including the new fourth role.
    assert {u["key"] for u in (await c.get("/api/org-units", headers=auth)).json()["units"]} == {
        "acme",
        "acme-ops",
        "acme-ops-night",
        "acme-sales",
        "globex",
        "globex-ops",
    }


async def test_permission_catalog_reports_can_grant_and_dynamic(env):
    """Design §4.1: key, label, dynamic flag, and whether *this* operator may grant it."""
    from tests.support.auth import create_user

    c, _srv, auth = env
    catalog = (await c.get("/api/users/permissions", headers=auth)).json()
    by_key = {item["key"]: item for item in catalog}
    # Every item carries the shape the editor needs, and the keys the catalog
    # gained in this round are all there.
    for key in ("mbti", "experts", "features", "acp", "channel_feishu"):
        assert key in by_key
        assert by_key[key]["label"]
        assert by_key[key]["category"] in {"settings", "control", "admin"}
        assert by_key[key]["can_grant"] is True  # a system administrator grants all
    assert by_key["channel_feishu"]["dynamic"] is True
    assert by_key["channels"]["dynamic"] is False

    # A department administrator sees what it may hand out, and only that: the
    # department's ``users`` grant is inside its held set, ``providers`` is not.
    await c.post(
        "/api/org-units", headers=auth, json={"key": "cat-ops", "label_zh": "甲", "label_en": "A"}
    )
    await c.put("/api/org-units/cat-ops/permissions", headers=auth, json={"permissions": ["users"]})
    lead = await create_user(c, auth, username="cat-lead", role="unit_admin", permissions=["users"])
    uid = next(
        u["id"]
        for u in (await c.get("/api/users", headers=auth)).json()
        if u["username"] == "cat-lead"
    )
    assert (
        await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "cat-ops"})
    ).status_code == 200
    held = {
        item["key"]: item for item in (await c.get("/api/users/permissions", headers=lead)).json()
    }
    assert held["users"]["can_grant"] is True
    assert held["providers"]["can_grant"] is False
    assert held["experts"]["can_grant"] is False
