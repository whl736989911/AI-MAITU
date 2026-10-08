"""Thread-scoped HITL bypass must never bypass explicit user questions."""

from types import SimpleNamespace

from octop.infra.agents.security.hitl_session import (
    HitlSessionPolicyStore,
    apply_session_bypass,
    hitl_thread_scope,
)


def test_allow_all_only_bypasses_approvals_in_selected_thread() -> None:
    store = HitlSessionPolicyStore()
    store.set("approved-thread", {"mode": "allow_all"})
    wrapped = apply_session_bypass(
        {
            "shell": {"when": lambda _request: True},
            "ask_user_question": {"when": lambda _request: True},
        },
        store,
    )
    assert wrapped is not None

    with hitl_thread_scope("approved-thread"):
        assert wrapped["shell"]["when"]({}) is False
        assert wrapped["ask_user_question"]["when"]({}) is True
    with hitl_thread_scope("other-thread"):
        assert wrapped["shell"]["when"]({}) is True


def test_allow_all_bypasses_interrupt_using_langgraph_runtime_thread_id() -> None:
    store = HitlSessionPolicyStore()
    store.set("runtime-thread", {"mode": "allow_all"})
    wrapped = apply_session_bypass(
        {
            "shell": {"when": lambda _request: True},
            "ask_user_question": {"when": lambda _request: True},
        },
        store,
    )
    assert wrapped is not None
    request = SimpleNamespace(
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "runtime-thread"}})
    )

    assert wrapped["shell"]["when"](request) is False
    assert wrapped["ask_user_question"]["when"](request) is True


def test_allow_tools_bypasses_only_selected_interrupt_tool() -> None:
    store = HitlSessionPolicyStore()
    store.set("runtime-thread", {"mode": "allow_tools", "tools": ["shell"]})
    wrapped = apply_session_bypass(
        {
            "shell": {"when": lambda _request: True},
            "browser": {"when": lambda _request: True},
        },
        store,
    )
    assert wrapped is not None
    request = SimpleNamespace(
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "runtime-thread"}})
    )

    assert wrapped["shell"]["when"](request) is False
    assert wrapped["browser"]["when"](request) is True
