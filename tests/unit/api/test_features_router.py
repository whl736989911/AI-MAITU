"""Unit tests for the enterprise feature directory router."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.api.routers import features as features_router
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import Feature
from octop.infra.users.identity import Role, User

USER_ID = 7


def _user(
    permissions: tuple[str, ...] = ("features",),
    denied: tuple[str, ...] = (),
) -> User:
    return User(
        id=USER_ID,
        username="u",
        role=Role.USER,
        display_name=None,
        permissions=list(permissions),
        denied_permissions=list(denied),
    )


def _request() -> SimpleNamespace:
    """Minimal request double for calling a ``require_permission`` dependency directly."""
    server = SimpleNamespace(_started=True, services=SimpleNamespace(repos=SimpleNamespace()))
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(octop_server=server)),
    )


def _feature(feature_id: str, *, unit: str = "general", output_kind: str = "markdown") -> Feature:
    return Feature(
        id=feature_id,
        version=1,
        label={"zh": f"{feature_id}·中文", "en": f"{feature_id} en"},
        description={"zh": "描述", "en": "description"},
        icon_name="receipt",
        color="#e5484d",
        unit=unit,
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string", "title": {"zh": "主题", "en": "Topic"}}},
            "required": ["topic"],
        },
        ui_schema={"order": ["topic"], "widgets": {"topic": "textarea"}},
        user_template="Draft about {{inputs}}",
        system_prompt="Be concise.",
        output_kind=output_kind,
        permissions={"allow_units": ["*"]},
    )


class _FakeTaskRepo:
    """Captures ``feature_tasks`` rows instead of touching the DB."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.created)}", **kwargs)


class _FakeRuleRepo:
    """Only the read ``run_feature`` needs: approved rules for one feature."""

    def __init__(self, approved: list[str] | None = None) -> None:
        self._approved = approved or []

    def list_approved(self, feature_id: str, limit: int) -> list[Any]:
        return [SimpleNamespace(rule_text=text) for text in self._approved[:limit]]


class _FakeGateway:
    def __init__(self) -> None:
        self.thread_registry = SimpleNamespace()
        self.session_keys: list[str] = []
        self.serialized = 0

    def require_session(self, agent_id: str, session_key: str) -> Any:
        self.session_keys.append(session_key)
        return SimpleNamespace(thread_id="thread-1", user_id=USER_ID, channel_type="dashboard")

    async def run_in_session(self, agent_id: str, session_key: str, operation: Any) -> None:
        self.serialized += 1
        await operation()


class _FakeAgentRegistry:
    def __init__(
        self,
        agents: list[Any],
        chunks: list[dict[str, Any]],
        *,
        running: bool = True,
    ) -> None:
        self._agents = agents
        self._chunks = chunks
        self._running = running
        self.requests: list[dict[str, Any]] = []
        self.started: list[str] = []

    def list_agents(self, user_id: int) -> list[Any]:
        return list(self._agents)

    def get_agent(self, agent_id: str) -> Any:
        """Live-registry lookup; raises when the agent is not loaded."""
        if not self._running:
            raise OctopError(ErrorCode.AGENT_NOT_RUNNING, f"agent {agent_id!r} is not running")
        return object()

    async def start(self, agent_id: str) -> None:
        self.started.append(agent_id)
        self._running = True

    async def stream(self, agent_id: str, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        for chunk in self._chunks:
            yield chunk


def _server(
    *,
    features: list[Feature],
    agents: list[Any] | None = None,
    chunks: list[dict[str, Any]] | None = None,
) -> tuple[Any, _FakeTaskRepo, _FakeAgentRegistry, _FakeGateway]:
    repo = _FakeTaskRepo()
    gateway = _FakeGateway()
    registry = _FakeAgentRegistry(agents if agents is not None else [], chunks or [])
    by_id = {feature.id: feature for feature in features}
    server = SimpleNamespace(
        feature_catalog=SimpleNamespace(list=lambda: list(features), get=by_id.get),
        app_runtime=SimpleNamespace(gateway=gateway, agent_registry=registry),
        services=SimpleNamespace(
            repos=SimpleNamespace(feature_tasks_repo=repo, feature_rules_repo=_FakeRuleRepo()),
        ),
    )
    return server, repo, registry, gateway


@pytest.fixture(autouse=True)
def _stub_thread_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the run path off the real thread registry."""

    async def _resolve(**kwargs: Any) -> tuple[str, str]:
        assert kwargs["thread_id"] is None and kwargs["session_key"] is None
        return "thread-1", "dashboard:agent-1:7"

    monkeypatch.setattr(features_router, "resolve_thread_id", _resolve)


async def test_list_features_returns_cards_and_unit_order() -> None:
    server, _, _, _ = _server(
        features=[
            _feature("quote-draft", unit="sales", output_kind="json"),
            _feature("meeting-notes"),
        ]
    )

    payload = await features_router.list_features(_user(), server)

    assert payload["units"] == [
        {"key": "sales", "count": 1},
        {"key": "general", "count": 1},
    ]
    assert [card["id"] for card in payload["features"]] == ["quote-draft", "meeting-notes"]
    card = payload["features"][0]
    assert card == {
        "id": "quote-draft",
        "version": 1,
        "label": {"zh": "quote-draft·中文", "en": "quote-draft en"},
        "description": {"zh": "描述", "en": "description"},
        "icon_name": "receipt",
        "color": "#e5484d",
        "unit": "sales",
        "output_kind": "json",
        "permissions": {"allow_units": ["*"]},
    }


async def test_list_features_counts_units_across_features() -> None:
    server, _, _, _ = _server(
        features=[
            _feature("a", unit="sales"),
            _feature("b", unit="sales"),
            _feature("c", unit="general"),
        ]
    )

    payload = await features_router.list_features(_user(), server)

    assert payload["units"] == [
        {"key": "sales", "count": 2},
        {"key": "general", "count": 1},
    ]


async def test_get_feature_returns_the_full_definition() -> None:
    server, _, _, _ = _server(features=[_feature("meeting-notes")])

    payload = await features_router.get_feature("meeting-notes", _user(), server)

    assert payload["input_schema"] == _feature("meeting-notes").input_schema
    assert payload["ui_schema"] == {"order": ["topic"], "widgets": {"topic": "textarea"}}
    assert payload["user_template"] == "Draft about {{inputs}}"
    assert payload["system_prompt"] == "Be concise."


async def test_get_feature_unknown_id_is_not_found() -> None:
    server, _, _, _ = _server(features=[_feature("meeting-notes")])

    with pytest.raises(OctopError) as excinfo:
        await features_router.get_feature("nope", _user(), server)

    assert excinfo.value.code is ErrorCode.NOT_FOUND


async def test_run_feature_returns_draft_and_logs_succeeded_row() -> None:
    server, repo, registry, gateway = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[
            {"type": "reasoning", "content": "ignored"},
            {"type": "token", "content": "<thinking>plan</thinking>\n"},
            {"type": "delta", "text": "## Draft\n"},
            {"type": "token", "content": "done"},
        ],
    )

    payload = await features_router.run_feature(
        "meeting-notes",
        features_router.FeatureRunBody(inputs={"topic": "增长"}),
        _user(),
        server,
    )

    assert payload == {"task_id": "task-1", "output": "## Draft\ndone", "output_kind": "markdown"}
    assert repo.created == [
        {
            "feature_id": "meeting-notes",
            "user_id": USER_ID,
            "inputs": json.dumps({"topic": "增长"}, ensure_ascii=False),
            "status": "succeeded",
            "draft": "## Draft\ndone",
        }
    ]


async def test_run_feature_sends_the_rendered_prompt_through_the_dashboard_session() -> None:
    server, _, registry, gateway = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[{"type": "token", "content": "ok"}],
    )

    await features_router.run_feature(
        "meeting-notes",
        features_router.FeatureRunBody(inputs={"topic": "增长"}),
        _user(),
        server,
    )

    assert gateway.serialized == 1
    assert gateway.session_keys == ["dashboard:agent-1:7"]
    request = registry.requests[0]
    assert request["thread_id"] == "thread-1"
    assert request["agent_id"] == "agent-1"
    assert request["source"] == "dashboard"
    assert request["configurable"]["session_key"] == "dashboard:agent-1:7"
    prompt = request["messages"][0]["content"]
    assert prompt.startswith("Draft about")
    assert "增长" in prompt


async def test_run_feature_logs_failed_row_when_output_is_empty() -> None:
    server, repo, _, _ = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[{"type": "reasoning", "content": "no visible text"}],
    )

    with pytest.raises(OctopError) as excinfo:
        await features_router.run_feature(
            "meeting-notes",
            features_router.FeatureRunBody(inputs={"topic": "增长"}),
            _user(),
            server,
        )

    assert excinfo.value.code is ErrorCode.INTERNAL_ERROR
    assert repo.created == [
        {
            "feature_id": "meeting-notes",
            "user_id": USER_ID,
            "inputs": json.dumps({"topic": "增长"}, ensure_ascii=False),
            "status": "failed",
            "error": "feature run produced no visible output",
        }
    ]


async def test_run_feature_logs_failed_row_when_interaction_is_required() -> None:
    server, repo, _, _ = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[{"type": "token", "content": "partial"}, {"type": "hitl_required"}],
    )

    with pytest.raises(OctopError) as excinfo:
        await features_router.run_feature(
            "meeting-notes",
            features_router.FeatureRunBody(inputs={"topic": "增长"}),
            _user(),
            server,
        )

    assert excinfo.value.code is ErrorCode.INTERNAL_ERROR
    assert repo.created[0]["status"] == "failed"
    assert repo.created[0]["error"] == "feature run requires user interaction"
    assert "draft" not in repo.created[0]


async def test_run_feature_starts_the_agent_when_it_is_not_running() -> None:
    """A feature run is often the user's first contact — nothing started the agent.

    Regression: the run path used to call ``stream`` straight away, so the very
    first feature run for a fresh user died with ``KeyError: 'main'`` from the
    harness registry.
    """
    server, _repo, registry, _gw = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[{"type": "token", "content": "ok"}],
    )
    registry._running = False

    await features_router.run_feature(
        "meeting-notes",
        features_router.FeatureRunBody(inputs={}),
        _user(),
        server,
    )

    assert registry.started == ["agent-1"]


async def test_run_feature_does_not_restart_a_running_agent() -> None:
    """Starting an already-loaded agent would rebuild it on every run."""
    server, _repo, registry, _gw = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
        chunks=[{"type": "token", "content": "ok"}],
    )

    await features_router.run_feature(
        "meeting-notes",
        features_router.FeatureRunBody(inputs={}),
        _user(),
        server,
    )

    assert registry.started == []


async def test_run_feature_logs_failed_row_when_the_agent_errors() -> None:
    class _ExplodingRegistry(_FakeAgentRegistry):
        async def stream(self, agent_id: str, request: dict[str, Any]) -> Any:
            raise RuntimeError("harness offline")
            yield  # pragma: no cover - keeps this an async generator

    server, repo, _, _ = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
    )
    server.app_runtime.agent_registry = _ExplodingRegistry(
        [SimpleNamespace(agent_id="agent-1")], []
    )

    with pytest.raises(OctopError):
        await features_router.run_feature(
            "meeting-notes",
            features_router.FeatureRunBody(inputs={}),
            _user(),
            server,
        )

    assert repo.created[0]["status"] == "failed"
    assert repo.created[0]["error"] == "RuntimeError: harness offline"


async def test_run_feature_unknown_id_logs_nothing() -> None:
    server, repo, registry, _ = _server(
        features=[_feature("meeting-notes")],
        agents=[SimpleNamespace(agent_id="agent-1")],
    )

    with pytest.raises(OctopError) as excinfo:
        await features_router.run_feature(
            "nope",
            features_router.FeatureRunBody(inputs={}),
            _user(),
            server,
        )

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert repo.created == []
    assert registry.requests == []


async def test_run_feature_without_an_agent_is_not_found() -> None:
    server, repo, _, _ = _server(features=[_feature("meeting-notes")])

    with pytest.raises(OctopError) as excinfo:
        await features_router.run_feature(
            "meeting-notes",
            features_router.FeatureRunBody(inputs={}),
            _user(),
            server,
        )

    assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
    assert repo.created == []


async def test_every_route_requires_the_features_permission() -> None:
    routes = {route.path: route for route in features_router.router.routes}
    assert set(routes) == {
        "",
        "/{feature_id}",
        "/{feature_id}/cases",
        "/{feature_id}/rules",
        "/{feature_id}/rules/extract",
        "/{feature_id}/run",
        "/rules/{rule_id}/approve",
        "/rules/{rule_id}/reject",
        "/tasks/{task_id}/finalize",
        "/tasks/{task_id}/promote",
    }

    for path, route in routes.items():
        keys: list[str] = []
        for dep in route.dependant.dependencies:
            try:
                # ``features`` sits in BASELINE_PERMISSIONS, so an empty grant list
                # still resolves to it. Denying the key is what actually proves the
                # gate is on ``features`` and not on some other permission. The
                # server double is passed explicitly because a direct call does not
                # run FastAPI's DI.
                await dep.call(
                    _request(),
                    _user(permissions=(), denied=("features",)),
                    _request().app.state.octop_server,
                )
            except OctopError as exc:
                if "permission" in exc.details:
                    keys.append(str(exc.details["permission"]))
            except Exception:
                continue  # not a permission gate (e.g. get_server needs a Request)
        assert keys == ["features"], path
