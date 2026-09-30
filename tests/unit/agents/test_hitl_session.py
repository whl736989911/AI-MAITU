"""Thread-scoped HITL bypass must never bypass explicit user questions."""

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
