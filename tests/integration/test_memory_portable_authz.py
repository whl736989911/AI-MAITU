"""Authorization of the portable-memory migration targets.

``POST /api/agents/{agent_id}/memory/portable/adopt`` and the sibling
``doctor`` endpoint used to resolve their target from caller-supplied form
values (``target_host`` / ``target_namespace`` / ``host_spec``). That made a
legacy agent's memory database reachable, and allowed a namespace containing
``..`` to steer the dependency into creating a SQLite file outside the agent
workspace.

The endpoints now resolve the target from the *authorized* ``agent_id`` only.
These tests pin both the pure resolver and the HTTP responses:

* another agent's namespace / a traversal namespace / another host -> 400;
* the agent's own store (explicit, or omitted and therefore server-resolved)
  passes the authorization check and reaches the dependency.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("harness_memory.operations.migration.portable")

from octop.api.common.memory_client import memory_db_path_for_cfg
from octop.api.routers.memory_portable import (
    resolve_adopt_target,
    resolve_doctor_host_spec,
)
from octop.infra.agents.memory_backend import memory_namespace
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import BASELINE_PERMISSIONS
from tests.support.auth import create_agent, create_user

_AGENT_ID = "agent-under-test"


# ---------------------------------------------------------------------------
# Pure resolver tests — hostile strings that a multipart form cannot carry
# ---------------------------------------------------------------------------


def _assert_bad_args(call: Any) -> OctopError:
    with pytest.raises(OctopError) as excinfo:
        call()
    error = excinfo.value
    assert error.code == ErrorCode.SLASH_BAD_ARGS
    assert error.status == 400
    return error


def test_adopt_target_accepts_only_the_agents_own_store() -> None:
    own = memory_namespace(_AGENT_ID)
    for host, namespace in (
        (None, None),
        ("", ""),
        ("agent", ""),
        (" AGENT ", None),
        ("agent", own),
        (None, own),
    ):
        assert resolve_adopt_target(_AGENT_ID, host, namespace) == ("agent", own)


@pytest.mark.parametrize(
    "target_host", ["openclaw", "hermes", "host", "agent:openclaw", "openclaw:agent"]
)
def test_adopt_rejects_other_hosts(target_host: str) -> None:
    error = _assert_bad_args(lambda: resolve_adopt_target(_AGENT_ID, target_host, None))
    # The refusal names no caller-supplied value and no filesystem path.
    assert "openclaw" not in error.message
    assert "/" not in error.message and "\\" not in error.message


@pytest.mark.parametrize(
    "target_namespace",
    [
        "agent_someone_else",
        f"agent_{_AGENT_ID}_user_1",  # a feature caller's private namespace
        "..",
        "../../tmp/memory",
        "agent_x/../y",
        "agent_x\\y",
        "agent_x:y",
        "agent_x\x00y",
        "agent_x\ny",
        "x" * 129,
        ".",
        "agent with spaces",
    ],
)
def test_adopt_rejects_foreign_or_unsafe_namespaces(target_namespace: str) -> None:
    error = _assert_bad_args(lambda: resolve_adopt_target(_AGENT_ID, "agent", target_namespace))
    assert target_namespace.strip() not in error.message
    assert "/" not in error.message and "\\" not in error.message


@pytest.mark.parametrize(
    ("host_spec", "expected_namespace"),
    [
        ("", None),
        ("agent", None),
        (" AGENT ", None),
        ("agent:", None),
        ("agent:{own}", "{own}"),
        ("agent: {own} ", "{own}"),
    ],
)
def test_doctor_host_spec_accepts_agent_forms(
    host_spec: str, expected_namespace: str | None
) -> None:
    own = memory_namespace(_AGENT_ID)
    assert resolve_doctor_host_spec(_AGENT_ID, host_spec.format(own=own)) == (
        "agent",
        expected_namespace.format(own=own) if expected_namespace else own,
    )


@pytest.mark.parametrize(
    "host_spec",
    [
        "openclaw",
        "hermes",
        "openclaw:agent_other",
        "agent:agent_other",
        "agent:..",
        "agent:x/y",
        "agent:x\\y",
        "agent:x:y",
        ":agent_other",
        "agent:\x00",
    ],
)
def test_doctor_host_spec_rejects_everything_but_the_agents_own_store(
    host_spec: str,
) -> None:
    error = _assert_bad_args(lambda: resolve_doctor_host_spec(_AGENT_ID, host_spec))
    assert "openclaw" not in error.message
    assert "agent_other" not in error.message
    assert "/" not in error.message and "\\" not in error.message


# ---------------------------------------------------------------------------
# HTTP route tests — the resolver is enforced before the dependency is called
# ---------------------------------------------------------------------------

_ADOPT_URL = "/api/agents/{aid}/memory/portable/adopt"
_DOCTOR_URL = "/api/agents/{aid}/memory/portable/doctor"


async def _post_adopt(
    client: Any,
    auth: dict[str, str],
    agent_id: str,
    *,
    payload: bytes = b"not-a-real-hmpkg",
    **form: str,
) -> Any:
    return await client.post(
        _ADOPT_URL.format(aid=agent_id),
        headers=auth,
        files={"pkg_file": ("memory.hmpkg", payload, "application/octet-stream")},
        data=form,
    )


async def _post_doctor(client: Any, auth: dict[str, str], agent_id: str, **form: str) -> Any:
    return await client.post(_DOCTOR_URL.format(aid=agent_id), headers=auth, data=form)


@pytest.fixture
def foreign_namespace() -> str:
    """A namespace that belongs to some other agent, not the URL's agent."""
    return f"agent_{_AGENT_ID}-neighbour"


@pytest.mark.asyncio
async def test_adopt_foreign_targets_are_refused_before_the_package_is_read(
    env_with_main_agent, foreign_namespace: str
) -> None:
    client, _srv, auth, agent_id = env_with_main_agent

    for form in (
        {"target_host": "openclaw", "dry_run": "true"},
        {"target_host": "hermes", "dry_run": "true"},
        {"target_host": "agent", "target_namespace": foreign_namespace, "dry_run": "true"},
        {"target_host": "agent", "target_namespace": "../../memory", "dry_run": "true"},
    ):
        response = await _post_adopt(client, auth, agent_id, **form)
        assert response.status_code == 400, (form, response.text)
        assert response.json()["error"]["code"] == "SLASH_BAD_ARGS"
        # Garbage package bytes must not have been parsed: the target refusal
        # happens first.
        assert "hmpkg" not in response.text.lower()


@pytest.mark.asyncio
async def test_doctor_foreign_targets_are_refused(
    env_with_main_agent, foreign_namespace: str
) -> None:
    client, _srv, auth, agent_id = env_with_main_agent

    for host_spec in ("openclaw", f"agent:{foreign_namespace}", "agent:../../memory"):
        response = await _post_doctor(client, auth, agent_id, host_spec=host_spec)
        assert response.status_code == 400, (host_spec, response.text)
        assert response.json()["error"]["code"] == "SLASH_BAD_ARGS"


@pytest.mark.asyncio
async def test_adopt_between_two_agents_of_one_owner_is_refused(
    env_with_agent,
) -> None:
    """Even a legitimate owner cannot point agent B at agent A's memory store."""
    client, _srv, auth, agent_b = env_with_agent
    agent_a = await create_agent(client, auth, name="other-bot")

    response = await _post_adopt(
        client,
        auth,
        agent_b,
        target_host="agent",
        target_namespace=memory_namespace(agent_a),
        dry_run="true",
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SLASH_BAD_ARGS"


@pytest.mark.asyncio
async def test_peer_user_cannot_target_another_users_agent(env_with_agent) -> None:
    """B (a regular user) cannot point B's agent at A's memory store."""
    client, _srv, admin_auth, admin_agent = env_with_agent
    peer_auth = await create_user(
        client,
        admin_auth,
        username="portable-peer",
        permissions=sorted(BASELINE_PERMISSIONS | {"memory"}),
    )
    peer_agent = await create_agent(client, peer_auth, name="peer-bot")
    admin_namespace = memory_namespace(admin_agent)

    adopt = await _post_adopt(
        client,
        peer_auth,
        peer_agent,
        target_host="agent",
        target_namespace=admin_namespace,
        dry_run="true",
    )
    assert adopt.status_code == 400, adopt.text
    assert adopt.json()["error"]["code"] == "SLASH_BAD_ARGS"

    doctor = await _post_doctor(client, peer_auth, peer_agent, host_spec=f"agent:{admin_namespace}")
    assert doctor.status_code == 400, doctor.text
    assert doctor.json()["error"]["code"] == "SLASH_BAD_ARGS"


# ---------------------------------------------------------------------------
# Valid targets reach the dependency and dry-run without writing
# ---------------------------------------------------------------------------


def _agent_config(server: Any, agent_id: str) -> dict[str, Any]:
    row = server.services.agent_repo.get(agent_id)
    if row is None or not row.config_json:
        return {}
    try:
        parsed = json.loads(row.config_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_package(server: Any, agent_id: str, tmp_path: Path) -> bytes:
    """Seed one raw event into the agent store and pack a real ``.hmpkg``."""
    from harness_memory.core import Memory
    from harness_memory.operations.migration.portable import pack
    from harness_memory.operations.migration.portable.models import SourceInfo
    from harness_memory.types import RawEvent

    workspace = server.app_runtime.agent_registry.resolve_workspace_dir(agent_id)
    db_path = memory_db_path_for_cfg(workspace, _agent_config(server, agent_id))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    namespace = memory_namespace(agent_id)
    memory = Memory(
        namespace=namespace,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )
    memory.add_raw_batch(
        [
            RawEvent(
                id="raw-portable-authz-1",
                host="octop",
                session_id="sess-portable-authz",
                thread_id="thread-portable-authz",
                user="owner",
                timestamp=datetime.now(UTC),
                event_type="user_message",
                content="portable memory authorization fixture",
            )
        ]
    )
    out = tmp_path / "memory-portable-authz.hmpkg"
    pack(
        SourceInfo(
            host_kind="agent",
            db_path=str(db_path),
            namespace=namespace,
            agent_name=agent_id,
        ),
        out=out,
    )
    return out.read_bytes()


@pytest.mark.asyncio
async def test_adopt_own_store_dry_run_is_allowed(env_with_agent, tmp_path: Path) -> None:
    client, server, auth, agent_id = env_with_agent
    payload = _write_package(server, agent_id, tmp_path)

    response = await _post_adopt(
        client,
        auth,
        agent_id,
        payload=payload,
        target_host="agent",
        target_namespace=memory_namespace(agent_id),
        dry_run="true",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["target_namespace"] == memory_namespace(agent_id)


@pytest.mark.asyncio
async def test_adopt_defaults_resolve_to_own_store(env_with_agent, tmp_path: Path) -> None:
    """Omitting both selectors is the dashboard's normal call; it is allowed."""
    client, server, auth, agent_id = env_with_agent
    payload = _write_package(server, agent_id, tmp_path)

    response = await _post_adopt(
        client,
        auth,
        agent_id,
        payload=payload,
        dry_run="true",
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_namespace"] == memory_namespace(agent_id)


@pytest.mark.asyncio
async def test_doctor_own_store_is_allowed(env_with_agent, tmp_path: Path) -> None:
    client, server, auth, agent_id = env_with_agent
    _write_package(server, agent_id, tmp_path)

    response = await _post_doctor(client, auth, agent_id, host_spec="agent")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["host_kind"] == "agent"
    assert isinstance(body["checks"], list)
