"""tests/integration/test_users_api.py"""

from __future__ import annotations


async def test_create_list_get_delete(env):
    c, srv, auth = env
    r = await c.post(
        "/api/users",
        headers=auth,
        json={"username": "alice", "password": "TestPass12", "role": "user"},
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
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "bob", "password": "TestPass12", "role": "user"},
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
    r = await c.post(
        "/api/users",
        headers=auth,
        json={"username": "disabled_user", "password": "TestPass12", "role": "user"},
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
    r = await c.post(
        "/api/users",
        headers=auth,
        json={"username": "lock_user", "password": "TestPass12", "role": "user"},
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
    jail = tmp_path / "jail"
    jail.mkdir()

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "policy_create",
            "password": "TestPass12",
            "role": "user",
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
    jail = tmp_path / "jail"
    jail.mkdir()

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "policy_container",
            "password": "TestPass12",
            "role": "user",
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
    missing = tmp_path / "no-such-dir"

    r = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "bad_root",
            "password": "TestPass12",
            "role": "user",
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
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={"username": "alice", "password": "TestPass12", "role": "user"},
        )
    ).json()["id"]

    r = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": "sales"})
    assert r.status_code == 200, r.text
    assert r.json()["org_unit"] == "sales"

    listed = (await c.get("/api/users", headers=auth)).json()
    assert next(u for u in listed if u["id"] == uid)["org_unit"] == "sales"
    assert (await c.get(f"/api/users/{uid}", headers=auth)).json()["org_unit"] == "sales"

    # An omitted field keeps the binding; an explicit null clears it.
    kept = await c.patch(f"/api/users/{uid}", headers=auth, json={"display_name": "Alice"})
    assert kept.json()["org_unit"] == "sales"
    cleared = await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": None})
    assert cleared.json()["org_unit"] is None


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
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={"username": "dave", "password": "TestPass12", "role": "user"},
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
            json={"username": "alice", "password": "TestPass12", "role": "user"},
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

    # Leaving the department drops the grant again.
    await c.patch(f"/api/users/{uid}", headers=auth, json={"org_unit": None})
    assert "users" not in (await c.get("/api/auth/me", headers=alice)).json()["permissions"]
    assert (await c.get("/api/users", headers=alice)).status_code == 403


async def test_users_key_alone_cannot_move_the_authorization_boundary(env):
    """``users`` opens the page; it does not hand over roles or accounts."""
    from tests.support.auth import TEST_PASSWORD, create_user

    c, _srv, auth = env
    await c.post(
        "/api/org-units",
        headers=auth,
        json={"key": "ops", "label_zh": "运维", "label_en": "Operations"},
    )
    support = await create_user(c, auth, username="helpdesk", permissions=["users"])
    await create_user(c, auth, username="victim")
    rows = (await c.get("/api/users", headers=auth)).json()
    victim_id = next(u["id"] for u in rows if u["username"] == "victim")
    me = (await c.get("/api/auth/me", headers=support)).json()

    # The module key still opens what it names: listing and profile edits.
    assert (await c.get("/api/users", headers=support)).status_code == 200
    renamed = await c.patch(
        f"/api/users/{victim_id}", headers=support, json={"display_name": "renamed by support"}
    )
    assert renamed.status_code == 200, renamed.text

    # Everything that moves the authorization boundary is admin-only.
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
    # An empty deny list is the stored default: it moves nothing, so a plain
    # account that happens to carry the field is still creatable.
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
    ).status_code == 201
    # Creating a plain account is what the module key is *for*; it stays open.
    created = await c.post(
        "/api/users",
        headers=support,
        json={"username": "plain", "password": TEST_PASSWORD, "role": "user"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "user"

    # Nothing leaked through the refusals.
    victim = (await c.get(f"/api/users/{victim_id}", headers=auth)).json()
    assert victim["role"] == "user"
    assert victim["disabled"] is False
    assert victim["org_unit"] is None
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
            json={"username": f"made_{role}", "password": TEST_PASSWORD, "role": role},
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
    created = await c.post(
        "/api/users",
        headers=auth,
        json={
            "username": "alice",
            "password": TEST_PASSWORD,
            "role": "user",
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
    uid = (
        await c.post(
            "/api/users",
            headers=auth,
            json={
                "username": "alice",
                "password": TEST_PASSWORD,
                "role": "user",
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
    operator = await create_user(c, auth, username="operator", permissions=["users", "terminal"])
    operator_id = await resolve_user_id(c, auth, "operator")
    await create_user(c, auth, username="member")
    member_id = await resolve_user_id(c, auth, "member")
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
